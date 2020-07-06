""" Defines the Project object.
"""
import arcgis
from arcgis.features import FeatureLayer
from arcgis.gis import Group
from arcgis._impl.common._utils import _lazy_property
import json

from ._schemas import *
from .managers import *


class Project:
    """
    A Workforce Project

    ==================     ====================================================================
    **Argument**           **Description**
    ------------------     --------------------------------------------------------------------
    item                   Required :class:`~arcgis.gis.Item`. The item that
                           the contains the project.
    ==================     ====================================================================

    .. code-block:: python

        # Get a Project and search the assignments and workers.

        import arcgis
        gis = arcgis.gis.GIS("https://arcgis.com", "<username>", "<password>")
        item = gis.content.get("<item-id>")
        project = arcgis.apps.workforce.Project(item)
        assignments = project.assignments.search()
        workers = project.workers.search()


    """

    def __init__(self, item):
        """
            :param item: The project's arcigs.gis.Item
        """
        self.gis = item._gis
        self._item = item
        self._item_data = item.get_data()
        self._assignment_schema = AssignmentSchema(self.assignments_layer)
        self._track_schema = TrackSchema(self.tracks_layer)
        self._worker_schema = WorkerSchema(self.workers_layer)
        self._dispatcher_schema = DispatcherSchema(self.dispatchers_layer)
        self._update_cached_objects()

    def _update_cached_assignment_types(self):
        """
            Updates the cached assignment types
        """
        self._cached_assignment_types = {a.code: a for a in self.assignment_types.search()}

    def _update_cached_objects(self):
        """
            Caches the types, workers, and dispatchers for quicker assignment creation when querying
            Should be called when querying assignments
        """
        self._update_cached_assignment_types()
        self._cached_workers = {w.id: w for w in self.workers.search()}
        self._cached_dispatchers = {d.id: d for d in self.dispatchers.search()}
        for d in self._cached_dispatchers.values():
            if d.user_id == self.gis.users.me.username:
                self._cached_dispatcher = d
                break
        else:
            raise Exception("'{}' is not a dispatcher, please authenticate as a dispatcher".format(self.gis.users.me.username))

    def __str__(self):
        return self.title

    def __repr__(self):
        return "<Project {}>".format(self.id)

    def delete(self):
        """
            Deletes the project, group, folder, layers, and webmaps.
            Assumes the currently signed in user owns the project or is an admin.
        """
        owner = self._item.owner
        self.assignments_item.protect(False)
        self.assignments_item.delete()
        self.tracks_item.protect(False)
        self.tracks_item.delete()
        self.workers_item.protect(False)
        self.workers_item.delete()
        self.dispatchers_item.protect(False)
        self.dispatchers_item.delete()
        self.dispatcher_webmap.item.protect(False)
        self.dispatcher_webmap.item.delete()
        self.worker_webmap.item.protect(False)
        self.worker_webmap.item.delete()
        self._item.protect(False)
        self._item.delete()
        self.group.delete()
        for folder in self.gis.users.get(owner).folders:
            if folder['id'] == self._item_data['folderId']:
                self.gis.content.delete_folder(folder['title'], owner=owner)

    def _update_data(self):
        self._item.update({
            "text": json.dumps(self._item_data)
        })

    def update(self, summary=None):
        """
        Updates the project on the server

        ==================     ====================================================================
        **Argument**           **Description**
        ------------------     --------------------------------------------------------------------
        summary                  Optional :class:`String`. The summary of the project.
        ==================     ====================================================================
        """
        item_properties = {}
        if summary:
            item_properties['snippet'] = summary
        self._item.update(item_properties)

    @property
    def _tracking_enabled(self):
        return self._item_data["tracks"]["enabled"]

    @_tracking_enabled.setter
    def _tracking_enabled(self, value):
        self._item_data["tracks"]["enabled"] = value
        self._update_data()

    @property
    def _tracking_interval(self):
        return self._item_data["tracks"]["updateInterval"]

    @_tracking_interval.setter
    def _tracking_interval(self, value):
        self._item_data["tracks"]["updateInterval"] = value
        self._update_data()

    @property
    def integrations(self):
        """The :class:`~arcgis.apps.workforce.managers.AssignmentIntegrationManager` for the project"""
        return AssignmentIntegrationManager(self)

    @property
    def id(self):
        """The item id of the project"""
        return self._item['id']

    @property
    def title(self):
        """Gets the title of the project"""
        return self._item['title']

    @property
    def summary(self):
        """The title of the project"""
        return self._item['snippet']

    @summary.setter
    def summary(self, value):
        self._item['snippet'] = value

    @property
    def owner_user_id(self):
        """The user id of the project owner."""
        return self._item['owner']

    @property
    def version(self):
        """The version of the project"""
        return self._item_data['version']

    @_lazy_property
    def assignments_item(self):
        """The assignments :class:`~arcgis.gis.Item`"""
        return self.gis.content.get(self._item_data['assignments']['serviceItemId'])

    @property
    def assignments_layer_url(self):
        """The assignments feature layer url"""
        return self._item_data['assignments']['url']

    @_lazy_property
    def dispatchers_item(self):
        """The dispatchers :class:`~arcgis.gis.Item`"""
        return self.gis.content.get(self._item_data['dispatchers']['serviceItemId'])

    @property
    def dispatchers_layer_url(self):
        """The dispatchers feature layer url"""
        return self._item_data['dispatchers']['url']

    @_lazy_property
    def tracks_item(self):
        """The tracks :class:`~arcgis.gis.Item`"""
        return self.gis.content.get(self._item_data['tracks']['serviceItemId'])

    @property
    def tracks_layer_url(self):
        """The tracks feature layer url"""
        return self._item_data['tracks']['url']

    @property
    def workers_layer_url(self):
        """The workers feature layer url"""
        return self._item_data['workers']['url']

    @property
    def dispatcher_web_map_id(self):
        """The dispatcher webmap item id"""
        return self._item_data['dispatcherWebMapId']

    @property
    def worker_web_map_id(self):
        """The worker webmap item id"""
        return self._item_data['workerWebMapId']

    @property
    def group_id(self):
        """The group id that all project items are part of"""
        return self._item_data['groupId']

    @_lazy_property
    def owner(self):
        """The owner :class:`~arcgis.gis.User` of the project"""
        return self.gis.users.get(self.owner_user_id)

    @_lazy_property
    def assignments_layer(self):
        """The assignments :class:`~arcgis.features.FeatureLayer`"""
        return FeatureLayer(self.assignments_layer_url, self.gis)

    @_lazy_property
    def dispatchers_layer(self):
        """The dispatchers :class:`~arcgis.features.FeatureLayer`"""
        return FeatureLayer(self.dispatchers_layer_url, self.gis)

    @_lazy_property
    def tracks_layer(self):
        """The tracks :class:`~arcgis.features.FeatureLayer`"""
        return FeatureLayer(self.tracks_layer_url, self.gis)

    @_lazy_property
    def workers_item(self):
        """The workers :class:`~arcgis.gis.Item`"""
        return self.gis.content.get(self._item_data['workers']['serviceItemId'])

    @_lazy_property
    def workers_layer(self):
        """The workers :class:`~arcgis.features.FeatureLayer`"""
        return FeatureLayer(self.workers_layer_url, self.gis)

    @_lazy_property
    def dispatcher_webmap(self):
        """The dispatcher :class:`~arcgis.mapping.WebMap` for the project"""
        return arcgis.mapping.WebMap(self.gis.content.get(self.dispatcher_web_map_id))

    @_lazy_property
    def worker_webmap(self):
        """The worker :class:`~arcgis.mapping.WebMap` for the project"""
        return arcgis.mapping.WebMap(self.gis.content.get(self.worker_web_map_id))

    @_lazy_property
    def group(self):
        """The :class:`~arcgis.gis.Group` that the project resources are part of"""
        return Group(self.gis, self.group_id)

    @property
    def assignments(self):
        """The :class:`~arcgis.apps.workforce.managers.AssignmentManager` for the project"""
        return AssignmentManager(self)

    @property
    def workers(self):
        """The :class:`~arcgis.apps.workforce.managers.WorkerManager` for the project"""
        return WorkerManager(self)

    @property
    def dispatchers(self):
        """The :class:`~arcgis.apps.workforce.managers.DispatcherManager` for the project"""
        return DispatcherManager(self)

    @property
    def tracks(self):
        """The :class:`~arcgis.apps.workforce.managers.TrackManager` for the project"""
        return TrackManager(self)

    @property
    def assignment_types(self):
        """The :class:`~arcgis.apps.workforce.managers.AssignmentTypeManager` for the project"""
        return AssignmentTypeManager(self)
