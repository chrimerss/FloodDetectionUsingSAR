"""

These tools are used for data enrichment using geoanalytics

"""
import json as _json
import logging as _logging
import arcgis as _arcgis
from arcgis.features import FeatureSet as _FeatureSet
from arcgis.geoprocessing._support import _execute_gp_tool
from arcgis.geoanalytics._util import _id_generator, _feature_input, _set_context, _create_output_service, GAJob

_log = _logging.getLogger(__name__)

_use_async = True

def enrich_from_grid(input_layer,
                     grid_layer,
                     enrichment_attributes=None,
                     output_name=None,
                     gis=None,
                     context=None,
                     future=False):
    """
    .. image:: _static/images/enrich_from_grid/enrich_from_grid.png 

    The Enrich From Multi-Variable Grid task joins attributes from a multivariable grid to a point layer. 
    The multivariable grid must be created using the ``build_multivariable_grid`` task. Metadata from the 
    multivariable grid is used to efficiently enrich the input point features, making it faster than the 
    Join Features task. Attributes in the multivariable grid are joined to the input point features when 
    the features intersect the grid.

    The attributes in the multivariable grid can be used as explanatory variables when modeling spatial 
    relationships with your input point features, and this task allows you to join those attributes to 
    the point features quickly.

    .. note::
        Only available at ArcGIS Enterprise 10.7 and later.

    ======================  ===============================================================
    **Argument**            **Description**
    ----------------------  ---------------------------------------------------------------
    input_layer             Required layer. The point features that will be enriched
                            by the multi-variable grid. See :ref:`Feature Input<gaxFeatureInput>`.
    ----------------------  ---------------------------------------------------------------
    grid_layer              Required layer. The multivariable grid layer created using the Build Multi-Variable Grid task. 
                            See :ref:`Feature Input<gaxFeatureInput>`.
    ----------------------  ---------------------------------------------------------------
    enrichment_attributes   optional string. A list of fields in the multi-variable grid
                            that will be joined to the input point features. If the
                            attributes are not provided, all fields in the multi-variable
                            grid will be joined to the input point features.
    ----------------------  ---------------------------------------------------------------
    output_name             optional string. The task will create a feature service of the
                            results. You define the name of the service.
    ----------------------  ---------------------------------------------------------------
    gis                     optional GIS. The GIS object where the analysis will take place.
    ----------------------  ---------------------------------------------------------------
    context                 Optional dict. The context parameter contains additional settings that affect task execution. For this task, there are five settings:

                            #. Extent (``extent``) - A bounding box that defines the analysis area. Only those features that intersect the bounding box will be analyzed.
                            #. Processing spatial reference (``processSR``) - The features will be projected into this coordinate system for analysis.
                            #. Output spatial reference (``outSR``) - The features will be projected into this coordinate system after the analysis to be saved. The output spatial reference for the spatiotemporal big data store is always WGS84.
                            #. Data store (``dataStore``) - Results will be saved to the specified data store. The default is the spatiotemporal big data store.
                            #. Default aggregation styles (``defaultAggregationStyles``) - If set to 'True', results will have square, hexagon, and triangle aggregation styles enabled on results map services.
    ----------------------  ---------------------------------------------------------------
    future                  optional boolean. If 'True', a GPJob is returned instead of
                            results. The GPJob can be queried on the status of the execution.

                            The default value is 'False'.
    ======================  ===============================================================

    :returns: result_layer : Output Features as feature layer item.

    .. code-block:: python

            # Usage Example: To enrich a layer of crime data with a multivariable grid containing demographic information.

            enrich_result = enrich_from_grid(input_layer=crime_lyr, 
                                             grid_layer=mvg_layer,
                                             output_name="chicago_crimes_enriched")

            
    """
    kwargs = locals()
    tool_name = "EnrichFromMultiVariableGrid"
    gis = _arcgis.env.active_gis if gis is None else gis
    url = gis.properties.helperServices.geoanalytics.url
    params = {
        "f" : "json",
    }
    for key, value in kwargs.items():
        if value is not None:
            params[key] = value

    if output_name is None:
        output_service_name = 'Enrich_Grid_' + _id_generator()
        output_name = output_service_name.replace(' ', '_')
    else:
        output_service_name = output_name.replace(' ', '_')

    output_service = _create_output_service(gis, output_name, output_service_name, 'Enrich Grid Layers')

    params['output_name'] = _json.dumps({
        "serviceProperties": {"name" : output_name, "serviceUrl" : output_service.url},
        "itemProperties": {"itemId" : output_service.itemid}})

    if context is not None:
       params["context"] = context
    else:
        _set_context(params)

    param_db = {
        "input_layer": (_FeatureSet, "inputFeatures"),
        "grid_layer" : (_FeatureSet, "gridLayer"),
        "enrichment_attributes" : (str, "enrichAttributes"),
        "output_name": (str, "outputName"),
        "context": (str, "context"),
        "output": (_FeatureSet, "output"),
    }

    return_values = [
        {"name": "output", "display_name": "Output Features", "type": _FeatureSet},
    ]

    try:
        if future:
            gpjob = _execute_gp_tool(gis, tool_name, params, param_db, return_values, _use_async, url, True, future=future)
            return GAJob(gpjob=gpjob, return_service=output_service)
        _execute_gp_tool(gis, tool_name, params, param_db, return_values, _use_async, url, True, future=future)
        return output_service
    except:
        output_service.delete()
        raise
    return
