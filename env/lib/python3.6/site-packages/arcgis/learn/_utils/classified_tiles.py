import torch
import numpy as np
from .pointcloud_data import calculate_metrics
import pandas as pd
import math

def calculate_precision_recall(all_y, all_pred, false_positives, true_positives, false_negatives, class_mapping, ignore_mapped_class):
    
    for i in range(len(all_y)): 
        if all_y[i] in ignore_mapped_class:
            continue       
        false_positives[all_pred[i]] += int(all_y[i] != all_pred[i])
        true_positives[all_pred[i]] += int(all_y[i] == all_pred[i])
        false_negatives[all_y[i]] += int(all_y[i] != all_pred[i])

    precision, recall, f_1 = calculate_metrics(false_positives, true_positives, false_negatives)
    data = [precision, recall, f_1]
    index = ['precision', 'recall', 'f1_score']
    class_mapping = {z+1:v for z, v in enumerate(class_mapping.values()) if z+1 not in ignore_mapped_class}
    if ignore_mapped_class == []:
        columns = ['background']+[class_mapping[i] for i in range(1, len(false_negatives))]
    else:
        columns = [class_mapping[i] for i in range(1, len(false_negatives)) if i not in ignore_mapped_class]
        data = np.array(data)
        data = data[:, np.logical_not(np.isin(np.arange(len(false_negatives)), ignore_mapped_class))]
    df = pd.DataFrame(data, columns=columns, index=index) 
    return df        

def per_class_metrics(self, **kwargs):
    dl = kwargs.get('dl', None)
    ignore_mapped_class = kwargs.get('ignore_mapped_class', [])
    model = self.learn.model.eval()
    all_y = []
    all_pred = []    
    false_positives = [0] * self._data.c
    true_positives = [0] * self._data.c
    false_negatives = [0] * self._data.c
    for batch in self._data.valid_dl if dl is None else dl:
        x, y = batch
        y = y.cpu().numpy()
        with torch.no_grad():
            predictions = model(x).detach()
            for k in ignore_mapped_class:
                predictions[:, k] = -1
            predictions = predictions.argmax(dim=1).cpu().numpy()
        all_y.append(y.reshape(-1))
        all_pred.append(predictions.reshape(-1))

    all_y = np.concatenate(all_y)
    all_pred = np.concatenate(all_pred)

    return calculate_precision_recall(all_y, all_pred, false_positives, true_positives, false_negatives, self._data.class_mapping, ignore_mapped_class)

def show_batch_classified_tiles(self, rows=3, alpha=0.7, **kwargs):
    import matplotlib.pyplot as plt
    from .._utils.common import kwarg_fill_none, get_nbatches, find_data_loader, get_top_padding, get_symbology_bands, dynamic_range_adjustment, denorm_x, image_tensor_checks_plotting

    imsize = kwarg_fill_none(kwargs, 'imsize', 5)
    nrows = rows
    ncols = kwarg_fill_none(kwargs, 'ncols', 3)
    imsize = kwarg_fill_none(kwargs, 'imsize', 5)
    statistics_type = kwarg_fill_none(kwargs, 'statistics_type', 'dataset') # Accepted Values `dataset`, `DRA`
        
    n_items = kwargs.get('n_items', nrows*ncols)
    n_items = min(n_items, len(self.x))
    nrows = math.ceil(n_items/ncols)

    top = kwargs.get('top', None)
    title_font_size=16
    if top is None:
        top = get_top_padding(
            title_font_size=title_font_size, 
            nrows=nrows, 
            imsize=imsize
            )

    # Get n batches
    type_data_loader = kwarg_fill_none(kwargs, 'data_loader', 'training') # options : traininig, validation, testing
    data_loader = find_data_loader(type_data_loader, self)
    x_batch, y_batch = get_nbatches(data_loader, math.ceil(n_items/self.batch_size))
    symbology_x_batch = x_batch = torch.cat(x_batch)
    y_batch = torch.cat(y_batch)


    symbology_bands = [0, 1, 2]
    if self._is_multispectral:
        # Get RGB Bands for plotting
        rgb_bands = kwarg_fill_none(kwargs, 'rgb_bands', self._symbology_rgb_bands)

        # Get Symbology bands
        symbology_bands = get_symbology_bands(rgb_bands, self._extract_bands, self._bands)

    # Denormalize X
    x_batch = denorm_x(x_batch, self)

    # Extract RGB Bands for plotting
    symbology_x_batch = x_batch[:, symbology_bands]

    # Apply Image Strecthing
    if statistics_type == 'DRA':
        symbology_x_batch = dynamic_range_adjustment(symbology_x_batch)

    symbology_x_batch = image_tensor_checks_plotting(symbology_x_batch)

    # Get color Array
    color_array = self._multispectral_color_array
    color_array[1:, 3] = alpha

    # Plot now
    fig, axs = plt.subplots(nrows=nrows, ncols=ncols, figsize=(ncols*imsize, nrows*imsize))
    idx = 0
    for r in range(nrows):
        for c in range(ncols):
            axi = axs
            if nrows == 1:
                axi = axi
            else:
                axi = axi[r]
            if ncols == 1:
                axi = axi
            else:
                axi = axi[c]
            axi.axis('off')
            if idx < symbology_x_batch.shape[0]:
                axi.imshow(symbology_x_batch[idx].cpu().numpy())
                y_rgb = color_array[y_batch[idx][0]].cpu().numpy()
                axi.imshow(y_rgb, alpha=alpha)
            idx+=1

