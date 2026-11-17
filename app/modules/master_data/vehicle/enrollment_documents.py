"""Documents a vehicle must arrive with at enrollment.

The client's rule: a vehicle is enrolled together with its CR, its OR,
and a front and a rear photograph. Two System Parameters hold it, both
editable under System Administration > System Parameters:

  REQUIRE_VEHICLE_ENROLLMENT_DOCUMENTS   BOOLEAN
      The switch. Off for a migration (an Excel file cannot carry a
      scan or a photo), on again afterwards.

  VEHICLE_ENROLLMENT_REQUIRED_DOCUMENTS  STRING
      WHICH documents, as a comma-separated list of attachment
      document-type codes from Lookup Maintenance (ATTACHMENT_DOC_TYPE):
      "CR,OR,PHOTO_FRONT,PHOTO_BACK".

Two parameters rather than one so that switching the rule off never
loses the list, and so the list itself is the client's to change --
adding the insurance policy is a value edit, not a code release.

Defaults when a row is MISSING: the switch reads as OFF. That is what
every install did before this rule existed, so deploying the code
alone changes nothing; the seed (`flask seed all`) adds both rows with
the rule ON, which records the client's decision where they can see
it. This is the same principle as REQUIRE_DRIVER_LICENSE -- "an install
predating the parameter behaves exactly as it did" -- arriving at the
opposite default because the prior behaviour here was permissive.

Read per call, never cached: flipping the switch has to take effect on
the very next enrollment.
"""
from app.modules.master_data.vehicle.service import InvalidVehicleDataError

SWITCH_PARAMETER = "REQUIRE_VEHICLE_ENROLLMENT_DOCUMENTS"
LIST_PARAMETER = "VEHICLE_ENROLLMENT_REQUIRED_DOCUMENTS"
DEFAULT_DOCUMENTS = "CR,OR,PHOTO_FRONT,PHOTO_BACK"

#: The name each document's file travels under in a multipart request,
#: and the key its error is reported against: "doc_" + the code.
FIELD_PREFIX = "doc_"

#: Document types whose code starts with this are photographs and must
#: be image files. The Vehicle Assignment Memo prints PHOTO_FRONT and
#: PHOTO_BACK inline; a PDF filed there would print as a broken image.
PHOTO_PREFIX = "PHOTO"


class MissingEnrollmentDocumentError(InvalidVehicleDataError):
    """One or more enrollment documents are absent or unusable.

    `fields` maps each offending input ("doc_CR") to its own message so
    a form can mark every one at once; `missing` lists the codes that
    were not supplied at all. A subclass of InvalidVehicleDataError so
    every existing caller that already reports that error (the legacy
    form, the importer's per-row handler) reports this one too.
    """

    def __init__(self, fields: dict, missing: list):
        self.fields = dict(fields)
        self.missing = list(missing)
        self.field = next(iter(self.fields))
        super().__init__(" ".join(dict.fromkeys(self.fields.values())))


def field_name(code: str) -> str:
    return f"{FIELD_PREFIX}{code}"


def _document_type_labels() -> dict:
    from app.core.attachments.attachment_service import AttachmentService
    return {row.code: row.description
            for row in AttachmentService().document_types()}


def required_documents() -> list:
    """`[{code, label, image_only, field}]` in the order the parameter
    lists them; empty when the rule is off.

    A code that is not an attachment document type is left out rather
    than enforced: no file could ever be filed under it, so enforcing
    it would make enrollment impossible over a typing mistake.
    """
    from app.modules.system_admin.services.system_parameter_service import (
        SystemParameterService)
    params = SystemParameterService()
    if not params.get(SWITCH_PARAMETER, default=False):
        return []
    raw = params.get(LIST_PARAMETER, default=DEFAULT_DOCUMENTS) or ""
    labels = _document_type_labels()
    documents, seen = [], set()
    for part in str(raw).split(","):
        code = part.strip().upper()
        if not code or code in seen or code not in labels:
            continue
        seen.add(code)
        documents.append({
            "code": code, "label": labels[code],
            "image_only": code.startswith(PHOTO_PREFIX),
            "field": field_name(code)})
    return documents


def _supplied(file) -> bool:
    return file is not None and bool(getattr(file, "filename", ""))


def supplied_documents(documents) -> dict:
    """Only the entries that actually carry a file, keyed by upper-case
    code. An untouched file input arrives as an empty FileStorage."""
    return {str(code).strip().upper(): file
            for code, file in (documents or {}).items() if _supplied(file)}


def check_documents(documents) -> dict:
    """Validate what was supplied against what is required, WITHOUT
    writing anything. Returns the usable documents `{code: file}`.

    Called before the vehicle row is created, so a missing or unusable
    file never leaves a vehicle behind without the documents the rule
    exists to guarantee. Raises MissingEnrollmentDocumentError naming
    every problem at once, not just the first.
    """
    from app.core.attachments.attachment_service import (
        AttachmentError, AttachmentService)

    supplied = supplied_documents(documents)
    required = required_documents()
    labels = _document_type_labels()
    attachments = AttachmentService()

    problems, missing = {}, []
    for doc in required:
        if doc["code"] not in supplied:
            missing.append(doc["code"])
            problems[doc["field"]] = (
                f"{doc['label']} is required to enroll a vehicle.")

    for code, file in supplied.items():
        label = labels.get(code, code)
        try:
            attachments.check(file, document_type=code)
        except AttachmentError as exc:
            problems[field_name(code)] = f"{label}: {exc}"
            continue
        if code.startswith(PHOTO_PREFIX) and not (
                file.content_type or "").startswith("image/"):
            problems[field_name(code)] = (
                f"{label} must be an image file (JPG or PNG).")

    if problems:
        raise MissingEnrollmentDocumentError(problems, missing)
    return supplied


def attach_documents(vehicle, documents: dict, user=None) -> list:
    """File each checked document against the new vehicle under its own
    document type. Returns the Attachment rows."""
    from app.core.attachments.attachment_service import AttachmentService
    attachments = AttachmentService()
    return [attachments.upload(file, reference_table="vehicles",
                               reference_id=vehicle.id, user=user,
                               document_type=code)
            for code, file in documents.items()]
