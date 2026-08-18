"""Reading text off a scanned document.

Preprocessing is BLACK AND WHITE, not greyscale, and that is the single
biggest factor in whether a Certificate of Registration reads at all.

Measured on a real client scan whose CR fields are highlighted in
yellow:

    greyscale             GEELY=no   COOLRAY=no   SUV=yes  2022=yes
    black & white         GEELY=YES  COOLRAY=YES  SUV=yes  2022=yes

Yellow highlighting over black text has poor luminance separation, so
converting to greyscale flattens the contrast and the characters
dissolve. Thresholding to pure black and white recovers them, because
the highlight goes to white and the ink goes to black. The same applies
to the red CR number and the blue LTO stamps that overlay the form.

Several thresholds and scales are tried because no single combination
suits every scan -- a faint photocopy and a crisp digital scan need
different cut points. The readings are parsed separately and voted on
rather than concatenated; see cr_parser.parse_many for why.

Locating the tesseract binary
-----------------------------
This has to work in two very different places: a Linux container where
tesseract is on PATH at /usr/bin/tesseract, and a Windows development
machine where it is installed under Program Files and usually is not on
PATH at all.

So the search order is: TESSERACT_CMD if set, then PATH, then the usual
Windows install locations. Hardcoding a Windows path as the default
would break the container, and requiring the variable would make local
setup needlessly fiddly.

Nothing here raises. When OCR cannot run, diagnose() explains WHY --
"pytesseract is not installed in this environment" and "the binary is
not where TESSERACT_CMD points" are completely different problems with
completely different fixes, and collapsing both into a bare False
wasted an afternoon.
"""
import io
import os
import platform
import shutil
import sys

THRESHOLDS = (110, 128, 150)
SCALES = (3, 4, 6)
PSM_MODES = (6, 4)

#: Where tesseract usually lands on Windows when installed with the
#: standard installer. Checked only if it is neither configured nor on
#: PATH.
_WINDOWS_FALLBACKS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)


def _configured_cmd():
    """TESSERACT_CMD from the environment, cleaned up.

    Stripped of surrounding quotes because a path with spaces invites
    them, and pytesseract passes the value straight to the OS -- a
    literal quote character makes the executable "not found" in a way
    that reads as a missing install.
    """
    raw = os.getenv("TESSERACT_CMD", "").strip()
    if not raw:
        return None
    return raw.strip('"').strip("'")


def resolve_tesseract():
    """The binary to use, or None. Never raises."""
    configured = _configured_cmd()
    if configured:
        # Returned even if it does not exist, so diagnose() can say
        # "configured but not found there" rather than silently falling
        # back and leaving someone puzzling over an ignored setting.
        return configured
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    for candidate in _WINDOWS_FALLBACKS:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def diagnose():
    """Why OCR can or cannot run here.

    Returns a dict with `ok`, the resolved command, and a `reason`
    written for whoever has to fix it.
    """
    # WHICH Python is asking matters as much as the answer. A dev
    # machine typically has several environments in play at once -- a
    # PyCharm venv, a system Python, and a Linux container -- and
    # "pytesseract is not installed" is meaningless until you know which
    # of them is reporting it. Naming the interpreter turns a guessing
    # game into a one-line answer.
    info = {"ok": False, "command": None, "version": None,
            "source": None, "reason": "",
            "interpreter": sys.executable,
            "platform": platform.system(),
            "in_container": os.path.exists("/.dockerenv")}

    try:
        import pytesseract  # noqa: F401
    except ImportError:
        where = ("the Docker container" if info["in_container"]
                else f"{info['platform']} interpreter {sys.executable}")
        info["reason"] = (
            f"The pytesseract package is not installed in {where}. "
            + ("Rebuild the image so it picks up requirements.txt: "
               "docker compose build web worker && docker compose up -d"
               if info["in_container"] else
               "Run: pip install -r requirements.txt — and check this "
               "is the same interpreter your app runs under, not just "
               "the one in your terminal."))
        return info

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        info["reason"] = ("Pillow is not installed. Run: "
                         "pip install -r requirements.txt")
        return info

    cmd = resolve_tesseract()
    info["command"] = cmd
    configured = _configured_cmd()
    info["source"] = ("TESSERACT_CMD" if configured
                     else "PATH" if cmd and shutil.which("tesseract") == cmd
                     else "default install location" if cmd else None)

    if not cmd:
        info["reason"] = (
            "The tesseract program could not be found. It is a separate "
            "install, not a Python package. On Windows install it from "
            "the UB-Mannheim build and set TESSERACT_CMD in .env to the "
            "full path of tesseract.exe; in Docker it comes from the "
            "image.")
        return info

    if configured and not os.path.exists(cmd):
        info["reason"] = (
            f"TESSERACT_CMD is set to {cmd!r} but there is no file there. "
            f"Check the path, and note that if you wrapped it in DOUBLE "
            f"quotes in .env, the \\t in \\tesseract.exe is read as a tab "
            f"character \u2014 use no quotes, single quotes, or forward "
            f"slashes.")
        return info

    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = cmd
        info["version"] = str(pytesseract.get_tesseract_version())
        info["ok"] = True
        info["reason"] = "OCR is available."
    except Exception as exc:
        info["reason"] = (
            f"Found {cmd!r} but running it failed: {exc}. If this is "
            f"Windows, check the file is the real tesseract.exe and that "
            f"your user can execute it.")
    return info


def ocr_available() -> bool:
    return diagnose()["ok"]


def read_passes(content: bytes, crop=None):
    """Several independent readings of the same image.

    Returns a list of raw text. Never raises -- an unreadable file
    yields no readings, and the caller falls back to manual entry.
    """
    try:
        from PIL import Image, ImageOps, ImageFilter
        import pytesseract
    except ImportError:
        return []

    cmd = resolve_tesseract()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    try:
        im = Image.open(io.BytesIO(content))
        if crop:
            x0, y0, x1, y1 = crop
            im = im.crop((int(im.width * x0), int(im.height * y0),
                         int(im.width * x1), int(im.height * y1)))
        base = im.convert("L")
    except Exception:
        return []

    passes = []
    for scale in SCALES:
        try:
            big = base.resize((base.width * scale, base.height * scale),
                             Image.LANCZOS)
            big = ImageOps.autocontrast(big)
            big = big.filter(ImageFilter.SHARPEN)
        except Exception:
            continue
        for threshold in THRESHOLDS:
            try:
                mono = big.point(lambda v, t=threshold: 0 if v < t else 255)
                for psm in PSM_MODES:
                    passes.append(pytesseract.image_to_string(
                        mono, config=f"--psm {psm}"))
            except Exception:
                continue
    return passes
