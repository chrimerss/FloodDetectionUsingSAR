# MIT License

# PointCNN
# Copyright (c) 2018 Shandong University
# Copyright (c) 2018 Yangyan Li, Rui Bu, Mingchao Sun, Baoquan Chen

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import glob
import importlib
import sys
import warnings
import os
import math
from pathlib import Path
import json
import types
import random
import logging
import shutil
logger = logging.getLogger()

try:
    from torch.utils.data import DataLoader, Dataset, SubsetRandomSampler
    import torch.nn.functional as F
    import torch
    import numpy as np
    from fastai.data_block import DataBunch
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D
    import arcgis
    from fastai.data_block import ItemList
    from fastprogress.fastprogress import master_bar, progress_bar
    from transforms3d.euler import euler2mat
except ImportError:
    # To avoid breaking builds.
    class Dataset():
        pass
    class ItemList():
        pass

def try_imports(list_of_modules):
    ## Not a generic function.
    try:
        for module in list_of_modules:
            importlib.import_module(module)
    except Exception as e:
        raise Exception(f"""This function requires {' '.join(list_of_modules)}. Install plotly and laspy using 'conda install -c esri -c plotly laspy=1.6.0 plotly=4.5.0 plotly-orca=1.2.1 psutil' and install transforms3d and h5py using `pip install transforms3d==0.3.1 h5py==2.10.0`.
\n On Linux systems, Also install `xvfb` \n Additionally visit: https://developers.arcgis.com/python/guide/point-cloud-segmentation-using-pointcnn/ for step by step setup.""")

def try_import(module):
    try:
        importlib.import_module(module)
    except ModuleNotFoundError:
        if module == 'plotly':
            raise Exception("This function requires plotly. Install it using 'conda install -c plotly plotly=4.5.0 plotly-orca=1.2.1 psutil'")
        elif module == 'laspy':
            raise Exception("This function requires laspy. Install it using 'conda install -c esri laspy=1.6.0'")
        elif module == 'h5py':
            raise Exception(f"This function requires h5py. Install it using 'pip install h5py==2.10.0'")
        else:
            raise Exception(f"This function requires {module}. Please install it in your environment.")

def pad_tensor(cur_tensor, max_points, to_float=True):
    cur_points = cur_tensor.shape[0]
    if cur_points < max_points:
        remaining_points = max_points - cur_points
        if len(cur_tensor.shape) < 2:
            remaining_tensor = torch.zeros(remaining_points)
        else:
            remaining_tensor = torch.zeros(remaining_points, cur_tensor.shape[1])
        if to_float:
            remaining_tensor = remaining_tensor.float()
        else:
            remaining_tensor = remaining_tensor.long()
        cur_tensor = torch.cat((cur_tensor, remaining_tensor), dim=0)
    else:
        cur_tensor = cur_tensor[:max_points]
        cur_points = max_points 
    return cur_tensor, cur_points

def concatenate_tensors(read_file, input_keys, tile, max_points):
    cat_tensor = []
    
    cur_tensor = torch.tensor(read_file['xyz'][tile[1]:tile[1]+tile[2]].astype(np.float32))
    if len(cur_tensor.shape) < 2:
        cur_tensor = cur_tensor[:, None]

    cur_tensor, cur_points = pad_tensor(cur_tensor, max_points)
    cat_tensor.append(cur_tensor)
    
    for key, min_max in input_keys.items():
        if key not in ['xyz']:
            cur_tensor = torch.tensor(read_file[key][tile[1]:tile[1]+tile[2]].astype(np.float32))
            if len(cur_tensor.shape) < 2:
                cur_tensor = cur_tensor[:, None]

            max_val = cur_tensor.new(min_max['max'])
            min_val = cur_tensor.new(min_max['min'])
            cur_tensor = cur_tensor = (cur_tensor - min_val) / (max_val )  ## Test with one_hot
            cur_tensor, cur_points = pad_tensor(cur_tensor, max_points)
            cat_tensor.append(cur_tensor)
    
    return torch.cat(cat_tensor, dim=1), cur_points

def remap_classes(class_values):
    flag = False
    if class_values[0] != 0:
        return True
    for i in range(len(class_values) - 1):
        if class_values[i] + 1 != class_values[i+1]:
            return True
    return flag    

class PointCloudDataset(Dataset):
    def __init__(self, path, max_point, extra_dim, class_mapping, **kwargs):
        try_import("h5py")
        import h5py
        self.init_kwargs = kwargs
        self.path = Path(path)
        self.max_point = max_point  ## maximum number of points
        
        with open(self.path / 'Statistics.json', 'r') as f:
            self.statistics = json.load(f)        
        
        self.block_size = self.statistics['parameters']['tileSize']
        self.input_keys = self.statistics['features']  ## Keys to include in training
        self.classification_key = kwargs.get('classification_key', 'classification')     ## Key which contain labels
        self.extra_dim = extra_dim
        self.total_dim = 3 + extra_dim
        self.extra_features = self.input_keys
        
        if class_mapping is None:
            self.class_mapping =  {value['classCode']:idx for idx,value in enumerate(self.statistics['classification']['table'])}
        else:
            self.class_mapping = class_mapping
        ## Helper attributes for remapping
        self.c = len(self.class_mapping)
        self.color_mapping = kwargs.get('color_mapping', np.array([np.random.randint(0, 255, 3) for i in range(self.c)])/255)
        self.remap = False
        self.remap = remap_classes(list(self.class_mapping.values()))
        
        self.classes = list(self.class_mapping.values())

        with h5py.File(self.path / 'ListTable.h5', 'r') as f:
            files = f['Files'][:]
            self.tiles = f['Tiles'][:]
        
        self.relative_files = files
        self.filenames = [self.path / file.decode() for file in files]
        self.h5files = [(h5py.File(filename, 'r')) for filename in self.filenames]
    
    def __len__(self):
        return len(self.tiles)
    
    def __getitem__(self, i):
        
        tile = self.tiles[i]        
        read_file = self.h5files[tile[0]]
        
        if self.classification_key in read_file.keys():
            
            classification, _ = pad_tensor(torch.tensor(read_file[self.classification_key][tile[1]:tile[1]+tile[2]].astype(int)),
                                        self.max_point,
                                        to_float=False
                                        )
            if not self.remap:
                return concatenate_tensors(read_file, self.input_keys, tile, self.max_point), classification
            else:
                return concatenate_tensors(read_file, self.input_keys, tile, self.max_point), remap_labels(classification)                                        
        else:
            logger.warning(f"key `{self.classification_key}` could not be found in the exported files.")
            return concatenate_tensors(read_file, self.input_keys, tile, self.max_point), None
        
    def close(self):
        [file.close() for file in self.h5files]

def minmax_scale(pc):
    min_val = np.amin(pc, axis=0)
    max_val = np.amax(pc, axis=0)
    return (pc - min_val[None])/max(max_val - min_val)

def recompute_color_mapping(color_mapping, all_classes):
    color_mapping = {int(k):v for k, v in color_mapping.items()}
    try:
        color_mapping = {k:color_mapping[k] for k in all_classes}
    except KeyError:
        raise Exception(f"Keys of your classes in your color_mapping do not match with classes present in data i.e {all_classes}")
    return color_mapping

def class_string(label_array, prefix=''):
    return [f'{prefix}class: {k}' for k in label_array]

def mask_classes(labels, mask_class, class_mapping=None):
    if class_mapping is not None:
        mask_class = [class_mapping[x] for x in mask_class]
    if mask_class == []:
        ## return complete mask
        return labels != None
    else:
        sample_idxs = np.concatenate([(labels[None]!=mask) for mask in mask_class])
        sample_idxs = sample_idxs.all(axis=0)
        return sample_idxs    

def get_max_display_points(self, kwargs):
    if "max_display_point" in kwargs.keys():
        max_display_point = kwargs['max_display_point']
        self.max_display_point = max_display_point
    else:
        if hasattr(self, 'max_display_point'):
            max_display_point = self.max_display_point
        else:
            max_display_point = 20000   
    return max_display_point

def show_point_cloud_batch(self, rows=2, figsize=(6,12), color_mapping=None, **kwargs):

    """
    It will plot 3d point cloud data you exported in the notebook.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    rows                    Optional rows. Number of rows to show. Default
                            value is 2 and maximum value is the `batch_size`
                            passed in `prepare_data`. 
    ---------------------   -------------------------------------------
    color_mapping           Optional dictionary. Mapping from class value
                            to RGB values. Default value
                            Example: {0:[220,220,220],
                                        1:[255,0,0],
                                        2:[0,255,0],
                                        3:[0,0,255]}                                                         
    =====================   ===========================================

    **kwargs**

    =====================   ===========================================
    **Argument**            **Description**
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

    filter_outliers = False
    try_import("h5py")
    import h5py
    try_import('plotly')
    import plotly.graph_objects as go
    mask_class = kwargs.get('mask_class', [])
    max_display_point = get_max_display_points(self, kwargs)
    rows = min(rows, self.batch_size)
    color_mapping = self.color_mapping if color_mapping is None else color_mapping
    color_mapping = recompute_color_mapping(color_mapping, self.classes)       
    color_mapping = np.array(list(color_mapping.values())) / 255

    h5_files = self.h5files.copy()
    random.shuffle(h5_files)  

    idx = 0
    file_idx = 0
    while (idx < rows):
        file = h5_files[file_idx]
        pc = file['xyz'][:]
        labels = file['classification'][:]
        unmapped_labels = labels.copy()
        if self.remap:
            labels = remap_labels(labels, self.class_mapping) 
        sample_idxs = mask_classes(labels, mask_class, self.class_mapping if self.remap else None)
        sampled_pc = pc[sample_idxs]

        if sampled_pc.shape[0] == 0:
            file_idx += 1
            continue
        x, y, z = recenter(sampled_pc).transpose(1,0)  ## convert to 3,N so that upacking works

        if filter_outliers:
            ## Filter on the basis of std.
            mask = filter_pc(pc)
        else:
            ## all points
            mask = x > -9999999    

        if sample_idxs.sum() > max_display_point:
            raise_maxpoint_warning(idx, kwargs, logger, max_display_point)
            mask = np.random.randint(0, sample_idxs.sum(), max_display_point)   
        
        color_list = color_mapping[labels[sample_idxs]][mask].tolist()
        scene=dict(aspectmode='data')

        layout = go.Layout(
            width=kwargs.get('width', 750),
            height=kwargs.get('height', 512),
            scene = scene)

        fig = go.Figure(data=[go.Scatter3d(x=x[mask], y=y[mask], z=z[mask], 
                                        mode='markers', marker=dict(size=1, color=color_list),
                                        text=class_string(unmapped_labels[sample_idxs][mask]))], layout=layout)
        fig.show()        

        if idx == rows-1:
            break        
        idx += 1
        file_idx += 1

def filter_pc(pc):
    mean = pc.mean(0)
    std = pc.std(0)
    mask = (pc[:, 0] < (mean[0] + 2*std[0])) & (pc[:, 1] < (mean[1] + 2*std[1])) & (pc[:, 2] < (mean[2] + 2*std[2]))
    return mask

def recenter(pc):
    min_val = np.amin(pc, axis=0)
    max_val = np.amax(pc, axis=0)
    return (pc - min_val[None])

def show_point_cloud_batch_TF(self, rows=2, color_mapping=None, **kwargs):

    """
    It will plot 3d point cloud data you exported in the notebook.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    rows                    Optional rows. Number of rows to show. Default
                            value is 2 and maximum value is the `batch_size`
                            passed in `prepare_data`. 
    ---------------------   -------------------------------------------
    color_mapping           Optional dictionary. Mapping from class value
                            to RGB values. Default value
                            Example: {0:[220,220,220],
                                        1:[255,0,0],
                                        2:[0,255,0],
                                        3:[0,0,255]}                                                         
    =====================   ===========================================

    **kwargs**

    =====================   ===========================================
    **Argument**            **Description**
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

    filter_outliers = False
    try_import("h5py")
    import h5py
    try_import('plotly')
    import plotly.graph_objects as go
    mask_class = kwargs.get('mask_class', [])
    rows = min(rows, self.batch_size)
    max_display_point = get_max_display_points(self, kwargs)
    color_mapping = self.color_mapping if color_mapping is None else color_mapping
    color_mapping = recompute_color_mapping(color_mapping, self.classes)       
    color_mapping = np.array(list(color_mapping.values())) / 255

    idx = 0
    import random
    keys = list(self.meta['files'].keys()).copy()
    keys = [k for k in keys if 'train' in Path(k).parts]
    random.shuffle(keys)
    
    for idx_file, fn in enumerate(keys):
        num_files = self.meta['files'][fn]['idxs']
        block_center = self.meta['files'][fn]['block_center']
        block_center = np.array(block_center)
        block_center[0][2], block_center[0][1] = block_center[0][1], block_center[0][2]
        if num_files == []:
            continue
        if not Path(fn).is_absolute():
            fn = str(self.path / fn)
        idxs = [h5py.File(fn[:-3] + f'_{i}.h5', 'r') for i in num_files]
        pc = []
        labels = []
        for i in idxs:
            current_block = i['unnormalized_data'][:, :3]
            data_num = i['data_num'][()]   
            pc.append(current_block[:data_num])
            labels.append(i['label_seg'][:data_num])  
            
        if pc == []:
            continue         
       
        pc = np.concatenate(pc, axis=0)
        labels = np.concatenate(labels, axis=0)
        unmapped_labels = labels.copy()    
        if self.remap:
            labels = remap_labels(labels, self.class_mapping)  
        sample_idxs = mask_classes(labels, mask_class, self.class_mapping if self.remap else None)
        sampled_pc = pc[sample_idxs]
        if sampled_pc.shape[0] == 0:
            continue
        x, y, z = recenter(sampled_pc).transpose(1,0)       
        if filter_outliers:
            ## Filter on the basis of std.
            mask = filter_pc(pc)
        else:
            ## all points
            mask = [True] * len(x)

        if sample_idxs.sum() > max_display_point:
            raise_maxpoint_warning(idx_file, kwargs, logger, max_display_point)
            mask = np.random.randint(0, sample_idxs.sum(), max_display_point)  
            
        color_list =  color_mapping[labels[sample_idxs]][mask].tolist()
        
        scene=dict(aspectmode='data')
        layout = go.Layout(
            width=kwargs.get('width', 750),
            height=kwargs.get('height', 512),
            scene = scene)

        figww = go.Figure(data=[go.Scatter3d(x=x[mask], y=z[mask], z=y[mask], 
                                        mode='markers', marker=dict(size=1, color=color_list),
                                        text=class_string(unmapped_labels[sample_idxs][mask]))], layout=layout)
        figww.show()

        if idx == rows-1:
            break
        idx += 1

def get_device():
    if getattr(arcgis.env, "_processorType", "") == "GPU" and torch.cuda.is_available():
        device = torch.device("cuda")
    elif getattr(arcgis.env, "_processorType", "") == "CPU":
        device = torch.device("cpu")
    else:
        device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    return device    

def read_xyzinumr_label_from_las(filename_las, extra_features):
    try_import('laspy')
    import laspy
    file = laspy.file.File(filename_las, mode='r')    
    h = file.header
    xyzirgb_num = h.point_records_count
    labels = file.Classification
    
    xyz = np.concatenate([file.x[:, None], file.y[:, None], file.z[:, None]] + [(np.clip(getattr(file, f[0]), None, f[1])[:, None] - f[2])/ (f[1] - f[2]) for f in extra_features],
                         axis=1)
    
    xyzirgb_num = len(xyz)
    return xyz, labels, xyzirgb_num

def prepare_las_data(root,
                     block_size,
                     max_point_num,
                     output_path,
                     extra_features=[('intensity', 5000, 0), ('num_returns', 5, 0)],
                     grid_size=1.0,
                     blocks_per_file=2048,
                     folder_names=['train', 'val'],
                     segregate=True,
                     **kwargs
                    ):
    try_import("h5py")
    import h5py
    block_size_ = block_size
    batch_size= blocks_per_file
    data = np.zeros((batch_size, max_point_num, 3 + len(extra_features))) #XYZ, Intensity, NumReturns
    unnormalized_data = np.zeros((batch_size, max_point_num, 3 + len(extra_features)))
    data_num = np.zeros((batch_size), dtype=np.int32)
    label = np.zeros((batch_size), dtype=np.int32)
    label_seg = np.zeros((batch_size, max_point_num), dtype=np.int32)
    indices_split_to_full = np.zeros((batch_size, max_point_num), dtype=np.int32)
    LOAD_FROM_EXT = '.las'
    os.makedirs(output_path, exist_ok=True)

    if (Path(output_path) / 'meta.json').exists() or (Path(output_path) / 'Statistics.json').exists():
        raise Exception(f"The given output path({output_path}) already contains exported data. Either delete those files or pass in a new output path.")

    folders = [os.path.join(root, folder) for folder in folder_names]  ## Folders are named train and val
    mb = master_bar(range(len(folders)))
    for itn in mb:
        folder = folders[itn]
        os.makedirs(os.path.join(output_path, Path(folder).stem), exist_ok=True)
        datasets = [filename[:-4] for filename in os.listdir(folder) if filename.endswith(LOAD_FROM_EXT)]
        # mb.write(f'{itn + 1}. Exporting {Path(folder).stem} folder')
        for dataset_idx, dataset in enumerate(progress_bar(datasets, parent=mb)):
            filename_ext = os.path.join(folder, dataset + LOAD_FROM_EXT)
            if LOAD_FROM_EXT == '.las':
                xyzinumr, labels, xyz_num = read_xyzinumr_label_from_las(filename_ext, extra_features)
                xyz, other_features = np.split(xyzinumr, (3,), axis=-1)
                if len(other_features.shape) < 2:
                    other_features = other_features[:, None]
            else:
                xyz, labels, xyz_num = read_xyz_label_from_txt(filename_ext)
            
            offsets = [('zero', 0.0), ('half', block_size_ / 2)]

            for offset_name, offset in offsets:
                idx_h5 = 0
                idx = 0

                xyz_min = np.amin(xyz, axis=0, keepdims=True) - offset
                xyz_max = np.amax(xyz, axis=0, keepdims=True)
                block_size = (block_size_, block_size_, 2 * (xyz_max[0, -1] - xyz_min[0, -1]))  
                xyz_blocks = np.floor((xyz - xyz_min) / block_size).astype(np.int)

                blocks, point_block_indices, block_point_counts = np.unique(xyz_blocks, return_inverse=True,
                                                                            return_counts=True, axis=0)
                block_point_indices = np.split(np.argsort(point_block_indices), np.cumsum(block_point_counts[:-1]))

                block_to_block_idx_map = dict()
                for block_idx in range(blocks.shape[0]):
                    block = (blocks[block_idx][0], blocks[block_idx][1])
                    block_to_block_idx_map[(block[0], block[1])] = block_idx

                # merge small blocks into one of their big neighbors
                block_point_count_threshold = max_point_num / 10
                nbr_block_offsets = [(0, 1), (1, 0), (0, -1), (-1, 0), (-1, 1), (1, 1), (1, -1), (-1, -1)]
                block_merge_count = 0
                for block_idx in range(blocks.shape[0]):
                    if block_point_counts[block_idx] >= block_point_count_threshold:
                        continue

                    block = (blocks[block_idx][0], blocks[block_idx][1])
                    for x, y in nbr_block_offsets:
                        nbr_block = (block[0] + x, block[1] + y)
                        if nbr_block not in block_to_block_idx_map:
                            continue

                        nbr_block_idx = block_to_block_idx_map[nbr_block]
                        if block_point_counts[nbr_block_idx] < block_point_count_threshold:
                            continue

                        block_point_indices[nbr_block_idx] = np.concatenate(
                            [block_point_indices[nbr_block_idx], block_point_indices[block_idx]], axis=-1)
                        block_point_indices[block_idx] = np.array([], dtype=np.int)
                        block_merge_count = block_merge_count + 1
                        break

                idx_last_non_empty_block = 0
                for block_idx in reversed(range(blocks.shape[0])):
                    if block_point_indices[block_idx].shape[0] != 0:
                        idx_last_non_empty_block = block_idx
                        break

                # uniformly sample each block
                for block_idx in range(idx_last_non_empty_block + 1):
                    point_indices = block_point_indices[block_idx]
                    if point_indices.shape[0] == 0:
                        continue
                    block_points = xyz[point_indices]
                    block_min = np.amin(block_points, axis=0, keepdims=True)
                    xyz_grids = np.floor((block_points - block_min) / grid_size).astype(np.int)
                    grids, point_grid_indices, grid_point_counts = np.unique(xyz_grids, return_inverse=True,
                                                                            return_counts=True, axis=0)
                    grid_point_indices = np.split(np.argsort(point_grid_indices), np.cumsum(grid_point_counts[:-1]))
                    grid_point_count_avg = int(np.average(grid_point_counts))
                    point_indices_repeated = []
                    for grid_idx in range(grids.shape[0]):
                        point_indices_in_block = grid_point_indices[grid_idx]
                        repeat_num = math.ceil(grid_point_count_avg / point_indices_in_block.shape[0])
                        if repeat_num > 1:
                            point_indices_in_block = np.repeat(point_indices_in_block, repeat_num)
                            np.random.shuffle(point_indices_in_block)
                            point_indices_in_block = point_indices_in_block[:grid_point_count_avg]
                        point_indices_repeated.extend(list(point_indices[point_indices_in_block]))
                    block_point_indices[block_idx] = np.array(point_indices_repeated)
                    block_point_counts[block_idx] = len(point_indices_repeated)
                for block_idx in range(idx_last_non_empty_block + 1):
                    point_indices = block_point_indices[block_idx]
                    if point_indices.shape[0] == 0:
                        continue

                    block_point_num = point_indices.shape[0]
                    block_split_num = int(math.ceil(block_point_num * 1.0 / max_point_num))
                    point_num_avg = int(math.ceil(block_point_num * 1.0 / block_split_num))
                    point_nums = [point_num_avg] * block_split_num
                    point_nums[-1] = block_point_num - (point_num_avg * (block_split_num - 1))
                    starts = [0] + list(np.cumsum(point_nums))

                    np.random.shuffle(point_indices)
                    block_points = xyz[point_indices]
                    block_min = np.amin(block_points, axis=0, keepdims=True)
                    block_max = np.amax(block_points, axis=0, keepdims=True)
                    block_center = (block_min + block_max) / 2
                    block_center[0][-1] = block_min[0][-1]
                    unnormalized_block_points = block_points.copy()
                    block_points = block_points - block_center  # align to block bottom center
                    x, y, z = np.split(block_points, (1, 2), axis=-1)
        
                    block_xzyrgbi = np.concatenate([x, z, y] + [i[point_indices][:, None] for i in other_features.transpose(1,0)], axis=-1) #XYZ, Intensity, NumReturns, RGB
                    block_labels = labels[point_indices]

                    ## unormalized points
                    x_u, y_u, z_u = np.split(unnormalized_block_points, (1, 2), axis=-1)
                    unnormalized_block_xzyrgbi = np.concatenate([x_u, z_u, y_u] + [i[point_indices][:, None] for i in other_features.transpose(1,0)], axis=-1)


                    for block_split_idx in range(block_split_num):
                        start = starts[block_split_idx]
                        point_num = point_nums[block_split_idx]
                        end = start + point_num
                        idx_in_batch = idx % batch_size
                        data[idx_in_batch, 0:point_num, ...] = block_xzyrgbi[start:end, :]
                        unnormalized_data[idx_in_batch, 0:point_num, ...] = unnormalized_block_xzyrgbi[start:end, :]
                        data_num[idx_in_batch] = point_num
                        label[idx_in_batch] = dataset_idx  # won't be used...
                        label_seg[idx_in_batch, 0:point_num] = block_labels[start:end]
                        indices_split_to_full[idx_in_batch, 0:point_num] = point_indices[start:end]

                        if ((idx + 1) % batch_size == 0) or \
                                (block_idx == idx_last_non_empty_block and block_split_idx == block_split_num - 1):
                            item_num = idx_in_batch + 1
                            filename_h5 = os.path.join(output_path, Path(folder).stem, Path(dataset).stem + '_%s_%d.h5' % (offset_name, idx_h5))

                            file = h5py.File(filename_h5, 'w')
                            file.create_dataset('unnormalized_data', data=unnormalized_data[0:item_num, ...])
                            file.create_dataset('data', data=data[0:item_num, ...])
                            file.create_dataset('data_num', data=data_num[0:item_num, ...])
                            file.create_dataset('label', data=label[0:item_num, ...])
                            file.create_dataset('label_seg', data=label_seg[0:item_num, ...])
                            file.create_dataset('indices_split_to_full', data=indices_split_to_full[0:item_num, ...])
                            file.create_dataset('block_center', data=block_center)
                            file.close()
                            idx_h5 = idx_h5 + 1
                        idx = idx + 1
    if segregate:
        ## Segregate data
        output_path = Path(output_path)
        path_convert = output_path
        
        GROUND_CLASS = 0
        mb = master_bar(range(len(folders)))
        meta_file = {}
        meta_file['files'] = {}
        all_classes = set()
        for itn in mb:
            folder = folders[itn]
            path = output_path / Path(folder).stem
            total = 0
            # mb.write(f'{itn + 1}. Segregating {Path(folder).stem} folder')
            all_files = list(path.glob('*.h5'))
            file_id = 0
            for idx, fn in enumerate(progress_bar(all_files, parent=mb)):
                file = h5py.File(fn, 'r')
                data = file['data']
                total += data.shape[0]
                label_seg = file['label_seg']
                data_num = file['data_num']
                unnormalized_data = file['unnormalized_data']
                block_center = file['block_center'][:]
                file_idxs = []
                for i in range(file['data_num'][:].shape[0]):
                    if not ((label_seg[i] != GROUND_CLASS).sum().tolist() == 0):
                        save_file = path_convert / Path(folder).stem /  (fn.stem + f'_{i}' + '.h5')
                        new_file = h5py.File(save_file, mode='w')
                        new_file.create_dataset('unnormalized_data', data=unnormalized_data[i])
                        new_file.create_dataset('data', data=data[i])
                        new_file.create_dataset('label_seg', data=label_seg[i])
                        new_file.create_dataset('data_num', data=data_num[i])
                        new_file.close()
                        all_classes = all_classes.union(np.unique(label_seg[i][:data_num[i]]).tolist())
                        file_idxs.append(i)
                meta_file['files'][os.path.join(*fn.parts[-2:])] = {'idxs':file_idxs,
                                                'block_center':block_center.tolist()}
                file.close()
                os.remove(fn)
        meta_file['num_classes'] = len(all_classes)
        meta_file['classes'] = list(all_classes)
        meta_file['max_point'] = max_point_num
        meta_file['num_extra_dim'] = len(extra_features)
        meta_file['extra_features'] = extra_features
        meta_file['block_size'] = block_size
        with open(output_path / 'meta.json', 'w') as f:
            json.dump(meta_file, f)

    if kwargs.get('print_it', True):
        print('Export finished.')

    return output_path

## Segregated data ItemList

def open_h5py_tensor(fn, keys=['data']):
    try_import("h5py")
    import h5py
    data_label = []
    file = h5py.File(fn, 'r')
    for key in keys:
        tensor = torch.tensor(file[key][...]).float()
        data_label.append(tensor)

    file.close()    
    return data_label ## While getting a specific index from the file
    
## It also stores the label so that we don't have to open the file twice.
class DataStore():
    pass
    
class PointCloudItemList(ItemList):
    def __init__(self, items, **kwargs):
        super().__init__(items, **kwargs)
        
        self.keys = ['data', 'label_seg', 'data_num']
        
    def get(self, i):
        data = self.open(self.items[i])
        DataStore.i = i
        DataStore.data = data
        return (data[0], data[2])
        
    def open(self, fn):
        return open_h5py_tensor(fn, keys=self.keys)

def remap_labels(labels, class_mapping):
    if isinstance(labels, torch.Tensor):
        remapped_label = torch.zeros_like(labels)
    else:
        remapped_label = np.zeros_like(labels)
    for k,v in class_mapping.items():
        remapped_label[labels == k] = v
    return remapped_label
    
class PointCloudLabelList(ItemList):
    def __init__(self, items, remap=False, class_mapping={}, **kwargs):
        super().__init__(items, **kwargs)
        self.key = 'label_seg'
        self.remap = remap
        self.class_mapping = class_mapping
        
    def get(self, i):
        if self.remap:
            # import pdb; pdb.set_trace();
            return remap_labels(DataStore.data[1].long(), self.class_mapping)
        else:
            return DataStore.data[1].long()
    
    def analyze_pred(self, pred):
        return pred.argmax(dim=1)
        
PointCloudItemList._label_cls = PointCloudLabelList

## Prepare data called in _data.py

def pointcloud_prepare_data(path, class_mapping, batch_size, val_split_pct, dataset_type='PointCloud', transform_fn=None, **kwargs):
    try_imports(['h5py', 'plotly', 'laspy', 'transforms3d'])
    databunch_kwargs = {'num_workers':0} if sys.platform == 'win32' else {}
    if (path / 'Statistics.json').exists():
        dataset_type = "PointCloud"
    elif (path / 'meta.json').exists():
        dataset_type = "PointCloud_TF"
    else:
        dataset_type = "Unknown"

    if dataset_type == 'PointCloud':
        with open(path / 'Statistics.json') as f:
            json_file = json.load(f)

        max_points = json_file['parameters']['numberOfPointsInEachTile']

        ## It is assumed that the pointcloud will have only X,Y & Z.
        extra_dim = sum([len(v['max']) if isinstance(v['max'], list) else 1 for k,v in json_file['features'].items()]) - 3
        pointcloud_dataset = PointCloudDataset(path, max_points, extra_dim, class_mapping, **kwargs)

        # Splitting in train and test based on files.
        total_files = len(pointcloud_dataset.filenames)
        total_files_idxs = list(range(total_files))
        random.shuffle(total_files_idxs)
        total_val_files = int(val_split_pct * total_files)
        val_files = total_files_idxs[-total_val_files:]
        if total_val_files == 0:
            raise Exception("No files could be added to validation dataset. Please increase the value of `val_split_pct`")
        tile_file_indices = pointcloud_dataset.tiles[:, 0]
        val_indices = torch.from_numpy(np.isin(tile_file_indices, val_files)).nonzero()
        train_indices = torch.from_numpy(np.logical_not(np.isin(tile_file_indices, val_files))).nonzero()
        train_sampler = SubsetRandomSampler(train_indices)
        val_sampler = SubsetRandomSampler(val_indices)
        train_dl = DataLoader(pointcloud_dataset, batch_size=batch_size, sampler=train_sampler, **databunch_kwargs)
        valid_dl = DataLoader(pointcloud_dataset, batch_size=batch_size, sampler=val_sampler, **databunch_kwargs)
        device = get_device()
        data = DataBunch(train_dl, valid_dl, device=device)
        data.show_batch = types.MethodType(show_point_cloud_batch, data)
        data.path = data.train_ds.path
        data.val_files = val_files

    elif dataset_type == 'PointCloud_TF':
        with open(Path(path) / 'meta.json', 'r') as f:
            meta = json.load(f)
        classes = meta['classes']
        remap = remap_classes(classes)
        class_mapping = class_mapping if class_mapping is not None else {v:k for k,v in enumerate(classes)}
        src = PointCloudItemList.from_folder(path, ['.h5'])
        train_idxs = [i for i,p in enumerate(src.items) if p.parent.name == 'train']
        val_idxs = [i for i,p in enumerate(src.items) if p.parent.name == 'val']
        src = src.split_by_idxs(train_idxs, val_idxs)\
            .label_from_func(lambda x: x, remap=remap, class_mapping=class_mapping)
        data = src.databunch(bs=batch_size, **databunch_kwargs)
        data.meta = meta
        data.remap = remap
        data.classes =  classes
        data.c = data.meta['num_classes']
        data.show_batch = types.MethodType(show_point_cloud_batch_TF, data)
        data.color_mapping = kwargs.get('color_mapping', {k:[random.choice(range(256)) for _ in range(3)]  for k,_ in data.class_mapping.items()})
        data.color_mapping = {int(k):v for k, v in data.color_mapping.items()}
        data.color_mapping = recompute_color_mapping(data.color_mapping, data.classes)
        data.class_mapping = class_mapping
        data.max_point = data.meta['max_point']
        data.extra_dim = data.meta['num_extra_dim']
        data.extra_features = data.meta['extra_features']
        data.block_size = data.meta['block_size']
        ## To accomodate save function to save in correct directory
        data.path = data.path / 'train' 
    else:
        raise Exception("Could not infer dataset type.")

    data.pc_type = dataset_type
    data.path = data.train_ds.path
    ## Below are the lines to make save function work
    data.chip_size = None
    data._image_space_used = None
    data.dataset_type = dataset_type
    data.transform_fn = transform_fn
    return data

def read_xyz_label_from_las(filename_las):
    try_import('laspy')
    import laspy
    msg = 'Loading {}...'.format(filename_las)
    f = laspy.file.File(filename_las, mode='r')    
    h = f.header
    xyzirgb_num = h.point_records_count
    xyz_offset = h.offset
    encoding = h.encoding
    xyz = np.ndarray((xyzirgb_num, 3))
    labels = np.ndarray(xyzirgb_num, np.int16)
    i = 0
    for p in f:
        xyz[i] = [p.x, p.y, p.z]
        labels[i] = p.classification
        i += 1
    return xyz, labels, xyzirgb_num, xyz_offset, encoding

def save_xyz_label_to_las(filename_las, xyz, xyz_offset, encoding, labels):  
    try_import('laspy')
    import laspy
    msg = 'Saving {}...'.format(filename_las)
    h = laspy.header.Header()
    h.dataformat_id = 1
    h.major = 1
    h.minor = 2
    h.min = np.min(xyz, axis=0)
    h.max = np.max(xyz, axis=0)
    h.scale = [1e-3, 1e-3, 1e-3]
    h.offset = xyz_offset
    h.encoding = encoding
    
    f = laspy.file.File(filename_las, mode='w', header=h)    
    for i in range(xyz.shape[0]):
        p = laspy.point.Point()
        p.x = xyz[i,0] / h.scale[0]
        p.y = xyz[i,1] / h.scale[1]
        p.z = xyz[i,2] / h.scale[2]
        p.classification = labels[i]
        p.color = laspy.color.Color()
        p.intensity = 100
        p.return_number = 1
        p.number_of_returns = 1
        p.scan_direction = 1
        p.scan_angle = 0
        f.write(p)
        
    f.close()

def prediction_remap_classes(labels, reclassify_classes, inverse_class_mapping):
    if reclassify_classes == {}:
        return labels
    else:
        labels = np.vectorize(inverse_class_mapping.get)(labels)
        labels = np.vectorize(reclassify_classes.get)(labels)
        return labels

def prediction_selective_classify(labels, las_file, selective_classify):
    all_indexes = list(range(len(labels)))
    classification = las_file.classification
    return np.vectorize(lambda i:labels[i] if labels[i] in selective_classify\
                                        else classification[i])(all_indexes)

def write_resulting_las(in_las_filename, 
                        out_las_filename, 
                        labels, 
                        num_classes, 
                        data,
                        print_metrics,
                        reclassify_classes={}, 
                        selective_classify=[]):
    try_import('laspy')
    import laspy
    false_positives = [0] * num_classes
    true_positives = [0] * num_classes
    false_negatives = [0] * num_classes
    inverse_class_mapping = {v:k for k,v in data.class_mapping.items()}
    shutil.copy(in_las_filename, out_las_filename)
    f = laspy.file.File(in_las_filename, mode='r')    
    f_out = laspy.file.File(out_las_filename, mode='rw')
    i = 0
    classification = []
    warn_flag = False
    
    ## remap classes
    old_labels = labels.copy()
    labels = prediction_remap_classes(labels, reclassify_classes, inverse_class_mapping)

    if print_metrics:
        for p in f:
            p = f[i]
            current_class = inverse_class_mapping[old_labels[i]] 
            if reclassify_classes != {}:
                current_class = reclassify_classes[current_class]       
            try:
                false_positives[old_labels[i]] += int(p.classification != current_class)
                true_positives[old_labels[i]] += int(p.classification == current_class)
                false_negatives[data.class_mapping[p.classification]] += int(p.classification != current_class)
            except (IndexError, KeyError) as _:
                warn_flag = True

            i += 1

    if selective_classify != []:
        #current_class if current_class in selective_classify else p.classification
        labels = prediction_selective_classify(labels, f, selective_classify)
            
    f.close()
    f_out.classification = labels.tolist()
    f_out.close()

    # if print_metrics and warn_flag:
    #     logger.warning(f"Some classes in your las file {in_las_filename} do not match the classes the model is trained on")
    #     print_metrics = False
    return false_positives, true_positives, false_negatives

def calculate_metrics(false_positives, true_positives, false_negatives):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        precision = np.divide(true_positives, np.add(true_positives, false_positives))
        recall = np.divide(true_positives, np.add(true_positives, false_negatives))
        f_1 = np.multiply(2.0, np.divide(np.multiply(precision, recall), np.add(precision, recall)))
    return precision, recall, f_1

def get_pred_prefixes(datafolder):
    fs = os.listdir(datafolder)
    preds = []
    for f in fs:
        if f[-8:] == '_pred.h5':
            preds += [f]
    pred_pfx = []
    for p in preds:
        to_check = "_half" #"_zero"
        if to_check in p: 
            pred_pfx += [p.split(to_check)[0]]
    return np.unique(pred_pfx)

def get_predictions(pointcnn_model, data, batch_idx, points_batch, sample_num, batch_size, point_num):
    ## Getting sampling indices
    tile_num = math.ceil((sample_num * batch_size) / point_num)
    indices_shuffle = np.tile(np.arange(point_num), tile_num)[0:sample_num * batch_size]
    np.random.shuffle(indices_shuffle)
    indices_batch_shuffle = np.reshape(indices_shuffle, (batch_size, sample_num, 1))

    model_input = np.concatenate([points_batch[i, s[:, 0]][None] for i, s in enumerate(indices_batch_shuffle)], axis=0)
    
    ## Putting model in evaluation mode and inferencing.
    pointcnn_model.learn.model.eval()
    with torch.no_grad():
        probs = pointcnn_model.learn.model(torch.tensor(model_input).to(pointcnn_model._device).float()).softmax(dim=-1).cpu()
        
    seg_probs = probs.numpy()
    
    probs_2d = np.reshape(seg_probs, (sample_num * batch_size, -1))  ## Complete probs
    predictions = [(-1, 0.0)] * point_num  ## predictions
    
    ## Assigning the confidences and labels to the appropriate index.
    for idx in range(sample_num * batch_size):
        point_idx = indices_shuffle[idx]
        probs = probs_2d[idx, :]
        confidence = np.amax(probs)
        label = np.argmax(probs)
        if confidence > predictions[point_idx][1]:
            predictions[point_idx] = [label, confidence]
        
    return predictions

def inference_las(path, pointcnn_model, out_path=None, print_metrics=False, remap_classes={}, selective_classify=[]):
    try_import("h5py")
    import h5py
    import pandas as pd    
    ## Export data
    path = Path(path)

    if len(list(path.glob('*.las'))) == 0:
        raise Exception(f"The given path({path}) contains no las files.")

    if out_path is None:
        out_path = path / 'results'
    else:    
        out_path = Path(out_path)

    reclassify_classes = remap_classes
    if reclassify_classes != {}:
        if not all([k in pointcnn_model._data.classes for k in reclassify_classes.keys()]):
            raise Exception(f"`remap_classes` dictionary keys are not present in dataset with classes {pointcnn_model._data.classes}.")
        reclassify_classes = {k:reclassify_classes.get(k, k) for k in pointcnn_model._data.class_mapping}

    if selective_classify != []:
        if reclassify_classes != {}:
            values_to_check = np.unique(np.array(list(reclassify_classes.values()))).tolist()
        else:
            values_to_check = list(pointcnn_model._data.classes)

        if not all([k in values_to_check for k in selective_classify]):
            raise Exception(f"`selective_classify` can only contain values from these class values {values_to_check}.")

    prepare_las_data(path.parent,
                     block_size=pointcnn_model._data.block_size[0],
                     max_point_num=pointcnn_model._data.max_point,
                     output_path=path.parent,
                     extra_features=pointcnn_model._data.extra_features,
                     folder_names=[path.stem],
                     segregate=False,
                     print_it=False
    )
    ## Predict and postprocess
    max_point_num = pointcnn_model._data.max_point
    sample_num = pointcnn_model.sample_point_num
    batch_size = 1 * math.ceil(max_point_num / sample_num) 
    filenames = list(glob.glob(str(path/ "*.h5")))

    mb = master_bar(range(len(filenames)))
    for itn in mb:  
        filename = filenames[itn]
        data_h5 = h5py.File(filename, 'r')
        data = data_h5['data'][...].astype(np.float32)  
        data_num =  data_h5['data_num'][...].astype(np.int32)
        batch_num = data.shape[0]
        labels_pred = np.full((batch_num, max_point_num), -1, dtype=np.int32)
        confidences_pred = np.zeros((batch_num, max_point_num), dtype=np.float32)


        for batch_idx in progress_bar(range(batch_num), parent=mb): 
            points_batch = data[[batch_idx] * batch_size, ...]
            point_num = data_num[batch_idx]
            predictions = get_predictions(pointcnn_model, data, batch_idx, points_batch, sample_num, batch_size, point_num)      
            labels_pred[batch_idx, 0:point_num] = np.array([label for label, _ in predictions])
            confidences_pred[batch_idx, 0:point_num] = np.array([confidence for _, confidence in predictions])

        ## Saving h5 predictions file
        if not os.path.exists(out_path):
            os.makedirs(out_path)
        filename_pred = os.path.join(out_path , Path(filename).stem + '_pred.h5')
        file = h5py.File(filename_pred, 'w')
        file.create_dataset('data_num', data=data_num)
        file.create_dataset('label_seg', data=labels_pred)
        file.create_dataset('confidence', data=confidences_pred)
        has_indices = 'indices_split_to_full' in data_h5
        if has_indices:
            file.create_dataset('indices_split_to_full', data=data_h5['indices_split_to_full'][...])
        file.close()
        data_h5.close()


    ## Merge H5 files and write las files
    SAVE_TO_EXT = '.las'
    LOAD_FROM_EXT = '.las'


    categories_list = get_pred_prefixes(out_path)

    global_false_positives = [0] * pointcnn_model._data.c
    global_true_positives = [0] * pointcnn_model._data.c
    global_false_negatives = [0] * pointcnn_model._data.c

    for category in categories_list:
        output_path = os.path.join(out_path ,category + "_pred" + SAVE_TO_EXT)
        if not os.path.exists(os.path.join(out_path)):
            os.makedirs(os.path.join(out_path))
        pred_list = [pred for pred in os.listdir(out_path)
                    if category in pred and pred.split(".")[0].split("_")[-1] == 'pred' and pred[-3:] != 'las']

        merged_label = None
        merged_confidence = None

        for pred_file in pred_list:
            data = h5py.File(os.path.join(out_path, pred_file), mode='r')
            labels_seg = data['label_seg'][...].astype(np.int64)
            indices = data['indices_split_to_full'][...].astype(np.int64)
            confidence = data['confidence'][...].astype(np.float32)
            data_num = data['data_num'][...].astype(np.int64)

            if merged_label is None:
                # calculating how many labels need to be there in the output
                label_length = 0
                for i in range(indices.shape[0]):
                    label_length = np.max([label_length, np.max(indices[i][:data_num[i]])])
                label_length += 1
                merged_label = np.zeros((label_length), dtype=int)
                merged_confidence = np.zeros((label_length), dtype=float)
            else:
                label_length2 = 0
                for i in range(indices.shape[0]):
                    label_length2 = np.max([label_length2, np.max(indices[i][:data_num[i]])])
                label_length2 += 1
                if label_length < label_length2:
                    # expanding labels and confidence arrays, as the new file appears having more of them
                    labels_more = np.zeros((label_length2 - label_length), dtype=merged_label.dtype)
                    conf_more = np.zeros((label_length2 - label_length), dtype=merged_confidence.dtype)
                    merged_label = np.append(merged_label, labels_more)
                    merged_confidence = np.append(merged_confidence, conf_more)
                    label_length = label_length2
            
            for i in range(labels_seg.shape[0]):
                temp_label = np.zeros((data_num[i]),dtype=int)
                pred_confidence = confidence[i][:data_num[i]]
                temp_confidence = merged_confidence[indices[i][:data_num[i]]]

                temp_label[temp_confidence >= pred_confidence] = merged_label[indices[i][:data_num[i]]][temp_confidence >= pred_confidence]
                temp_label[pred_confidence > temp_confidence] = labels_seg[i][:data_num[i]][pred_confidence > temp_confidence]

                merged_confidence[indices[i][:data_num[i]][pred_confidence > temp_confidence]] = pred_confidence[pred_confidence > temp_confidence]
                merged_label[indices[i][:data_num[i]]] = temp_label

            data.close()

        if len(pred_list) > 0:
            # concatenating source points with the final labels and writing out resulting file
            points_path = os.path.join(path, category + LOAD_FROM_EXT)
            
            false_positives, true_positives, false_negatives = write_resulting_las(points_path,
                                                                                   output_path,
                                                                                   merged_label,
                                                                                   pointcnn_model._data.c,
                                                                                   pointcnn_model._data,
                                                                                   print_metrics,
                                                                                   reclassify_classes,
                                                                                   selective_classify)
            global_false_positives = np.add(global_false_positives, false_positives)
            global_true_positives = np.add(global_true_positives, true_positives)
            global_false_negatives = np.add(global_false_negatives, false_negatives)

    if print_metrics:
        index = ['precision', 'recall', 'f1_score']
        inverse_class_mapping = {v:k for k,v in pointcnn_model._data.class_mapping.items()}
        unique_mapped_classes = np.unique(np.array(list(reclassify_classes.values())))
        if len(unique_mapped_classes) == len(pointcnn_model._data.classes) or remap_classes == {}:
            precision, recall, f_1 = calculate_metrics(global_false_positives, global_true_positives, global_false_negatives)
            data = [precision, recall, f_1]
            column_names = [inverse_class_mapping[cval] for cval in range(pointcnn_model._data.c)]
            if reclassify_classes != {}:
                remapping_class_mapping = {v:reclassify_classes[k] for k,v in pointcnn_model._data.class_mapping.items()}
                column_names = [remapping_class_mapping[cval] for cval in range(pointcnn_model._data.c)]
            df = pd.DataFrame(data, columns=column_names, index=index)
        else:
            inverse_reclassify_classes = {}
            for k, v in reclassify_classes.items():
                current_value = inverse_reclassify_classes.get(v, [])
                current_value.append(k)
                inverse_reclassify_classes[v] = current_value   
            map_dict = {u: [pointcnn_model._data.class_mapping[k] for k in inverse_reclassify_classes[u]] for u in unique_mapped_classes}
            global_false_positives =  recompute_globals(global_false_positives, map_dict)
            global_true_positives = recompute_globals(global_true_positives, map_dict)
            global_false_negatives = recompute_globals(global_false_negatives, map_dict)
            precision, recall, f_1 = calculate_metrics(global_false_positives, global_true_positives, global_false_negatives)
            data = [precision, recall, f_1]
            column_names = list(map_dict.keys())
            df = pd.DataFrame(data, columns=column_names, index=index)            

        from IPython.display import display
        display(df)

    for fn in glob.glob(str(path / '*.h5'), recursive=True): ## Remove h5 files in val directory.
        os.remove(fn) 

    for fn in glob.glob(str(out_path / '*.h5'), recursive=True):  ## Remove h5 files in results directory.
        os.remove(fn)        

    return out_path

def recompute_globals(global_count, map_dict):
    return [sum([global_count[ci] for ci in v]) for k,v in map_dict.items()]

def raise_maxpoint_warning(idx_file, kwargs, logger, max_display_point, save_html=False):
    if not save_html:
        if idx_file == 0:
            if 'max_display_point' not in kwargs.keys():
                logger.warning(f"Randomly sampling {max_display_point} points for visualization. You can adjust this using the `max_display_point` parameter.")

def get_title_text(idx, save_html, max_display_point):
    title_text = 'Ground Truth / Predictions' if idx==0 else ''
    if save_html:
        title_text = f'Ground Truth / Predictions (Displaying randomly sampled {max_display_point} points.)' if idx==0 else ''
    return title_text

def inverse_remap_predictions(predictions, class_mapping):
    if isinstance(predictions, torch.Tensor):
        remapped_predictions = torch.zeros_like(predictions)
    else:
        remapped_predictions = np.zeros_like(predictions).astype(int)
    for k,v in class_mapping.items():
        remapped_predictions[predictions == v] = k
    return remapped_predictions    

def show_results(self, rows, color_mapping=None, **kwargs):

    """
    It will plot results from your trained model with ground truth on the
    left and predictions on the right.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    rows                    Optional rows. Number of rows to show. Deafults
                            value is 2.
    ---------------------   -------------------------------------------
    color_mapping           Optional dictionary. Mapping from class value
                            to RGB values. Default value
                            Example: {0:[220,220,220],
                                        1:[255,0,0],
                                        2:[0,255,0],
                                        3:[0,0,255]}                                                         
    =====================   ===========================================

    **kwargs**

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    mask_class              Optional array of integers. Array containing
                            class values to mask. Default value is [].    
    ---------------------   -------------------------------------------
    width                   Optional integer. Width of the plot. Default 
                            value is 750.
    ---------------------   -------------------------------------------
    height                  Optional integer. Height of the plot. Default
                            value is 512
    ---------------------   -------------------------------------------
    max_display_point       Optional integer. Maximum number of points
                            to display. Default is 20000.                               
    =====================   ===========================================
    """
    
    filter_outliers = False
    try_import("h5py")
    try_import('plotly')
    import h5py    
    import plotly
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots    
    import random
    mask_class = kwargs.get('mask_class', [])    
    save_html = kwargs.get('save_html', False)
    save_path = kwargs.get('save_path', '.')
    max_display_point = get_max_display_points(self._data, kwargs)
    rows = min(rows, self._data.batch_size)
    color_mapping = self._data.color_mapping if color_mapping is None else color_mapping
    color_mapping = recompute_color_mapping(color_mapping, self._data.classes)       
    color_mapping = np.array(list(color_mapping.values())) / 255

    idx = 0
    keys = list(self._data.meta['files'].keys()).copy()
    keys = [f for f in keys if Path(f).parent.stem == 'val']
    random.shuffle(keys)

    for idx_file, fn in enumerate(keys):        
        fig = make_subplots(rows=1, cols=2, specs=[[{'type': 'scene'}, {'type': 'scene'}]])        
        num_files = self._data.meta['files'][fn]['idxs']
        block_center = self._data.meta['files'][fn]['block_center']
        block_center = np.array(block_center)
        block_center[0][2], block_center[0][1] = block_center[0][1], block_center[0][2]
        if num_files == []:
            continue
        if not Path(fn).is_absolute():
            fn = str(self._data.path / fn)            
        idxs = [h5py.File(fn[:-3] + f'_{i}.h5', 'r') for i in num_files]
        pc = []
        labels = []
        pred_class = []
        pred_confidence = []
        for i in idxs:
            # print(f'Running Show Results: Processing {nn+1} of {len(idxs)} blocks.', end='\r')
            current_block = i['unnormalized_data'][:, :3]
            data_num = i['data_num'][()] 
            data = i['data'][:] 
            pc.append(current_block[:data_num])
            labels.append(i['label_seg'][:data_num])

            max_point_num = self._data.max_point
            sample_num = self.sample_point_num
            batch_size = 1 * math.ceil(max_point_num / sample_num) 
            data = data[None]
            batch_idx = 0
            points_batch = data[[batch_idx] * batch_size, ...]
            point_num = data_num
            predictions = np.array(get_predictions(self, data, batch_idx, points_batch, sample_num, batch_size, point_num))
            pred_class.append(predictions[:, 0])
            pred_confidence.append(predictions[:, 1])
            
        if pc == []:
            continue         
                
        pc = np.concatenate(pc, axis=0)
        labels = np.concatenate(labels, axis=0)
        unmapped_labels = labels.copy().astype(int)
        if self._data.remap:
            labels = remap_labels(labels, self._data.class_mapping)
        pred_class = np.concatenate(pred_class, axis=0).astype(int)
        unmapped_pred_class = inverse_remap_predictions(pred_class, self._data.class_mapping)
        sample_idxs = mask_classes(labels, mask_class, self._data.class_mapping if self._data.remap else None)
        sampled_pc = pc[sample_idxs]
        if sampled_pc.shape[0] == 0:
            continue
        x, y, z = recenter(sampled_pc).transpose(1,0)       
        if filter_outliers:
            ## Filter on the basis of std.
            mask = filter_pc(pc)
        else:
            ## all points
            mask = x != None

        if sample_idxs.sum() > max_display_point:
            raise_maxpoint_warning(idx_file, kwargs, logger, max_display_point, save_html)
            mask = np.random.randint(0, sample_idxs.sum(), max_display_point)              
        
        color_list_true =  color_mapping[labels[sample_idxs]][mask].tolist()
        color_list_pred = color_mapping[pred_class[sample_idxs]][mask].tolist()
        
        scene=dict(aspectmode='data')


        fig.add_trace(go.Scatter3d(x=x[mask], y=z[mask], z=y[mask], 
                                        mode='markers', marker=dict(size=1, color=color_list_true),
                                        text=class_string(unmapped_labels[sample_idxs][mask])), row=1, col=1)

        fig.add_trace(go.Scatter3d(x=x[mask], y=z[mask], z=y[mask], 
                                        mode='markers', marker=dict(size=1, color=color_list_pred),
                                        text=class_string(unmapped_pred_class[sample_idxs][mask], prefix='pred_')), row=1, col=2)

        title_text = get_title_text(idx, save_html, max_display_point)

        fig.update_layout(
            scene=scene,
            scene2=scene,
            title_text=title_text,
            width=kwargs.get('width', 750),
            height=kwargs.get('width', 512),
            showlegend=False,
            title_x=0.5
        )

        if save_html:
            save_path = Path(save_path)
            plotly.io.write_html(fig, str(save_path / 'show_results.html'))
            fig.write_image(str(save_path / 'show_results.png'))
            return
        else:
            fig.show()

        if idx == rows-1:
            break
        idx += 1

def compute_precision_recall(self):
    from ..models._pointcnn_utils import get_indices
    import pandas as pd 

    valid_dl = self._data.valid_dl
    model = self.learn.model.eval()

    false_positives = [0] * self._data.c
    true_positives = [0] * self._data.c
    false_negatives = [0] * self._data.c
    class_count = [0] * self._data.c

    all_y = []
    all_pred = []
    for x_in, y_in in iter(valid_dl):
        x_in, point_nums = x_in   ## (batch, total_points, num_features), (batch,)
        batch, _, num_features = x_in.shape
        indices = torch.tensor(get_indices(batch, self.sample_point_num, point_nums.long())).to(x_in.device)
        indices = indices.view(-1, 2).long()
        x_in = x_in[indices[:, 0], indices[:, 1]].view(batch, self.sample_point_num, num_features).contiguous()  ## batch, self.sample_point_num, num_features                
        y_in = y_in[indices[:, 0], indices[:, 1]].view(batch, self.sample_point_num).contiguous().cpu().numpy() ## batch, self.sample_point_num        
        with torch.no_grad():
            preds = model(x_in).detach().cpu().numpy()
        predicted_labels = preds.argmax(axis=-1)
        all_y.append(y_in.reshape(-1))
        all_pred.append(predicted_labels.reshape(-1))

    all_y = np.concatenate(all_y)
    all_pred = np.concatenate(all_pred)
    
    for i in range(len(all_y)): 
        class_count[all_y[i]] += 1       
        false_positives[all_pred[i]] += int(all_y[i] != all_pred[i])
        true_positives[all_pred[i]] += int(all_y[i] == all_pred[i])
        false_negatives[all_y[i]] += int(all_y[i] != all_pred[i])        
    
    
    precision, recall, f_1 = calculate_metrics(false_positives, true_positives, false_negatives)
    data = [precision, recall, f_1]
    index = ['precision', 'recall', 'f1_score']
    inverse_class_mapping = {v:k for k,v in self._data.class_mapping.items()} 
    df = pd.DataFrame(data, columns=[inverse_class_mapping[cval] for cval in range(self._data.c)], index=index) 
    return df

def gauss_clip(mu, sigma, clip):
    v = random.gauss(mu, sigma)
    v = max(min(v, mu + clip * sigma), mu - clip * sigma)
    return v


def uniform(bound):
    return bound * (2 * random.random() - 1)


def scaling_factor(scaling_param, method):
    try:
        scaling_list = list(scaling_param)
        return random.choice(scaling_list)
    except:
        if method == 'g':
            return gauss_clip(1.0, scaling_param, 3)
        elif method == 'u':
            return 1.0 + uniform(scaling_param)


def rotation_angle(rotation_param, method):
    try:
        rotation_list = list(rotation_param)
        return random.choice(rotation_list)
    except:
        if method == 'g':
            return gauss_clip(0.0, rotation_param, 3)
        elif method == 'u':
            return uniform(rotation_param)


def get_xforms(xform_num, rotation_range=(0, 0, 0, 'u'), scaling_range=(0.0, 0.0, 0.0, 'u'), order='rxyz'):
    xforms = np.empty(shape=(xform_num, 3, 3))
    rotations = np.empty(shape=(xform_num, 3, 3))
    for i in range(xform_num):
        rx = rotation_angle(rotation_range[0], rotation_range[3])
        ry = rotation_angle(rotation_range[1], rotation_range[3])
        rz = rotation_angle(rotation_range[2], rotation_range[3])
        rotation = euler2mat(rx, ry, rz, order)

        sx = scaling_factor(scaling_range[0], scaling_range[3])
        sy = scaling_factor(scaling_range[1], scaling_range[3])
        sz = scaling_factor(scaling_range[2], scaling_range[3])
        scaling = np.diag([sx, sy, sz])

        xforms[i, :] = scaling * rotation
        rotations[i, :] = rotation
    return xforms, rotations

def augment(points, xforms, range=None):
    points_xformed = points@xforms
    if range is None:
        return points_xformed
    
    jitter_data = range * points.new(np.random.randn(*points_xformed.shape))
    jitter_clipped = torch.clamp(jitter_data, -5 * range, 5 * range)
    return points_xformed + jitter_clipped

class Transform3d(object):

    """
    Creates a Transform3d object which, when passed in prepare data will
    apply data augmentation to the PointCloud data.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    rotation_range          Optional tuple of length 4. It contains a list
                            of angles(in radians) for X, Z and Y coordinates
                            respectively. These angles will rotate the point
                            cloud block according to the randomly selected angle.
                            The fourth value in the tuple is the sampling method
                            where 'u' means uniform and 'g' means gaussian.
                            Deafult: [math.pi / 72, math.pi, math.pi / 72, 'u']
    ---------------------   -------------------------------------------
    scaling_range           Optional tuple of length 4. It contains a list
                            of scaling ranges[0-1] which will scale the points.
                            Please keep it a very small number otherwise,
                            point cloud block may get distorted. The fourth
                            value in the tuple is the sampling method
                            where 'u' means uniform and 'g' means gaussian.
                            Default: [0.05, 0.05, 0.05, 'g'] 
    ---------------------   -------------------------------------------
    jitter                  Optional float. The scale to which randomly
                            jitter the points in the point cloud block.
                            Default: 0.0
    =====================   ===========================================
    
    :returns: `Transform3d` object
    """

    def __init__(self, rotation_range=[math.pi / 72, math.pi, math.pi / 72, 'u'],
                 scaling_range=[0.05, 0.05, 0.05, 'g'], 
                 jitter=0.):
        self.rotation_range = rotation_range
        self.scaling_range = scaling_range
        self.order = 'rxyz'
        self.jitter = jitter

    def __call__(self, x_in):
        xforms, _ = get_xforms(x_in.shape[0], rotation_range=self.rotation_range, scaling_range=self.scaling_range, order=self.order)
        return augment(x_in[:, :, :3], x_in.new(xforms), x_in.new(np.array(self.jitter)))

def save_h5(filename, labels_pred, confidences_pred):
    try_import('h5py')
    import h5py
    filename = Path(filename)
    if not filename.parent.exists():
        filename.parent.mkdir(parents=True, exist_ok=True)

    filename_pred = filename.parent / (filename.stem + '_pred.h5')
    file = h5py.File(filename_pred, 'w')
    file.create_dataset('label_seg', data=labels_pred)
    file.create_dataset('confidence', data=confidences_pred)
    file.close()


def predict_h5(self, path, output_path, print_metrics=False):
    """
    self: PointCNN object
    path: path/to/h5/files/exported/by/tool
    """
    path = Path(path)
    if output_path is None:
        output_path = path / 'results'
    else:
        output_path = Path(output_path)

    point_cloud_dataset = PointCloudDataset(path, max_point=self._data.max_point, extra_dim=self._data.extra_dim, class_mapping=self._data.class_mapping)
    batch_size = 1 * math.ceil(self._data.max_point / self.sample_point_num)   
    max_point_num = self._data.max_point

    folder_name = Path(point_cloud_dataset.relative_files[0].decode()).parts[0]
    name_txt_path = path / folder_name / 'Name.txt'
    (output_path / folder_name).mkdir(parents=True, exist_ok=True)
    shutil.copy(str(name_txt_path), str(output_path / folder_name))

    current_file_name = ''
    for i in progress_bar(range(len(point_cloud_dataset))):
        tile = point_cloud_dataset.tiles[i]
        h5_file = point_cloud_dataset.h5files[tile[0]]
        fname = point_cloud_dataset.filenames[tile[0]]

        if fname != current_file_name:
            low = 0
            if i!=0:
                save_h5(output_path / point_cloud_dataset.relative_files[int(tile[0] - 1)].decode(), labels_pred, confidences_pred)
            current_file_name = fname
            batch_num, _ = h5_file['xyz'].shape
            labels_pred = np.full(batch_num, -1, dtype=np.int8)
            confidences_pred = np.zeros(batch_num, dtype=np.float32)

        (data, point_num), classification = point_cloud_dataset[i]
        data = data[None]
        points_batch = data[[0] * batch_size]
        predictions = get_predictions(self, data, 0, points_batch, self.sample_point_num, batch_size, point_num)
        high = low + point_num
        labels_pred[low:high] = np.array([label for label, _ in predictions])
        confidences_pred[low:high] = np.array([confidence for _, confidence in predictions])
        low = high

        if i == (len(point_cloud_dataset) - 1):
            save_h5(output_path / point_cloud_dataset.relative_files[tile[0]].decode(), labels_pred, confidences_pred)

        if print_metrics:
            if classification is None and i == 0:
                logger.warning("classification codes are not present in the h5 file.")
            else:
                pass        


    return output_path

def calculate_per_class_stats(all_pred, all_y, total_classes):

    true_positives = [0] * total_classes 
    false_positives = [0] * total_classes
    false_negatives = [0] * total_classes
    class_count = [0] * total_classes

    for i in range(len(all_y)): 
        class_count[all_y[i]] += 1       
        false_positives[all_pred[i]] += int(all_y[i] != all_pred[i])
        true_positives[all_pred[i]] += int(all_y[i] == all_pred[i])
        false_negatives[all_y[i]] += int(all_y[i] != all_pred[i])       

    return true_positives, false_positives, false_negatives

def show_results_tool(self, rows, color_mapping=None, **kwargs):

    """
    It will plot results from your trained model with ground truth on the
    left and predictions on the right.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    rows                    Optional rows. Number of rows to show. Deafults
                            value is 2.
    ---------------------   -------------------------------------------
    color_mapping           Optional dictionary. Mapping from class value
                            to RGB values. Default value
                            Example: {0:[220,220,220],
                                        1:[255,0,0],
                                        2:[0,255,0],
                                        3:[0,0,255]}                                                         
    =====================   ===========================================

    **kwargs**

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    mask_class              Optional array of integers. Array containing
                            class values to mask. Default value is [0].    
    ---------------------   -------------------------------------------
    width                   Optional integer. Width of the plot. Default 
                            value is 750.
    ---------------------   -------------------------------------------
    height                  Optional integer. Height of the plot. Default
                            value is 512
    ---------------------   -------------------------------------------
    max_display_point       Optional integer. Maximum number of points
                            to display. Default is 20000.                               
    =====================   ===========================================
    """
    filter_outliers = False
    try_import("h5py")
    try_import('plotly')
    import h5py    
    import plotly
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots    
    import random
    mask_class = kwargs.get('mask_class', [])    
    save_html = kwargs.get('save_html', False)
    save_path = kwargs.get('save_path', '.')
    max_display_point = get_max_display_points(self._data, kwargs)
    data = self._data
    rows = min(rows, data.batch_size)
    color_mapping = data.color_mapping if color_mapping is None else color_mapping
    color_mapping = recompute_color_mapping(color_mapping, self._data.classes)       
    color_mapping = np.array(list(color_mapping.values())) / 255    

    ## dataset tiles Get all files from the tiles
    tile_file_indices = self._data.train_ds.tiles[:, 0]
    ## iterate: on files
    for idx, file_idx in enumerate(data.val_files):
        ## Create subplot
        fig = make_subplots(rows=1, cols=2, specs=[[{'type': 'scene'}, {'type': 'scene'}]])

        # read that file
        indices = (tile_file_indices == file_idx).nonzero()[0]  

        ## predict on each block by iterating.
        pred_batch_size = 1 * math.ceil(self._data.max_point / self.sample_point_num) 
        labels = []
        pc = []
        pred_class = []
        for block_idx in indices:
            (block, point_num), classification = data.train_ds[block_idx]
            block = block[None]
            points_batch = block[[0] * 1]
            predictions = np.array(get_predictions(self, block, 0, points_batch, self.sample_point_num, pred_batch_size, point_num))
            pred_class.append(predictions[:point_num, 0])
            pc.append(block[0, :point_num].cpu().numpy())
            labels.append(classification[:point_num].cpu().numpy())

        labels = np.concatenate(labels)
        pc = np.concatenate(pc)
        pred_class = np.concatenate(pred_class, axis=0)

        unmapped_labels = labels.copy().astype(int)
        unmapped_predictions = inverse_remap_predictions(pred_class, self._data.class_mapping)
        ## remapping the labels from 0-N
        if self._data.remap:
            labels = remap_labels(labels, self._data.class_mapping)

        ## sample points
        sample_idxs = mask_classes(labels, mask_class, self._data.class_mapping if self._data.remap else None)
        sampled_pc = pc[sample_idxs]
        if sampled_pc.shape[0] == 0:
            continue  
        sampled_pc = sampled_pc[:, :3]
        x, y, z = recenter(sampled_pc).transpose(1,0)   

        ## resample points if exeeds limits.
        if sample_idxs.sum() > max_display_point:
            raise_maxpoint_warning(idx, kwargs, logger, max_display_point, save_html)
            mask = np.random.randint(0, sample_idxs.sum(), max_display_point) 
        
        ## Apply cmap
        color_list_true =  color_mapping[labels[sample_idxs]][mask].tolist()
        color_list_pred = color_mapping[pred_class[sample_idxs].astype(int)][mask].tolist()        

        ## Plot
        scene=dict(aspectmode='data')


        fig.add_trace(go.Scatter3d(x=x[mask], y=y[mask], z=z[mask], 
                                        mode='markers', marker=dict(size=1, color=color_list_true),
                                        text=class_string(unmapped_labels[sample_idxs][mask])), row=1, col=1)

        fig.add_trace(go.Scatter3d(x=x[mask], y=y[mask], z=z[mask], 
                                        mode='markers', marker=dict(size=1, color=color_list_pred),
                                        text=class_string(unmapped_predictions[sample_idxs][mask], prefix='pred_')), row=1, col=2)


        title_text = get_title_text(idx, save_html, max_display_point)
        fig.update_layout(
            scene=scene,
            scene2=scene,
            title_text=title_text,
            width=kwargs.get('width', 750),
            height=kwargs.get('width', 512),
            showlegend=False,
            title_x=0.5
        )

        if save_html:
            save_path = Path(save_path)
            plotly.io.write_html(fig, str(save_path / 'show_results.html'))
            fig.write_image(str(save_path / 'show_results.png'))
            return
        else:
            fig.show()

        if idx == rows-1:
            break
