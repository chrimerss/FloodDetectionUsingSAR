import arcgis
from pathlib import Path
import os
import time
import tempfile
import json
import logging
from .._data import _raise_fastai_import_error
from .._utils.env import HAS_TENSORFLOW, raise_tensorflow_import_error
from warnings import warn
import contextlib
import io
import sys
import socket
from functools import wraps  
import traceback    
import inspect

HAS_FASTAI = True
HAS_TENSORBOARDX = True

try:
    from fastai.callbacks import TrackerCallback, EarlyStoppingCallback
    from fastai.basic_train import LearnerCallback
    from fastai.vision.learner import model_meta, _default_meta
    from torch import nn
    import torch
    from torchvision import models
    import numpy as np
    import math
    import warnings
    from fastai.distributed import *
    import argparse
    import torch.distributed as dist
    from fastai.torch_core import get_model
    from torch.nn.parallel import DistributedDataParallel
    from .._utils.common import get_post_processed_model
except ImportError as e:
    import_exception = "\n".join(traceback.format_exception(type(e), e, e.__traceback__))
    HAS_FASTAI = False
    class TrackerCallback():
        pass
    class LearnerCallback():
        pass

try:
    import tensorboardX 
    # LearnerTensorboardWriter uses SummaryWriter from tensorboardX
    from fastai.callbacks.tensorboard import LearnerTensorboardWriter
except:
    HAS_TENSORBOARDX = False

logger = logging.getLogger()

#For lr computation, skip beginning and trailing values.
losses_skipped = 5
trailing_losses_skipped = 5
model_characteristics_folder = 'ModelCharacteristics'

if HAS_FASTAI:
    # Declare the family of backbones to be unpacked and used by different models as supported types
    _vgg_family = [models.vgg11.__name__, models.vgg11_bn.__name__, models.vgg13.__name__, models.vgg13_bn.__name__,
                        models.vgg16.__name__, models.vgg16_bn.__name__, models.vgg19.__name__, models.vgg19_bn.__name__]
    _resnet_family = [models.resnet18.__name__, models.resnet34.__name__, models.resnet50.__name__,
                           models.resnet101.__name__, models.resnet152.__name__]
    _densenet_family = [models.densenet121.__name__, models.densenet169.__name__, models.densenet161.__name__,
                             models.densenet201.__name__]

@contextlib.contextmanager
def nostdout():
    save_stdout = sys.stdout
    sys.stdout = io.StringIO()
    yield
    sys.stdout = save_stdout


class _EmptyDS(object):
    def __init__(self, size):
        self.size = (size, size)


class _EmptyData():
    def __init__(self, path, c, loss_func, chip_size, train_ds=True):
        self.path = path
        if getattr(arcgis.env, "_processorType", "") == "GPU" and torch.cuda.is_available():
            self.device = torch.device("cuda")
        elif getattr(arcgis.env, "_processorType", "") == "CPU":
            self.device = torch.device("cpu")
        else:
            self.device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
        self.c = c
        self.loss_func = loss_func
        self.chip_size = chip_size

        if train_ds:
            self.train_ds = [[_EmptyDS(chip_size)]]


class _MultiGPUCallback(LearnerCallback):
    """
    Parallize over multiple GPUs only if multiple GPUs are present.
    """
    def __init__(self, learn):
        super(_MultiGPUCallback, self).__init__(learn)
        
        self.multi_gpu = torch.cuda.device_count() > 1

    def on_train_begin(self, **kwargs):
        if self.multi_gpu:
            logger.info('Training on multiple GPUs')
            self.learn.model = nn.DataParallel(self.learn.model)
    
    def on_train_end(self, **kwargs):
        if self.multi_gpu:
            self.learn.model = self.learn.model.module

def _set_multigpu_callback(model):
    if (not hasattr(arcgis.env, "_gpuid")) or \
            (arcgis.env._gpuid >= torch.cuda.device_count()):
        model.learn.callback_fns.append(_MultiGPUCallback)

def _set_ddp_multigpu(model):

    parser = argparse.ArgumentParser()
    parser.add_argument("--local_rank", type=int)
    args, unknown = parser.parse_known_args()
    if 'RANK' in os.environ and 'WORLD_SIZE' in os.environ:
        args.rank = int(os.environ["RANK"])
        args.world_size = int(os.environ['WORLD_SIZE'])
        args.gpu = int(os.environ['LOCAL_RANK'])
    elif 'SLURM_PROCID' in os.environ:
        args.rank = int(os.environ['SLURM_PROCID'])
        args.gpu = args.rank % torch.cuda.device_count()
    elif hasattr(args, "rank"):
        pass
    else:
        model._multigpu_training = False
        return
    model._multigpu_training = True
    torch.cuda.set_device(args.gpu)
    torch.distributed.init_process_group(backend='nccl', init_method='env://',world_size=args.world_size, rank=args.rank)
    torch.distributed.barrier()
    model._rank_distributed = args.gpu

def _isnotebook():

    try:
        shell = get_ipython().__class__.__name__
        if shell == 'ZMQInteractiveShell':
            return True
        elif shell == 'TerminalInteractiveShell':
            return False
        else:
            return False
    except NameError:
        return False

def _create_zip(zipname, path):
    import shutil
    if os.path.exists(os.path.join(path, zipname) + '.dlpk'):
        os.remove(os.path.join(path, zipname) + '.dlpk')
        
    temp_dir = tempfile.TemporaryDirectory().name    
    zip_file = shutil.make_archive(os.path.join(temp_dir, zipname), 'zip', path)
    dlpk_base = os.path.splitext(zip_file)[0]
    os.rename(zip_file, dlpk_base + '.dlpk')
    dlpk_file = dlpk_base+'.dlpk'
    shutil.move(dlpk_file, path)


class SaveModelCallback(TrackerCallback):

    def __init__(self, model, every='improvement', name='bestmodel', load_best_at_end=True, **kwargs):
        super().__init__(learn=model.learn, **kwargs)
        self.model = model
        self.every = every
        self.name = name
        self.load_best_at_end = load_best_at_end
        if self.every not in ['improvement', 'epoch']:
            warn('SaveModel every {} is invalid, falling back to "improvement".'.format(self.every))
            self.every = 'improvement'

    def on_epoch_end(self, epoch, **kwargs):
        "Compare the value monitored to its best score and maybe save the model."

        if self.every == "epoch": self.model.save('{}_{}'.format(self.name, epoch))
        else: #every="improvement"
            current = self.get_monitor_value()
            if current is not None and self.operator(current, self.best):
                if arcgis.env.verbose:
                    print('saving checkpoint.')
                self.best = current
                self.model._save('{}'.format(self.name), zip_files=False, save_html=False)

    def on_train_end(self, **kwargs):
        "Load the best model."
        if int(os.environ.get('RANK', 0)):
            return
        if self.every == "improvement" and self.load_best_at_end:
            try:
                self.model.load('{}'.format(self.name))
            except FileNotFoundError:
                pass
            
            try:
                self.model.save('{}'.format(self.name))
            except:
                pass

# Multispectral Models Specific resources start #

valid_init_schemes = ['red_band', 'random', 'all_random']
rgb_map = {'r':0, 'g':1, 'b': 2}

def _get_tail(model):
    if hasattr(model, 'named_children'):
        child_name, child = next(model.named_children())
        if isinstance(child, nn.Conv2d):
            return child_name, child
            
    if hasattr(model, 'children'):
        for children in model.children():
            try:
                child_name, child =  _get_tail(children)
                return child_name, child
            except:
                pass

def _get_ms_tail(tail, data, type_init='random'):
    new_tail = nn.Conv2d(
        in_channels=len(data._extract_bands), 
        out_channels=tail.out_channels,
        kernel_size=tail.kernel_size,
        stride=tail.stride,
        padding=tail.padding,
        dilation=tail.dilation,
        groups=tail.groups,
        bias=tail.bias is not None,
        padding_mode=tail.padding_mode,
    )
    avg_weights = tail.weight.data.mean(dim=1)
    for i, j in enumerate(data._extract_bands):
        band = str(data._bands[j]).lower()
        b = rgb_map.get(band, None)
        if b is not None and not type_init == 'all_random':
            new_tail.weight.data[:, i] = tail.weight.data[:, b]
        else:
            if type_init == 'red_band':
                new_tail.weight.data[:, i] = tail.weight.data[:, 0] # Red Band Weights for all other band weights
            elif type_init == 'random' or type_init == 'all_random':
                # Random Weights for all other band weights
                pass
    return new_tail

def _set_tail(model, new_tail):
    updated = False
    if hasattr(model, 'named_children'):
        child_name, child = next(model.named_children())
        if isinstance(child, nn.Conv2d):
            setattr(model, child_name, new_tail)
            updated = True
    if hasattr(model, 'children') and not updated:
        for children in model.children():
            try:
                _set_tail(children, new_tail)
                return
            except:
                pass

def _change_tail(model, data):
    tail_name, tail = _get_tail(model)
    type_init = getattr(arcgis.env, 'type_init_tail_parameters', 'random')
    if type_init not in valid_init_schemes:
        raise Exception(f"""
        \n'{type_init}' is not a valid scheme for initializing model tail weights.
        \nplease set a valid scheme from 'red_band', 'random' or 'all_random'.
        \n`arcgis.env.type_init_tail_parameters={{valid_scheme}}`
        """)
    new_tail = _get_ms_tail(tail, data, type_init=type_init)
    _set_tail(model, new_tail)
    return model


def _get_backbone_meta(arch_name):
    _model_meta = {i.__name__:j for i, j in model_meta.items()}
    return _model_meta.get(arch_name, _default_meta)

# Multispectral Models Specific resources end #


class ArcGISModel(object):
    
    def __init__(self, data, backbone=None, **kwargs):
        if not HAS_FASTAI:
            _raise_fastai_import_error(import_exception=import_exception)

        if getattr(arcgis.env, "_processorType", "") == "GPU" and torch.cuda.is_available():
            self._device = torch.device("cuda")
        elif getattr(arcgis.env, "_processorType", "") == "CPU":
            self._device = torch.device("cpu")
        else:
            self._device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')

        if backbone is None:
            self._backbone = models.resnet34
        elif type(backbone) is str:
            if hasattr(models, backbone):
                self._backbone = getattr(models, backbone)
            elif hasattr(models.detection, backbone):
                self._backbone = getattr(models.detection, backbone)
        else:
            self._backbone = backbone

        if hasattr(data, '_is_multispectral'): # multispectral support
            self._is_multispectral = getattr(data, '_is_multispectral')
        else:
            self._is_multispectral = False
        if self._is_multispectral:
            self._imagery_type = data._imagery_type   
            self._bands = data._bands
            self._orig_backbone = self._backbone
            @wraps(self._orig_backbone)
            def backbone_wrapper(*args, **kwargs):
                return _change_tail(self._orig_backbone(*args, **kwargs), data)
            backbone_wrapper._is_multispectral = True
            self._backbone = backbone_wrapper

        self.learn = None
        self._data = data
        self._learning_rate = None
        self._backend = getattr(self, '_backend', 'pytorch')


    def _check_backbone_support(self, backbone):
        "Fetches the backbone name and returns True if it is in the list of supported backbones"
        backbone_name = backbone if type(backbone) is str else backbone.__name__
        return False if backbone_name not in self.supported_backbones else True
    
    def _arcgis_init_callback(self):
        if self._is_multispectral:
            if self._data._train_tail:
                params_iterator = self.learn.model.parameters()
                next(params_iterator).requires_grad = True # make first conv weights learnable

                tail_name, first_layer = _get_tail(self.learn.model)

                if first_layer.bias is not None or self.__class__.__name__ == 'MaskRCNN' or self.__class__.__name__ == 'ModelExtension':
                    # make first conv bias weights learnable 
                    # In case of maskrcnn make the batch norm trainable
                    next(params_iterator).requires_grad = True
                self.learn.create_opt(slice(3e-3))
            if hasattr(self, '_show_results_multispectral'):
                self.show_results = self._show_results_multispectral

    # function for checking if data exists for using class functions.
    def _check_requisites(self):
        if isinstance(self._data, _EmptyData) or getattr(self._data, '_is_empty', False):
            raise Exception("Can't call this function without data.")
    
    # function for checking if tensorflow is installed otherwise raise error.
    def _check_tf(self):
        if not HAS_TENSORFLOW:
            raise_tensorflow_import_error()

    def _init_tensorflow(self, data, backbone):
        self._check_tf()
        
        from .._utils.common import get_color_array
        from .._utils.common_tf import handle_backbone_parameter, get_input_shape, check_backbone_is_mobile_optimized

        # Get color Array
        color_array = get_color_array(data.color_mapping)
        if len(data.color_mapping) == (data.c -1 ):
            # Add Background color
            color_array = np.concatenate([np.array([[0.0, 0.0, 0.0, 0.0]]), color_array]) 
        data._multispectral_color_array = color_array

        # Handle Backbone
        self._backbone = handle_backbone_parameter(backbone)

        self._backbone_mobile_optimized = check_backbone_is_mobile_optimized(self._backbone)
    
        # Initialize Backbone
        in_shape = get_input_shape(data.chip_size)
        self._backbone_initalized = self._backbone(
            input_shape=in_shape, 
            include_top=False, 
            weights='imagenet'
        )

        self._backbone_initalized.trainable = False
        self._device = torch.device('cpu')
        self._data = data

    def lr_find(self, allow_plot=True):
        """
        Runs the Learning Rate Finder, and displays the graph of it's output.
        Helps in choosing the optimum learning rate for training the model.
        """
        self._check_requisites()

        self.learn.lr_find()
        from IPython.display import clear_output
        clear_output()
        lr, index = self._find_lr()
        if allow_plot:
            self._show_lr_plot(index)

        return lr

    def _show_lr_plot(self, index, losses_skipped=losses_skipped, trailing_losses_skipped=trailing_losses_skipped):
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 1)
        losses = self.learn.recorder.losses
        lrs = self.learn.recorder.lrs
        final_losses_skipped = 0
        if len(self.learn.recorder.losses[losses_skipped:-trailing_losses_skipped]) >= 5:
            losses = self.learn.recorder.losses[losses_skipped:-trailing_losses_skipped]
            lrs = self.learn.recorder.lrs[losses_skipped:-trailing_losses_skipped]
            final_losses_skipped = losses_skipped
        ax.plot(
            lrs,
            losses
        )
        ax.set_ylabel("Loss")
        ax.set_xlabel("Learning Rate")
        ax.set_xscale('log')
        ax.xaxis.set_major_formatter(plt.FormatStrFormatter('%.0e'))
        ax.plot(
            self.learn.recorder.lrs[index],
            self.learn.recorder.losses[index],
            markersize=10,
            marker='o',
            color='red'
        )

        plt.show()

    def _find_lr(self, losses_skipped=losses_skipped, trailing_losses_skipped=trailing_losses_skipped, section_factor=3):
        losses = self.learn.recorder.losses
        lrs = self.learn.recorder.lrs
        final_losses_skipped = 0
        if len(self.learn.recorder.losses[losses_skipped:-trailing_losses_skipped]) >=5:
            losses = self.learn.recorder.losses[losses_skipped:-trailing_losses_skipped]
            lrs = self.learn.recorder.lrs[losses_skipped:-trailing_losses_skipped]
            final_losses_skipped = losses_skipped

        n = len(losses)

        max_start = 0
        max_end = 0

        lds = [1] * n

        for i in range(1, n):
            for j in range(0, i):
                if losses[i] < losses[j] and lds[i] < lds[j] + 1:
                    lds[i] = lds[j] + 1
                if lds[max_end] < lds[i]:
                    max_end = i
                    max_start = max_end - lds[max_end]

        sections = (max_end - max_start) / section_factor
        final_index = max_start + int(sections) + int(sections/2)
        return lrs[final_index], final_losses_skipped + final_index

    @property
    def _model_metrics(self):
        raise NotImplementedError

    def fit(self, epochs=10, lr=None, one_cycle=True, early_stopping=False, checkpoint=True, tensorboard=False, **kwargs):
        """
        Train the model for the specified number of epochs and using the
        specified learning rates
        
        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        epochs                  Required integer. Number of cycles of training
                                on the data. Increase it if underfitting.
        ---------------------   -------------------------------------------
        lr                      Optional float or slice of floats. Learning rate
                                to be used for training the model. If ``lr=None``, 
                                an optimal learning rate is automatically deduced 
                                for training the model.
        ---------------------   -------------------------------------------
        one_cycle               Optional boolean. Parameter to select 1cycle
                                learning rate schedule. If set to `False` no 
                                learning rate schedule is used.       
        ---------------------   -------------------------------------------
        early_stopping          Optional boolean. Parameter to add early stopping.
                                If set to 'True' training will stop if validation
                                loss stops improving for 5 epochs.        
        ---------------------   -------------------------------------------
        checkpoint              Optional boolean. Parameter to save the best model
                                during training. If set to `True` the best model 
                                based on validation loss will be saved during 
                                training.
        ---------------------   -------------------------------------------
        tensorboard             Optional boolean. Parameter to write the training log. 
                                If set to 'True' the log will be saved at 
                                <dataset-path>/training_log which can be visualized in
                                tensorboard. Required tensorboardx version=1.7 (Experimental support).

                                The default value is 'False'.
        =====================   ===========================================
        """
        self._check_requisites()

        if lr is None:
            print('Finding optimum learning rate.')

            lr = self.lr_find(allow_plot=False)
            lr = slice(lr/10, lr)

        self._learning_rate = lr
        
        if arcgis.env.verbose:
            logger.info('Fitting the model.')        
        
        if getattr(self, '_backend', 'pytorch') == 'tensorflow':
            checkpoint = False

        callbacks = kwargs['callbacks'] if 'callbacks' in kwargs.keys() else []
        kwargs.pop('callbacks', None)
        if early_stopping:
            callbacks.append(EarlyStoppingCallback(learn=self.learn, monitor='valid_loss', min_delta=0.01, patience=5))
        if checkpoint:
            from datetime import datetime
            now = datetime.now()
            callbacks.append(SaveModelCallback(self, monitor='valid_loss', every='improvement', name=now.strftime("checkpoint_%Y-%m-%d_%H-%M-%S")))
        
        # If tensorboardx is installed write a log with name as timestamp
        if tensorboard and HAS_TENSORBOARDX:
            training_id = time.strftime("log_%Y-%m-%d_%H-%M-%S")
            log_path = Path(os.path.dirname(self._data.path)) / 'training_log'
            callbacks.append(LearnerTensorboardWriter(learn=self.learn, base_dir=log_path, name=training_id))
            hostname = socket.gethostname()
            print("Monitor training using Tensorboard using the following command: 'tensorboard --host={} --logdir={}'".format(hostname, log_path))
        # Send out a warning if tensorboardX is not installed
        elif tensorboard:
            warn("Install tensorboardX 1.7 'pip install tensorboardx==1.7' to write training log")

        if one_cycle:
            self.learn.fit_one_cycle(epochs, lr, callbacks=callbacks, **kwargs)
        else:
            self.learn.fit(epochs, lr, callbacks=callbacks, **kwargs)
        
    def unfreeze(self):
        """
        Unfreezes the earlier layers of the model for fine-tuning.
        """
        self.learn.unfreeze()

    def plot_losses(self):
        """
        Plot validation and training losses after fitting the model.
        """
        if hasattr(self.learn, 'recorder'):
            self.learn.recorder.plot_losses()

    def _create_emd_template(self, path):

        _emd_template = {}
        #For old models - add lr, ModelName
        if isinstance(self._data, _EmptyData) or getattr(self._data, '_is_empty', False):
            _emd_template = self._data.emd
            _emd_template["ModelFile"] = path.name
            if not _emd_template.get("ModelName"):
                _emd_template["ModelName"] = type(self).__name__

            if not _emd_template.get("LearningRate"):
                _emd_template["LearningRate"] = "0.0"

            return _emd_template
        
        if self._backbone is None:
            backbone = self._backbone
        else:
            if self._backend == 'tensorflow':
                backbone = self._backbone._keras_api_names[-1].split('.')[-1]
            else:
                backbone = self._backbone.__name__
            if backbone == 'backbone_wrapper':
                backbone = self._orig_backbone.__name__

        _emd_template = self._get_emd_params()
        
        if isinstance(self._learning_rate, slice):
            _emd_lr = slice('{0:1.4e}'.format(self._learning_rate.start), '{0:1.4e}'.format(self._learning_rate.stop))
        elif self._learning_rate is not None:
            _emd_lr = '{0:1.4e}'.format(self._learning_rate)
        else:
            _emd_lr = None

        _emd_template["ModelFile"] = path.name

        if hasattr(self._data, 'chip_size'):
            _emd_template["ImageHeight"] = self._data.chip_size
            _emd_template["ImageWidth"] = self._data.chip_size

        if hasattr(self._data, '_image_space_used'):
            _emd_template["ImageSpaceUsed"] = self._data._image_space_used

        _emd_template["LearningRate"] = str(_emd_lr)
        _emd_template["ModelName"] = type(self).__name__
        _emd_template["backend"] = self._backend

        model_params = {
            "backbone": backbone,
            "backend": self._backend
            }
        if _emd_template.get("ModelParameters", None) is None:
            _emd_template["ModelParameters"] = model_params
        else:
            for _key in model_params:
                _emd_template["ModelParameters"][_key] = model_params[_key]

        model_metrics = self._model_metrics

        if model_metrics.get('accuracy'):
            _emd_template['accuracy'] = model_metrics.get('accuracy')
        
        if model_metrics.get('average_precision_score'):
            _emd_template['average_precision_score'] = model_metrics.get('average_precision_score')
            
        if model_metrics.get('psnr_metric'):
            _emd_template['psnr_metric'] = model_metrics.get('psnr_metric')

        if model_metrics.get('score'):
            _emd_template['score'] = model_metrics.get('score')

        resize_to = None
        if hasattr(self._data, 'resize_to') and self._data.resize_to:
            resize_to = self._data.resize_to

        _emd_template['resize_to'] = resize_to
        
        # Check if model is Multispectral and dump parameters for that
        _emd_template["IsMultispectral"] = getattr(self, '_is_multispectral', False)
        if _emd_template.get("IsMultispectral", False):
            _emd_template["Bands"] = self._data._bands
            _emd_template["ImageryType"] = self._data._imagery_type
            _emd_template["ExtractBands"] = self._data._extract_bands
            _emd_template["NormalizationStats"] = {
                "band_min_values": self._data._band_min_values,
                "band_max_values": self._data._band_max_values,
                "band_mean_values": self._data._band_mean_values,
                "band_std_values": self._data._band_std_values,
                "scaled_min_values": self._data._scaled_min_values,
                "scaled_max_values": self._data._scaled_max_values,
                "scaled_mean_values": self._data._scaled_mean_values,
                "scaled_std_values": self._data._scaled_std_values
            }
            for _stat in _emd_template["NormalizationStats"]:
                if _emd_template["NormalizationStats"][_stat] is not None:
                    _emd_template["NormalizationStats"][_stat] = _emd_template["NormalizationStats"][_stat].tolist()
            _emd_template["DoNormalize"] = self._data._do_normalize

        return _emd_template

    @staticmethod
    def _write_emd(_emd_template, path):
        json.dump(_emd_template, open(path, 'w'), indent=4)

        return path.stem

    def _get_emd_params(self):
        return {}

    @staticmethod
    def _create_html(path_model):
        import base64

        model_characteristics_dir = os.path.join(path_model.parent.absolute(), model_characteristics_folder)
        loss_graph = os.path.join(model_characteristics_dir, 'loss_graph.png')
        show_results = os.path.join(model_characteristics_dir, 'show_results.png')
        confusion_matrix = os.path.join(model_characteristics_dir, 'confusion_matrix.png')

        encoded_losses_img = None
        if os.path.exists(loss_graph):
            encoded_losses_img = "data:image/png;base64,{0}".format(base64.b64encode(open(loss_graph, 'rb').read()).decode('utf-8'))

        encoded_showresults = None
        if os.path.exists(show_results):
            encoded_showresults = "data:image/png;base64,{0}".format(base64.b64encode(open(show_results, 'rb').read()).decode('utf-8'))

        confusion_matrix_img = None
        if os.path.exists(confusion_matrix):
            confusion_matrix_img = "data:image/png;base64,{0}".format(base64.b64encode(open(confusion_matrix, 'rb').read()).decode('utf-8'))

        html_file_path = os.path.join(path_model.parent, 'model_metrics.html')

        emd_path = os.path.join(path_model.parent, path_model.stem + '.emd')
        if not os.path.exists(emd_path):
            return

        emd_template = json.load(open(emd_path, 'r'))

        encoded_losses = ""
        if encoded_losses_img:
            encoded_losses = f"""
                <p><b>Training and Validation loss</b></p>
                <img src="{encoded_losses_img}" alt="training and validation losses">
            """

        HTML_TEMPLATE = f"""        
                <p><b> {emd_template.get("ModelName").replace('>', '').replace('<', '')} </b></p>
                <p><b>Backbone:</b> {emd_template.get('ModelParameters', {}).get('backbone')}</p>
                <p><b>Learning Rate:</b> {emd_template.get('LearningRate')}</p>
                {encoded_losses}
        """

        model_analysis = None
        if confusion_matrix_img:
             model_analysis = f""" <p><b>Confusion Matrix</p></b>
                    <img src="{confusion_matrix_img}" alt="Confusion Matrix" width="500" height="333">
            """

        if emd_template.get('accuracy'):
            model_analysis = f"""
            <p><b>Accuracy:</b> {emd_template.get('accuracy')}</p>
        """

        if emd_template.get('average_precision_score'):
            model_analysis = f"""
            <p><b>Average Precision Score:</b> {emd_template.get('average_precision_score')}</p>
        """

        if emd_template.get('score'):
            model_analysis = f"""
            <p><b>Score:</b> {emd_template.get('score')}</p>
        """

        if emd_template.get('psnr_metric'):
            model_analysis = f"""
            <p><b>PSNR Metric:</b> {emd_template.get('psnr_metric')}</p>
        """

        if model_analysis:
            HTML_TEMPLATE += f"""
            <p><b>Analysis of the model</b></p>
            {model_analysis}
        """

        if encoded_showresults:
            HTML_TEMPLATE += f"""
                <p><b>Sample Results</b></p>
                <img src="{encoded_showresults}" alt="Sample Results">
            """

        file = open(html_file_path, 'w')
        file.write(HTML_TEMPLATE)
        file.close()

    def _save(self, name_or_path, framework='PyTorch', zip_files=True, save_html=True, publish=False, gis=None, **kwargs):
        save_format = kwargs.get('save_format', 'default') # 'default', 'tflite'
        post_processed = kwargs.get('post_processed', True) # True, False
        quantized = kwargs.get('quantized', False) # True, False
        temp = self.learn.path

        if '\\' in name_or_path or '/' in name_or_path:
            path = Path(name_or_path)
            name = path.parts[-1]
            # to make fastai save to both path and with name    
            self.learn.path = path
            self.learn.model_dir = ''
            if not os.path.exists(self.learn.path):
                os.makedirs(self.learn.path)
        else:
            # fixing fastai bug
            self.learn.path = self.learn.path.parent
            self.learn.model_dir = Path(self.learn.model_dir) / name_or_path
            if not os.path.exists(self.learn.path / self.learn.model_dir):
                os.makedirs(self.learn.path / self.learn.model_dir)
            name = name_or_path

        try:
            _framework = framework.lower()
            if self._backend == 'tensorflow' and _framework == 'tflite':
                saved_path = self._save_tflite(name, post_processed=post_processed, quantized=quantized)
            # elif self._backend == 'tensorflow' and _framework != 'tflite':
            #     _err_msg = """
            #     Models initialized with parameter backend="tensorflow" are currently only supported to be saved into tflite framework
            #     \nPlease set parameter framework="tflite"
            #     """
            #     raise Exception(_err_msg)
            elif self._backend != 'tensorflow' and _framework == 'tflite':
                _err_msg = """
                Only models initialized with parameter backend="tensorflow" are supported to be saved into tflite framework
                """
                raise Exception(_err_msg)
            else:

                if isinstance(self.learn.model, (DistributedDataParallel)):
                    if not int(os.environ.get('RANK', 0)):
                        saved_path = self.learn.save(name,  return_path=True)
                    return

                saved_path = self.learn.save(name,  return_path=True)

            # undoing changes to self.learn.path
        except Exception as e:
            raise e
        finally:

            self.learn.path = temp
            self.learn.model_dir = 'models'

        _emd_template = self._create_emd_template(saved_path.with_suffix('.pth'))

        if framework.lower() == "tf-onnx":
            batch_size = kwargs.get('batch_size', 16)

            with nostdout():
                self._save_as_tfonnx(saved_path, batch_size)

            self._create_tfonnx_emd_template(_emd_template, saved_path.with_suffix('.onnx'), batch_size)
            os.remove(saved_path.with_suffix('.pth'))

        ArcGISModel._write_emd(_emd_template, saved_path.with_suffix('.emd'))
        zip_name = saved_path.stem

        if save_html:
            try:
                self._save_model_characteristics(saved_path.parent.absolute() / model_characteristics_folder)
                ArcGISModel._create_html(saved_path)
            except:
                pass

        if _emd_template.get('InferenceFunction', False):
            with open(saved_path.parent / _emd_template['InferenceFunction'], 'w') as f:
                f.write(self._code)

        if _emd_template.get('ModelConfigurationFile', False):
            with open(saved_path.parent / _emd_template['ModelConfigurationFile'], 'w') as f:
                f.write(inspect.getsource(self.model_conf_class))

        if zip_files:
            _create_zip(str(zip_name), str(saved_path.parent))

        if arcgis.env.verbose:
            print('Created model files at {spp}'.format(spp=saved_path.parent))

        if publish:
            self._publish_dlpk((saved_path.parent/saved_path.stem).with_suffix('.dlpk'), gis=gis, overwrite=kwargs.get('overwrite', False))

        return saved_path.parent

    def _save_tflite(self, name, post_processed=True, quantized=False):
        if post_processed or quantized:
            input_normalization = quantized is False
            return self.learn._save_tflite(name, return_path=True, model_to_save=self._get_post_processed_model(input_normalization=input_normalization), quantized=quantized, data=self._data)
        return self.learn._save_tflite(name)

    def _get_post_processed_model(self, input_normalization=True):
        return get_post_processed_model(self, input_normalization=input_normalization)

    def _save_model_characteristics(self, model_characteristics_dir):

        import shutil
        import matplotlib.pyplot as plt

        if isinstance(self._data, _EmptyData) or getattr(self._data, '_is_empty', False):
            if not os.path.exists(os.path.join(self._data.emd_path.parent, model_characteristics_folder)):
                return
            temp_path = tempfile.NamedTemporaryFile().name
            shutil.copytree(
                os.path.join(self._data.emd_path.parent, model_characteristics_folder),
                temp_path
            )
            if os.path.exists(os.path.join(model_characteristics_dir, model_characteristics_dir)):
                shutil.rmtree(os.path.join(model_characteristics_dir, model_characteristics_dir), ignore_errors=True)

            shutil.copytree(
                temp_path,
                os.path.join(model_characteristics_dir, model_characteristics_dir)
            )

            return

        if not os.path.exists(os.path.join(model_characteristics_dir, model_characteristics_dir)):
            os.mkdir(os.path.join(model_characteristics_dir, model_characteristics_dir))

        if hasattr(self.learn, 'recorder'):
            try:
                self.learn.recorder.plot_losses()
                plt.savefig(os.path.join(model_characteristics_dir, 'loss_graph.png'))
                plt.close()
            except:
                plt.close()

        if self.__str__() == '<PointCNN>':
            self.show_results(save_html=True, save_path=model_characteristics_dir)
        elif hasattr(self, 'show_results'):
            self.show_results()
            plt.savefig(os.path.join(model_characteristics_dir, 'show_results.png'))
            plt.close()

        if hasattr(self, '_save_confusion_matrix'):
            self._save_confusion_matrix(model_characteristics_dir)

    def _publish_dlpk(self, dlpk_path, gis=None, overwrite=False):
        gis_user = arcgis.env.active_gis if gis is None else gis
        if not gis_user:
            warn('No active gis user found!')
            return

        if not os.path.exists(dlpk_path):
            warn('DLPK file not found!')
            return

        emd_path = os.path.join(dlpk_path.parent, dlpk_path.stem + '.emd')

        if not os.path.exists(emd_path):
            warn('EMD File not found!')
            return

        emd_data = json.load(open(emd_path, 'r'))
        formatted_description = f"""
                <p><b> {emd_data.get('ModelName').replace('>', '').replace('<', '')} </b></p>
                <p><b>Backbone:</b> {emd_data.get('ModelParameters', {}).get('backbone')}</p>
                <p><b>Learning Rate:</b> {emd_data.get('LearningRate')}</p>
        """

        if emd_data.get('accuracy'):
            formatted_description = formatted_description + f"""
                <p><b>Analysis of the model</b></p>
                <p><b>Accuracy:</b> {emd_data.get('accuracy')}</p>
            """

        if emd_data.get('average_precision_score'):
            formatted_description = formatted_description + f"""
                <p><b>Analysis of the model</b></p>
                <p><b>Average Precision Score:</b> {emd_data.get('average_precision_score')}</p>
            """

        item = gis_user.content.add(
            {'type': 'Deep Learning Package', 'description': formatted_description, 'title': dlpk_path.stem, 'overwrite':'true' if overwrite else 'false'},
            data=str(dlpk_path.absolute())
        )

        print(f"Published DLPK Item Id: {item.itemid}")

        model_characteristics_dir = os.path.join(dlpk_path.parent.absolute(), model_characteristics_folder)
        screenshots = [os.path.join(model_characteristics_dir, screenshot) for screenshot in os.listdir(model_characteristics_dir)]

        item.update(item_properties={'screenshots': screenshots})

    def _create_tfonnx_emd_template(self, _emd_template, saved_path, batch_size):
        _emd_template.update(self._get_tfonnx_emd_params())
        _emd_template['BatchSize'] = batch_size
        _emd_template["ModelFile"] = saved_path.name

        return _emd_template

    def _get_tfonnx_emd_params(self):
        # Raises error if framework specified is TF-ONNX but is not supported by the model
        raise NotImplementedError('TF-ONNX framework is currently not supported by this model.')

    def _save_as_tfonnx(self, saved_path, batch_size):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import onnx
                from onnx_tf.backend import prepare
        except:
            raise Exception('Tensorflow(version 1.13.1 or above), Onnx(version 1.5.0) and Onnx_tf(version 1.3.0) libraries are not installed. Install Tensorflow using "conda install tensorflow-gpu=1.13.1". Install onnx and onnx_tf using "pip install onnx onnx_tf".')

        batch_size = int(math.sqrt(int(batch_size)))**2
        dummy_input = torch.randn(batch_size, 3, self._data.chip_size, self._data.chip_size, device=self._device, requires_grad=True)
        torch.onnx.export(self.learn.model, dummy_input, saved_path.with_suffix('.onnx'))

    def save(self, name_or_path, framework='PyTorch', publish=False, gis=None, **kwargs):
        """
        Saves the model weights, creates an Esri Model Definition and Deep
        Learning Package zip for deployment to Image Server or ArcGIS Pro.   
        
        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        name_or_path            Required string. Name of the model to save. It
                                stores it at the pre-defined location. If path
                                is passed then it stores at the specified path
                                with model name as directory name and creates
                                all the intermediate directories.
        ---------------------   -------------------------------------------
        framework               Optional string. Defines the framework of the
                                model. (Only supported by ``SingleShotDetector``, currently.)
                                If framework used is ``TF-ONNX``, ``batch_size`` can be
                                passed as an optional keyword argument. 
                                
                                Framework choice: 'PyTorch' and 'TF-ONNX'
        ---------------------   -------------------------------------------
        publish                 Optional boolean. Publishes the DLPK as an item.
        ---------------------   -------------------------------------------
        gis                     Optional GIS Object. Used for publishing the item.
                                If not specified then active gis user is taken.
        ---------------------   -------------------------------------------
        kwargs                  Optional Parameters:
                                Boolean `overwrite` if True, it will overwrite
                                the item on ArcGIS Online/Enterprise, default False.                                
        =====================   ===========================================
        """    
        if int(os.environ.get('RANK', 0)):
            return
        return self._save(name_or_path, framework=framework, publish=publish, gis=gis, **kwargs)
        
    def load(self, name_or_path):
        """
        Loads a saved model for inferencing or fine tuning from the specified
        path or model name.
        
        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        name_or_path            Required string. Name of the model to load from
                                the pre-defined location. If path is passed then
                                it loads from the specified path with model name
                                as directory name. Path to ".pth" file can also
                                be passed
        =====================   ===========================================
        """
        temp = self.learn.path
        if '\\' in name_or_path or '/' in name_or_path:
            path = Path(name_or_path)
            # to make fastai from both path and with name
            if path.is_file():
                name = path.stem
                self.learn.path = path.parent
            else:
                name = path.parts[-1]
                self.learn.path = path
            self.learn.model_dir = ''
        else:
            # fixing fastai bug
            self.learn.path = self.learn.path.parent
            self.learn.model_dir = Path(self.learn.model_dir) / name_or_path
            name = name_or_path

        try:
            self.learn.load(name, purge=False)
        except Exception as e:
            raise e
        finally:
            # undoing changes to self.learn.path
            self.learn.path = temp
            self.learn.model_dir = 'models'
