from pathlib import Path
import json
from ._model_extension import ModelExtension
from ._arcgis_model import _EmptyData

try:
    from fastai.vision import flatten_model
    import torch
    from fastai.torch_core import split_model_idx
    from .._utils.common import get_multispectral_data_params_from_emd

    HAS_FASTAI = True

except Exception as e:
    HAS_FASTAI = False

class MyFasterRCNN():
    """
    Create class with following fixed function names and the number of arguents to train your model from external source
    """

    try:
        import torch
        import torchvision
        import pathlib
        import os
        import fastai
    except:
        pass
    
    def get_model(self, data, backbone=None):
        """
        In this fuction you have to define your model with following two arguments!
        
        data - Object returned from prepare_data method(Fastai databunch)
        
        These two arguments comes from dataset which you have prepared from prepare_data method above.
        
        """
        if backbone is None:
            backbone = self.torchvision.models.resnet50

        elif type(backbone) is str:
            if hasattr(self.torchvision.models, backbone):
                backbone = getattr(self.torchvision.models, backbone)
            elif hasattr(self.torchvision.models.detection, backbone):
                backbone = getattr(self.torchvision.models.detection, backbone)
        else:
            backbone = backbone
        if backbone.__name__ is 'resnet50':
            model = self.torchvision.models.detection.fasterrcnn_resnet50_fpn(
                pretrained=True, min_size = 1.5*data.chip_size, max_size = 2*data.chip_size)
        elif backbone.__name__ in ['resnet18','resnet34']:
            backbone_small = self.fastai.vision.learner.create_body(backbone)
            backbone_small.out_channels = 512
            model = self.torchvision.models.detection.FasterRCNN(backbone_small, 91, min_size = 1.5*data.chip_size, max_size = 2*data.chip_size)
        else:
            backbone_fpn = self.torchvision.models.detection.backbone_utils.resnet_fpn_backbone(backbone.__name__, True)
            model = self.torchvision.models.detection.FasterRCNN(backbone_fpn, 91, min_size = 1.5*data.chip_size, max_size = 2*data.chip_size)

        in_features = model.roi_heads.box_predictor.cls_score.in_features
        model.roi_heads.box_predictor = self.torchvision.models.detection.faster_rcnn.FastRCNNPredictor(in_features, len(data.classes))
        
        if data._is_multispectral:
            scaled_mean_values = data._scaled_mean_values[data._extract_bands].tolist()
            scaled_std_values = data._scaled_std_values[data._extract_bands].tolist()
            model.transform.image_mean = scaled_mean_values
            model.transform.image_std = scaled_std_values
        
        return model
    
    def on_batch_begin(self, learn, model_input_batch, model_target_batch):
        """
        This fuction is dedicated to put the inputs and outputs of the model before training. This is equivalent to fastai
        on_batch_begin function. In this function you will get the inputs and targets with applied transormations. You should
        be very carefull to return the model input and target during traing, model will only accept model_input(in many cases it
        is possible to model accept input and target both to return the loss during traing and you don't require to compute loss
        from the model output and the target by yourself), if you want to compute the loss by yourself by taking the output of the
        model and targets then you have to return the model_target in desired format to calculate loss in the loss function.
        
        learn - Fastai learner object.                
        model_input_batch - transformed input batch(images) with tensor shape [N,C,H,W].        
        model_target_batch - transformed target batch. list with [bboxes, classes]. Where bboxes tensor shape will be
                            [N, maximum_num_of_boxes_pesent_in_one_image_of_the_batch, 4(y1,x1,y2,x2 fastai default bbox
                            formate)] and bboxes in the range from -1 to 1(default fastai formate), and classes is the tenosr
                            of shape [N, maximum_num_of_boxes_pesent_in_one_image_of_the_batch] which represents class of each
                            bboxes.
        if you are synthesizing new data from the model_target_batch and model_input_batch, in that case you need to put 
        your data on correct device.

        return model_input and model_target from this function.
        
        """
        
        #during training after each epoch, validation loss is required on validation set of datset.
        #torchvision FasterRCNN model gives losses only on training mode that is why set your model in train mode
        #such that you can get losses for your validation datset as well after each epoch.
        learn.model.train()

        target_list = []

        #denormalize from imagenet_stats
        if not learn.data._is_multispectral:
            imagenet_stats = [[0.485, 0.456, 0.406], [0.229, 0.224, 0.225]]
            mean = self.torch.tensor(imagenet_stats[0], dtype=self.torch.float32).to(model_input_batch.device)
            std  = self.torch.tensor(imagenet_stats[1], dtype=self.torch.float32).to(model_input_batch.device)
            model_input_batch = (model_input_batch.permute(0, 2, 3, 1)*std + mean).permute(0, 3, 1, 2)
        
        for bbox, label in zip(*model_target_batch):

            bbox = ((bbox+1)/2)*learn.data.chip_size # FasterRCNN model require bboxes with values between 0 and H and 0 and W.
            target = {}#FasterRCNN require target of each image in the formate of dictionary.
            #If image comes without any bboxes.
            if bbox.nelement() == 0:        
                bbox = self.torch.tensor([[0.,0.,0.,0.]]).to(learn.data.device)
                label = self.torch.tensor([0]).to(learn.data.device)
            # FasterRCNN require the formate of bboxes [x1,y1,x2,y2].
            bbox = self.torch.index_select(bbox, 1, self.torch.tensor([1,0,3,2]).to(learn.data.device))
            target["boxes"] = bbox
            target["labels"] = label
            target_list.append(target) #FasterRCNN require batches target in form of list of dictionary.
        
        #FasterRCNN require model input with images and coresponding targets in training mode to return the losses so append
        #the targets in model input itself.
        model_input = [list(model_input_batch), target_list]
        #Model target is not required in traing mode so just return the same model_target to train the model.
        model_target = model_target_batch

        #return model_input and model_target
        return model_input, model_target
    
    def transform_input(self, xb):# transform_input
        """
        function for feding the input to the model in validation/infrencing mode.
        
        xb - tensor with shape [N, C, H, W]
        """
        #denormalize from imagenet_stats
        imagenet_stats = [[0.485, 0.456, 0.406], [0.229, 0.224, 0.225]]
        mean = self.torch.tensor(imagenet_stats[0], dtype=self.torch.float32).to(xb.device)
        std  = self.torch.tensor(imagenet_stats[1], dtype=self.torch.float32).to(xb.device)

        xb = (xb.permute(0, 2, 3, 1)*std + mean).permute(0, 3, 1, 2)
        
        return list(xb) # model input require in the formate of list
    
    def transform_input_multispectral(self, xb):
        return list(xb)

    def loss(self, model_output, *model_target):
        """
        Define loss in this function.
        
        model_output - model output after feding input to the model in traing mode.
        *model_target - targets of the model which you have return in above on_batch_begin function.
        
        return loss for the model
        """
        #FasterRCNN model return loss in traing mode by feding input to the model it does not require target to compute the loss
        final_loss = 0.
        for i in model_output.values():
            i[self.torch.isnan(i)] = 0.
            i[self.torch.isinf(i)] = 0.
            final_loss += i
        
        return final_loss
    
    def post_process(self, pred, nms_overlap, thres, chip_size, device):
        """
        Fuction dedicated for post processing your output of the model in validation/infrencing mode.
        
        pred - Predictions(output) of the model after feding the batch of input image.
        nms_overlap - If your model post processing require nms_overlap.
        thres - detction thresold if required in post processing.
        chip_size - If chip_size required in model post processing.
        device - device on which you should put you output after post processing.
        
        It should return the bboxes in range -1 to 1 and the formate of the post processed result is list of tuple for each
        image and tuple should contain (bboxes, label, score) for each image. bboxes should be the tensor of shape
        [Number_of_bboxes_in_image, 4], label should be the tensor of shape[Number_of_bboxes_in_image,] and score should be
        the tensor of shape[Number_of_bboxes_in_image,].
        """
        post_processed_pred = []
        for p in pred:
            
            bbox, label, score = p["boxes"], p["labels"], p["scores"]
            #take only those predictions which have probabilty greater than thresold
            score_mask = score>thres
            bbox, label, score = bbox[score_mask], label[score_mask], score[score_mask]
            #convert bboxes in range -1 to 1.
            bbox = bbox/(chip_size/2) - 1
            #convert bboxes in format [y1,x1,y2,x2]
            bbox = self.torch.index_select(bbox, 1, self.torch.tensor([1,0,3,2]).to(bbox.device))
            #Append the tuple in list for each image
            post_processed_pred.append((bbox.to(device), label.to(device), score.to(device)))
            
        return post_processed_pred

class FasterRCNN(ModelExtension):
    """
    Creates a ``FasterRCNN`` model

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    data                    Required fastai Databunch. Returned data object from
                            ``prepare_data`` function.
    ---------------------   -------------------------------------------
    backbone                Optional function. Backbone CNN model to be used for
                            creating the base of the `FasterRCNN`, which
                            is `resnet50` by default. 
                            Compatible backbones: 'resnet18', 'resnet34', 'resnet50', 'resnet101', 'resnet152'
    ---------------------   -------------------------------------------
    pretrained_path         Optional string. Path where pre-trained model is
                            saved.
    =====================   ===========================================

    :returns: ``FasterRCNN`` Object
    """
    def __init__(self, data, backbone='resnet50', pretrained_path=None):

        super().__init__(data, MyFasterRCNN, backbone, pretrained_path)

        idx = 27
        if self._backbone.__name__ in ['resnet18','resnet34']:
            idx = self._freeze()
        self.learn.layer_groups = split_model_idx(self.learn.model, [idx])
        self.learn.create_opt(lr=3e-3)

    def unfreeze(self):
        for _, param in self.learn.model.named_parameters():
            param.requires_grad = True

    def _freeze(self):
        "Freezes the pretrained backbone."
        for idx, i in enumerate(flatten_model(self.learn.model.backbone)):
            if isinstance(i, (torch.nn.BatchNorm2d)):
                continue
            for p in i.parameters():
                p.requires_grad = False
        return idx

    @classmethod
    def from_model(cls, emd_path, data=None):
        """
        Creates a ``FasterRCNN`` object from an Esri Model Definition (EMD) file.

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

        :returns: `FasterRCNN` Object
        """
        emd_path = Path(emd_path)

        with open(emd_path) as f:
            emd = json.load(f)
            
        model_file = Path(emd['ModelFile'])
        
        if not model_file.is_absolute():
            model_file = emd_path.parent / model_file
        
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
            data.classes = ['background']
            for k, v in class_mapping.items():
                data.classes.append(v)
            data = get_multispectral_data_params_from_emd(data, emd)
        
        return cls(data, backbone, pretrained_path=str(model_file))
