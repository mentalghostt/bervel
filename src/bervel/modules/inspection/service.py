"""Read use case; HTTP, Docker, files and SQL remain in their adapters."""
from bervel.modules.inspection.contracts import validate_operation


class InspectionReader:
    def __init__(self, *, files, database, docker, redactor):
        self.files, self.database, self.docker, self.redactor = files, database, docker, redactor

    def read(self, operation: str, arguments: dict) -> dict:
        validate_operation(operation, arguments, reads_only=True)
        routes = {'system.list': (self.files, 'list'), 'system.read': (self.files, 'read'),
            'database.tables': (self.database, 'tables'), 'database.schema': (self.database, 'schema'),
            'database.rows': (self.database, 'rows'), 'container.list': (self.docker, 'list'),
            'container.inspect': (self.docker, 'inspect'), 'container.logs': (self.docker, 'logs'),
            'container.files': (self.docker, 'files')}
        adapter, method = routes[operation]
        options = dict(arguments)
        if operation == 'database.rows' and 'filters' in options:
            options['where'] = {item['column']: item['value'] for item in options.pop('filters')}
        return self.redactor.clean(getattr(adapter, method)(**options))
