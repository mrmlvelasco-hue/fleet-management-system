"""Auth WTForms."""
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField
from wtforms.validators import DataRequired, Length, EqualTo, ValidationError


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember me")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    # Length is enforced dynamically in validate_new_password() below,
    # against the live PASSWORD_MIN_LENGTH/MAX_LENGTH System Parameters
    # (previously hardcoded here as Length(min=8), inconsistent with
    # PASSWORD_MIN_LENGTH's own default of 6) and also checks
    # PASSWORD_HISTORY_LENGTH previous passwords, which nothing
    # previously enforced at all.
    new_password = PasswordField("New password", validators=[DataRequired()])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password",
                                            message="Passwords must match")])

    def validate_new_password(self, field):
        from app.core.security.password_policy import (
            PasswordPolicyService, PasswordPolicyError)
        from flask_login import current_user
        try:
            PasswordPolicyService().validate_new_password(
                field.data, user=current_user)
        except PasswordPolicyError as e:
            raise ValidationError(str(e))
