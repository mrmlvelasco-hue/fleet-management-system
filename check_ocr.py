"""Standalone OCR check — run this from the project root:

    python check_ocr.py

Deliberately imports NOTHING from the FMS app, so it works whatever
state your checkout is in. It answers one question: can this machine run
text extraction, and if not, exactly what is stopping it?

Every check is separated because the fixes are completely different:
a missing Python package, a missing program, a mis-set path and an
unrunnable binary all look identical from inside the app.
"""
import os
import shutil
import sys

WINDOWS_FALLBACKS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)

ok = True


def line(label, value):
    print(f"  {label:<24} {value}")


print("\n" + "=" * 62)
print("  FMS — OCR availability check")
print("=" * 62)

# ── 1. Python packages ──────────────────────────────────────────────
print("\n[1] Python packages (in the interpreter running this script)")
line("interpreter", sys.executable)

try:
    import pytesseract
    line("pytesseract", "installed")
except ImportError:
    ok = False
    line("pytesseract", "*** NOT INSTALLED ***")
    print("\n  FIX: pip install pytesseract Pillow")
    print("  In PyCharm, check Settings > Project > Python Interpreter is")
    print("  the .venv you are running from.")

try:
    from PIL import Image  # noqa: F401
    line("Pillow", "installed")
except ImportError:
    ok = False
    line("Pillow", "*** NOT INSTALLED ***")

# ── 2. The .env value ───────────────────────────────────────────────
print("\n[2] TESSERACT_CMD")
raw = os.getenv("TESSERACT_CMD")

if raw is None:
    line("from environment", "not set (will search PATH)")
    # .env is only loaded by the app, not by this script, so read it
    # directly -- otherwise a value that IS set would look absent here.
    if os.path.exists(".env"):
        for entry in open(".env", encoding="utf-8", errors="replace"):
            if entry.strip().startswith("TESSERACT_CMD"):
                line("found in .env", entry.strip())
                raw = entry.split("=", 1)[1].strip()
                break
else:
    line("from environment", repr(raw))

if raw:
    if "\t" in raw:
        ok = False
        line("PROBLEM", "*** contains a TAB character ***")
        print("\n  This is the double-quote trap. python-dotenv processes")
        print("  escapes inside DOUBLE quotes, so the \\t in \\tesseract.exe")
        print("  became a real tab and the path is broken.")
        print("  FIX: remove the double quotes, or use forward slashes:")
        print("       TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe")
    cleaned = raw.strip().strip('"').strip("'")
    if cleaned != raw.strip():
        line("note", "quotes stripped -> " + repr(cleaned))
    raw = cleaned

# ── 3. Locating the binary ──────────────────────────────────────────
print("\n[3] Locating tesseract.exe")
resolved = None

if raw:
    line("configured path", raw)
    if os.path.exists(raw):
        line("exists", "yes")
        resolved = raw
    else:
        ok = False
        line("exists", "*** NO FILE AT THAT PATH ***")

if resolved is None:
    on_path = shutil.which("tesseract")
    line("on PATH", on_path or "no")
    resolved = resolved or on_path

if resolved is None:
    for candidate in WINDOWS_FALLBACKS:
        if candidate and os.path.exists(candidate):
            line("found at", candidate)
            resolved = candidate
            break

if resolved is None:
    ok = False
    print("\n  Tesseract could not be found anywhere.")
    print("  It is a SEPARATE PROGRAM, not a Python package.")
    print("  Install the UB-Mannheim Windows build, then set in .env:")
    print("       TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe")

# ── 4. Can it actually run? ─────────────────────────────────────────
print("\n[4] Running it")
if resolved and "pytesseract" in sys.modules:
    try:
        pytesseract.pytesseract.tesseract_cmd = resolved
        line("version", str(pytesseract.get_tesseract_version()))
        line("result", "WORKS")
    except Exception as exc:
        ok = False
        line("result", f"*** FAILED: {exc}")
else:
    line("result", "skipped (see above)")

print("\n" + "=" * 62)
print("  RESULT:", "OCR is available." if ok else "OCR is NOT available.")
print("=" * 62 + "\n")

if ok:
    print("Extraction should work. If the Extract button still says")
    print("otherwise, restart the Flask process — .env is read once at")
    print("startup, so a value added while it was running is not picked up.\n")
