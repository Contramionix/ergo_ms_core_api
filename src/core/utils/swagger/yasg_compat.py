"""Совместимый слой swagger_auto_schema / openapi поверх drf-spectacular."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)

IN_QUERY = OpenApiParameter.QUERY
IN_PATH = OpenApiParameter.PATH
IN_HEADER = OpenApiParameter.HEADER
IN_FORM = OpenApiParameter.QUERY
IN_BODY = OpenApiParameter.QUERY

TYPE_STRING = OpenApiTypes.STR
TYPE_INTEGER = OpenApiTypes.INT
TYPE_NUMBER = OpenApiTypes.NUMBER
TYPE_BOOLEAN = OpenApiTypes.BOOL
TYPE_OBJECT = OpenApiTypes.OBJECT
TYPE_ARRAY = OpenApiTypes.OBJECT
TYPE_FILE = OpenApiTypes.BINARY

FORMAT_UUID = OpenApiTypes.UUID
FORMAT_DATE = OpenApiTypes.DATE
FORMAT_DATETIME = OpenApiTypes.DATETIME
FORMAT_EMAIL = OpenApiTypes.EMAIL
FORMAT_URI = OpenApiTypes.URI
FORMAT_INT64 = OpenApiTypes.INT64
FORMAT_INT32 = OpenApiTypes.INT32
FORMAT_FLOAT = OpenApiTypes.FLOAT
FORMAT_DOUBLE = OpenApiTypes.DOUBLE
FORMAT_DECIMAL = OpenApiTypes.DECIMAL
FORMAT_BYTE = OpenApiTypes.BYTE
FORMAT_BINARY = OpenApiTypes.BINARY
FORMAT_PASSWORD = OpenApiTypes.PASSWORD


def _type_name(value):
    mapping = {
        TYPE_STRING: 'string',
        TYPE_INTEGER: 'integer',
        TYPE_NUMBER: 'number',
        TYPE_BOOLEAN: 'boolean',
        TYPE_OBJECT: 'object',
        TYPE_ARRAY: 'array',
        TYPE_FILE: 'string',
    }
    return mapping.get(value, value if isinstance(value, str) else None)


class Schema:
    def __init__(self, type=None, properties=None, items=None, description=None, **kwargs):
        self.type = type
        self.properties = properties or {}
        self.items = items
        self.description = description
        self.extra = kwargs

    def to_dict(self):
        data = {}
        type_name = _type_name(self.type)
        if type_name:
            data['type'] = type_name
        if self.description:
            data['description'] = self.description
        if self.properties:
            data['properties'] = {
                key: (value.to_dict() if isinstance(value, Schema) else value)
                for key, value in self.properties.items()
            }
        if self.items is not None:
            data['items'] = self.items.to_dict() if isinstance(self.items, Schema) else self.items
        data.update(self.extra)
        return data


class Items(Schema):
    pass


class Response:
    def __init__(self, description='', schema=None, examples=None):
        self.description = description
        self.schema = schema
        self.examples = examples

    def to_spectacular(self):
        payload = self.schema.to_dict() if isinstance(self.schema, Schema) else self.schema
        return OpenApiResponse(description=self.description, response=payload)


class Parameter:
    def __init__(
        self,
        name,
        location,
        description='',
        type=TYPE_STRING,
        required=False,
        format=None,
        **kwargs,
    ):
        self.name = name
        self.location = location
        self.description = description
        self.type = format or type
        self.required = required
        self.extra = kwargs

    def to_spectacular(self):
        return OpenApiParameter(
            name=self.name,
            type=self.type,
            location=self.location,
            description=self.description,
            required=self.required,
            **{key: value for key, value in self.extra.items() if key in ('enum', 'default', 'pattern', 'deprecated')},
        )


class Info:
    def __init__(self, **kwargs):
        self.data = kwargs


class _OpenApi:
    IN_QUERY = IN_QUERY
    IN_PATH = IN_PATH
    IN_HEADER = IN_HEADER
    IN_FORM = IN_FORM
    IN_BODY = IN_BODY
    TYPE_STRING = TYPE_STRING
    TYPE_INTEGER = TYPE_INTEGER
    TYPE_NUMBER = TYPE_NUMBER
    TYPE_BOOLEAN = TYPE_BOOLEAN
    TYPE_OBJECT = TYPE_OBJECT
    TYPE_ARRAY = TYPE_ARRAY
    TYPE_FILE = TYPE_FILE
    FORMAT_UUID = FORMAT_UUID
    FORMAT_DATE = FORMAT_DATE
    FORMAT_DATETIME = FORMAT_DATETIME
    FORMAT_EMAIL = FORMAT_EMAIL
    FORMAT_URI = FORMAT_URI
    FORMAT_INT64 = FORMAT_INT64
    FORMAT_INT32 = FORMAT_INT32
    FORMAT_FLOAT = FORMAT_FLOAT
    FORMAT_DOUBLE = FORMAT_DOUBLE
    FORMAT_DECIMAL = FORMAT_DECIMAL
    FORMAT_BYTE = FORMAT_BYTE
    FORMAT_BINARY = FORMAT_BINARY
    FORMAT_PASSWORD = FORMAT_PASSWORD
    Schema = Schema
    Items = Items
    Response = Response
    Parameter = Parameter
    Info = Info


openapi = _OpenApi()


def _convert_responses(responses):
    if responses is None:
        return None
    if not isinstance(responses, dict):
        return responses
    converted = {}
    for status, value in responses.items():
        if isinstance(value, Response):
            converted[status] = value.to_spectacular()
        elif isinstance(value, Schema):
            converted[status] = value.to_dict()
        else:
            converted[status] = value
    return converted


def _convert_parameters(manual_parameters):
    if not manual_parameters:
        return None
    result = []
    for item in manual_parameters:
        if isinstance(item, Parameter):
            result.append(item.to_spectacular())
        elif isinstance(item, OpenApiParameter):
            result.append(item)
        else:
            result.append(item)
    return result


def _convert_request(request_body):
    if isinstance(request_body, Schema):
        return request_body.to_dict()
    return request_body


def swagger_auto_schema(
    *,
    operation_description=None,
    operation_summary=None,
    operation_id=None,
    request_body=None,
    responses=None,
    manual_parameters=None,
    query_serializer=None,
    tags=None,
    deprecated=None,
    methods=None,
    method=None,
    security=None,
    **kwargs,
):
    parameters = _convert_parameters(manual_parameters) or []
    if query_serializer is not None:
        parameters.append(query_serializer)
    extra = dict(kwargs)
    extra.pop('consumes', None)
    extra.pop('produces', None)
    extra.pop('field_inspectors', None)
    extra.pop('filter_inspectors', None)
    extra.pop('paginator_inspectors', None)
    extra.pop('auto_schema', None)
    if security is not None:
        extra.setdefault('auth', security)
    return extend_schema(
        description=operation_description,
        summary=operation_summary,
        operation_id=operation_id,
        request=_convert_request(request_body),
        responses=_convert_responses(responses),
        parameters=parameters or None,
        tags=tags,
        deprecated=deprecated,
        methods=methods or ([method] if method else None),
        **extra,
    )
