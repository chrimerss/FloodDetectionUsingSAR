import torch
import matplotlib.pyplot as plt
import math
from .common import get_nbatches

def show_batch_labeled_tiles(self, rows=3, **kwargs): # parameters adjusted in kwargs   
    nrows = rows
    ncols = kwargs.get('ncols', nrows)
    #start_index = kwargs.get('start_index', 0) # Does not work with dataloader
    
    n_items = kwargs.get('n_items', nrows*ncols)
    n_items = min(n_items, len(self.x))
    nrows = math.ceil(n_items/ncols)

    type_data_loader = kwargs.get('data_loader', 'training') # options : traininig, validation, testing
    if type_data_loader == 'training':
        data_loader = self.train_dl
    elif type_data_loader == 'validation':
        data_loader = self.valid_dl
    elif type_data_loader == 'testing':
        data_loader = self.test_dl
    else:
        e = Exception(f'could not find {type_data_loader} in data. Please ensure that the data loader type is traininig, validation or testing ')
        raise(e)

    rgb_bands = kwargs.get('rgb_bands', self._symbology_rgb_bands)
    nodata = kwargs.get('nodata', 0)
    imsize = kwargs.get('imsize', 5)
    statistics_type = kwargs.get('statistics_type', 'dataset') # Accepted Values `dataset`, `DRA`

    e = Exception('`rgb_bands` should be a valid band_order, list or tuple of length 3 or 1.')
    symbology_bands = []
    if not ( len(rgb_bands) == 3 or len(rgb_bands) == 1 ):
        raise(e)
    for b in rgb_bands:
        if type(b) == str:
            b_index = self._bands.index(b)
        elif type(b) == int:
            self._bands[b] # To check if the band index specified by the user really exists.
            b_index = b
        else:
            raise(e)
        b_index = self._extract_bands.index(b_index)
        symbology_bands.append(b_index)

    # Get Batch
    x_batch, y_batch = get_nbatches(data_loader, n_items)
    x_batch = torch.cat(x_batch)
    # Denormalize X
    x_batch = (self._scaled_std_values[self._extract_bands].view(1, -1, 1, 1).to(x_batch) * x_batch ) + self._scaled_mean_values[self._extract_bands].view(1, -1, 1, 1).to(x_batch)
    y_batch = torch.cat(y_batch)

    # Extract RGB Bands
    symbology_x_batch = x_batch[:, symbology_bands]
    if statistics_type == 'DRA':
        shp = symbology_x_batch.shape
        min_vals = symbology_x_batch.view(shp[0], shp[1], -1).min(dim=2)[0]
        max_vals = symbology_x_batch.view(shp[0], shp[1], -1).max(dim=2)[0]
        symbology_x_batch = symbology_x_batch / ( max_vals.view(shp[0], shp[1], 1, 1) - min_vals.view(shp[0], shp[1], 1, 1) + .001 )

    # Channel first to channel last and clamp float values to range 0 - 1 for plotting
    symbology_x_batch = symbology_x_batch.permute(0, 2, 3, 1)
    # Clamp float values to range 0 - 1
    if symbology_x_batch.mean() < 1:
        symbology_x_batch = symbology_x_batch.clamp(0, 1)

    # Squeeze channels if single channel (1, 224, 224) -> (224, 224)
    if symbology_x_batch.shape[-1] == 1:
        symbology_x_batch = symbology_x_batch.squeeze()

    # Get color Array
    color_array = self._multispectral_color_array

    # Size for plotting
    fig, axs = plt.subplots(nrows=nrows, ncols=ncols, figsize=(ncols*imsize, nrows*imsize))
    idx = 0
    for r in range(nrows):
        for c in range(ncols):
            if idx < symbology_x_batch.shape[0]:
                axi = axs
                if nrows == 1:
                    axi = axi
                else:
                    axi = axi[r]
                if ncols == 1:
                    axi = axi
                else:
                    axi = axi[c]
                axi.imshow(symbology_x_batch[idx].cpu().numpy())
                title = f"{self.classes[y_batch[idx].item()]}"
                axi.set_title(title)
                axi.axis('off')
            else:
                ax[r][c].axis('off')
            idx += 1
