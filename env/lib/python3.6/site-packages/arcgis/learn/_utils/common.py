import os
import traceback
import json
import math

HAS_FASTAI = False
try:    
    from .env import raise_fastai_import_error
    from fastai.vision.data import ImageList
    from fastai.vision import Image, imagenet_stats
    import torch
    import numpy as np
    from matplotlib import pyplot as plt
    HAS_FASTAI = True
except Exception:
    import_exception = traceback.format_exc()
    pass

class ArcGISMSImage(Image):

    def show(self, ax=None, rgb_bands=None):
        if rgb_bands is None:
            rgb_bands = getattr(self, 'rgb_bands', [0, 1, 2])
        symbology_data = self.data[rgb_bands]
        im_shape = symbology_data.shape
        min_vals = symbology_data.view(im_shape[0], -1).min(dim=1)[0]
        max_vals = symbology_data.view(im_shape[0], -1).max(dim=1)[0]
        strechted_data = ( symbology_data - min_vals.view(im_shape[0], 1, 1) ) / ( max_vals.view(im_shape[0], 1, 1) - min_vals.view(im_shape[0], 1, 1) + .001 )
        data_to_plot = strechted_data.permute(1, 2, 0)
        if ax is not None:
            return ax.imshow(data_to_plot)
        else:
            return plt.imshow(data_to_plot)

    def print_method(self):
        return self.show()

    def _repr_png_(self):
        return self.show()
    
    def _repr_jpeg_(self): 
        return self.show()

    @classmethod
    def open_gdal(cls, path):
        try:
            import gdal
        except ImportError as e:
            message = f"""
            {e}\n\nPlease install gdal using the following command 
            \nconda install gdal=2.3.3
            """
            raise Exception(message)
        path = str(os.path.abspath(path))
        x = gdal.Open(path).ReadAsArray()
        x = torch.tensor(x.astype(np.float32))
        if len(x.shape)==2:
            x = x.unsqueeze(0)
        return cls(x)

class ArcGISMSImageList(ImageList):
    "`ImageList` suitable for classification tasks."
    _square_show_res = False
    def open(self, fn):
        return ArcGISMSImage.open_gdal(fn)

def get_multispectral_data_params_from_emd(data, emd):
    data._is_multispectral = emd.get('IsMultispectral', False)
    if data._is_multispectral:
        data._bands = emd.get('Bands')
        data._imagery_type = emd.get("ImageryType")
        data._extract_bands = emd.get("ExtractBands")
        data._train_tail = False # Hardcoded because we are never going to train a model with empty data
        normalization_stats = dict(emd.get("NormalizationStats")) # Copy the normalization stats so that self._data.emd has no tensors other wise it will raise error while creating emd
        for _stat in normalization_stats:
            if normalization_stats[_stat] is not None:
                normalization_stats[_stat] = torch.tensor(normalization_stats[_stat])
            setattr(data, ('_'+_stat), normalization_stats[_stat])
        data._do_normalize = emd.get("DoNormalize")
    return data

def get_post_processed_model(arcgis_model, input_normalization=True):
    if arcgis_model._backend == 'tensorflow':
        from .postprocessing_tf import get_post_processed_model_tf
        return get_post_processed_model_tf(arcgis_model, input_normalization=input_normalization)

def get_color_array(color_mapping: dict, alpha=0.7):
    color_array = np.array(list(color_mapping.values()), dtype=np.float) / 255
    color_array = np.concatenate([color_array, np.repeat([alpha], color_array.shape[0]).reshape(color_array.shape[0], 1)], axis=-1)
    return color_array

## show_batch() show_results() helper functions start ##

def to_torch_tensor(x):
    if type(x) == torch.Tensor:
        return x.clone().detach()
    return torch.tensor(x)

def get_symbology_bands(rgb_bands, extract_bands, bands):
    e = Exception('`rgb_bands` should be a valid band_order, list or tuple of length 3 or 1.')
    symbology_bands = []
    if not ( len(rgb_bands) == 3 or len(rgb_bands) == 1 ):
        raise(e)
    for b in rgb_bands:
        if type(b) == str:
            b_index = bands.index(b)
        elif type(b) == int:
            # check if the band index specified by the user really exists.
            bands[b]
            b_index = b
        else:
            raise(e)
        b_index = extract_bands.index(b_index)
        symbology_bands.append(b_index)
    return symbology_bands

def get_top_padding(title_font_size, nrows, imsize):
    top = 1 - (math.sqrt(title_font_size)/math.sqrt(100*nrows*imsize))
    return top

def kwarg_fill_none(kwargs, kwarg_key, default_value=None):
    value = kwargs.get(kwarg_key, None)
    if value is None:
        return default_value
    return value

def find_data_loader(type_data_loader, data):
    if type_data_loader == 'training':
        data_loader = data.train_dl
    elif type_data_loader == 'validation':
        data_loader = data.valid_dl
    elif type_data_loader == 'testing':
        data_loader = data.test_dl
    else:
        e = Exception(f'could not find {type_data_loader} in data.')
        raise(e)
    return data_loader

def get_nbatches(data_loader, nbatches):
    x_batch, y_batch = [], []
    dl_iterater = iter(data_loader)
    get_next = True
    i = 0
    while i < nbatches and get_next:
        i+=1
        try:
            x, y = next(dl_iterater)
            x_batch.append(x)
            y_batch.append(y)
        except StopIteration:
            get_next = False
    return x_batch, y_batch

def image_tensor_checks_plotting(imagetensor_batch):
    symbology_x_batch = imagetensor_batch
    # Channel first to channel last for plotting
    symbology_x_batch = symbology_x_batch.permute(0, 2, 3, 1)

    # Clamp float values to range 0 - 1
    if symbology_x_batch.mean() < 1:
        symbology_x_batch = symbology_x_batch.clamp(0, 1)

    # Squeeze channels if single channel (1, 224, 224) -> (224, 224)
    if symbology_x_batch.shape[-1] == 1:
        symbology_x_batch = symbology_x_batch.squeeze(-1)
    return symbology_x_batch

def denorm_x(imagetensor_batch, self=None):
    """
    denormalizes a imagetensor_batch for plotting
    -------------------------
    imagetensor_batch: imagebatch with shape (batch, bands, rows, columns)
    
    self: optional. can be an instance of ArcGISModel or arcgis.learn data object(databunch)
    -------------------------
    returns denormalized imagetensor_batch
    """
    from .. import models
    if isinstance(self, models._arcgis_model.ArcGISModel):
        data = self._data
    else:
        data = self
    if data is not None and data._is_multispectral:
        return denorm_image(
            imagetensor_batch, 
            mean=data._scaled_mean_values[data._extract_bands],
            std=data._scaled_std_values[data._extract_bands]
        )
    return denorm_image(imagetensor_batch)
    
def denorm_image(imagetensor_batch, mean=None, std=None):
    # prepare normalization stats
    if mean is None or std is None:
        mean = imagenet_stats[0]
        std = imagenet_stats[1]
    mean = to_torch_tensor(mean).to(imagetensor_batch).view(1, -1, 1, 1)
    std = to_torch_tensor(std).to(imagetensor_batch).view(1, -1, 1, 1)
    return (imagetensor_batch * std) + mean

def predict_batch(self, imagetensor_batch):
    if self._backend == 'pytorch':
        predictions = self.learn.model.eval()(imagetensor_batch.to(self._device).float()).detach()
        return predictions
    elif self._backend == 'tensorflow':
        from .common_tf import predict_batch_tf
        return predict_batch_tf(self, imagetensor_batch)

## show_batch() show_results() helper functions end ##

## Image Stretching Functions start ##

def dynamic_range_adjustment(imagetensor_batch):
    shp = imagetensor_batch.shape
    min_vals = imagetensor_batch.view(shp[0], shp[1], -1).min(dim=2)[0]
    max_vals = imagetensor_batch.view(shp[0], shp[1], -1).max(dim=2)[0]
    imagetensor_batch = imagetensor_batch / ( max_vals.view(shp[0], shp[1], 1, 1) - min_vals.view(shp[0], shp[1], 1, 1) + .001 )
    return imagetensor_batch

## Image Stretching Functions end ##

def load_model(emd_path, data=None):
    from .. import models
    # if not HAS_FASTAI:
    #     raise_fastai_import_error(import_exception=import_exception)
        
    _emd_path = os.path.abspath(emd_path)
    if not os.path.exists(emd_path):
        raise Exception(f"Could not find an EMD file at the specified path does not exist '{emd_path}'")

    with open(_emd_path) as f:
        emd = json.load(f)
    model_name = emd['ModelName']
    model_cls = getattr(models, model_name, None)

    if model_cls is None:
        raise Exception(f"Failed to load model, Could not find class '{model_name}' in arcgis.learn.models")

    model_obj = model_cls.from_model(_emd_path, data=data)

    return model_obj
