import json
import uuid
from arcgis.gis import GIS
from arcgis._impl.common._isd import InsensitiveDict
from ._base import BaseOGC
###########################################################################
class GeoJSONLayer(BaseOGC):
    """
    The GeoJSONLayer class is used to create a layer based on GeoJSON.
    GeoJSON is a format for encoding a variety of geographic data
    structures. The GeoJSON data must comply with the RFC 7946
    specification which states that the coordinates are in
    spatial reference: WGS84 (wkid 4326).


    ===============     ====================================================================
    **Argument**        **Description**
    ---------------     --------------------------------------------------------------------
    url                 Required string. The web location of the GeoJSON file.
    ---------------     --------------------------------------------------------------------
    copyright           Optional String. Describes limitations and usage of the data.
    ---------------     --------------------------------------------------------------------
    opacity             Optional Float.  This value can range between 1 and 0, where 0 is 100 percent transparent and 1 is completely opaque.
    ---------------     --------------------------------------------------------------------
    renderer            Optional Dictionary. A custom set of symbology for the given geojson dataset.
    ---------------     --------------------------------------------------------------------
    scale               Optional Tuple. The min/max scale of the layer where the positions are: (min, max) as float values.
    ---------------     --------------------------------------------------------------------
    title               Optional String. The title of the layer used to identify it in places such as the Legend and Layer List widgets.
    ===============     ====================================================================


    """
    _type = "geojson"
    #----------------------------------------------------------------------
    def __init__(self, url, **kwargs):
        """init"""
        super(GeoJSONLayer, self)
        self._url = url
        self._type = "geojson"
        self._copyright = kwargs.pop("copyright", "")
        self._title = kwargs.pop("title", "GeoJSON Layer")
        self._id = kwargs.pop('id', uuid.uuid4().hex) # hidden input, but accepted
        self._min_scale, self._max_scale = kwargs.pop('scale', (0,0))
        self._opacity = kwargs.pop("opacity", 0)
        if 'renderer' in kwargs:
            r = kwargs.pop('renderer', None)
            if isinstance(r, dict):
                self._renderer = InsensitiveDict(r)
            else:
                self._renderer = None
        else:
            self._renderer = None
    #----------------------------------------------------------------------
    @property
    def renderer(self) -> InsensitiveDict:
        """Gets/Sets the renderer for the layer"""
        return self._renderer
    #----------------------------------------------------------------------
    @renderer.setter
    def renderer(self, renderer:dict):
        """Gets/Sets the renderer for the layer"""
        if isinstance(renderer, dict) and renderer:
            self._renderer = InsensitiveDict(renderer)
    #----------------------------------------------------------------------
    @property
    def _esri_json(self) -> dict:
        lyr = {
            "type" : self._type,
            "url" : self._url,
            "copyright" : self._copyright,
            "title" : self._title,
            "id" : self._id,
            "minScale" : self.scale[0],
            "maxScale" : self.scale[1]
        }
        if self._renderer:
            lyr['renderer'] = self._renderer._json
        return lyr
