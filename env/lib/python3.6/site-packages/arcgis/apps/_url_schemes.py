import urllib.parse
import arcgis
import json


def build_collector_url(webmap=None, center=None, feature_layer=None, fields=None, search=None, portal=None,  action=None, geometry=None, callback=None, callback_prompt=None):
    """
    Creates a url that can be used to open Collector for ArcGIS

    ==================     ====================================================================
    **Argument**           **Description**
    ------------------     --------------------------------------------------------------------
    webmap                 Optional :class:`String`, :class:`~arcgis.mapping.WebMap`, :class:`~arcgis.gis.Item`.
                           The item id, webmap, or item representing the map to open in Collector.
    ------------------     --------------------------------------------------------------------
    center                 Optional :class:`String`, :class:`list`, :class:`tuple`.
                           The "lat,long" in WGS84 of where to center the map
    ------------------     --------------------------------------------------------------------
    feature_layer          Optional :class:`String` or :class:`~arcgis.features.FeatureLayer`.
                           The feature layer url as string or the feature layer representing the layer to open
                           for collection.
    ------------------     --------------------------------------------------------------------
    fields                 Optional :class:`Dict`. The feature attributes dictionary {"field":"value"}
    ------------------     --------------------------------------------------------------------
    search                 Optional :class:`String` An address, place, coordinate, or feature to search for
                           Requires webmap and action=search to be set.
                           Value must be URL encoded
    ------------------     --------------------------------------------------------------------
    portal                 Optional :class:`String`, :class:`~arcgis.gis.GIS`.
                           The URL of the portal the mobile worker must be connected to.
    ------------------     --------------------------------------------------------------------
    action                 Optional :class:`String` What the app should do, if anything, once open
                           and the user is signed in.
                           The following values are supported: addFeature, center, open, search.
    ------------------     --------------------------------------------------------------------
    geometry               Optional :class:`String`. Defines the location for the newly collectoed
                           or edited feature
                           Requires webmap, action=addFeature, and feature_layer.
                           Value is a coordinate containing x, y (z if available)
    ------------------     --------------------------------------------------------------------
    callback               Optional :class:`String`. The URL to call when capturing the asset or
                           observation is complete.
                           Requires webmap, action=addFeature, and feature_layer to be set.
                           Optionally, before calling the URL provide a prompt for the user,
                           specified with the callback_prompt parameter.
    ------------------     --------------------------------------------------------------------
    callback_prompt        Optional :class:`String`. Prompt the mobile worker before executing the callback,
                           and display this value in the prompt as where the mobile worker will be taken.
                           Requires webmap, action=addFeature, feature_layer, and callback to be specified.
                           Value must be URL encoded
    ==================     ====================================================================

    Additional info can be found here: https://github.com/Esri/collector-integration
    :return: :class:`String`
    """
    params = []

    # Branch out based on the version of Collector.
    if portal or action:
        url = "https://collector.arcgis.app"
        if portal:
            if isinstance(portal,arcgis.gis.GIS):
                portal = portal.url
            params.append("portalURL=" + portal)

        if action:
            params.append("referenceContext=" + action)
            if not webmap:
                raise ValueError("Invalid parameters -- Must specify a webmap")
            else:
                item_id = webmap
                if isinstance(item_id, arcgis.mapping.WebMap):
                    item_id = item_id.item.id
                elif isinstance(item_id, arcgis.gis.Item):
                    item_id = item_id.id
                params.append("itemID=" + item_id)

            actions = {'open': lambda: _build_collector_url_for_open_action(params),
                       'center': lambda: _build_collector_url_for_center_action(params, center),
                       'search': lambda: _build_collector_url_for_search_action(params, search),
                       'addFeature': lambda: _build_collector_url_for_addFeature_action(params, feature_layer, geometry, fields, callback, callback_prompt)}

            params = actions.get(action)()

    # Collector Classic app integration logic.
    else:
        url = "arcgis-collector://"
        _validate_collector_url(webmap, center, feature_layer, fields)
        item_id = webmap
        # webmap falsy bug #1244
        if webmap is not None:
            if isinstance(webmap, arcgis.mapping.WebMap):
                item_id = webmap.item.id
            elif isinstance(webmap, arcgis.gis.Item):
                item_id = webmap.id
            params.append("itemID=" + item_id)
        if center:
            if isinstance(center,(list, tuple)):
                center = '{},{}'.format(center[0],center[1])
            params.append("center=" + center)
        if feature_layer:
            feature_source_url = feature_layer
            if isinstance(feature_layer, arcgis.features.FeatureLayer):
                feature_source_url = feature_layer.url
            params.append("featureSourceURL=" + feature_source_url)
        if fields:
            attributes = []
            # unencoded format is featureAttributes={"fieldName":"value","fieldName2":"value2"}
            for k, v in fields.items():
                attributes.append(_encode_string('"{}":"{}"'.format(k, v)))
            params.append("featureAttributes=%7B" + ",".join(attributes) + "%7D")

    if params:
        url += "?" + "&".join(params)
    return url


def _build_collector_url_for_open_action(params):
    return params


def _build_collector_url_for_center_action(params, center):
    if center:
        if isinstance(center, (list, tuple)):
            center = '{},{}'.format(center[0], center[1])
        params.append("center=" + center)
        return params
    else:
        raise ValueError("Invalid parameters -- Must specify a center parameter if action = center")


def _build_collector_url_for_search_action(params, search):
    if search:
        params.append("search=" + _encode_string(search))
        return params
    else:
        raise ValueError("Invalid parameters -- Must specify a search parameter if action = search")


def _build_collector_url_for_addFeature_action(params, feature_layer, geometry, fields, callback, callback_prompt):
    if feature_layer:
        feature_source_url = feature_layer
        if isinstance(feature_layer, arcgis.features.FeatureLayer):
            feature_source_url = feature_layer.url
        params.append("featureSourceURL=" + feature_source_url)
    else:
        raise ValueError("Invalid parameters -- Must specify a feature_layer parameter if action = addFeature")

    if geometry:
        if isinstance(geometry, dict):
            geometry = json.dumps(geometry)
        params.append("geometry=" + geometry)

    if fields:
        attributes = []
        for k, v in fields.items():
            attributes.append(_encode_string('"{}":"{}"'.format(k, v)))
        params.append("featureAttributes=%7B" + ",".join(attributes) + "%7D")

    if callback:
        params.append("callback=" + callback)
        if callback_prompt:
            params.append("callbackPrompt=" + _encode_string(callback_prompt))

    return params


def _validate_collector_url(webmap, center, feature_layer, fields):
    if webmap is not None and not any([isinstance(webmap, str), isinstance(webmap, arcgis.gis.Item), isinstance(webmap, arcgis.mapping.WebMap)]):
        raise ValueError("Invalid type for webmap parameter")
    if center:
        if webmap is None:
            raise ValueError("Invalid parameters -- Must specify a webmap if setting center")
    if feature_layer:
        if webmap is None:
            raise ValueError("Invalid parameters -- Must specify a webmap if setting feature layer")
    if fields:
        if webmap is None:
            raise ValueError("Invalid parameters -- Must specify a webmap if setting feature attributes")
        if not feature_layer:
            raise ValueError("Invalid parameters -- Must specify a webmap if setting feature layer")


def build_explorer_url(webmap=None, search=None, bookmark=None, center=None, scale=None, wkid=None, rotation=None,
                       markup=None, url_type="Web"):
    """
    Creates a url that can be used to open Explorer for ArcGIS

    ==================     ====================================================================
    **Argument**           **Description**
    ------------------     --------------------------------------------------------------------
    webmap                 Optional :class:`String`, :class:`~arcgis.mapping.WebMap`, :class:`~arcgis.gis.Item`.
                           The item id, webmap, or item representing the map to open in Explorer.
                           Item can be of type Web Map or Mobile Map Package.
    ------------------     --------------------------------------------------------------------
    search                 Optional :class:`String`. The location to search for.
    ------------------     --------------------------------------------------------------------
    bookmark               Optional :class:`String`. The name of the bookmark in the map to open.
    ------------------     --------------------------------------------------------------------
    center                 Optional :class:`String`, :class:`list`, :class:`tuple`.
                           The "lat,long" in WGS84 of where to center the map
    ------------------     --------------------------------------------------------------------
    scale                  Optional :class:`Int`. The scale at which to open the map.
    ------------------     --------------------------------------------------------------------
    rotation               Optional :class:`Int`. The rotation, in degrees, at which to open the map.
    ------------------     --------------------------------------------------------------------
    markup                 Optional :class:`Boolean`. Determines if the app should open in markup mode.
    ------------------     --------------------------------------------------------------------
    url_type               Optional :class:`String`. The type of url to be returned (e.g. 'Web' or 'App')
    ==================     ====================================================================

    Additional info can be found here: https://github.com/Esri/explorer-integration

    :return: :class:`String`
    """
    _validate_explorer_url(webmap, search, bookmark, center, scale, wkid, rotation, markup, url_type)
    if url_type == "Web":
        url = "https://explorer.arcgis.app"
    else:
        url = "arcgis-explorer://"
    params = []
    item_id = webmap
    if webmap is not None:
        if isinstance(webmap, arcgis.mapping.WebMap):
            item_id = webmap.item.id
        elif isinstance(webmap, arcgis.gis.Item):
            item_id = webmap.id
        params.append("itemID=" + item_id)
    if search:
        params.append("search=" + _encode_string(search))
    if bookmark:
        params.append("bookmark=" + _encode_string(bookmark))
    if center:
        if isinstance(center, (list, tuple)):
            center = '{},{}'.format(center[0], center[1])
        params.append("center=" + _encode_string(center))
    if scale:
        params.append("scale=" + str(scale))
    if wkid:
        params.append("wkid=" + str(wkid))
    if rotation:
        params.append("rotation=" + str(rotation))
    if markup:
        params.append("markup=" + str(markup).lower())
    if params:
        url += "?" + "&".join(params)
    return url


def _validate_explorer_url(webmap, search, bookmark, center, scale, wkid, rotation, markup, url_type):
    if url_type not in {'Web', 'App'}:
        raise ValueError("Invalid type -- url_type must be 'Web' or 'App'")
    if webmap is not None and not any([isinstance(webmap, str), isinstance(webmap, arcgis.gis.Item), isinstance(webmap, arcgis.mapping.WebMap)]):
        raise ValueError("Invalid type for webmap parameter")
    if search and webmap is None:
        raise ValueError("Invalid parameters -- search requires a webmap")
    if bookmark and webmap is None:
        raise ValueError("Invalid parameters -- bookmark requires a webmap")
    if (center or scale) and webmap is None:
        raise ValueError("Invalid parameters -- center and scale requires a webmap")
    if center and not scale:
        raise ValueError("Invalid parameters -- URL is missing scale")
    if scale and not center:
        raise ValueError("Invalid parameters -- URL is missing center")
    if ((search and bookmark) or
            (search and center) or
            (bookmark and center)):
        raise ValueError("Invalid parameters -- URL contains conflicting parameters")
    if (wkid or rotation or markup) and not (center and scale):
        raise ValueError("Invalid parameters -- wkid, rotation, or markup requires center and scale")


def build_navigator_url(start=None, stops=None, optimize=None, navigate=None,
                        travel_mode=None, callback=None, callback_prompt=None, url_type="Web", webmap=None, route_item=None):
    """
    Creates a url that can be used to open Navigator for ArcGIS

    ==================     ====================================================================
    **Argument**           **Description**
    ------------------     --------------------------------------------------------------------
    start                  Optional :class:`String` or :class:`Tuple`. The starting location.
                           Can be a single string such as '45,-77' or a tuple containing the
                           location and the name ('45,-77','Home')
    ------------------     --------------------------------------------------------------------
    stops                  Optional :class:`List`. The list of locations. A location can be either
                           a single string or a tuple containing the location and the
                           name ('45,-77', 'Home').
    ------------------     --------------------------------------------------------------------
    optimize               Optional :class:`Boolean`. Determines if the route should be optimized.
    ------------------     --------------------------------------------------------------------
    navigate               Optional :class:`Boolean`. Determines if navigation should begin immediately.
    ------------------     --------------------------------------------------------------------
    travel_mode            Optional :class:`String`. The travel mode to use (e.g. 'Walking Time')
    ------------------     --------------------------------------------------------------------
    callback               Optional :class:`String`. The url to open when the route completes.
    ------------------     --------------------------------------------------------------------
    callback_prompt        Optional :class:`String`. The text to show when the route finishes and the
                           callback is about to be invoked.
    ------------------     --------------------------------------------------------------------
    url_type               Optional :class:`String`. The type of url to be returned (e.g. 'Web' or 'App')
    ------------------     --------------------------------------------------------------------
    webmap                 Optional :class:`String`, :class:`~arcgis.gis.Item`.
                           The item id or item representing the map to open in Navigator.
                           Item can be of type Mobile Map Package.
    ------------------     --------------------------------------------------------------------
    route_item             Optional :class:`String`, :class:`~arcgis.gis.Item`.
                           The item id or item representing the route layer to open.
    ==================     ====================================================================

    Additional info can be found here: https://github.com/Esri/navigator-integration

    :return: :class:`String`
    """
    _validate_navigator_url(start, stops, optimize, navigate, travel_mode, callback, callback_prompt, url_type, webmap, route_item)
    if url_type == "Web":
        url = "https://navigator.arcgis.app"
    else:
        url = "arcgis-navigator://"
    params = []
    item_id = webmap
    if webmap is not None:
        if isinstance(webmap, arcgis.mapping.WebMap):
            item_id = webmap.item.id
        elif isinstance(webmap, arcgis.gis.Item):
            item_id = webmap.id
        params.append("itemID=" + item_id)
    if stops:
        params.extend(_encode_navigator_stops(stops))
    if start:
        params.extend(_encode_navigator_start(start))
    if optimize:
        params.append("optimize=" + str(optimize).lower())
    if navigate:
        params.append("navigate=" + str(navigate).lower())
    if travel_mode:
        params.append("travelmode=" + _encode_string(travel_mode))
    if route_item:
        if isinstance(route_item, arcgis.gis.Item):
            route_id = route_item.id
        else:
            route_id = route_item
        params.append("routeItemID=" + route_id)
    if callback:
        params.append("callback=" + callback)
    if callback_prompt:
        params.append("callbackprompt=" + _encode_string(callback_prompt))
    if params:
        url += "?" + "&".join(params)
    return url


def _validate_navigator_url(start, stops, optimize, navigate, travel_mode, callback, callback_prompt, url_type, webmap, route_item):
    if url_type not in {'Web', 'App'}:
        raise ValueError("Invalid type -- url_type must be 'Web' or 'App'")
    if webmap is not None and not any([isinstance(webmap, str), isinstance(webmap, arcgis.gis.Item)]):
        raise ValueError("Invalid type for webmap parameter")
    if stops:
        if len([stop for stop in stops if not isinstance(stop, tuple) and not isinstance(stop, str)]) > 0:
            raise ValueError("Invalid parameters -- stops must be a single string or tuple containing strings")
    if navigate and not stops:
        raise ValueError("Invalid parameters -- navigate param requires stops")
    if optimize and not stops:
        raise ValueError("Invalid parameters --- optimize param requires stops")
    if travel_mode and not stops:
        raise ValueError("Invalid parameters --- travel mode param requires stops")
    if route_item and any([start, stops, optimize, travel_mode]):
        raise ValueError("Invalid parameters -- cannot provide route_item and stop list params")
    if callback and not stops:
        raise ValueError("Invalid parameters -- callback param requires stops")


def _encode_navigator_stops(stops):
    """
    Returns a list of encoded stops and stopnames as a list of parameters
    """
    params = []
    for stop in stops:
        # handle empty second parameter in tuple
        has_name = (isinstance(stop, list) or isinstance(stop, tuple)) and len(stop) > 1 and bool(stop[1])
        if has_name:
            params.extend(["stop="+_encode_string(stop[0]), "stopname="+_encode_string(stop[1])])
        else:
            # handle empty second parameter in tuple
            if isinstance(stop, list) or isinstance(stop, tuple):
                stop = stop[0]
            params.append("stop="+_encode_string(stop))
    return params


def _encode_navigator_start(start):
    """
    Returns a list of encoded start and startname as a list of parameters
    """
    params = []
    # handle empty second parameter in tuple
    has_name = (isinstance(start, list) or isinstance(start, tuple)) and len(start) > 1 and bool(start[1])
    if has_name:
        params.extend(["start="+_encode_string(start[0]), "startname="+_encode_string(start[1])])
    else:
        # handle empty second parameter in tuple
        if isinstance(start, list) or isinstance(start, tuple):
            start = start[0]
        params.append("start="+_encode_string(start))
    return params


def build_survey123_url(survey=None, center=None, fields=None):
    """
    Creates a url that can be used to open Survey123 for ArcGIS

    ==================     ====================================================================
    **Argument**           **Description**
    ------------------     --------------------------------------------------------------------
    survey                 Optional :class:`String` or :class:`~arcgis.gis.Item`. The item id or
                           item representing the survey to open.
    ------------------     --------------------------------------------------------------------
    center                 Optional :class:`String`. The "lat,long" in WGS84 of where to center the map
    ------------------     --------------------------------------------------------------------
    fields                 Optional :class:`Dict`. The feature attributes dictionary {"field":"value"}
    ==================     ====================================================================

    Additional info can be found here: https://doc.arcgis.com/en/survey123/reference/integratewithotherapps.htm

    :return: :class:`String`
    """
    _validate_survey123_url(survey, center, fields)
    params = []
    url = "arcgis-survey123://"
    if survey:
        item_id = survey
        if isinstance(survey, arcgis.gis.Item):
            item_id = survey.id
        params.append("itemID=" + item_id)
    if center:
        # Do not encode
        params.append("center=" + center)
    if fields:
        for k, v in fields.items():
            params.append('field:{}={}'.format(_encode_string(k), _encode_string(v)))
    if params:
        url += "?" + "&".join(params)
    return url


def _validate_survey123_url(survey, center, fields):
    if survey is not None and not any([isinstance(survey, str), isinstance(survey, arcgis.gis.Item)]):
        raise ValueError("Invalid type for survey parameter")
    if center:
        if not survey:
            raise ValueError("Invalid parameters -- Must specify a survey if setting center")
    if fields:
        if not survey:
            raise ValueError("Invalid parameters -- Must specify a survey if setting fields")


def build_tracker_url(portal_url=None):
    """
        Creates a url that can be used to open Tracker for ArcGIS

        ==================     ====================================================================
        **Argument**           **Description**
        ------------------     --------------------------------------------------------------------
        portal_url             Optional :class:`String` The portal that should be used when tracker
                               is launched via the url scheme.
        ==================     ====================================================================

        :return: :class:`String`
    """
    url = "arcgis-tracker://"
    if portal_url is not None:
        url += "?portalURL={}".format(portal_url)
    return url


def _encode_string(string):
    # allow for template values (e.g. "{assignment.location}"
    return urllib.parse.quote(str(string), safe="${},:")
