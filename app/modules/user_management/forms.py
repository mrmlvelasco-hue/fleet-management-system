"""WTForms for user/role CRUD. Role/permission choices are populated in the
route from the DB (Select2 multi-selects)."""
from flask_wtf import FlaskForm
from wtforms import (StringField, PasswordField, SelectMultipleField,
                     BooleanField, SelectField)
from wtforms.validators import DataRequired, Email, Length, Optional, ValidationError


class UserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    first_name = StringField("First name", validators=[Optional(), Length(max=80)])
    last_name = StringField("Last name", validators=[Optional(), Length(max=80)])
    employee_id = StringField("Employee ID", validators=[Optional(), Length(max=40)])
    branch_id = SelectField("Branch", coerce=int, validators=[Optional()])
    department_id = SelectField("Department", coerce=int, validators=[Optional()])
    # Length is enforced dynamically in validate_password() below, against
    # the live PASSWORD_MIN_LENGTH/MAX_LENGTH System Parameters, rather
    # than a hardcoded Length(min=8) here -- which is what this used to
    # be, and which didn't even match PASSWORD_MIN_LENGTH's own default
    # of 6, so this form and the configured policy actively disagreed
    # with each other.
    password = PasswordField("Password", validators=[Optional()])
    roles = SelectMultipleField("Roles", coerce=int)
    must_change_password = BooleanField("Require password change at next login")

    def validate_password(self, field):
        if not field.data:
            return
        from app.core.security.password_policy import (
            PasswordPolicyService, PasswordPolicyError)
        try:
            # A brand-new user (no id assigned to this form / created
            # fresh) has no password history to check against yet --
            # history enforcement only makes sense for an existing
            # account changing its password.
            existing_user = getattr(self, "_editing_user", None)
            PasswordPolicyService().validate_new_password(
                field.data, user=existing_user)
        except PasswordPolicyError as e:
            raise ValidationError(str(e))


class RoleForm(FlaskForm):
    name = StringField("Role name", validators=[DataRequired(), Length(max=80)])
    description = StringField("Description", validators=[Optional(), Length(max=255)])
    permissions = SelectMultipleField("Permissions", coerce=int)
