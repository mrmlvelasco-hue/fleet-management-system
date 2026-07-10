"""Marshmallow schemas (JSON serialisation for AJAX/DataTables endpoints)."""
from marshmallow import Schema, fields


class PermissionSchema(Schema):
    id = fields.Int()
    code = fields.Str()
    module = fields.Str()
    action = fields.Str()
    description = fields.Str()


class RoleSchema(Schema):
    id = fields.Int()
    name = fields.Str()
    description = fields.Str()
    is_system_role = fields.Bool()
    permissions = fields.List(fields.Nested(PermissionSchema))


class UserSchema(Schema):
    id = fields.Int()
    username = fields.Str()
    email = fields.Str()
    first_name = fields.Str()
    last_name = fields.Str()
    full_name = fields.Str()
    is_active = fields.Bool()
    last_login_at = fields.DateTime(allow_none=True)
    roles = fields.List(fields.Nested(RoleSchema(only=("id", "name"))))
