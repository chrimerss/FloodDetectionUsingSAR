import traceback
from .._utils.env import raise_fastai_import_error

import_exception = None
try:
    from ._arcgis_model import ArcGISModel, SaveModelCallback, _set_multigpu_callback
    from ._pointcnn_utils import PointCNNSeg, SamplePointsCallback, CrossEntropyPC, accuracy, accuracy_non_zero, AverageMetric
    from .._utils.pointcloud_data import get_device, inference_las, show_results, compute_precision_recall, predict_h5, show_results_tool
    from ._unet_utils import is_no_color
    from fastai.basic_train import Learner
    import torch
    import numpy as np
    from fastai.callbacks import EarlyStoppingCallback
    from functools import partial
    from ._arcgis_model import _EmptyData
    import json
    from pathlib import Path
    HAS_FASTAI = True
except Exception as e:
    import_exception = traceback.format_exc()
    HAS_FASTAI = False

class PointCNN(ArcGISModel):

    """
    Model architecture from https://arxiv.org/abs/1801.07791.
    Creates a Point Cloud Segmentation/ Point Classification model. 

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    data                    Required fastai Databunch. Returned data object from
                            `prepare_data` function.
    ---------------------   -------------------------------------------
    pretrained_path         Optional string. Path where pre-trained PointCNN model is
                            saved.                            
    =====================   ===========================================

    **kwargs**

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    encoder_params          Optional dictionary. The keys of the dictionary are 
                            `out_channels`, `P`, `K`, `D` and `m`.

                              Examples:
                                {'out_channels':[16, 32, 64, 96],
                                'P':[-1, 768, 384, 128],
                                'K':[12, 16, 16, 16],
                                'D':[1, 1, 2, 2],
                                'm':8
                                }  

                            Length of `out_channels`, `P`, `K`, `D` should be same.
                            The length denotes the number of layers in encoder.
                              Parameter Explanation
                                - 'out_channels': Number of channels in each layer multiplied by `m`,
                                - 'P': Number of points in each layer,
                                - 'K': Number of K-nearest neighbor in each layer,
                                - 'D': Dilation in each layer,
                                - 'm': Multiplier which is multiplied by each out_channel.
    ---------------------   -------------------------------------------
    dropout                 Optional float. This parameter will control overfitting.                          
                            The range of this parameter is [0,1).
    ---------------------   -------------------------------------------
    sample_point_num        Optional integer. The number of points that the models
                            will actually process.     
    =====================   ===========================================

    :returns: `PointCNN` Object
    """

    def __init__(self, data, pretrained_path=None, *args, **kwargs):
        super().__init__(data, None)

        if not HAS_FASTAI:
            raise_fastai_import_error(import_exception=import_exception, message="This model requires module 'torch_geometric' to be installed.", installation_steps=' ')
        
        self._backbone = None
        self.sample_point_num = kwargs.get('sample_point_num', data.max_point)
        self.learn = Learner(data,
                PointCNNSeg(self.sample_point_num, data.c, data.extra_dim, kwargs.get('encoder_params', None), kwargs.get('dropout', None)),
                loss_func=CrossEntropyPC(data.c),
                metrics=[AverageMetric(accuracy)],
                callback_fns=[partial(SamplePointsCallback, sample_point_num=self.sample_point_num)])
        self.encoder_params = self.learn.model.encoder_params

        self.learn.model = self.learn.model.to(self._device)

        if pretrained_path is not None:
            self.load(pretrained_path)

    @classmethod
    def from_model(cls, emd_path, data=None):
        
        """
        Creates a PointCNN model from an Esri Model Definition (EMD) file.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        emd_path                Required string. Path to Esri Model Definition
                                file.
        ---------------------   -------------------------------------------
        data                    Required fastai Databunch or None. Returned data
                                object from `prepare_data` function or None for
                                inferencing.
        =====================   ===========================================

        :returns: `PointCNN` Object
        """      

        emd_path = Path(emd_path)
        with open(emd_path) as f:
            emd = json.load(f)

        model_file = Path(emd['ModelFile'])
        if not model_file.is_absolute():
            model_file = emd_path.parent / model_file
        model_params = emd['ModelParameters']

        try:
            class_mapping = {i['Value'] : i['Name'] for i in emd['Classes']}
            color_mapping = {i['Value'] : i['Color'] for i in emd['Classes']}
        except KeyError:
            class_mapping = {i['ClassValue'] : i['ClassName'] for i in emd['Classes']} 
            color_mapping = {i['ClassValue'] : i['Color'] for i in emd['Classes']}                

        if data is None:
            data = _EmptyData(path=emd_path.parent.parent, loss_func=None, c=len(class_mapping), chip_size=emd['ImageHeight'])
            data.emd_path = emd_path
            data.emd = emd
            for key, value in emd['DataAttributes'].items():
                setattr(data, key, value)
            ## backward compatibility.
            if not hasattr(data, 'class_mapping'):
                data.class_mapping = class_mapping
            if not hasattr(data, 'color_mapping'):
                data.color_mapping = color_mapping
            if not hasattr(data, 'classes'):
                data.classes = list(class_mapping.values())

            data.class_mapping = {int(k): int(v) for k, v in data.class_mapping.items()}
            data.color_mapping = {int(k): v for k, v in data.color_mapping.items()}


            ## Below are the lines to make save function work
            data.chip_size = None
            data._image_space_used = None
            data.dataset_type = 'PointCloud'                 

        return cls(data, **model_params, pretrained_path=str(model_file))

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return '<%s>' % (type(self).__name__)

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
                                tensorboard. Required tensorboardx version=1.7 
                                (Experimental support).        
                                The default value is 'False'.
        =====================   ===========================================

        **kwargs**

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        iters_per_epoch         Optional integer. The number of iterations 
                                to run during the training phase.
        =====================   ===========================================
                
        """
        iterations = kwargs.get('iters_per_epoch', None)
        from ._pointcnn_utils import IterationStop
        callbacks = kwargs['callbacks'] if 'callbacks' in kwargs.keys() else []
        if iterations is not None:
            del kwargs['iters_per_epoch']
            stop_iteration_cb = IterationStop(self.learn, iterations)
            callbacks.append(stop_iteration_cb)
            kwargs['callbacks'] = callbacks
        self._check_requisites()

        if lr is None:
            print('Finding optimum learning rate.')
            lr = self.lr_find(allow_plot=False)
        
        super().fit(epochs, lr, one_cycle, early_stopping, checkpoint, tensorboard, **kwargs)
        
    @property
    def _model_metrics(self):
        return {'accuracy': self._get_model_metrics()}            

    def _get_model_metrics(self, **kwargs):
        checkpoint = kwargs.get('checkpoint', True)
        if not hasattr(self.learn, 'recorder'):
            return 0.0

        model_accuracy = self.learn.recorder.metrics[-1][0]
        if checkpoint:
            model_accuracy = np.max([i[0] for i in self.learn.recorder.metrics])

        return float(model_accuracy)

    def _get_emd_params(self):
        import random
        _emd_template = {"DataAttributes" : {}, "ModelParameters" : {}}
        _emd_template["Framework"] = "N/A"
        _emd_template["ModelConfiguration"] = "N/A"
        _emd_template["ExtractBands"] = "N/A"
        _emd_template["ModelParameters"]["encoder_params"] = self.encoder_params
        _emd_template["ModelParameters"]["sample_point_num"] = self.sample_point_num

        _emd_template['DataAttributes']['block_size'] = self._data.block_size
        _emd_template['DataAttributes']['max_point'] = self._data.max_point
        _emd_template['DataAttributes']['extra_features'] = self._data.extra_features
        _emd_template['DataAttributes']['extra_dim'] = self._data.extra_dim
        _emd_template['DataAttributes']['class_mapping'] = self._data.class_mapping
        _emd_template['DataAttributes']['color_mapping'] = self._data.color_mapping
        _emd_template['DataAttributes']['remap'] = self._data.remap
        _emd_template['DataAttributes']['classes'] = self._data.classes

        _emd_template['Classes'] = []
        class_data = {}
        for i, class_name in enumerate(self._data.classes):  # 0th index is background
            inverse_class_mapping = {v: k for k, v in self._data.class_mapping.items()}
            class_data["Value"] = inverse_class_mapping[i]
            class_data["Name"] = class_name
            color = [random.choice(range(256)) for i in range(3)] if is_no_color(self._data.color_mapping) else \
                self._data.color_mapping[class_name]
            class_data["Color"] = np.array(color).astype(int).tolist()
            _emd_template['Classes'].append(class_data.copy())

        return _emd_template
        
    def show_results(self, rows=2, **kwargs):

        """
        Displays the results from your model on the validation set
        with ground truth on the left and predictions on the right.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        rows                    Optional rows. Number of rows to show. Default
                                value is 2 and maximum value is the `batch_size`
                                passed in `prepare_data`. 
        =====================   ===========================================

        **kwargs**

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        color_mapping           Optional dictionary. Mapping from class value
                                to RGB values. Default value
                                Example: {0:[220,220,220],
                                            1:[255,0,0],
                                            2:[0,255,0],
                                            3:[0,0,255]}          
        ---------------------   -------------------------------------------
        mask_class              Optional list of integers. Array containing
                                class values to mask. Use this parameter to 
                                display the classes of interest.
                                Default value is []. 
                                Example: All the classes are in [0, 1, 2]
                                to display only class `0` set the mask class
                                parameter to be [1, 2]. List of all classes
                                can be accessed from `data.classes` attribute
                                where `data` is the `Databunch` object returned
                                by `prepare_data` function.    
        ---------------------   -------------------------------------------
        width                   Optional integer. Width of the plot. Default 
                                value is 750.
        ---------------------   -------------------------------------------
        height                  Optional integer. Height of the plot. Default
                                value is 512.   
        ---------------------   -------------------------------------------
        max_display_point       Optional integer. Maximum number of points
                                to display. Default is 20000. A warning will
                                be raised if the total points to display exceeds
                                this parameter. Setting this parameter will
                                randomly sample the specified number of points
                                and once set, it will be used for future uses.
        =====================   ===========================================
        """

        if self._data.pc_type == 'PointCloud_TF':
            return show_results(self, rows, **kwargs)
        elif self._data.pc_type == 'PointCloud':
            return show_results_tool(self, rows, **kwargs)

    def predict_las(self, path, output_path=None, print_metrics=False, **kwargs):

        """
        Predicts and writes the resulting las file on the disk.
        The block size which was used for training will be used for prediction.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        path                    Required string. The path to folder where the las
                                files which needs to be predicted are present.   
        ---------------------   -------------------------------------------
        output_path             Optional string. The path to folder where to dump
                                the resulting las files. Defaults to `results` folder
                                in input path.  
        ---------------------   -------------------------------------------
        print_metrics           Optional boolean. If True, print metrics such as precision,
                                recall and f1_score. Defaults to False.
        =====================   ===========================================                                

        **kwargs**

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        remap_classes           Optional dictionary {int:int}. Mapping from  
                                class values to user defined values. Please query 
                                `pointcnn._data.classes` to get the class values
                                on which the model is trained on.
                                Default is {} 
        ---------------------   -------------------------------------------
        selective_classify      Optional list of integers. If passed, predict_las 
                                will selectively classify only those points 
                                belonging to the specified class-codes. Other 
                                points in the input point clouds will retain 
                                their class-codes. 
                                Please query `pointcnn._data.classes` to get 
                                the class values on which the model is trained 
                                on. If `remap_classes` is specified, the new 
                                mapped values will be used for classification. 
                                Default value is [].
        =====================   ===========================================
        
        :returns: Path where files are dumped.
        """
        
        return inference_las(path, self, output_path, print_metrics, **kwargs)

    def compute_precision_recall(self):
        
        """
        Computes precision, recall and f1-score on the validation sets.
        """

        return compute_precision_recall(self)

    def predict_h5(self, path, output_path=None, **kwargs):
        """
        Predicts and writes the resulting las file on the disk. 
        The block size which was used for training will be used for prediction.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        path                    Required string. The path to folder where the h5
                                files which needs to be predicted are present.   
        ---------------------   -------------------------------------------
        output_path             Optional string. The path to folder where to dump
                                the resulting h5 block files. Defaults to `results`
                                folder in input path.
        =====================   ===========================================
        
        :returns: Path where files are dumped.
        """
        
        return predict_h5(self, path, output_path)        
        
