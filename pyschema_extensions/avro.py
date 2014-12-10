# Copyright (c) 2013 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
"""
Extension for generating Avro schemas from PySchema Record classes

Usage:

>>> class MyRecord(pyschema.Record):
>>>     foo = Text()
>>>     bar = Integer()
>>>
>>> [pyschema_extensions.avro.]get_schema_string(MyRecord)

'{"fields": [{"type": "string", "name": "foo"},
{"type": "long", "name": "bar"}],
"type": "record", "name": "MyRecord"}'

"""
from pyschema import core
from pyschema.types import Field, Boolean, Integer, Float
from pyschema.types import Bytes, Text, Enum, List, Map, SubRecord
try:
    import simplejson as json
except ImportError:
    import json


Boolean.avro_type_name = "boolean"
Bytes.avro_type_name = "bytes"
Text.avro_type_name = "string"
# "ENUM" is the avro 'type name' of all enums generated by pyschema
# this is pyschema convention, not avro, so it might change if
# need b
Enum.avro_type_name = "ENUM"
List.avro_type_name = "array"
Map.avro_type_name = "map"


@Float.mixin
class FloatMixin:
    @property
    def avro_type_name(self):
        if self.size <= 4:
            return 'float'
        return 'double'


@Integer.mixin
class IntegerMixin:
    @property
    def avro_type_name(self):
        if self.size <= 4:
            return 'int'
        return 'long'


@Field.mixin
class FieldMixin:
    def avro_type_schema(self, state):
        """Full type specification for the field

        I.e. the same as would go into the "type" field.
        For most field, only simplified_avro_type_schema has
        to be implemented.
        """
        simple_type = self.simplified_avro_type_schema(state)
        if self.nullable:
            # first value in union needs to be same as default
            if self.default in (None, core.NO_DEFAULT):
                return ["null", simple_type]
            else:
                return [simple_type, "null"]
        else:
            return simple_type

    def simplified_avro_type_schema(self, state):
        """The basic avro type for this field

        Not including nullability.
        """
        return self.avro_type_name

    def avro_dump(self, o):
        if o is None:
            return None
        else:
            # relying on the reference json dump behavior
            # could be a bit dangerous
            if self.nullable:
                return {self.avro_type_name: self.dump(o)}
            else:
                return self.dump(o)

    def avro_load(self, o):
        if o is None:
            return None
        else:
            if self.nullable:
                return self.load(o[self.avro_type_name])
            else:
                return self.load(o)

    def avro_default_value(self):
        return self.default


@List.mixin
class ListMixin:
    def simplified_avro_type_schema(self, state):
        return {
            "type": "array",
            "items": self.field_type.avro_type_schema(state)
        }

    def avro_dump(self, obj):
        if obj is None:
            return None
        else:
            l = [self.field_type.avro_dump(o) for o in obj]
            if self.nullable:
                return {self.avro_type_name: l}
            else:
                return l

    def avro_load(self, obj):
        if obj is None:
            return None
        else:
            if self.nullable:
                obj = obj[self.avro_type_name]
            return [
                self.field_type.avro_load(o)
                for o in obj
            ]


### `Enum` extensions
@Enum.mixin
class EnumMixin:
    def simplified_avro_type_schema(self, state):
        return {
            "type": "enum",
            "name": self.avro_type_name,
            "symbols": list(self.values)
        }


@SubRecord.mixin
class SubRecordMixin:
    def simplified_avro_type_schema(self, state):
        return get_schema_dict(self._schema, state)

    @property
    def avro_type_name(self):
        if hasattr(self._schema, '_avro_namespace_'):
            return '.'.join([self._schema._avro_namespace_,
                             self._schema._schema_name])
        else:
            return self._schema._schema_name

    def avro_dump(self, obj):
        if obj is None:
            return None
        if self.nullable:
            return {self.avro_type_name: to_json_compatible(obj)}
        else:
            return to_json_compatible(obj)

    def avro_load(self, obj):
        if obj is None:
            return None
        if self.nullable:
            return from_json_compatible(
                self._schema,
                obj[self.avro_type_name]
            )
        else:
            return from_json_compatible(
                self._schema,
                obj
            )


@Map.mixin
class MapMixin:
    def simplified_avro_type_schema(self, state):
        assert isinstance(self.key_type, Text)
        return {
            "type": "map",
            "values": self.value_type.avro_type_schema(state)
        }

    def avro_dump(self, obj):
        if obj is None:
            return None
        else:
            m = dict([(
                # using json loader for key is kind of a hack
                # since this isn't an actual type in avro (always text)
                self.key_type.dump(k),
                self.value_type.avro_dump(v)
            ) for k, v in obj.iteritems()])
            if self.nullable:
                return {self.avro_type_name: m}
            else:
                return m

    def avro_load(self, obj):
        if obj is None:
            return None
        else:
            if self.nullable:
                obj = obj[self.avro_type_name]
            m = dict([(
                # using json loader for key is kind of a hack
                # since this isn't an actual type in avro (always text)
                self.key_type.load(k),
                self.value_type.avro_load(v)
            ) for k, v in obj.iteritems()])
            return m


# Schema generation
class SchemaGeneratorState(object):
    def __init__(self):
        self.declared_records = set()


def get_schema_dict(record, state=None):
    state = state or SchemaGeneratorState()

    if hasattr(record, '_avro_namespace_'):
        namespace = record._avro_namespace_
        record_name = namespace + '.' + record._schema_name
    else:
        namespace = None
        record_name = record._schema_name

    if record_name in state.declared_records:
        return record_name
    state.declared_records.add(record_name)

    avro_record = {
        "type": "record",
        "name": record._schema_name,
    }
    if namespace:
        avro_record["namespace"] = namespace

    avro_fields = []
    for field_name, field_type in record._fields.iteritems():
        field_spec = {
            "name": field_name,
            "type": field_type.avro_type_schema(state)
        }
        if field_type.default is not core.NO_DEFAULT:
            field_spec["default"] = field_type.avro_default_value()
        avro_fields.append(field_spec)

    avro_record["fields"] = avro_fields
    return avro_record


def get_schema_string(record):
    return json.dumps(get_schema_dict(record))


def dumps(record):
    return json.dumps(to_json_compatible(record))


def to_json_compatible(record):
    dct = {}
    for name, fieldtype in record._fields.iteritems():
        value = getattr(record, name)
        dct[name] = fieldtype.avro_dump(value)
    return dct


def from_json_compatible(schema, dct):
    "Load from json-encodable"
    kwargs = {}

    for key in dct:
        field_type = schema._fields.get(key)
        if field_type is None:
            raise core.ParseError("Unexpected field encountered in line for record %s: %s" % (schema.__name__, key))
        kwargs[key] = field_type.avro_load(dct[key])

    return schema(**kwargs)


def loads(s, record_store=None, schema=None):
    return core.loads(s, record_store, schema, from_json_compatible)
