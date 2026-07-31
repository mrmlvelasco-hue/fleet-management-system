"""Guard against an external CDN reference ever reappearing.

Reported from the office network: DataTables search, Select2, and
SweetAlert2 all silently stopped working, because the browser's proxy
does SSL inspection with a certificate it doesn't trust for third-party
domains (jsdelivr, datatables.net, code.jquery.com, fonts.googleapis.com)
-- every request to those hosts failed with ERR_CERT_AUTHORITY_INVALID,
and nothing in the UI itself indicated why; it only showed up in the
browser console.

Every third-party CSS/JS/font is now served from our own static/vendor/
folder. This test scans the actual template SOURCE (not just spot-checks
one rendered page) so a future change that reintroduces a CDN link, in
any template, fails the build rather than silently reintroducing the
office-network breakage.
"""
import pathlib
import re

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Any of these appearing in a real href/src is the bug. Matched against
# actual URL attributes, not comments, so an explanatory comment
# mentioning a hostname by name (as this fix's own commit message does)
# can never trip it.
EXTERNAL_HOSTS = ("cdn.jsdelivr.net", "cdn.datatables.net",
                  "code.jquery.com", "fonts.googleapis.com",
                  "fonts.gstatic.com", "cdnjs.cloudflare.com")

ATTR_URL = re.compile(r'(?:href|src)\s*=\s*"(https?://[^"]+)"')


def _html_files():
    for path in PROJECT_ROOT.glob("app/**/*.html"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_external_cdn_reference_in_any_template():
    problems = []
    for path in _html_files():
        text = path.read_text(encoding="utf-8")
        for match in ATTR_URL.finditer(text):
            url = match.group(1)
            if any(host in url for host in EXTERNAL_HOSTS):
                line = text[:match.start()].count("\n") + 1
                problems.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{line}: {url}")

    assert not problems, (
        "External CDN reference(s) found -- these break on any network "
        "whose proxy does SSL inspection (exactly what was reported from "
        "the office). Vendor the asset into app/static/vendor/ and "
        "reference it via url_for('static', ...) instead.\n\n"
        + "\n".join(problems))


@pytest.mark.parametrize("path", [
    "vendor/bootstrap/css/bootstrap.min.css",
    "vendor/bootstrap/js/bootstrap.bundle.min.js",
    "vendor/bootstrap-icons/font/bootstrap-icons.min.css",
    "vendor/bootstrap-icons/font/fonts/bootstrap-icons.woff2",
    "vendor/datatables/css/dataTables.bootstrap5.min.css",
    "vendor/datatables/js/jquery.dataTables.min.js",
    "vendor/datatables/js/dataTables.bootstrap5.min.js",
    "vendor/select2/css/select2.min.css",
    "vendor/select2/js/select2.min.js",
    "vendor/sweetalert2/sweetalert2.min.js",
    "vendor/jquery/jquery-3.7.1.min.js",
    "vendor/qrcode-generator/qrcode.min.js",
    "vendor/fonts/fonts.css",
    "vendor/fonts/inter/inter-400.woff2",
    "vendor/fonts/space-grotesk/space-grotesk-600.woff2",
])
def test_every_vendored_asset_file_exists(path):
    full = PROJECT_ROOT / "app" / "static" / path
    assert full.exists(), f"missing vendored asset: {path}"
    assert full.stat().st_size > 0, f"vendored asset is empty: {path}"


def test_base_html_references_local_vendor_paths_not_cdn():
    base = (PROJECT_ROOT / "app/templates/layout/base.html").read_text(
        encoding="utf-8")
    for expected in ("vendor/bootstrap/css/bootstrap.min.css",
                     "vendor/jquery/jquery-3.7.1.min.js",
                     "vendor/datatables/js/jquery.dataTables.min.js",
                     "vendor/select2/js/select2.min.js",
                     "vendor/sweetalert2/sweetalert2.min.js",
                     "vendor/fonts/fonts.css"):
        assert expected in base, f"base.html no longer references {expected}"
