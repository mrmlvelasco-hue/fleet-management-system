"""The fill-in workbook for Import PM Task List.

Three sheets, the first two exactly as scripts/import_pm_task_list.py
reads them:

  "PM Task List"   one row per PM package (the importer always reads
                   the FIRST sheet, whatever it is called)
  "Scope Details"  one row per checklist line, joined to its package
                   on Make + Model + PM_CD + Task_CD
  "Reference"      what each column means, the categories the importer
                   accepts, and every interval code it understands

The interval codes and categories are passed in by the caller rather
than imported here: they live in scripts/ (the importer's own module),
which is not on the import path of the app package -- see
`_pm_task_list_module` in app/modules/api/pm_config.py. Taking them as
arguments keeps this sheet in step with the importer by construction:
a code added to the reference table appears on the next download.
"""
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

MAIN_SHEET = "PM Task List"
SCOPE_SHEET = "Scope Details"
EXAMPLE_TASK_CD = "EXAMPLE-0001"
FILE_NAME = "PM_Task_List_Import_Template.xlsx"

# (column, required, note)
MAIN_COLUMNS = [
    ("Make", True, "Vehicle brand, spelled as in Vehicle Brands (e.g. Mitsubishi)."),
    ("Model", True, "Vehicle model, spelled as in Vehicle Models (e.g. Strada)."),
    ("Description", True,
     "Name of this package, e.g. 'Vehicle Preventive Maintenance Mitsubishi "
     "Strada 5,000 km'."),
    ("Task_CD", True,
     "PMS Profile code. Every package of one profile shares it. Must be NEW: "
     "a Task_CD that is already a PMS Profile in the system is skipped."),
    ("PM_CD", True,
     "Package code, unique inside its Task_CD. Links the package to its "
     "checklist lines on the Scope Details sheet."),
    ("WorkDescription", True,
     "Work order description. Put the odometer milestone first, e.g. "
     "'5,000 km' -- the number before 'km' decides which package a vehicle "
     "is due for."),
    ("Scope", False,
     "Leave as 'See Sheet Scope Details'. The checklist itself goes on the "
     "Scope Details sheet."),
    ("KM Reading", False,
     "Kilometre interval CODE (see 'Kilometre interval codes' below). Leave "
     "blank or 0 for a calendar-only package."),
    ("KMDesc", False, "Free text for your own reading. Not imported."),
    ("Calendar", False,
     "Calendar interval CODE (see 'Calendar interval codes' below). Leave "
     "blank or 0 for a kilometre-only package."),
    ("CALDesc", False, "Free text for your own reading. Not imported."),
    ("Hourly", False, "Engine-hours interval: a code such as 500HRS, or 0."),
    ("HourDesc", False, "Free text for your own reading. Not imported."),
    ("PMDescription", True,
     "Category. Must be exactly one of the categories listed below."),
    ("Planned_CD", False, "Free text for your own reading. Not imported."),
    ("Planned", False, "Free text for your own reading. Not imported."),
    ("Sort", True,
     "Order of the package inside its profile: 1, 2, 3 ..."),
]

SCOPE_COLUMNS = [
    ("Make", True, "Same as the package's Make on the first sheet."),
    ("Model", True, "Same as the package's Model on the first sheet."),
    ("PM_CD", True, "Same as the package's PM_CD on the first sheet."),
    ("Task_CD", True, "Same as the package's Task_CD on the first sheet."),
    ("Line_No", True, "Order of the line inside the checklist: 1, 2, 3 ..."),
    ("Scope_Detail", True, "One checklist step (up to 255 characters)."),
]

_HEADER_FILL = PatternFill("solid", fgColor="1F3B4D")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_EXAMPLE_FONT = Font(italic=True, color="888888")
_NOTE_FONT = Font(italic=True, bold=True, color="C00000")
_SECTION_FONT = Font(bold=True)

_UNIT_WORDS = {"M": "month", "Y": "year", "W": "week", "Q": "quarter"}


def _describe(value, unit):
    """'Every 5,000 km' / 'Every 3 months' / 'Every 500 hours'."""
    number = (int(value) if Decimal(str(value)) == int(value)
              else Decimal(str(value)).normalize())
    if unit == "K":
        return f"Every {number:,} km"
    if unit == "H":
        return f"Every {number:,} hours"
    word = _UNIT_WORDS[unit]
    return f"Every {number} {word}{'' if number == 1 else 's'}"


_UNIT_WEIGHT = {"K": 1, "H": 1, "W": 7, "M": 30, "Q": 90, "Y": 365}


def _sorted_codes(frequency_reference, units):
    """`(code, description)` for the given units, shortest interval
    first (a week before a month before a year)."""
    rows = [(Decimal(str(v)) * _UNIT_WEIGHT[u], code, v, u)
            for code, (v, u) in frequency_reference.items() if u in units]
    return [(code, _describe(v, u)) for _key, code, v, u in sorted(rows)]


def _write_header(ws, columns):
    ws.append([name for name, _req, _note in columns])
    for col, (name, _req, _note) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        ws.column_dimensions[cell.column_letter].width = max(16, len(name) + 4)
    ws.freeze_panes = "A2"


def build_pm_task_list_template(*, frequency_reference, categories,
                                excluded_categories=()) -> bytes:
    """The workbook as bytes.

    frequency_reference: {code: (value, unit)} -- the importer's own
        table. categories: the PMDescription values it accepts.
        excluded_categories: values it deliberately skips.
    """
    wb = Workbook()

    # ── Sheet 1: packages ──────────────────────────────────────────────
    main = wb.active
    main.title = MAIN_SHEET
    _write_header(main, MAIN_COLUMNS)
    category = "Vehicle Preventive Maintenance"
    examples = [
        ("000-000-00001", "1,000 km", "1KM", "First 1,000 km",
         "1MTH", "Every month", 1),
        ("000-000-00002", "5,000 km", "5KMS", "Every 5,000 km",
         "3MTH", "Every 3 months", 2),
    ]
    for pm_cd, work, km_code, km_desc, cal_code, cal_desc, sort in examples:
        main.append([
            "Mitsubishi", "Strada",
            f"{category} Mitsubishi Strada {work}", EXAMPLE_TASK_CD, pm_cd,
            work, f"See Sheet {SCOPE_SHEET}", km_code, km_desc, cal_code,
            cal_desc, 0, "NULL", category, "S02", category, sort])
    for row in (2, 3):
        for col in range(1, len(MAIN_COLUMNS) + 1):
            main.cell(row=row, column=col).font = _EXAMPLE_FONT
    main.cell(row=4, column=1).value = (
        "^ Example rows. Replace them with your own packages. Rows whose "
        "Task_CD starts with EXAMPLE are never imported.")
    main.cell(row=4, column=1).font = _NOTE_FONT
    main.column_dimensions["C"].width = 58
    main.column_dimensions["G"].width = 28

    # ── Sheet 2: checklist lines ───────────────────────────────────────
    scope = wb.create_sheet(SCOPE_SHEET)
    _write_header(scope, SCOPE_COLUMNS)
    example_lines = {
        "000-000-00001": [
            "Perform first 1,000 km PMS.",
            "Check engine oil level and look for leaks.",
            "Check tire pressure and condition.",
        ],
        "000-000-00002": [
            "Change engine oil and oil filter.",
            "Inspect brake pads and brake fluid level.",
            "Rotate tires.",
        ],
    }
    for pm_cd, lines in example_lines.items():
        for line_no, text in enumerate(lines, start=1):
            scope.append(["Mitsubishi", "Strada", pm_cd, EXAMPLE_TASK_CD,
                          line_no, text])
    for row in scope.iter_rows(min_row=2):
        for cell in row:
            cell.font = _EXAMPLE_FONT
    scope.column_dimensions["F"].width = 70

    # ── Sheet 3: reference ─────────────────────────────────────────────
    ref = wb.create_sheet("Reference")
    ref.column_dimensions["A"].width = 26
    ref.column_dimensions["B"].width = 12
    ref.column_dimensions["C"].width = 100

    def section(title):
        if ref.max_row > 1 or ref.cell(1, 1).value is not None:
            ref.append([])
        ref.append([title])
        ref.cell(ref.max_row, 1).font = _SECTION_FONT

    def table(columns):
        ref.append(["Column", "Required", "Notes"])
        for cell in ref[ref.max_row]:
            cell.font = _SECTION_FONT
        for name, required, note in columns:
            ref.append([name, "YES" if required else "optional", note])
            ref.cell(ref.max_row, 3).alignment = Alignment(wrap_text=True,
                                                           vertical="top")

    section("How this file is read")
    for line in (
        f"1. The FIRST sheet ('{MAIN_SHEET}') has one row per PM package.",
        f"2. The '{SCOPE_SHEET}' sheet has one row per checklist line.",
        "3. A checklist line belongs to the package with the same Make, "
        "Model, PM_CD and Task_CD. If you change the Model on the first "
        "sheet, change it on the checklist sheet too -- otherwise the "
        "package imports with no checklist.",
        "4. Upload the file and read the Review step before importing. "
        "Nothing is saved until you confirm.",
        "5. To give one model the same PMS as another, use 'Copy PMS to "
        "another model' on the PM Templates screen instead of editing a "
        "file.",
    ):
        ref.append([line])

    section(f"Sheet 1 -- {MAIN_SHEET}")
    table(MAIN_COLUMNS)

    section(f"Sheet 2 -- {SCOPE_SHEET}")
    table(SCOPE_COLUMNS)

    section("Categories (PMDescription) -- use exactly as written")
    for name in categories:
        ref.append([name])
    for name in excluded_categories:
        ref.append([name, "", "Skipped on import (handled by its own module)."])

    section("Kilometre interval codes (KM Reading)")
    for code, description in _sorted_codes(frequency_reference, {"K"}):
        ref.append([code, "", description])

    section("Calendar interval codes (Calendar)")
    for code, description in _sorted_codes(frequency_reference,
                                           {"W", "M", "Q", "Y"}):
        ref.append([code, "", description])

    hours = _sorted_codes(frequency_reference, {"H"})
    if hours:
        section("Engine-hours interval codes (Hourly)")
        for code, description in hours:
            ref.append([code, "", description])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
