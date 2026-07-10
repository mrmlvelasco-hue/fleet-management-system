"""WTForms for Document Type and Numbering Scheme maintenance."""
from flask_wtf import FlaskForm
from wtforms import (StringField, BooleanField, IntegerField, SelectField)
from wtforms.validators import DataRequired, Length, Optional, NumberRange


class DocumentTypeForm(FlaskForm):
    code = StringField("Code", validators=[DataRequired(), Length(max=20)])
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    description = StringField("Description",
                              validators=[Optional(), Length(max=255)])
    requires_approval = BooleanField("Requires approval")
    auto_numbering = BooleanField("Auto numbering")
    printable = BooleanField("Printable")
    mobile_available = BooleanField("Mobile available")
    attachment_allowed = BooleanField("Attachments allowed")


class NumberingSchemeForm(FlaskForm):
    document_type_id = SelectField("Document type", coerce=int,
                                   validators=[DataRequired()])
    prefix = StringField("Prefix", validators=[Optional(), Length(max=20)])
    suffix = StringField("Suffix", validators=[Optional(), Length(max=20)])
    include_year = BooleanField("Include year", default=True)
    include_month = BooleanField("Include month")
    digit_count = IntegerField("Running number digits",
                               validators=[DataRequired(),
                                           NumberRange(min=1, max=12)],
                               default=6)
    separator = StringField("Separator",
                            validators=[DataRequired(), Length(max=3)],
                            default="-")
    reset_policy = SelectField("Reset policy", choices=[
        ("NEVER", "Never"), ("YEARLY", "Yearly"), ("MONTHLY", "Monthly")],
        default="YEARLY")
