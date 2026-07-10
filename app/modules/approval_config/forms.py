"""WTForms for Approval Path and Approval Matrix maintenance.

Path levels are posted as parallel arrays (level_number implied by order)
and parsed in the route — WTForms FieldList is awkward with dynamic
role-or-user rows, so the form holds only name/description and the route
assembles levels from request.form lists.
"""
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, DecimalField, DateField
from wtforms.validators import DataRequired, Length, Optional


class ApprovalPathForm(FlaskForm):
    name = StringField("Path name", validators=[DataRequired(), Length(max=120)])
    description = StringField("Description",
                              validators=[Optional(), Length(max=255)])


class ApprovalMatrixForm(FlaskForm):
    document_type_id = SelectField("Document type", coerce=int,
                                   validators=[DataRequired()])
    approval_path_id = SelectField("Approval path", coerce=int,
                                   validators=[DataRequired()])
    min_amount = DecimalField("Minimum amount", places=2,
                              validators=[Optional()])
    max_amount = DecimalField("Maximum amount", places=2,
                              validators=[Optional()])
    effective_from = DateField("Effective from", validators=[Optional()])
    effective_to = DateField("Effective to", validators=[Optional()])
