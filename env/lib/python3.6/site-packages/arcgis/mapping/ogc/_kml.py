import os
import sys
import json
import uuid
from arcgis.gis import GIS
from arcgis import env as _env
from arcgis._impl.common._isd import InsensitiveDict
from ._base import BaseOGC
###########################################################################
class KMLLayer(BaseOGC):
    """
    The KMLLayer class is used to create a layer based on a KML file (.kml, .kmz).
    KML is an XML-based file format used to represent geographic features.

    ======================  =====================================================================
    **Arguement**           **Value**
    ----------------------  ---------------------------------------------------------------------
    url                     Required String.  The web location of the KML file.
    ----------------------  ---------------------------------------------------------------------
    copyright               Optional String. Describes limitations and usage of the data.
    ----------------------  ---------------------------------------------------------------------
    opacity                 Optional Float.  This value can range between 1 and 0, where 0 is 100 percent transparent and 1 is completely opaque.
    ----------------------  ---------------------------------------------------------------------
    scale                   Optional Tuple. The min/max scale of the layer where the positions are: (min, max) as float values.
    ----------------------  ---------------------------------------------------------------------
    title                   Optional String. The title of the layer used to identify it in places such as the Legend and LayerList widgets.
    ======================  =====================================================================


    """
    _type = "kml"
    def __init__(self, url, **kwargs):
        """initializer"""
        super(KMLLayer, self)
        self._url = url
        self._title = kwargs.pop('title', "KML Layer")
        self._id = kwargs.pop('id', uuid.uuid4().hex)
        self._min_scale, self._max_scale = kwargs.pop('scale', (-1,-1))
        self._opacity = kwargs.pop('opacity', 0)
        self._copyright = kwargs.pop('copyright', None)
        self._gis = None
    #----------------------------------------------------------------------
    @property
    def _esri_json(self) -> dict:
        """creates a dictionary for web map item."""
        add_layer =  {
            "type" : "kml",
            'url' : self._url,
            'opacity' : self.opacity,
            'minScale' : self.scale[0],
            'maxScale' : self.scale[1],
            'id' : self._id,
            'title' : self.title
        }
        return add_layer