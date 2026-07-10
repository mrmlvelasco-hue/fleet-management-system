"""WTForms for user/role CRUD. Role/permission choices are populated in the
route from the DB (Select2 multi-selects)."""
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectMultipleField, BooleanField
from wtforms.validators import DataRequired, Email, Length, Optional


class UserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    first_name = StringField("First name", validators=[Optional(), Length(max=80)])
    last_name = StringField("Last name", validators=[Optional(), Length(max=80)])
    password = PasswordField("Password", validators=[Optional(), Length(min=8)])
    roles = SelectMultipleField("Roles", coerce=int)
    must_change_password = BooleanField("Require password change at next login")


class RoleForm(FlaskForm):
    name = StringField("Role name", validators=[DataRequired(), Length(max=80)])
    description = StringField("Description", validators=[Optional(), Length(max=255)])
    permissions = SelectMultipleField("Permissions", coerce=int)
