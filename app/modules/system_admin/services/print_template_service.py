"""Print templates: storage, editing, and token resolution.

The body of a printed document (currently the Oath of Undertaking that
accompanies a Vehicle Assignment Memo) is company policy text, so it is
stored and edited rather than compiled in. Tokens let one stored body
serve every vehicle and assignee.

Token behaviour is deliberately forgiving in one direction and strict in
the other:

  * An UNKNOWN token is left exactly as written. A typo in a hand-edited
    clause then shows up as `{ASIGNEE_NAME}` on the paper, which someone
    will notice and fix. Silently deleting it would leave a blank in a
    signed undertaking -- a far worse failure, because nobody can see
    that anything is missing.
  * A KNOWN token with no value renders as an em dash, never the word
    "None", which would read as a deliberate statement on a legal form.
"""
from app.extensions import db
from app.modules.system_admin.models import PrintTemplate

DASH = "\u2014"

#: Built-in defaults, used to seed the table AND as a fallback when the
#: row is absent -- a fresh install, or a database seeded before this
#: feature existed, must still be able to print the undertaking. The
#: same pattern LookupService uses for its module-level registry.
PRINT_TEMPLATE_DEFAULTS = {
    "OATH_OF_UNDERTAKING": (
        "Oath of Undertaking",
        "Signed by the assignee and issued with every Vehicle "
        "Assignment Memo.",
        """<p>As an assignee of a company vehicle with VAM no. <strong>{VAM_NO}</strong> for
<strong>{VEHICLE_MODEL} / {VEHICLE_YEAR}</strong>, with Plate No.
<strong>{PLATE_NO}</strong>, I hereby agree to abide with the following company
regulations:</p>

<ol>
<li>The company vehicle is assigned to me, <strong>{ASSIGNEE_NAME}</strong>.</li>
<li>Responsible for the daily check-up and upkeep of the vehicle to ensure that it is roadworthy and projects a good image of the company.</li>
<li>Responsible for the PM service &amp; corrective repair scheduling, implementation and pull-out of the vehicle from the authorized auto shop, in coordination with the Fleet Services representative, covered by a Work Order and an approved Purchase Request. All replaced parts must be turned over to Fleet Services for proper disposal.</li>
<li>For a vehicle with a gasoline engine, the assignee must only use unleaded fuel with RON 91 or higher.</li>
<li>Must not lease, sell, transfer, mortgage, pledge or loan the vehicle.</li>
<li>Assigned tools such as jack and tire wrench must not be removed from the vehicle. Any tool lost or unaccounted for must be replaced by the assignee or charged to their account.</li>
<li>Replacement of high-value items such as radiator, starter, alternator, compressor or shock absorber not included in the approved Purchase Request must have prior approval from the Fleet Manager.</li>
<li>Must obtain a copy of the invoice or receipt for any servicing or repair performed and forward it to Fleet Services for recording of activities and expenses, and for payment processing. Invoices must be addressed to {COMPANY_NAME}.</li>
<li>Must ensure that no part, accessory or logo is removed, changed or modified, and that the body colour is not altered in any way affecting its standard design, performance or original colour.</li>
<li>Installation of accessories that are not original parts and that may damage or alter related parts requires the approval of Fleet Services.</li>
<li>Delegated use of the vehicle is allowed only for official use by a company-authorized driver and must be covered by a Vehicle Trip Ticket signed by the assignee. Lending the vehicle to any other person is prohibited.</li>
<li>Must read and be aware of the Vehicular Accident Policy and its implementing guidelines and procedures.</li>
<li>Any accident or damage involving the vehicle, or loss of any part, accessory or regulated safety item, must be reported within 48 hours to the Fleet Manager, HR Manager and the immediate superior. Failure to report is subject to disciplinary action.</li>
<li>The company reserves the right to charge the assignee in whole or in part for repair costs arising from an accident caused by their negligence, or where the unit was driven by an unauthorized person. No repair work may be done without prior approval of Fleet Services.</li>
<li>A PM service vehicle will only be used for PM servicing of assigned company-owned vehicles.</li>
<li>Car washing of the assigned unit is allowed during its PM servicing; washing done between PM services is for the assignee's account.</li>
<li>An assignee resigning from the company must provide a copy of the resignation letter to Fleet Services and personally surrender the vehicle at the end of their last working day. The unit will be inspected prior to clearance.</li>
<li>The company reserves the right to reassign this vehicle to any eligible employee during its serviceable life.</li>
<li>Must hold a valid driver's licence with the correct restriction code and strictly observe all traffic rules and government requirements when operating the company vehicle.</li>
<li>Must ensure photocopies of the Certificate of Registration (CR No. {CR_NO}) and Official Receipt (OR No. {OR_NO}) of the updated vehicle registration are kept inside the vehicle at all times.</li>
<li>Responsible for the installation of LTO stickers on the upper right portion of the windshield and on the licence plates, or as otherwise required by law.</li>
<li>Must read the Owner's Manual and Warranty Booklet and follow all instructions therein regarding proper operation and maintenance of the vehicle.</li>
</ol>""",
        "A4",
    ),
}


class InvalidPaperSizeError(ValueError):
    """Raised when a save would store a paper size the print CSS has no
    @page rule for -- caught at the service rather than producing a
    document that silently prints at the wrong size."""


def _v(value):
    """A token's rendered value: never None, never the literal 'None'."""
    if value is None:
        return DASH
    text = str(value).strip()
    return text if text else DASH


class PrintTemplateService:

    #: token -> what it means, published for the edit screen so the
    #: fleet manager is not guessing at names.
    TOKENS = (
        ("{ASSIGNEE_NAME}", "Full name of the person the vehicle is assigned to"),
        ("{ASSIGNEE_POSITION}", "Assignee's position / job title"),
        ("{EMPLOYEE_NO}", "Assignee's employee number"),
        ("{ASSIGNEE_ADDRESS}", "Assignee's complete address"),
        ("{ASSIGNEE_PHONE}", "Assignee's contact number"),
        ("{VEHICLE_MODEL}", "Brand and model, e.g. Toyota Avanza"),
        ("{VEHICLE_YEAR}", "Model year"),
        ("{PLATE_NO}", "Plate number (falls back to conduction number)"),
        ("{CONDUCTION_NO}", "Conduction number"),
        ("{BODY_COLOR}", "Body colour"),
        ("{ENGINE_NO}", "Engine number"),
        ("{CHASSIS_NO}", "Chassis number"),
        ("{FAR_NO}", "FAR number"),
        ("{KM_READING}", "Odometer reading at assignment"),
        ("{BRANCH}", "Branch / plant the vehicle belongs to"),
        ("{VAM_NO}", "Document number of this assignment memo"),
        ("{WO_NO}", "Work order number"),
        ("{CR_NO}", "Certificate of Registration number (latest registration)"),
        ("{OR_NO}", "Official Receipt number (latest registration)"),
        ("{DATE_ISSUED}", "Date the vehicle was issued"),
        ("{COMPANY_NAME}", "Company name from the Company Profile"),
    )

    def available_tokens(self):
        return list(self.TOKENS)

    # ── storage ─────────────────────────────────────────────────────

    def get(self, code):
        """The stored template, or a transient default, or None.

        Falls back to PRINT_TEMPLATE_DEFAULTS so a printout works before
        anyone has run the seeder -- the object returned in that case is
        NOT attached to the session, so nothing is written to the
        database as a side effect of printing. An unrecognised code
        still returns None.

        Never raises: a printout is a live operational document and must
        not 500 because a row was deleted.
        """
        try:
            row = PrintTemplate.query.filter_by(code=code,
                                               is_active=True).first()
            if row is not None:
                return row
        except Exception:
            pass
        default = PRINT_TEMPLATE_DEFAULTS.get(code)
        if default is None:
            return None
        name, desc, body, paper = default
        return PrintTemplate(code=code, name=name, description=desc,
                            body_html=body, paper_size=paper,
                            orientation="PORTRAIT")

    def list(self):
        return (PrintTemplate.query.filter_by(is_active=True)
                .order_by(PrintTemplate.name).all())

    def update(self, template_id, body_html=None, paper_size=None,
               orientation=None, name=None):
        tpl = db.session.get(PrintTemplate, template_id)
        if tpl is None:
            return None
        if tpl not in db.session:
            db.session.add(tpl)
        if paper_size is not None:
            paper_size = str(paper_size).strip().upper()
            if paper_size not in PrintTemplate.PAPER_SIZES:
                raise InvalidPaperSizeError(
                    f"{paper_size!r} is not a paper size this system can "
                    f"print. Choose one of: "
                    f"{', '.join(PrintTemplate.PAPER_SIZES)}.")
            tpl.paper_size = paper_size
        if orientation is not None:
            orientation = str(orientation).strip().upper()
            tpl.orientation = ("LANDSCAPE" if orientation == "LANDSCAPE"
                              else "PORTRAIT")
        if body_html is not None:
            tpl.body_html = body_html
        if name:
            tpl.name = name
        db.session.commit()
        return tpl

    # ── rendering ───────────────────────────────────────────────────

    def context_for(self, order):
        """Token values for a Maintenance Order being printed.

        Every lookup is guarded: this runs against real records where a
        driver, vehicle or registration may legitimately be absent, and
        the printout has to degrade to a dash rather than fail.
        """
        v = getattr(order, "vehicle", None)
        d = getattr(order, "driver", None)

        reg = None
        try:
            from app.modules.transactions.vehicle_registration.models import (
                VehicleRegistration)
            if v is not None:
                reg = (VehicleRegistration.query
                      .filter_by(vehicle_id=v.id, status="COMPLETED")
                      .filter(VehicleRegistration.expiry_date.isnot(None))
                      .order_by(VehicleRegistration.expiry_date.desc())
                      .first())
        except Exception:
            reg = None

        company_name = None
        try:
            from app.modules.system_admin.services.company_service import (
                CompanyProfileService)
            profile = CompanyProfileService().get()
            company_name = getattr(profile, "company_name", None)
        except Exception:
            pass

        model = None
        if v is not None:
            model = " ".join(x for x in (v.brand, v.model) if x) or None

        km = getattr(order, "odometer_at_service", None)
        if km is None and v is not None:
            km = v.current_odometer
        if km is not None:
            try:
                km = f"{int(km):,}"
            except (TypeError, ValueError):
                pass

        return {
            "{ASSIGNEE_NAME}": _v(getattr(d, "full_name", None)),
            "{ASSIGNEE_POSITION}": _v(getattr(d, "position", None)
                                     or getattr(d, "job_title", None)),
            "{EMPLOYEE_NO}": _v(getattr(d, "employee_number", None)),
            "{ASSIGNEE_ADDRESS}": _v(getattr(d, "complete_address", None)),
            "{ASSIGNEE_PHONE}": _v(getattr(d, "phone", None)),
            "{VEHICLE_MODEL}": _v(model),
            "{VEHICLE_YEAR}": _v(getattr(v, "year", None)),
            # Plate falls back to conduction: a brand-new unit carries a
            # conduction number before LTO issues a plate, and the
            # undertaking still has to identify the vehicle.
            "{PLATE_NO}": _v(getattr(v, "plate_number", None)
                            or getattr(v, "conduction_number", None)),
            "{CONDUCTION_NO}": _v(getattr(v, "conduction_number", None)),
            "{BODY_COLOR}": _v(getattr(v, "color", None)),
            "{ENGINE_NO}": _v(getattr(v, "engine_number", None)),
            "{CHASSIS_NO}": _v(getattr(v, "chassis_number", None)),
            "{FAR_NO}": _v(getattr(v, "far_number", None)),
            "{KM_READING}": _v(km),
            "{BRANCH}": _v(getattr(getattr(v, "branch", None), "name", None)),
            "{VAM_NO}": _v(getattr(order, "document_number", None)),
            "{WO_NO}": _v(getattr(order, "document_number", None)),
            "{CR_NO}": _v(getattr(reg, "cr_number", None)
                         or getattr(v, "cr_number", None)),
            "{OR_NO}": _v(getattr(reg, "or_number", None)),
            "{DATE_ISSUED}": _v(getattr(order, "completed_date", None)
                               or getattr(order, "scheduled_date", None)),
            "{COMPANY_NAME}": _v(company_name),
        }

    def render(self, body_html, order=None, context=None):
        """Substitute known tokens. Unknown ones are left untouched --
        see the module docstring for why that is the safer direction."""
        if not body_html:
            return ""
        values = context if context is not None else self.context_for(order)
        out = body_html
        for token, value in values.items():
            out = out.replace(token, str(value))
        return out

    def render_template(self, code, order=None):
        tpl = self.get(code)
        if tpl is None:
            return None
        return self.render(tpl.body_html, order=order)
