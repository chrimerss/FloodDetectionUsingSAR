import json as _json
from arcgis.raster._layer import ImageryLayer as _ImageryLayer
#from arcgis.raster._layer import Raster as _Raster
from arcgis.features import FeatureLayer as _FeatureLayer
import arcgis as _arcgis
import string as _string
import random as _random
from arcgis._impl.common._utils import _date_handler
import datetime
from arcgis.geometry import Geometry  as _Geometry
import numbers


import logging as _logging
_LOGGER = _logging.getLogger(__name__)

try:
    import numpy as _np
    import matplotlib.pyplot as _plt
    from matplotlib.pyplot import cm as _cm
except:
    pass


def _set_context(params, function_context = None):
    out_sr = _arcgis.env.out_spatial_reference
    process_sr = _arcgis.env.process_spatial_reference
    out_extent = _arcgis.env.analysis_extent
    mask = _arcgis.env.mask
    snap_raster = _arcgis.env.snap_raster
    cell_size = _arcgis.env.cell_size
    parallel_processing_factor = _arcgis.env.parallel_processing_factor

    context = {}

    if out_sr is not None:
        context['outSR'] = {'wkid': int(out_sr)}

    if out_extent is not None:
        context['extent'] = out_extent

    if process_sr is not None:
        context['processSR'] = {'wkid': int(process_sr)}


    if mask is not None:
        if isinstance(mask, _ImageryLayer):
            context['mask'] = {"url":mask._url}
        elif isinstance(mask,str):
            context['mask'] = {"url":mask}
    
    if cell_size is not None:
        if isinstance(cell_size, _ImageryLayer):
            context['cellSize'] = {"url":cell_size._url}
        elif isinstance(cell_size,str):
            if 'http:' in cell_size or 'https:' in cell_size:
                context['cellSize'] = {"url":cell_size}
            else:
                context['cellSize'] = cell_size
        else:
            context['cellSize'] = cell_size

    if snap_raster is not None:
        if isinstance(snap_raster, _ImageryLayer):
            context['snapRaster'] = {"url":snap_raster._url}
        elif isinstance(mask,str):
            context['snapRaster'] = {"url":snap_raster}


    if parallel_processing_factor is not None:
        context['parallelProcessingFactor'] = parallel_processing_factor


    if function_context is not None:
        if context is not None:
            context.update({k: function_context[k] for k in function_context.keys()})

        else:
            context = function_context

    if context:
        params["context"] = _json.dumps(context)

def _id_generator(size=6, chars=_string.ascii_uppercase + _string.digits):
    return ''.join(_random.choice(chars) for _ in range(size))

def _set_time_param(time):
    time_val = time
    if time is not None:
        if type(time) is list:
            if isinstance(time[0], datetime.datetime) or isinstance(time[0], datetime.date):
                if time[0].tzname() is None or time[0].tzname() != "UTC":
                    time[0] = time[0].astimezone(datetime.timezone.utc)
            if isinstance(time[1], datetime.datetime) or isinstance(time[1], datetime.date):
                if time[1].tzname() is None or time[1].tzname() != "UTC":
                    time[1] = time[1].astimezone(datetime.timezone.utc)
            starttime = _date_handler(time[0])
            endtime = _date_handler(time[1])
            if starttime is None:
                starttime = 'null'
            if endtime is None:
                endtime = 'null'
            time_val = "%s,%s" % (starttime, endtime)
        else:
            time_val = _date_handler(time)

    return time_val

def _to_datetime(dt):
    import datetime
    try:
        return  datetime.datetime.utcfromtimestamp(dt/1000)
    except:
        return dt

def _datetime2ole(date):
    #date = datetime.strptime(date, '%d-%b-%Y')
    import datetime
    OLE_TIME_ZERO = datetime.datetime(1899, 12, 30)
    delta = date - OLE_TIME_ZERO
    return float(delta.days) + (float(delta.seconds) / 86400)

def _ole2datetime(oledt):
    import datetime
    OLE_TIME_ZERO = datetime.datetime(1899, 12, 30, 0, 0, 0)
    try:
        return OLE_TIME_ZERO + datetime.timedelta(days=float(oledt))
    except:
        return datetime.datetime.utcfromtimestamp(oledt/1000)

def _iso_to_datetime(timestamp):
    format_string = '%Y-%m-%dT%H:%M:%S%z'
    try:
        colon = timestamp[-3]
        colonless_timestamp = timestamp
        if colon == ':':
            colonless_timestamp = timestamp[:-3] + timestamp[-2:]
        dt_ob = datetime.datetime.strptime(colonless_timestamp, format_string)
        return dt_ob.replace(tzinfo=None)
    except:
        try:
            format_string = '%Y-%m-%dT%H:%M:%S'
            dt_ob = datetime.datetime.strptime(timestamp, format_string)
            return dt_ob
        except:
            return timestamp

def _check_if_iso_format(timestamp):
    format_string = '%Y-%m-%dT%H:%M:%S%z'
    try:
        colon = timestamp[-3]
        colonless_timestamp = timestamp
        if colon == ':':
            colonless_timestamp = timestamp[:-3] + timestamp[-2:]
        dt_ob = datetime.datetime.strptime(colonless_timestamp, format_string)
        return True
    except:
        try:
            format_string = '%Y-%m-%dT%H:%M:%S'
            dt_ob = datetime.datetime.strptime(timestamp, format_string)
            return dt_ob
        except:
            return False

def _time_filter(time_extent,ele):
    if time_extent is not None:
        if isinstance(time_extent, datetime.datetime):
            if(ele<time_extent):
                return True
            else:
                return False
        elif isinstance(time_extent, list):
            if isinstance(time_extent[0], datetime.datetime) and isinstance(time_extent[1], datetime.datetime):                                                
                if(time_extent[0] < ele and ele < time_extent[1]):
                    return True
                else:
                    return False

        else:                            
            return True
    else:
        return True


def _linear_regression(sample_size, date_list, x, y):
    ncoefficient = 2
    if sample_size < ncoefficient:
        _LOGGER.warning("Trend line cannot be drawn. Insufficient points to plot Linear Trend Line")
        return [],[]

    AA = _np.empty([sample_size,ncoefficient], dtype=float, order='C')
    BB = _np.empty([sample_size,1], dtype=float, order='C')
    XX = _np.empty([ncoefficient,1], dtype=float, order='C')
    for i in range(sample_size):
        n=0
        AA[i][n] = date_list[i] 
        AA[i][n+1] = 1
        BB[i] = y[i]

    x1 = _np.linalg.lstsq(AA, BB, rcond=None)[0]

    YY=[]
    for i in range(sample_size):
        y_temp=x1[0][0]*date_list[i] + x1[1][0]
        YY.append(y_temp)
    return x,YY


def _harmonic_regression(sample_size, date_list, x, y, trend_order):
    PI2_Year = 3.14159265*2/365.25

    ncoefficient = 2 * (trend_order + 1)
    if sample_size < ncoefficient:
        _LOGGER.warning("Trend line cannot be drawn. Insufficient points to plot Harmonic Trend Line for trend order "+str(trend_order)+". Please try specifying a lower trend order.")
        return [],[]

    AA = _np.empty([sample_size,ncoefficient], dtype=float, order='C')
    BB = _np.empty([sample_size,1], dtype=float, order='C')
    XX = _np.empty([ncoefficient,1], dtype=float, order='C')

    for i in range(sample_size):
        n=0
        AA[i][n] = date_list[i] 
        AA[i][n+1] = 1

        for j in range(1,trend_order+1):
            AA[i][n + 2 * j] = _np.sin(PI2_Year * j * date_list[i])
            AA[i][n + 2 * j + 1] = _np.cos(PI2_Year * j * date_list[i])

        BB[i] = y[i]

    x1 = _np.linalg.lstsq(AA, BB, rcond=None)[0]
    YY=[]
    for i in range(sample_size):
        y_temp=x1[0][0]*date_list[i] + x1[1][0]
        for q in range(2,len(x1),2):
            y_temp=y_temp + x1[q][0] * _np.sin(2 * 3.14159265358979323846 * (q / 2) * date_list[i] / 365.25)
            y_temp=y_temp + x1[q+1][0] * _np.cos(2 * 3.14159265358979323846 * (q / 2) * date_list[i] / 365.25)
        YY.append(y_temp)
    return x, YY

def _epoch_to_iso(dt):
    import datetime
    try:
        return  datetime.datetime.fromtimestamp(dt/1000, tz=datetime.timezone.utc).isoformat()
    except:
        return dt

def _datetime2ole(date):
    #date = datetime.strptime(date, '%d-%b-%Y')
    import datetime
    OLE_TIME_ZERO = datetime.datetime(1899, 12, 30)
    delta = date - OLE_TIME_ZERO
    return float(delta.days) + (float(delta.seconds) / 86400)

def _ole2datetime(oledt):
    import datetime
    OLE_TIME_ZERO = datetime.datetime(1899, 12, 30, 0, 0, 0)
    try:
        return OLE_TIME_ZERO + datetime.timedelta(days=float(oledt))
    except:
        return datetime.datetime.utcfromtimestamp(oledt/1000)

def _iso_to_datetime(timestamp):
    format_string = '%Y-%m-%dT%H:%M:%S%z'
    try:
        colon = timestamp[-3]
        colonless_timestamp = timestamp
        if colon == ':':
            colonless_timestamp = timestamp[:-3] + timestamp[-2:]
        dt_ob = datetime.datetime.strptime(colonless_timestamp, format_string)
        return dt_ob.replace(tzinfo=None)
    except:
        try:
            format_string = '%Y-%m-%dT%H:%M:%S.%f%z'
            dt_ob = datetime.datetime.strptime(colonless_timestamp, format_string)
            return dt_ob.replace(tzinfo=None)
        except:
            try:
                format_string = '%Y-%m-%dT%H:%M:%S'
                dt_ob = datetime.datetime.strptime(timestamp, format_string)
                return dt_ob
            except:
                return timestamp

def _check_if_iso_format(timestamp):
    format_string = '%Y-%m-%dT%H:%M:%S%z'
    try:
        colon = timestamp[-3]
        colonless_timestamp = timestamp
        if colon == ':':
            colonless_timestamp = timestamp[:-3] + timestamp[-2:]
        dt_ob = datetime.datetime.strptime(colonless_timestamp, format_string)
        return True
    except:
        try:
            format_string = '%Y-%m-%dT%H:%M:%S'
            dt_ob = datetime.datetime.strptime(timestamp, format_string)
            return dt_ob
        except:
            return False

def _local_function_template(operation_number=None):
    template_dict = {
  "name" : "max_rft",
  "description" : "A raster function template.",
  "function" : {
    "pixelType" : "UNKNOWN",
    "name" : "Cell Statistics",
    "description" : "Calculates a per-cell statistic from multiple rasters.  The available statistics are Majority, Maximum, Mean, Median, Minimum, Minority, Range, Standard Deviation, Sum, and Variety.",
    "type" : "LocalFunction",
    "_object_id" : 1
  },
  "arguments" : {
    "Rasters" : {
      "name" : "Rasters",
      "value" : {
        "elements" : [
        ],
        "type" : "ArgumentArray",
        "_object_id" : 2
      },
      "aliases" : [
        "__IsRasterArray__"
      ],
      "isDataset" : False,
      "isPublic" : False,
      "type" : "RasterFunctionVariable",
      "_object_id" : 3
    },
    "Operation" : {
      "name" : "Operation",
      "value" : "",
      "isDataset" : False,
      "isPublic" : False,
      "type" : "RasterFunctionVariable",
      "_object_id" : 4
    },
    "CellsizeType" : {
      "name" : "CellsizeType",
      "value" : 2,
      "isDataset" : False,
      "isPublic" : False,
      "type" : "RasterFunctionVariable",
      "_object_id" : 5
    },
    "ExtentType" : {
      "name" : "ExtentType",
      "value" : 1,
      "isDataset" : False,
      "isPublic" : False,
      "type" : "RasterFunctionVariable",
      "_object_id" : 6
    },
    "ProcessAsMultiband" : {
      "name" : "ProcessAsMultiband",
      "value" : True,
      "isDataset" : False,
      "isPublic" : False,
      "type" : "RasterFunctionVariable",
      "_object_id" : 7
    },
    "MatchVariable" : {
      "name" : "MatchVariable",
      "value" : True,
      "isDataset" : False,
      "isPublic" : False,
      "type" : "RasterFunctionVariable",
      "_object_id" : 8
    },
    "UnionDimension" : {
      "name" : "UnionDimension",
      "value" : False,
      "isDataset" : False,
      "isPublic" : False,
      "type" : "RasterFunctionVariable",
      "_object_id" : 9
    },
    "type" : "LocalFunctionArguments",
    "_object_id" : 10
  },
  "functionType" : 0,
  "thumbnail" : ""
}
    if operation_number is not None:
        template_dict["arguments"]["Operation"]["value"]=operation_number
    return template_dict

def _get_geometry(data):
    if data is None:
        return None

    if isinstance(data, _Geometry):
        return data
    elif isinstance(data, arcgis.raster.Raster):
        return _Geometry(data.extent)
    elif isinstance(data, _ImageryLayer):
        return _Geometry(data.extent)
    elif isinstance(data, _FeatureLayer):
        return _get_geometry_from_feature_layer(data)
    else:
        return data

def _get_geometry_from_feature_layer(data):
    geo=None
    layer_fset = layer.query()
    for ele in layer_fset.features:
        geo = geo.union(_Geometry(ele.geometry)) if geo else _Geometry(ele.geometry)
    return geometry

def build_query_string(field_name, operator, field_values):
    operator_map = {
    "equals": "=",
    "less_than": "<",
    "greater_than": ">",
    "not_equals": "<>",
    "not_less_than": ">=",
    "not_greater_than": "<=",
        }

    if operator in operator_map:
        if isinstance(field_values, numbers.Number):
            return field_name + ' ' + operator_map[operator] + ' ' + str(field_values)
        elif isinstance(field_values, str):
            return field_name + ' ' + operator_map[operator] + ' \'' + field_values + '\''
        else:
            raise TypeError('field_value must be numeric or string')

    elif operator in ['starts_with', 'ends_with', 'not_starts_with', 'not_ends_with', 'contains', 'not_contains']:
        if not isinstance(field_values, str):
            raise TypeError('field_value must be string')
        if operator == 'starts_with':
            return field_name + ' LIKE ' + '\'' + field_values + '%\''
        elif operator == 'ends_with':
            return field_name + ' LIKE' + '\'%' + field_values + '\''
        elif operator == 'not_starts_with':
            return field_name + ' NOT LIKE ' + '\'' + field_values + '%\''
        elif operator == 'not_ends_with':
            return field_name + ' NOT LIKE ' + '\'%' + field_values + '\''
        elif operator == 'contains':
            return field_name + ' LIKE ' + '\'%' + field_values + '%\''
        elif operator == 'not_contains':
            return field_name + ' NOT LIKE ' + '\'%' + field_values + '%\''
    elif operator == 'in':
        if not isinstance(field_values, list):
            raise TypeError('field_values must be type list for operator "in"')
        values = '('
        for item in field_values:
            if not (isinstance(item, numbers.Number) or isinstance(item, str)):
                raise TypeError('item in field_values must be numeric or string')
            if values == '(':
                values += '\'' + item + '\'' if isinstance(item, str) else str(item)
            else:
                values += ',\'' + item + '\'' if isinstance(item, str) else ',' + str(item)
        values += ')'
        return field_name + ' IN ' + values
    elif operator == 'not_in':
        values = '('
        for item in field_values:
            if not (isinstance(item, numbers.Number) or isinstance(item, str)):
                raise TypeError('item in field_values must be numeric or string')
            if values == '(':
                values += '\'' + item + '\'' if isinstance(item, str) else str(item)
            else:
                values += ',\'' + item + '\'' if isinstance(item, str) else ',' + str(item)
        values += ')'
        return field_name + ' NOT IN ' + values
    else:
        raise ValueError('invalid operator value')