"""
These tools help answer one of the most common questions posed in spatial analysis: What is near what?

create_buffers() creates areas of a specified distance from features.
"""
import json as _json

import logging as _logging
import arcgis as _arcgis
from arcgis.features import FeatureSet as _FeatureSet, FeatureCollection
from arcgis.geoprocessing._support import _execute_gp_tool
from ._util import _id_generator, _feature_input, _set_context, _create_output_service, GAJob

_log = _logging.getLogger(__name__)

_use_async = True

def create_buffers(input_layer,
                   distance=1,
                   distance_unit="Miles",
                   field=None,
                   method="Planar",
                   dissolve_option="None",
                   dissolve_fields=None,
                   summary_fields=None,
                   multipart=False,
                   output_name=None,
                   context=None,
                   gis=None,
                   future=False):
    """

    .. image:: _static/images/create_buffers_geo/create_buffers_geo.png

    Buffers are typically used to create areas that can be further analyzed
    using other tools such as ``aggregate_points``. For example, ask the question,
    "What buildings are within one mile of the school?" The answer can be found
    by creating a one-mile buffer around the school and overlaying the buffer
    with the layer containing building footprints. The end result is a layer
    of those buildings within one mile of the school.

    ================================================    =========================================================
    **Parameter**                                       **Description**
    ------------------------------------------------    ---------------------------------------------------------
    input_layer                                         Required layer. The point, line, or polygon features to be buffered.
                                                        See :ref:`Feature Input<gaxFeatureInput>`.
    ------------------------------------------------    ---------------------------------------------------------
    distance (Required if field is not provided)        Optional float. A float value used to buffer the input features.
                                                        You must supply a value for either the distance or field parameter.
                                                        You can only enter a single distance value. The units of the
                                                        distance value are supplied by the ``distance_unit`` parameter.

                                                        The default value is 1.
    ------------------------------------------------    ---------------------------------------------------------
    distance_unit (Required if distance is used)        Optional string. The linear unit to be used with the value specified in distance.

                                                        Choice list:['Feet', 'Yards', 'Miles', 'Meters', 'Kilometers', 'NauticalMiles']

                                                        The default value is "Miles"
    ------------------------------------------------    ---------------------------------------------------------
    field (Required if distance not provided)           Optional string. A field on the ``input_layer`` containing a buffer distance or a field expression.
                                                        A buffer expression must begin with an equal sign (=). To learn more about buffer expressions
                                                        see: `Buffer Expressions <https://developers.arcgis.com/rest/services-reference/bufferexpressions.htm>`_
    ------------------------------------------------    ---------------------------------------------------------
    method                                              Optional string. The method used to apply the buffer with. There are two methods to choose from:

                                                        Choice list:['Geodesic', 'Planar']

                                                        * ``Planar`` - This method applies a Euclidean buffers and is appropriate for local analysis on projected data. This is the default.
                                                        * ``Geodesic`` - This method is appropriate for large areas and any geographic coordinate system.
    ------------------------------------------------    ---------------------------------------------------------
    dissolve_option                                     Optional string. Determines how output polygon attributes are processed.

                                                        Choice list:['All', 'List', 'None']

                                                        +----------------------------------+---------------------------------------------------------------------------------------------------+
                                                        |Value                             | Description                                                                                       |
                                                        +----------------------------------+---------------------------------------------------------------------------------------------------+
                                                        | All - All features are dissolved | You can calculate summary statistics and determine if you want multipart or single part features. |
                                                        | into one feature.                |                                                                                                   |
                                                        +----------------------------------+---------------------------------------------------------------------------------------------------+
                                                        | List - Features with the same    | You can calculate summary statistics and determine if you want multipart or single part features. |
                                                        | value in the specified field     |                                                                                                   |
                                                        | will be dissolve together.       |                                                                                                   |
                                                        +----------------------------------+---------------------------------------------------------------------------------------------------+
                                                        | None - No features are dissolved.| There are no additional dissolve options.                                                         |
                                                        +----------------------------------+---------------------------------------------------------------------------------------------------+
    ------------------------------------------------    ---------------------------------------------------------
    dissolve_fields                                     Specifies the fields to dissolve on. Multiple fields may be provided.
    ------------------------------------------------    ---------------------------------------------------------
    summary_fields                                      Optional string. A list of field names and statistical summary types
                                                        that you want to calculate for resulting polygons. Summary statistics
                                                        are only available if dissolveOption = List or All. By default, all
                                                        statistics are returned.

                                                        Example: [{"statisticType": "statistic type", "onStatisticField": "field name"}, ..}]

                                                        fieldName is the name of the fields in the input point layer.

                                                        statisticType is one of the following for numeric fields:

                                                            * ``Count`` - Totals the number of values of all the points in each polygon.
                                                            * ``Sum`` - Adds the total value of all the points in each polygon.
                                                            * ``Mean`` - Calculates the average of all the points in each polygon.
                                                            * ``Min`` - Finds the smallest value of all the points in each polygon.
                                                            * ``Max`` - Finds the largest value of all the points in each polygon.
                                                            * ``Range`` - Finds the difference between the Min and Max values.
                                                            * ``Stddev`` - Finds the standard deviation of all the points in each polygon.
                                                            * ``Var`` - Finds the variance of all the points in each polygon.

                                                        statisticType is the following for string fields:

                                                            * ``Count`` - Totals the number of strings for all the points in each polygon.
                                                            * ``Any`` - Returns a sample string of a point in each polygon.

    ------------------------------------------------    ---------------------------------------------------------
    multipart                                           Optional boolean. Determines if output features are multipart or single part.
                                                        This option is only available if a ``dissolve_option`` is applied.
    ------------------------------------------------    ---------------------------------------------------------
    output_name                                         Optional string. The task will create a feature service of the results. You define the name of the service.
    ------------------------------------------------    ---------------------------------------------------------
    gis                                                 Optional, the GIS on which this tool runs. If not specified, the active GIS is used.
    ------------------------------------------------    ---------------------------------------------------------
    context                                             Optional dict. The context parameter contains additional settings that affect task execution. For this task, there are four settings:

                                                        #. Extent (``extent``) - A bounding box that defines the analysis area. Only those features that intersect the bounding box will be analyzed.
                                                        #. Processing spatial reference (``processSR``) - The features will be projected into this coordinate system for analysis.
                                                        #. Output spatial reference (``outSR``) - The features will be projected into this coordinate system after the analysis to be saved. The output spatial reference for the spatiotemporal big data store is always WGS84.
                                                        #. Data store (``dataStore``) - Results will be saved to the specified data store. The default is the spatiotemporal big data store.
    ------------------------------------------------    ---------------------------------------------------------
    future                                              Optional boolean. If 'True', the value is returned as a GPJob.

                                                        The default value is 'False'
    ================================================    =========================================================

    :returns: Output Features as a feature layer collection item

    .. code-block:: python

            # Usage Example: To create buffer based on distance field.

            buffer = create_buffers(input_layer=lyr,
                                    field='dist',
                                    method='Geodesic',
                                    dissolve_option='All',
                                    dissolve_fields='Date')
    """
    kwargs = locals()


    gis = _arcgis.env.active_gis if gis is None else gis
    url = gis.properties.helperServices.geoanalytics.url

    if isinstance(input_layer, FeatureCollection) and \
       'layers' in input_layer.properties and \
       len(input_layer.properties.layers) > 0:
        input_layer = _FeatureSet.from_dict(
            featureset_dict=input_layer._lazy_properties.layers[0].featureSet)

    params = {}
    for key, value in kwargs.items():
        if key != 'field':
            if value is not None:
                params[key] = value
        else:
            params['distance'] = None
            params['distance_unit'] = None

    if output_name is None:
        output_service_name = 'Create Buffers Analysis_' + _id_generator()
        output_name = output_service_name.replace(' ', '_')
    else:
        output_service_name = output_name.replace(' ', '_')

    output_service = _create_output_service(gis, output_name, output_service_name, 'Create Buffers')

    params['output_name'] = _json.dumps({
        "serviceProperties": {"name" : output_name, "serviceUrl" : output_service.url},
        "itemProperties": {"itemId" : output_service.itemid}})

    if context is not None:
        params["context"] = context
    else:
        _set_context(params)

    param_db = {
        "input_layer": (_FeatureSet, "inputLayer"),
        "distance": (float, "distance"),
        "distance_unit": (str, "distanceUnit"),
        "field": (str, "field"),
        "method": (str, "method"),
        "dissolve_option": (str, "dissolveOption"),
        "dissolve_fields": (str, "dissolveFields"),
        "summary_fields": (str, "summaryFields"),
        "multipart": (bool, "multipart"),
        "output_name": (str, "outputName"),
        "context": (str, "context"),
        "output": (_FeatureSet, "Output Features"),
    }
    return_values = [
        {"name": "output", "display_name": "Output Features", "type": _FeatureSet},
    ]

    try:
        if future:
            gpjob = _execute_gp_tool(gis, "CreateBuffers", params, param_db, return_values, _use_async, url, True, future=future)
            return GAJob(gpjob=gpjob, return_service=output_service)
        _execute_gp_tool(gis, "CreateBuffers", params, param_db, return_values, _use_async, url, True, future=future)
        return output_service
    except:
        output_service.delete()
        raise

create_buffers.__annotations__ = {
    'distance': float,
    'distance_unit': str,
    'field': str,
    'method': str,
    'dissolve_option': str,
    'dissolve_fields': str,
    'summary_fields': str,
    'multipart': bool,
    'output_name': str,
    'context': str}