import os
from pathlib import Path
from functools import partial
import xml.etree.ElementTree as ET
import math
import sys
import json 
import logging      
import types
import tempfile
import traceback

from ._utils.env import ARCGIS_ENABLE_TF_BACKEND                                                                                                                                            


import_exception = None
try:
    import arcgis
    import numpy as np
    from fastai.vision.data import imagenet_stats, ImageList, bb_pad_collate, ImageImageList
    from fastai.vision.transform import crop, rotate, dihedral_affine, brightness, contrast, skew, rand_zoom, get_transforms, flip_lr, ResizeMethod
    from fastai.vision import ImageDataBunch, parallel
    from fastai.torch_core import data_collate
    import torch
    from .models._unet_utils import ArcGISSegmentationItemList, ArcGISSegmentationMSItemList, is_no_color
    from .models._maskrcnn_utils import ArcGISInstanceSegmentationItemList, ArcGISInstanceSegmentationMSItemList
    from .models._ner_utils import ner_prepare_data
    from ._utils.pascal_voc_rectangles import ObjectDetectionItemList
    from .models._superres_utils import resize_one
    from ._utils.common import ArcGISMSImageList, ArcGISMSImage
    from ._utils.classified_tiles import show_batch_classified_tiles
    from ._utils.labeled_tiles import show_batch_labeled_tiles
    from ._utils.rcnn_masks import show_batch_rcnn_masks
    from ._utils.pascal_voc_rectangles import ObjectMSItemList, show_batch_pascal_voc_rectangles
    from ._utils.pointcloud_data import pointcloud_prepare_data
    from fastai.tabular import TabularDataBunch
    from fastai.tabular.transform import FillMissing, Categorify, Normalize
    from fastai.tabular import cont_cat_split, add_datepart
    from ._utils.tabular_data import TabularDataObject
    import random
    import PIL
    HAS_FASTAI = True
except Exception as e:
    import_exception = "\n".join(traceback.format_exception(type(e), e, e.__traceback__))
    HAS_FASTAI = False


band_abrevation_lib = {
    'b': 'BLUE',
    'c': 'CIRRUS',
    'ca': 'COASTAL AEROSOL',
    'g': 'GREEN',
    'nir': 'NEAR INFRARED',
    'nnir': 'NARROW NEAR INFRARED',
    'p': 'PANCHROMATIC',
    'r': 'RED',
    'swir': 'SHORT WAVELENGTH INFRARED',
    'swirc': 'SHORT WAVELENGTH INFRARED – Cirrus',
    'tir': 'THERMAL INFRARED',
    'vre': 'Vegetation red edge',
    'wv': 'WATER VAPOUR'
}

imagery_type_lib = {
    'landsat8': {
        "bands": ['ca', 'b', 'g', 'r', 'nir', 'swir', 'swir', 'c', 'qa', 'tir', 'tir'],
        "bands_info": { # incomplete
        }
    },
    "naip": {
        "bands": ['r', 'g', 'b', 'nir'],
        "bands_info": { # incomplete
        }
    },
    'sentinel2': { 
        "bands": ['ca', 'b', 'g', 'r', 'vre', 'vre', 'vre', 'nir', 'nnir', 'wv', 'swirc', 'swir', 'swir'],
        "bands_info": { # incomplete
            "b1": {
                "Name": "costal",
                "max": 10000,
                "min": 10000
            },            
            "b2": {
                "Name": "blue",
                "max": 10000,
                "min": 10000
            }
        }
    }
}

def get_installation_command():
    installation_steps = "Install them using 'conda install -c esri arcgis=1.8.1 pillow scikit-image'\n'conda install -c fastai -c pytorch fastai pytorch=1.4.0 torchvision=0.5.0 tensorflow-gpu=2.1.0'\n'conda install gdal=2.3.3'"

    return installation_steps 

def _raise_fastai_import_error(import_exception=import_exception):
    installation_steps = get_installation_command()
    raise Exception(f"""{import_exception} \n\nThis module requires fastai, PyTorch, torchvision and scikit-image as its dependencies.\n{installation_steps}""")

class _ImagenetCollater():
    def __init__(self, chip_size):
        self.chip_size = chip_size
    def __call__(self, batch):
        _xb = []
        for sample in batch:
            data = sample[0].data
            if data.shape[1] < self.chip_size or data.shape[2] < self.chip_size:
                data = sample[0].resize(self.chip_size).data
            _xb.append(data)
        _xb = torch.stack(_xb)
        _yb = torch.stack([torch.tensor(sample[1].data) for sample in batch])
        return _xb, _yb

def _bb_pad_collate(samples, pad_idx=0):
    "Function that collect `samples` of labelled bboxes and adds padding with `pad_idx`."
    if isinstance(samples[0][1], int):
        return data_collate(samples)
    max_len = max([len(s[1].data[1]) for s in samples])
    bboxes = torch.zeros(len(samples), max_len, 4)
    labels = torch.zeros(len(samples), max_len).long() + pad_idx
    imgs = []
    for i,s in enumerate(samples):
        imgs.append(s[0].data[None])
        bbs, lbls = s[1].data

        if not (bbs.nelement() == 0) or list(bbs) == [[0,0,0,0]]:
            bboxes[i,-len(lbls):] = bbs
            labels[i,-len(lbls):] = torch.tensor(lbls, device=bbs.device).long()
    return torch.cat(imgs,0), (bboxes,labels)    


def _get_bbox_classes(xmlfile, class_mapping , height_width=[]):

    tree = ET.parse(xmlfile)
    xmlroot = tree.getroot()
    bboxes = []
    classes = []
    for tag_obj in xmlroot.findall('object'):
        bnd_box = tag_obj.find('bndbox')
        xmin, ymin, xmax, ymax = float(bnd_box.find('xmin').text), \
                                 float(bnd_box.find('ymin').text), \
                                 float(bnd_box.find('xmax').text), \
                                 float(bnd_box.find('ymax').text)
        data_class_text = tag_obj.find('name').text
        
        if (not data_class_text.isnumeric() and not class_mapping.get(data_class_text))\
             or (data_class_text.isnumeric() and not(class_mapping.get(data_class_text) or class_mapping.get(int(data_class_text)))):
            continue

        if data_class_text.isnumeric():
            data_class_mapping = class_mapping[data_class_text] if class_mapping.get(data_class_text) else class_mapping[int(data_class_text)]
        else:
            data_class_mapping = class_mapping[data_class_text]

        classes.append(data_class_mapping)
        bboxes.append([ymin, xmin, ymax, xmax])
        height_width.append(((xmax - xmin)*1.25, (ymax - ymin)*1.25))
    
    if len(bboxes) == 0:
        return [[[0, 0, 0, 0]], [list(class_mapping.values())[0]]]
    return [bboxes, classes]


def _get_bbox_lbls(imagefile, class_mapping, height_width):
    xmlfile = imagefile.parents[1] / 'labels' / imagefile.name.replace('{ims}'.format(ims=imagefile.suffix), '.xml')
    return _get_bbox_classes(xmlfile, class_mapping, height_width)


def _get_lbls(imagefile, class_mapping):
    xmlfile = imagefile.parents[1] / 'labels' / imagefile.name.replace('{ims}'.format(ims=imagefile.suffix), '.xml')
    return _get_bbox_classes(xmlfile, class_mapping)[1][0]


def _check_esri_files(path):
    if os.path.exists(path / 'esri_model_definition.emd') \
        and os.path.exists(path / 'map.txt') \
            and os.path.exists(path / 'esri_accumulated_stats.json'):
        return True

    return False


def _get_class_mapping(path):
    class_mapping = {}
    for xmlfile in os.listdir(path):
        if not xmlfile.endswith('.xml'):
            continue
        tree = ET.parse(os.path.join(path, xmlfile))
        xmlroot = tree.getroot()
        for tag_obj in xmlroot.findall('object'):
            class_mapping[tag_obj.find('name').text] = tag_obj.find('name').text

    return class_mapping


def _get_batch_stats(image_list, norm_pct=1, _band_std_values=False):
    n_normalization_samples = round(len(image_list)*norm_pct)
    #n_normalization_samples = max(256, n_normalization_samples)
    random_indexes = np.random.randint(0, len(image_list), size=min(n_normalization_samples, len(image_list)))

    # Original Band Stats
    min_values_store = []
    max_values_store = []
    mean_values_store = []

    data_shape = image_list[0].data.shape
    n_bands = data_shape[0]
    feasible_chunk = round(512*4*400/(n_bands*data_shape[1])) # ~3gb footprint
    chunk = min(feasible_chunk, n_normalization_samples)
    i = 0
    for i in range(0, n_normalization_samples, chunk):
        x_tensor_chunk = torch.stack([ x.data for x in image_list[random_indexes[i:i+chunk]] ] )
        """
        min_values = torch.zeros(n_bands)
        max_values = torch.zeros(n_bands)
        mean_values = torch.zeros(n_bands)
        for bi in range(n_bands):
            min_values[bi] = x_tensor_chunk[:, bi].min()
            max_values[bi] = x_tensor_chunk[:, bi].max()
            mean_values[bi] = x_tensor_chunk[:, bi].mean()
        """
        min_values = x_tensor_chunk.min(dim=0)[0].min(dim=1)[0].min(dim=1)[0]
        max_values = x_tensor_chunk.max(dim=0)[0].max(dim=1)[0].max(dim=1)[0]
        mean_values = x_tensor_chunk.mean((0, 2, 3))
        min_values_store.append(min_values)
        max_values_store.append(max_values)
        mean_values_store.append(mean_values)

    band_max_values = torch.stack(max_values_store).max(dim=0)[0]
    band_min_values = torch.stack(min_values_store).min(dim=0)[0]
    band_mean_values = torch.stack(mean_values_store).mean(dim=0)
    
    view_shape = _get_view_shape(image_list[0].data, band_mean_values)

    if _band_std_values:
        std_values_store = []
        for i in range(0, n_normalization_samples, chunk):
            x_tensor_chunk = torch.stack([ x.data for x in image_list[random_indexes[i:i+chunk]] ] )
            std_values = (x_tensor_chunk - band_mean_values.view(view_shape)).pow(2).sum((0, 2, 3))
            std_values_store.append(std_values)
        band_std_values = (torch.stack(std_values_store).sum(dim=0) / ((n_normalization_samples * data_shape[1] * data_shape[2])-1)).sqrt()
    else:
        band_std_values = None

    # Scaled Stats
    scaled_min_values = torch.tensor([0 for i in range(n_bands)], dtype=torch.float32)
    scaled_max_values = torch.tensor([1 for i in range(n_bands)], dtype=torch.float32)
    scaled_mean_values = _tensor_scaler(band_mean_values, band_min_values, band_max_values, mode='minmax')
    
    scaled_std_values_store = []
    for i in range(0, n_normalization_samples, chunk):
        x_tensor_chunk = torch.stack([ x.data for x in image_list[random_indexes[i:i+chunk]] ] )
        x_tensor_chunk = _tensor_scaler(x_tensor_chunk, band_min_values, band_max_values, mode='minmax')
        std_values = (x_tensor_chunk - scaled_mean_values.view(view_shape)).pow(2).sum((0, 2, 3))
        scaled_std_values_store.append(std_values)
    scaled_std_values = (torch.stack(scaled_std_values_store).sum(dim=0) / ((n_normalization_samples * data_shape[1] * data_shape[2])-1)).sqrt()

    #return band_min_values, band_max_values, band_mean_values, band_std_values, scaled_min_values, scaled_max_values, scaled_mean_values, scaled_std_values
    return {
        "band_min_values": band_min_values, 
        "band_max_values": band_max_values, 
        "band_mean_values": band_mean_values, 
        "band_std_values": band_std_values, 
        "scaled_min_values": scaled_min_values, 
        "scaled_max_values": scaled_max_values, 
        "scaled_mean_values": scaled_mean_values, 
        "scaled_std_values": scaled_std_values
    }


def _get_view_shape(tensor_batch, band_factors):
    view_shape = [1 for i in range(len(tensor_batch.shape))]
    view_shape[tensor_batch.shape.index(band_factors.shape[0])] = band_factors.shape[0]
    return tuple(view_shape)


def _tensor_scaler(tensor_batch, min_values, max_values, mode='minmax', create_view=True):
    if create_view:
        view_shape = _get_view_shape(tensor_batch, min_values)
        max_values = max_values.view(view_shape)
        min_values = min_values.view(view_shape)
    if mode=='minmax':
        # new_value = (((old_value - old_min) / (old_max-old_min))*(new_max-new_min))+new_min
        scaled_tensor_batch = (tensor_batch - min_values) / ( (max_values - min_values) + 1e-05)
    return scaled_tensor_batch


def _tensor_scaler_tfm(tensor_batch, min_values, max_values, mode='minmax'):
    x = tensor_batch[0]
    y = tensor_batch[1]
    max_values = max_values.view(1, -1, 1, 1).to(x.device)
    min_values = min_values.view(1, -1, 1, 1).to(x.device)
    x = _tensor_scaler(x, min_values, max_values, mode, create_view=False)
    return (x, y)


def _extract_bands_tfm(tensor_batch, band_indices):
    x_batch = tensor_batch[0][:, band_indices]
    y_batch = tensor_batch[1]
    return (x_batch, y_batch)


def prepare_tabulardata(
        input_features,
        variable_predict,
        explanatory_variables=None,
        explanatory_rasters=None,
        date_field=None,
        distance_features=None,
        preprocessors=None,
        val_split_pct=0.1,
        seed=42,
        batch_size=64
    ):

    """
    Prepares a databunch object from dataframe and fields_mapping dictionary.
    The first two inputs can be prepared using process_dataframe function.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    input_features          Required input feature layer or spatially enabled dataframe.
                            This contains features denoting the value of the dependent variable.
    ---------------------   -------------------------------------------
    variable_predict        Required String, optionally 2-sized tuple
                            denoting field_name, Categorical/Continuous.
                            For example:
                                ("Field_Name", True)
                            By default: Automatically deduces the type.
    ---------------------   -------------------------------------------
    explanatory_variables   Optional list containing field names from input_features
                            By default the field type is continuous.
                            To override field type to categorical, pass
                            a 2-sized tuple containing:
                                1. field to be taken as input from the input_features.
                                2. True/False denoting Categorical/Continuous variable.
    ---------------------   -------------------------------------------
    explanatory_rasters     Optional list containing Raster objects.
                            By default the rasters are continuous.
                            To mark a raster categorical, pass a 2-sized tuple containing:
                                1. Raster object.
                                2. True/False denoting Categorical/Continuous variable.
    ---------------------   -------------------------------------------
    date_field              Optional field_name.
                            This field contains the date in the input_layer.
                            If specified, the field will be split into
                            Year, month, week, day, dayofweek, dayofyear,
                            is_month_end, is_month_start, is_quarter_end,
                            is_quarter_start, is_year_end, is_year_start,
                            hour, minute, second, elapsed.
                            If specified here,
                            no need to specify in the feature_variables list.
    ---------------------   -------------------------------------------
    distance_features       Optional list of feature_layers.
                            These layers are used for calculation of field "NEAR_DIST_1",
                            "NEAR_DIST_2" etc, in the output dataframe.
                            These field contains the nearest feature distance
                            from the input_layer feature.
    ---------------------   -------------------------------------------
    preprocessors           For Fastai: Optional transforms list.
                            For Scikit-learn: supply a column transformer object.
                            Categorical data is by default encoded.
                            If nothing is specified, default transforms are applied
                            to fill missing values and normalize categorical data.
    ---------------------   -------------------------------------------
    val_split_pct           Optional float. Percentage of training data to keep
                            as validation.
                            By default 10% data is kept for validation.
    ---------------------   -------------------------------------------
    seed                    Optional integer. Random seed for reproducible
                            train-validation split.
                            Default value is 42.
    ---------------------   -------------------------------------------
    batch_size              Optional integer. Batch size for mini batch gradient
                            descent (Reduce it if getting CUDA Out of Memory
                            Errors).
                            Default value is 64.
    =====================   ===========================================

    :returns: `TabularData` object

    """

    if not HAS_FASTAI:
        _raise_fastai_import_error(import_exception)

    dependent_variable = variable_predict
    if isinstance(variable_predict, tuple):
        dependent_variable = variable_predict[0]

    return TabularDataObject.prepare_data_for_layer_learner(
        input_features,
        dependent_variable,
        feature_variables=explanatory_variables,
        raster_variables=explanatory_rasters,
        date_field=date_field,
        distance_feature_layers=distance_features,
        procs=preprocessors,
        val_split_pct=val_split_pct,
        seed=seed,
        batch_size=batch_size
    )

def prepare_data(path,
                 class_mapping=None, 
                 chip_size=224, 
                 val_split_pct=0.1, 
                 batch_size=64, 
                 transforms=None, 
                 collate_fn=_bb_pad_collate, 
                 seed=42, 
                 dataset_type=None, 
                 resize_to=None,
                 **kwargs):
    """
    Prepares a data object from training sample exported by the 
    Export Training Data tool in ArcGIS Pro or Image Server, or training 
    samples in the supported dataset formats. This data object consists of 
    training and validation data sets with the specified transformations, 
    chip size, batch size, split percentage, etc. 
    -For object detection, use Pascal_VOC_rectangles format.
    -For feature categorization use Labelled Tiles or ImageNet format.
    -For pixel classification, use Classified Tiles format.
    -For entity extraction from text, use IOB, BILUO or ner_json formats. 

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    path                    Required string. Path to data directory.
    ---------------------   -------------------------------------------
    class_mapping           Optional dictionary. Mapping from id to
                            its string label.
                            For dataset_type=IOB, BILUO or ner_json:
                                Provide address field as class mapping
                                in below format:
                                class_mapping={'address_tag':'address_field'}.
                                Field defined as 'address_tag' will be treated
                                as a location. In cases where trained model extracts
                                multiple locations from a single document, that 
                                document will be replicated for each location.

    ---------------------   -------------------------------------------
    chip_size               Optional integer, default 224. Size of the image to train the
                            model. Images are cropped to the specified chip_size. If image size is less
                            than chip_size, the image size is used as chip_size.
    ---------------------   -------------------------------------------
    val_split_pct           Optional float. Percentage of training data to keep
                            as validation.
    ---------------------   -------------------------------------------
    batch_size              Optional integer. Batch size for mini batch gradient
                            descent (Reduce it if getting CUDA Out of Memory
                            Errors).
    ---------------------   -------------------------------------------
    transforms              Optional tuple. Fast.ai transforms for data
                            augmentation of training and validation datasets
                            respectively (We have set good defaults which work
                            for satellite imagery well). If transforms is set
                            to `False` no transformation will take place and 
                            `chip_size` parameter will also not take effect.
                            If the dataset_type is 'PointCloud', use 
                            `Transform3d` class from `arcgis.learn`.
    ---------------------   -------------------------------------------
    collate_fn              Optional function. Passed to PyTorch to collate data
                            into batches(usually default works).
    ---------------------   -------------------------------------------
    seed                    Optional integer. Random seed for reproducible
                            train-validation split.
    ---------------------   -------------------------------------------
    dataset_type            Optional string. `prepare_data` function will infer 
                            the `dataset_type` on its own if it contains a 
                            map.txt file. If the path does not contain the 
                            map.txt file pass either of 'PASCAL_VOC_rectangles', 
                            'RCNN_Masks', 'Classified_Tiles', 'Labeled_Tiles', 
                            'Imagenet' and 'PointCloud'.                    
    ---------------------   -------------------------------------------
    resize_to               Optional integer. Resize the image to given size.
    =====================   ===========================================

    **Keyword Arguments**

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    imagery_type            Optional string. Type of imagery used to export 
                            the training data, valid values are:
                                - 'naip'
                                - 'sentinel2'
                                - 'landsat8'
                                - 'ms' - any other type of imagery
    ---------------------   -------------------------------------------
    bands                   Optional list. Bands of the imagery used to export 
                            training data. 
                            For example ['r', 'g', 'b', 'nir', 'u'] 
                            where 'nir' is near infrared band and 'u' is a miscellaneous band.
    ---------------------   -------------------------------------------
    rgb_bands               Optional list. Indices of red, green and blue bands 
                            in the imagery used to export the training data.
                            for example: [2, 1, 0] 
    ---------------------   -------------------------------------------
    extract_bands           Optional list. Indices of bands to be used for
                            training the model, same as in the imagery used to 
                            export the training data.
                            for example: [3, 1, 0] where we will not be using 
                            the band at index 2 to train our model. 
    ---------------------   -------------------------------------------
    norm_pct                Optional float. Percentage of training data to be 
                            used for calculating imagery statistics for  
                            normalizing the data. 
                            Default is 0.3 (30%) of data.
    ---------------------   -------------------------------------------
    downsample_factor       Optional integer. Factor to downsample the images 
                            for image SuperResolution. 
                            for example: if value is 2 and image size 256x256,
                            it will create label images of size 128x128.
                            Default is 4
    =====================   ===========================================

    :returns: data object

    """
    height_width = []
    not_label_count = [0]

    if not HAS_FASTAI:
        _raise_fastai_import_error()

    if isinstance(path, str) and not os.path.exists(path):
        raise Exception("Invalid input path. Please ensure that the input path is correct.")

    if type(path) is str:
        path = Path(path)

    databunch_kwargs = {'num_workers':0} if sys.platform == 'win32' else {}
    databunch_kwargs['bs'] = batch_size
    
    if hasattr(arcgis, "env") and getattr(arcgis.env, "_processorType", "") == "CPU":
        databunch_kwargs["device"] = torch.device('cpu')

    if ARCGIS_ENABLE_TF_BACKEND:
        databunch_kwargs["device"] = torch.device('cpu')
        databunch_kwargs["pin_memory"] = False

    kwargs_transforms = {}
    if resize_to:
        kwargs_transforms['size'] = resize_to
        # Applying SQUISH ResizeMethod to avoid reflection padding
        kwargs_transforms['resize_method'] = ResizeMethod.SQUISH

    has_esri_files = _check_esri_files(path)
    alter_class_mapping = False
    color_mapping = None

    # Multispectral Kwargs init
    _bands = None
    _imagery_type = None
    _is_multispectral = False
    _show_batch_multispectral = None

    if dataset_type is None and not has_esri_files:
        raise Exception("Could not infer dataset type. Please specify a supported dataset type or ensure that the path contains valid esri files")
    
    stats_file = path / 'esri_accumulated_stats.json'
    if dataset_type == "superres" and has_esri_files:

        json_file = path/ 'esri_model_definition.emd'
        with open(json_file) as f:
            emd = json.load(f)

    elif dataset_type != "Imagenet" and has_esri_files:
        with open(stats_file) as f:
            stats = json.load(f)
            dataset_type = stats['MetaDataMode']

        with open(path / 'map.txt') as f:
            while True:
                line = f.readline()
                if len(line.split())==2:
                    break
        try:
            img_size = ArcGISMSImage.open_gdal((path/(line.split()[0]).replace('\\', os.sep))).shape[-1]
        except:            
            img_size = PIL.Image.open((path/(line.split()[0]).replace('\\', os.sep))).size[-1]
        if chip_size > img_size:
            chip_size = img_size
        right = line.split()[1].split('.')[-1].lower()

        json_file = path / 'esri_model_definition.emd'
        with open(json_file) as f:
            emd = json.load(f)

        # Create Class Mapping from EMD if not specified by user
        ## Validate user defined class_mapping keys with emd (issue #3064)
        # Get classmapping from emd file.
        try:
            emd_class_mapping = {i['Value']: i['Name'] for i in emd['Classes']}
        except KeyError:
            emd_class_mapping = {i['ClassValue']: i['ClassName'] for i in emd['Classes']}

        ## Change all keys to int.
        if class_mapping is not None:
            class_mapping = {int(key):value for key, value in class_mapping.items()}
        else:
            class_mapping = {}

        ## Map values from user defined classmapping to emd classmapping.
        for key, _ in emd_class_mapping.items():
            if class_mapping.get(key) is not None:
                emd_class_mapping[key] = class_mapping[key]
            
        class_mapping = emd_class_mapping

        color_mapping = {(i.get('Value', 0) or i.get('ClassValue', 0)): i['Color'] for i in emd.get('Classes', [])}

        if color_mapping.get(None):
            del color_mapping[None]

        if class_mapping.get(None):
            del class_mapping[None]

        # Multispectral support from EMD 
        # Not Implemented Yet
        if emd.get('bands', None) is not None: 
            _bands = emd.get['bands'] # Not Implemented
            
        if emd.get('imagery_type', None) is not None: 
            _imagery_type = emd.get['imagery_type'] # Not Implemented        

    elif dataset_type == 'PASCAL_VOC_rectangles' and not has_esri_files:
        if class_mapping is None:
            class_mapping = _get_class_mapping(path / 'labels')
            alter_class_mapping = True

    _map_space = "MAP_SPACE"
    _pixel_space = "PIXEL_SPACE"
    if has_esri_files:
        _image_space_used = emd.get('ImageSpaceUsed', _map_space)
    else:
        _image_space_used = _pixel_space

    # Multispectral check
    imagery_type = 'RGB'
    if kwargs.get('imagery_type', None) is not None:
        imagery_type = kwargs.get('imagery_type')
    elif _imagery_type is not None:
        imagery_type = _imagery_type

    bands = None
    if kwargs.get('bands', None) is not None:
        bands = kwargs.get('bands')
        for i, b in enumerate(bands):
            if type(b) == str:
                bands[i] = b.lower()
    elif imagery_type_lib.get(imagery_type, None) is not None:
        bands = imagery_type_lib.get(imagery_type)['bands']
    elif _bands is not None:
        bands = _bands

    rgb_bands = None
    if kwargs.get('rgb_bands', None) is not None:
        rgb_bands = kwargs.get('rgb_bands')
    elif bands is not None:
        rgb_bands = [ bands.index(b) for b in ['r', 'g', 'b'] if b in bands ]
    
    if (bands is not None) or (rgb_bands is not None) or (not imagery_type == 'RGB'):
        if imagery_type == 'RGB':
            imagery_type = 'multispectral'
        _is_multispectral = True
    
    if kwargs.get('norm_pct', None) is not None:
        norm_pct= kwargs.get('norm_pct')
        norm_pct = min(max(0, norm_pct), 1)
    else:
        norm_pct = .3

    lighting_transforms = kwargs.get('lighting_transforms', True)

    if dataset_type == 'RCNN_Masks':

        def get_labels(x, label_dirs, ext=right):
            label_path = []
            for lbl in label_dirs:
                if os.path.exists(Path(lbl) / (x.stem + '.{}'.format(ext))):
                    label_path.append(Path(lbl) / (x.stem + '.{}'.format(ext)))
            return label_path

        label_dirs = []
        index_dir = {} #for handling calss value with any number
        for i, k in enumerate(sorted(class_mapping.keys())):
            label_dirs.append(class_mapping[k])
            index_dir[k] = i+1
        label_dir = [os.path.join(path/'labels', lbl) for lbl in label_dirs if os.path.isdir(os.path.join(path/'labels', lbl))]
        get_y_func = partial(get_labels, label_dirs= label_dir)

        def image_without_label(imagefile, not_label_count=[0]):
            label_mask = get_y_func(imagefile)
            if label_mask == []:
                not_label_count[0] += 1
                return False
            return True
        
        remove_image_without_label = partial(image_without_label, not_label_count=not_label_count)

        if class_mapping.get(0):
            del class_mapping[0]

        if color_mapping.get(0):
            del color_mapping[0]

        # Handle Multispectral
        if _is_multispectral:
            src = (ArcGISInstanceSegmentationMSItemList.from_folder(path/'images')
                .filter_by_func(remove_image_without_label)
                .split_by_rand_pct(val_split_pct, seed=seed)
                .label_from_func(get_y_func, chip_size=chip_size, classes=['NoData'] + list(class_mapping.values()), class_mapping=class_mapping, color_mapping=color_mapping, index_dir=index_dir))
            _show_batch_multispectral = show_batch_rcnn_masks
        else:
            src = (ArcGISInstanceSegmentationItemList.from_folder(path/'images')
                .filter_by_func(remove_image_without_label)
                .split_by_rand_pct(val_split_pct, seed=seed)
                .label_from_func(get_y_func, chip_size=chip_size, classes=['NoData'] + list(class_mapping.values()), class_mapping=class_mapping, color_mapping=color_mapping, index_dir=index_dir))
    
    elif dataset_type == 'Classified_Tiles':

        def get_y_func(x, ext=right):
            return x.parents[1] / 'labels' / (x.stem + '.{}'.format(ext))

        def image_without_label(imagefile, not_label_count=[0], ext=right):
            xmlfile = imagefile.parents[1] / 'labels' / (imagefile.stem + '.{}'.format(ext))
            if not os.path.exists(xmlfile):
                not_label_count[0] += 1
                return False
            return True
        
        remove_image_without_label = partial(image_without_label, not_label_count=not_label_count)

        if class_mapping.get(0):
            del class_mapping[0]

        if color_mapping.get(0):
            del color_mapping[0]

        if is_no_color(color_mapping):
            color_mapping = {j:[random.choice(range(256)) for i in range(3)] for j in class_mapping.keys()}
            
        # TODO : Handle NoData case

        # Handle Multispectral
        if _is_multispectral:
            data = ArcGISSegmentationMSItemList.from_folder(path/'images')\
                .filter_by_func(remove_image_without_label)\
                .split_by_rand_pct(val_split_pct, seed=seed)\
                .label_from_func(
                    get_y_func, classes=(['NoData'] + list(class_mapping.values())),
                    class_mapping=class_mapping,
                    color_mapping=color_mapping
                )
            _show_batch_multispectral = show_batch_classified_tiles            

            def classified_tiles_collate_fn(samples): # The default fastai collate_fn was causing memory leak on tensors
                r = ( torch.stack([x[0].data for x in samples]), torch.stack([x[1].data for x in samples]) )
                return r
            databunch_kwargs['collate_fn'] = classified_tiles_collate_fn
        else:
            data = ArcGISSegmentationItemList.from_folder(path/'images')\
                .filter_by_func(remove_image_without_label)\
                .split_by_rand_pct(val_split_pct, seed=seed)\
                .label_from_func(
                    get_y_func, classes=(['NoData'] + list(class_mapping.values())),
                    class_mapping=class_mapping,
                    color_mapping=color_mapping
                )

        if transforms is None:
            if _image_space_used == _map_space:
                transforms = get_transforms(
                    flip_vert=True,
                    max_rotate=90.,
                    max_zoom=3.0,
                    max_lighting=0.5
                )
            else:
                transforms = get_transforms(
                    max_zoom=3.0,
                    max_lighting=0.5
                )

        kwargs_transforms['tfm_y'] = True
        kwargs_transforms['size'] = chip_size
    elif dataset_type == 'PASCAL_VOC_rectangles':

        def image_without_label(imagefile, not_label_count=[0]):
            xmlfile = imagefile.parents[1] / 'labels' / imagefile.name.replace('{ims}'.format(ims=imagefile.suffix), '.xml')
            if not os.path.exists(xmlfile):
                not_label_count[0] += 1
                return False
            return True

        remove_image_without_label = partial(image_without_label, not_label_count=not_label_count)
        get_y_func = partial(
            _get_bbox_lbls,
            class_mapping=class_mapping,
            height_width=height_width
        )

        if _is_multispectral:
            data = ObjectMSItemList.from_folder(path/'images')\
            .filter_by_func(remove_image_without_label)\
            .split_by_rand_pct(val_split_pct, seed=seed)\
            .label_from_func(get_y_func)
            _show_batch_multispectral = show_batch_pascal_voc_rectangles
        else:
            data = ObjectDetectionItemList.from_folder(path/'images')\
                .filter_by_func(remove_image_without_label)\
                .split_by_rand_pct(val_split_pct, seed=seed)\
                .label_from_func(get_y_func)

        if transforms is None:
            ranges = (0, 1)
            if _image_space_used == _map_space:
                train_tfms = [
                    crop(size=chip_size, p=1., row_pct=ranges, col_pct=ranges),
                    dihedral_affine(),
                    brightness(change=(0.4, 0.6)),
                    contrast(scale=(0.75, 1.5)),
                    rand_zoom(scale=(1.0, 1.5))
                ]
            else:
                train_tfms = [
                    crop(size=chip_size, p=1., row_pct=ranges, col_pct=ranges),
                    brightness(change=(0.4, 0.6)),
                    contrast(scale=(0.75, 1.5)),
                    rand_zoom(scale=(1.0, 1.5))
                ]
            val_tfms = [crop(size=chip_size, p=1., row_pct=0.5, col_pct=0.5)]
            transforms = (train_tfms, val_tfms)

        kwargs_transforms['tfm_y'] = True
        databunch_kwargs['collate_fn'] = collate_fn
    elif dataset_type in ['Labeled_Tiles', 'Imagenet']:
        if dataset_type == 'Labeled_Tiles':
            get_y_func = partial(_get_lbls, class_mapping=class_mapping)
        else:
            # Imagenet
            def get_y_func(x):
                return x.parent.stem
            if collate_fn is not _bb_pad_collate:
                databunch_kwargs['collate_fn'] = collate_fn
            else:
                databunch_kwargs['collate_fn'] = _ImagenetCollater(chip_size)
            _images_folder = os.path.join(os.path.abspath(path), 'images')
            if not os.path.exists(_images_folder):
                raise Exception(f"""Could not find a folder "images" in "{os.path.abspath(path)}",
                \na folder "images" should be present in the supplied path to work with "Imagenet" data_type. """
                )

        if _is_multispectral:
            data = ArcGISMSImageList.from_folder(path/'images')\
                .split_by_rand_pct(val_split_pct, seed=42)\
                .label_from_func(get_y_func)
            _show_batch_multispectral = show_batch_labeled_tiles
        else:
            data = ImageList.from_folder(path/'images')\
                .split_by_rand_pct(val_split_pct, seed=42)\
                .label_from_func(get_y_func)

        if dataset_type == 'Imagenet':
            if class_mapping is None:
                class_mapping = {}
                index = 1
                for class_name in data.classes:
                    class_mapping[index] = class_name
                    index = index + 1

        if transforms is None:
            ranges = (0, 1)
            if _image_space_used == _map_space:
                train_tfms = [
                    rotate(degrees=30, p=0.5),
                    crop(size=chip_size, p=1., row_pct=ranges, col_pct=ranges),
                    dihedral_affine(),
                    brightness(change=(0.4, 0.6)),
                    contrast(scale=(0.75, 1.5))
                ]
            else:
                train_tfms = [
                    rotate(degrees=30, p=0.5),
                    crop(size=chip_size, p=1., row_pct=ranges, col_pct=ranges),
                    brightness(change=(0.4, 0.6)),
                    contrast(scale=(0.75, 1.5))
                ]
            val_tfms = [crop(size=chip_size, p=1.0, row_pct=0.5, col_pct=0.5)]
            transforms = (train_tfms, val_tfms)
    elif dataset_type == "superres":
        path_hr = path/'images'
        path_lr = path/'labels'
        il = ImageList.from_folder(path_hr)
        img_size = il[0].shape[1]
        if chip_size > img_size:
            chip_size = img_size
        downsample = kwargs.get('downsample_factor', 4)
        parallel(partial(resize_one, path_lr=path_lr, size=img_size/downsample, path_hr=path_hr, img_size=img_size), il.items, max_workers=0)

        data = ImageImageList.from_folder(path_lr)\
            .split_by_rand_pct(val_split_pct, seed=seed)\
            .label_from_func(lambda x: path_hr/x.name)
        if transforms is None:
            ranges = (0, 1)
            train_tfms = [
                crop(size=chip_size, p=1., row_pct=ranges, col_pct=ranges),
                brightness(change=(0.4, 0.6)),
                contrast(scale=(0.75, 1.5))
                ]
            val_tfms = [crop(size=chip_size, p=1.0, row_pct=0.5, col_pct=0.5)]
            transforms = (train_tfms, val_tfms)
        kwargs_transforms['tfm_y'] = True
        
    elif dataset_type in ['ner_json','BIO','IOB','LBIOU','BILUO']:
        if batch_size == 64:
            batch_size = 8
        return ner_prepare_data(dataset_type=dataset_type, path=path, class_mapping=class_mapping, val_split_pct=val_split_pct,batch_size=batch_size)
    elif dataset_type == "PointCloud":
        from ._utils.pointcloud_data import Transform3d
        if transforms is None:
            transform_fn = Transform3d()
        elif transforms is False:
            transform_fn = None
        else:
            transform_fn = transforms
        return pointcloud_prepare_data(path, class_mapping, batch_size, val_split_pct, dataset_type, transform_fn, **kwargs)
    else:
        raise NotImplementedError('Unknown dataset_type="{}".'.format(dataset_type))

    if _is_multispectral:
        if dataset_type == 'RCNN_Masks':
            kwargs['do_normalize'] = False
            if transforms ==  None:
                data = (src.transform(size=chip_size, tfm_y=True)
                    .databunch(**databunch_kwargs))
            else:
                data = (src.transform(transforms, size=chip_size, tfm_y=True) 
                        .databunch(**databunch_kwargs))
        else:
            data = (data.transform(transforms, **kwargs_transforms)
                        .databunch(**databunch_kwargs))
        
        if len(data.x) < 300:
            norm_pct = 1

        # Statistics        
        dummy_stats = {
            "batch_stats_for_norm_pct_0" : {
                "band_min_values":None, 
                "band_max_values":None, 
                "band_mean_values":None, 
                "band_std_values":None, 
                "scaled_min_values":None, 
                "scaled_max_values":None, 
                "scaled_mean_values":None, 
                "scaled_std_values":None
            }
        }
        normstats_json_path = os.path.abspath(data.path / '..' / 'esri_normalization_stats.json')
        if not os.path.exists(normstats_json_path):
            normstats = dummy_stats
            with open(normstats_json_path, 'w', encoding='utf-8') as f:
                json.dump(normstats, f, ensure_ascii=False, indent=4)
        else:
            with open(normstats_json_path) as f:
                normstats = json.load(f)

        norm_pct_search = f"batch_stats_for_norm_pct_{round(norm_pct*100)}"
        if norm_pct_search in normstats:
            batch_stats = normstats[norm_pct_search]
            for s in batch_stats:
                if batch_stats[s] is not None:
                    batch_stats[s] = torch.tensor(batch_stats[s])
        else:
            batch_stats = _get_batch_stats(data.x, norm_pct)
            normstats[norm_pct_search] = dict(batch_stats)
            for s in normstats[norm_pct_search]:
                if normstats[norm_pct_search][s] is not None:
                    normstats[norm_pct_search][s] = normstats[norm_pct_search][s].tolist()
            with open(normstats_json_path, 'w', encoding='utf-8') as f:
                json.dump(normstats, f, ensure_ascii=False, indent=4)

                

        # batch_stats -> [band_min_values, band_max_values, band_mean_values, band_std_values, scaled_min_values, scaled_max_values, scaled_mean_values, scaled_std_values]
        data._band_min_values = batch_stats['band_min_values']
        data._band_max_values = batch_stats['band_max_values']
        data._band_mean_values = batch_stats['band_mean_values']
        data._band_std_values = batch_stats['band_std_values']
        data._scaled_min_values = batch_stats['scaled_min_values']
        data._scaled_max_values = batch_stats['scaled_max_values']
        data._scaled_mean_values = batch_stats['scaled_mean_values']
        data._scaled_std_values = batch_stats['scaled_std_values']

        # Prevent Divide by zeros
        data._band_max_values[data._band_min_values == data._band_max_values]+=1
        data._scaled_std_values[data._scaled_std_values == 0]+=1e-02
        
        # Scaling
        data._min_max_scaler = partial(_tensor_scaler, min_values=data._band_min_values, max_values=data._band_max_values, mode='minmax')
        data._min_max_scaler_tfm = partial(_tensor_scaler_tfm, min_values=data._band_min_values, max_values=data._band_max_values, mode='minmax')
        #data.add_tfm(data._min_max_scaler_tfm)
        
        # Transforms
        def _scaling_tfm(x): 
            ## Scales Fastai Image Scaling | MS Image Values -> 0 - 1 range
            return x.__class__(data._min_max_scaler_tfm((x.data,None))[0][0])
        
        ## Fastai need tfm, order and resolve.
        class dummy():
            pass
        _scaling_tfm.tfm = dummy()
        _scaling_tfm.tfm.order = 0
        _scaling_tfm.resolve = dummy

        ## Scaling the images before applying any  other transform
        if getattr(data.train_ds, 'tfms') is not None:
            data.train_ds.tfms = [_scaling_tfm] + data.train_ds.tfms
        else:
            data.train_ds.tfms = [_scaling_tfm]
        if getattr(data.valid_ds, 'tfms') is not None:
            data.valid_ds.tfms = [_scaling_tfm] + data.valid_ds.tfms
        else:
            data.valid_ds.tfms = [_scaling_tfm]

        # Normalize
        data._do_normalize = True
        if kwargs.get('do_normalize', None) is not None:
            data._do_normalize = kwargs.get('do_normalize', True)
        if data._do_normalize:
            data = data.normalize(stats=(data._scaled_mean_values, data._scaled_std_values), do_x=True, do_y=False)
        
    elif dataset_type == 'RCNN_Masks':
        if transforms ==  None:
            data = (src.transform(size=chip_size, tfm_y=True)
                .databunch(**databunch_kwargs))
        else:
            data = (src.transform(transforms, tfm_y=True) 
                    .databunch(**databunch_kwargs))
        data.show_batch = types.MethodType( show_batch_rcnn_masks, data )

    else:
        data = (data.transform(transforms, **kwargs_transforms)
            .databunch(**databunch_kwargs)
            .normalize(imagenet_stats))

    # Assigning chip size from training dataset and not data.x 
    # to consider transforms and resizing
    data.chip_size = data.train_ds[0][0].shape[-1]

    if alter_class_mapping:
        new_mapping = {}
        for i, class_name in enumerate(class_mapping.keys()):
            new_mapping[i+1] = class_name
        class_mapping = new_mapping

    ## For calculating loss from inverse of frquency.
    if dataset_type == 'Classified_Tiles':
        with open(stats_file) as f:
            stats_json = json.load(f)
            pixel_stats = stats_json.get('ClassPixelStats', None)
            if pixel_stats is not None:
                data.num_pixels_per_class = pixel_stats.get('NumPixelsPerClass', None)
            else:
                data.num_pixels_per_class = None

           ## Might want to change the variable name
            if data.num_pixels_per_class is not None:
                num_pixels_per_class = np.array(data.num_pixels_per_class, dtype=np.int64)
                if num_pixels_per_class.sum() < 0:
                    data.overflow_encountered = True
                    data.class_weight = None
                else:
                    data.class_weight = num_pixels_per_class.sum() /num_pixels_per_class
            else:
                data.class_weight = None

    data.class_mapping = class_mapping
    data.color_mapping = color_mapping
    data.show_batch = types.MethodType(
        types.FunctionType(
            data.show_batch.__code__, data.show_batch.__globals__, data.show_batch.__name__,
            (min(int(math.sqrt(data.batch_size)), 5), *data.show_batch.__defaults__[1:]), data.show_batch.__closure__
        ),
        data
    )
    data.orig_path = path
    data.resize_to = kwargs_transforms.get('size', None)
    data.height_width = height_width
    data.downsample_factor = kwargs.get("downsample_factor")
    
    data._is_multispectral = _is_multispectral
    if data._is_multispectral:
        data._imagery_type = imagery_type
        data._bands = bands
        data._norm_pct = norm_pct
        data._rgb_bands = rgb_bands
        data._symbology_rgb_bands = rgb_bands

        # Handle invalid color mapping
        data._multispectral_color_mapping = color_mapping
        if any( -1 in x for x in data._multispectral_color_mapping.values() ):
            random_color_list = np.random.randint(low=0, high=255, size=(len(data._multispectral_color_mapping), 3)).tolist()
            for i, c in enumerate(data._multispectral_color_mapping):
                if -1 in data._multispectral_color_mapping[c]:
                    data._multispectral_color_mapping[c] = random_color_list[i]

        # prepare color array
        alpha = kwargs.get('alpha', 0.7)
        color_array = torch.tensor( list(data.color_mapping.values()) ).float() / 255
        alpha_tensor = torch.tensor( [alpha]*len(color_array) ).view(-1, 1).float()
        color_array = torch.cat( [ color_array, alpha_tensor ], dim=-1)
        background_color = torch.tensor( [[0, 0, 0, 0]] ).float()
        data._multispectral_color_array = torch.cat( [background_color, color_array] )

        # Prepare unknown bands list if bands data is missing
        if data._bands is None:
            n_bands = data.x[0].data.shape[0]
            if n_bands == 1:# Handle Pancromatic case
                data._bands = ['p']
                data._symbology_rgb_bands = [0]
            else:
                data._bands = ['u' for i in range(n_bands)]
                if n_bands == 2:# Handle Data with two channels
                    data._symbology_rgb_bands = [0]
        
        # 
        if data._rgb_bands is None:
            data._rgb_bands = []

        # 
        if data._symbology_rgb_bands is None:
            data._symbology_rgb_bands = [0, 1, 2][:min(n_bands, 3)]

        # Complete symbology rgb bands
        if len(data._bands) > 2 and len(data._symbology_rgb_bands) < 3:
            data._symbology_rgb_bands += [ min(max(data._symbology_rgb_bands)+1, len(data._bands)-1)  for i in range(3 - len(data._symbology_rgb_bands)) ]
        
        # Overwrite band values at r g b indexes with 'r' 'g' 'b'
        for i, band_idx in enumerate(data._rgb_bands):
            if band_idx is not None:
                if data._bands[band_idx] == 'u':
                    data._bands[band_idx] = ['r', 'g', 'b'][i]

        # Attach custom show batch
        if _show_batch_multispectral is not None:
            data.show_batch = types.MethodType( _show_batch_multispectral, data )

        # Apply filter band transformation if user has specified extract_bands otherwise add a generic extract_bands
        """
        extract_bands : List containing band indices of the bands from imagery on which the model would be trained. 
                        Useful for benchmarking and applied training, for reference see examples below.
                        
                        4 band naip ['r, 'g', 'b', 'nir'] + extract_bands=[0, 1, 2] -> 3 band naip with bands ['r', 'g', 'b'] 

        """
        data._extract_bands = kwargs.get('extract_bands', None) 
        if data._extract_bands is None:
            data._extract_bands = list(range(len(data._bands)))
        else:
            data._extract_bands_tfm = partial(_extract_bands_tfm, band_indices=data._extract_bands)
            data.add_tfm(data._extract_bands_tfm)   

        # Tail Training Override
        _train_tail = True
        if [data._bands[i] for i in data._extract_bands] == ['r', 'g', 'b']:
            _train_tail = False
        data._train_tail = kwargs.get('train_tail', _train_tail)

    if not_label_count[0]:
            logger = logging.getLogger()
            logger.warning("Please check your dataset. " + str(not_label_count[0]) + " images dont have the corresponding label files.")

    data._image_space_used = _image_space_used

    return data
