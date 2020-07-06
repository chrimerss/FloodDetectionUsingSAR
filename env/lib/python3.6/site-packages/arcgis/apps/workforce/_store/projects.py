""" Defines store functions for working with Projects.
"""

from arcgis.gis import Item

from ... import workforce


def get_project(project_id, gis):
    """ Loads and returns a workforce project.
        :param gis: An authenticated arcigs.gis.GIS object.
        :param project_id: The project's id.
        :returns: workforce.Project
    """
    item = Item(gis, project_id)
    return workforce.Project(item)
