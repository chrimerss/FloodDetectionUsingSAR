from pathlib import Path
import json
from ._arcgis_model import _EmptyData, _change_tail, ArcGISModel
from ._codetemplate import code
import warnings
import arcgis
import sys, os, importlib

try:
    import torch
    from torch import nn
    from fastai.basic_train import Learner, LearnerCallback
    from fastai.torch_core import split_model_idx
    from fastai.vision import ImageList
    from fastai.core import has_arg, split_kwargs_by_func
    from fastai.basic_data import DatasetType
    from fastai.callback import Callback
    from fastai.torch_core import to_cpu, grab_idx
    from fastai.basic_train import loss_batch 
    from ._ssd_utils import compute_class_AP
    from .._utils.pascal_voc_rectangles import show_results_multispectral
    from .._utils.common import get_multispectral_data_params_from_emd
    from ._arcgis_model import _set_ddp_multigpu, _isnotebook
    import inspect
    HAS_FASTAI = True
    
except Exception as e:
    HAS_FASTAI = False

class ModelExtension(ArcGISModel):
    """
    Creates a ``ModelExtension`` object, object detection model to train a model from your own source.

    =====================   ============================================================
    **Argument**            **Description**
    ---------------------   ------------------------------------------------------------
    data                    Required fastai Databunch. Returned data object from
                            ``prepare_data`` function.
    ---------------------   ------------------------------------------------------------
    model_conf              A class definition contains the following methods:

                                * ``get_model(self, data, backbone=None)``: for model definition,
                                
                                * ``on_batch_begin(self, learn, model_input_batch, model_target_batch)``: for 
                                  feeding input to the model during training, 

                                * ``transform_input(self, xb)``: for feeding input to the model during
                                  inferencing/validation,

                                * ``transform_input_multispectral(self, xb)``: for feeding input to the
                                  model during inferencing/validation in case of multispectral data,

                                * ``loss(self, model_output, *model_target)``: to return loss value of the model, and 

                                * ``post_process(self, pred, nms_overlap, thres, chip_size, device)``: to post-process
                                  the output of the model.
    ---------------------   ------------------------------------------------------------
    backbone                Optional function. If custom model requires any backbone.
    ---------------------   ------------------------------------------------------------
    pretrained_path         Optional string. Path where pre-trained model is
                            saved.
    =====================   ============================================================

    :return: ``ModelExtension`` Object
    """

    def __init__(self, data, model_conf, backbone=None, pretrained_path=None):

        super().__init__(data, backbone)
        self.model_conf = model_conf()
        self.model_conf_class  = model_conf
        self._backend = 'pytorch'
        model = self.model_conf.get_model(data, backbone)
        if self._is_multispectral:
            model = _change_tail(model, data)
        if not _isnotebook() and os.name=='posix':
            _set_ddp_multigpu(self)
            if self._multigpu_training:
                self.learn = Learner(data, model, loss_func=self.model_conf.loss).to_distributed(self._rank_distributed)
            else:
                self.learn = Learner(data, model, loss_func=self.model_conf.loss)
        else:
            self.learn = Learner(data, model, loss_func=self.model_conf.loss)
        self.learn.callbacks.append(self.train_callback(self.learn, self.model_conf.on_batch_begin))
        self._code = code
        self._arcgis_init_callback() # make first conv weights learnable
        if pretrained_path is not None:
            self.load(pretrained_path)


    if HAS_FASTAI:
        class train_callback(LearnerCallback):

            def __init__(self, learn, on_batch_begin_fn):
                super().__init__(learn)
                self.on_batch_begin_fn = on_batch_begin_fn

            def on_batch_begin(self, last_input, last_target, train, **kwargs):

                last_input, last_target = self.on_batch_begin_fn(self.learn, last_input, last_target)

                return {'last_input':last_input, 'last_target':last_target}

    def _analyze_pred(self, pred, thresh=0.5, nms_overlap=0.1, ret_scores=True, device=None):
        return self.model_conf.post_process(pred, nms_overlap, thresh, self.learn.data.chip_size, device)
       
    def _get_emd_params(self):
        import random
        _emd_template = {}
        _emd_template["Framework"] = "arcgis.learn.models._inferencing"
        _emd_template["InferenceFunction"] = "ArcGISObjectDetector.py"
        _emd_template["ModelConfiguration"] = "_model_extension_inferencing"
        _emd_template["ModelType"] = "ObjectDetection"
        _emd_template["ExtractBands"] = [0, 1, 2]
        _emd_template['Classes'] = []
        _emd_template['ModelConfigurationFile'] = "ModelConfiguration.py"
        _emd_template['ModelFileConfigurationClass'] = type(self.model_conf).__name__

        class_data = {}
        for i, class_name in enumerate(self._data.classes[1:]):  # 0th index is background
            inverse_class_mapping = {v: k for k, v in self._data.class_mapping.items()}
            class_data["Value"] = inverse_class_mapping[class_name]
            class_data["Name"] = class_name
            color = [random.choice(range(256)) for i in range(3)]
            class_data["Color"] = color
            _emd_template['Classes'].append(class_data.copy())

        return _emd_template

    @property
    def _is_model_extension(self):
        return True

    @classmethod
    def from_model(cls, emd_path, data=None):
        """
        Creates a ``ModelExtension`` object from an Esri Model Definition (EMD) file.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        emd_path                Required string. Path to Esri Model Definition
                                file.
        ---------------------   -------------------------------------------
        data                    Required fastai Databunch or None. Returned data
                                object from ``prepare_data`` function or None for
                                inferencing.

        =====================   ===========================================

        :returns: `ModelExtension` Object
        """

        emd_path = Path(emd_path)

        with open(emd_path) as f:
            emd = json.load(f)
            
        model_file = Path(emd['ModelFile'])
        
        if not model_file.is_absolute():
            model_file = emd_path.parent / model_file
        
        modelconf = Path(emd['ModelConfigurationFile'])

        if not modelconf.is_absolute():
            modelconf = emd_path.parent / modelconf

        modelconfclass = emd['ModelFileConfigurationClass']

        sys.path.append(os.path.dirname(modelconf))
        model_configuration = getattr(importlib.import_module('{}'.format(modelconf.name[0:-3])), modelconfclass)

        backbone = emd['ModelParameters']['backbone']

        try:
            class_mapping = {i['Value'] : i['Name'] for i in emd['Classes']}
            color_mapping = {i['Value'] : i['Color'] for i in emd['Classes']}
        except KeyError:
            class_mapping = {i['ClassValue'] : i['ClassName'] for i in emd['Classes']} 
            color_mapping = {i['ClassValue'] : i['Color'] for i in emd['Classes']}                

        if data is None:
            data = _EmptyData(path=emd_path.parent.parent, loss_func=None, c=len(class_mapping) + 1, chip_size=emd['ImageHeight'])
            data.class_mapping = class_mapping
            data.color_mapping = color_mapping
            data.emd_path = emd_path
            data.emd = emd
            data.classes =['background']
            for k, v in class_mapping.items():
                data.classes.append(v)
            data = get_multispectral_data_params_from_emd(data, emd)
        return cls(data, model_configuration, backbone, pretrained_path=str(model_file))

    @property
    def _model_metrics(self):
        return {'average_precision_score': self.average_precision_score(show_progress=False)}

    def _get_y(self, bbox, clas):
        try:
            bbox = bbox.view(-1, 4)
        except Exception:
            bbox = torch.zeros(size=[0, 4])
        bb_keep = ((bbox[:,2]-bbox[:,0])>0).nonzero()[:,0]
        return bbox[bb_keep],clas[bb_keep]

    def _intersect(self,box_a, box_b):
        max_xy = torch.min(box_a[:, None, 2:], box_b[None, :, 2:])
        min_xy = torch.max(box_a[:, None, :2], box_b[None, :, :2])
        inter = torch.clamp((max_xy - min_xy), min=0)
        return inter[:, :, 0] * inter[:, :, 1]

    def _box_sz(self, b):
        return (b[:, 2]-b[:, 0]) * (b[:, 3]-b[:, 1])

    def _jaccard(self, box_a, box_b):
        inter = self._intersect(box_a, box_b)
        union = self._box_sz(box_a).unsqueeze(1) + self._box_sz(box_b).unsqueeze(0) - inter
        return inter / union

    def show_results(self, rows=5, thresh=0.5, nms_overlap=0.1):

        """
        Displays the results of a trained model on a part of the validation set.
        """
        self._check_requisites()
        if rows > len(self._data.valid_ds):
            rows = len(self._data.valid_ds)
        self._show_results_modified(rows=rows, thresh=thresh, nms_overlap=nms_overlap, model=self)

    def _show_results_multispectral(self, rows=5, thresh=0.3, nms_overlap=0.1, alpha=1, **kwargs):
        ax = show_results_multispectral(
            self,
            nrows=rows, 
            thresh=thresh, 
            nms_overlap=nms_overlap, 
            alpha=alpha, 
            **kwargs
        )

    def _show_results_modified(self, rows=5, **kwargs):

        if rows > len(self._data.valid_ds):
            rows = len(self._data.valid_ds)

        ds_type = DatasetType.Valid
        n_items = rows ** 2 if self.learn.data.train_ds.x._square_show_res else rows
        if self.learn.dl(ds_type).batch_size < n_items: n_items = self.learn.dl(ds_type).batch_size
        ds = self.learn.dl(ds_type).dataset
        xb,yb = self.learn.data.one_batch(ds_type, detach=False, denorm=False)
        self.learn.model.eval()
        preds = self.learn.model(self.model_conf.transform_input(xb))
        x,y = to_cpu(xb),to_cpu(yb)
        norm = getattr(self.learn.data,'norm',False)
        if norm:
            x = self.learn.data.denorm(x)
            if norm.keywords.get('do_y',False):
                y     = self.learn.data.denorm(y, do_x=True)
                preds = self.learn.data.denorm(preds, do_x=True)
        analyze_kwargs,kwargs = split_kwargs_by_func(kwargs, ds.y.analyze_pred)
        preds = ds.y.analyze_pred(preds,**analyze_kwargs)
        xs = [ds.x.reconstruct(grab_idx(x, i)) for i in range(n_items)]
        if has_arg(ds.y.reconstruct, 'x'):
            ys = [ds.y.reconstruct(grab_idx(y, i), x=x) for i,x in enumerate(xs)]
            zs = [ds.y.reconstruct(z, x=x) for z,x in zip(preds,xs)]
        else :
            ys = [ds.y.reconstruct(grab_idx(y, i)) for i in range(n_items)]
            zs = [ds.y.reconstruct(z) for z in preds]
        ds.x.show_xyzs(xs, ys, zs, **kwargs)

    def average_precision_score(self, detect_thresh=0.2, iou_thresh=0.1, mean=False, show_progress=True):

        """
        Computes average precision on the validation set for each class.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        detect_thresh           Optional float. The probabilty above which
                                a detection will be considered for computing
                                average precision.
        ---------------------   -------------------------------------------
        iou_thresh              Optional float. The intersection over union
                                threshold with the ground truth labels, above
                                which a predicted bounding box will be
                                considered a true positive.
        ---------------------   -------------------------------------------
        mean                    Optional bool. If False returns class-wise
                                average precision otherwise returns mean
                                average precision.
        =====================   ===========================================

        :returns: `dict` if mean is False otherwise `float`
        """
        self._check_requisites()

        aps = compute_class_AP(self, self._data.valid_dl, self._data.c - 1, show_progress, detect_thresh=detect_thresh, iou_thresh=iou_thresh)
        if mean:
            import statistics
            return statistics.mean(aps)
        else:
            return dict(zip(self._data.classes[1:], aps))