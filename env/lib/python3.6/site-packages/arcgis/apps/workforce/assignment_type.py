"""
Defines the AssignmentType class.
"""

from .exceptions import ValidationError
from .model import Model
from ._store import *


class AssignmentType(Model):
    """
    Defines the acceptable values for :class:`~arcgis.apps.workforce.Assignment` types.

    ==================     ====================================================================
    **Argument**           **Description**
    ------------------     --------------------------------------------------------------------
    project                Required :class:`~arcgis.apps.workforce.Project`. The project that
                           this assignment belongs to.
    ------------------     --------------------------------------------------------------------
    coded_value            Optional :class:`dict`. The dictionary storing the code and
                           name of the type.
    ------------------     --------------------------------------------------------------------
    name                   Optional :class:`String`. The name of the assignment type.
    ==================     ====================================================================

    """

    def __init__(self, project, coded_value=None, name=None):
        super().__init__()
        self.project = project
        if coded_value:
            self._coded_value = coded_value
        else:
            self._coded_value = {'code': None, 'name': name}

    def __str__(self):
        return self.name

    def __repr__(self):
        return "<AssignmentType {}>".format(self.code)

    def update(self, name=None):
        """
            Updates the assignment type on the server

            ==================     ====================================================================
            **Argument**           **Description**
            ------------------     --------------------------------------------------------------------
            name                   Optional :class:`String`.
                                   The name of the assignment type
            ==================     ====================================================================
        """
        update_assignment_type(self.project, self, name)

    def delete(self):
        """Deletes the assignment type from the server"""
        delete_assignment_types(self.project, [self])

    @property
    def id(self):
        """Gets the id of the assignment type"""
        return self.code

    @property
    def code(self):
        """Gets the internal code that uniquely identifies the assignment type"""
        return self._coded_value['code']

    @property
    def name(self):
        """Gets/Sets The name of the assignment type"""
        return self._coded_value['name']

    @name.setter
    def name(self, value):
        self._coded_value['name'] = value

    @property
    def coded_value(self):
        """Gets the coded value"""
        return self._coded_value

    def _validate(self, **kwargs):
        errors = super()._validate(**kwargs)
        errors += self._validate_name()
        errors += self._validate_name_uniqueness(**kwargs)
        return errors

    def _validate_for_update(self, **kwargs):
        return super()._validate_for_update(**kwargs) + self._validate_code()

    def _validate_for_remove(self, **kwargs):
        assignments = kwargs['assignments']
        errors = super()._validate_for_remove(**kwargs) + self._validate_code()
        if assignments is None:
            schema = self.project._assignment_schema
            where = "{}={}".format(schema.assignment_type, self.code)
            assignments = self.project.assignments.search(where=where)
        else:
            assignments = [a for a in assignments if a.assignment_type.code == self.code]
        if assignments:
            errors.append(ValidationError("Cannot remove an in-use AssignmentType", self))
        return errors

    def _validate_name(self):
        errors = []
        if self.name is None or self.name.isspace():
            errors.append(ValidationError("AssignmentType must have a name", self))
        elif '>' in self.name or '<' in self.name or '%' in self.name:
            errors.append(ValidationError("AssignmentType name contains invalid characters", self))
        return errors

    def _validate_name_uniqueness(self, assignment_types=None):
        errors = []
        if assignment_types is None:
            assignment_types = self.project.assignment_types.search()
        for assignment_type in assignment_types:
            if assignment_type.name == self.name and assignment_type.code != self.code:
                errors.append(ValidationError("AssignmentType name must be unique", self))
        return errors

    def _validate_code(self):
        errors = []
        if not isinstance(self.code, int):
            errors.append(ValidationError("Code must be a unique integer", self))
        return errors
