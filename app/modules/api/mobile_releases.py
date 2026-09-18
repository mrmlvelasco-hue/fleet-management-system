"""APK release endpoints.

Two audiences with different guards.

The FIELD APP asks "is my build current" and downloads the answer. Those
two endpoints require authentication but NO permission code: every user
of the app must be able to find out whether they are behind, and gating
that behind a permission would mean the people most likely to be running
an old build are exactly the ones who cannot discover it.

ADMINISTRATORS upload and publish, behind `mobileapp.*`. Deliberately
its own permission family rather than reusing user.* or vehicle.*:
publishing an executable that runs on company phones is its own
authority and should be grantable to precisely the people who do it.
"""
from io import BytesIO

from flask import jsonify, request, send_file

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp
from app.modules.system_admin.mobile_release_service import (
    MobileReleaseError, MobileReleaseService)


def _row(r, download=True):
    return {
        "id": r.id,
        "version_name": r.version_name,
        "version_code": r.version_code,
        "platform": r.platform,
        "status": r.status,
        "is_current": bool(r.is_current),
        "min_supported_version_code": r.min_supported_version_code,
        "release_notes": r.release_notes,
        "file_name": r.file_name,
        "file_size": r.file_size,
        "checksum_sha256": r.checksum_sha256,
        "file_purged": bool(r.file_purged),
        "released_at": r.released_at.isoformat() if r.released_at else None,
        "download_url": (f"/api/v1/mobile/releases/{r.id}/download"
                         if download else None),
    }


@bp.route("/mobile/version", methods=["GET"])
@api_auth_required()
def mobile_version(api_user):
    """What the app asks on start-up.

    Nulls rather than a 404 when nothing is published. A client that has
    to distinguish "no release configured" from "endpoint missing" by
    status code ends up guessing, and the guess it makes on a bad
    connection is usually the wrong one.
    """
    platform = request.args.get("platform", "ANDROID")
    current = MobileReleaseService().current(platform)
    if current is None:
        return jsonify({
            "version_name": None, "version_code": None,
            "min_supported_version_code": None, "release_notes": None,
            "download_url": None, "file_size": None,
        })
    return jsonify({
        "version_name": current.version_name,
        "version_code": current.version_code,
        # The client compares its own versionCode against this. Null
        # means nothing is blocked -- the common and safe state.
        "min_supported_version_code": current.min_supported_version_code,
        "release_notes": current.release_notes,
        "download_url": f"/api/v1/mobile/releases/{current.id}/download",
        "file_size": current.file_size,
        "checksum_sha256": current.checksum_sha256,
    })


@bp.route("/mobile/releases/<int:rid>/download", methods=["GET"])
@api_auth_required()
def mobile_release_download(api_user, rid):
    """The APK bytes.

    Authenticated, per the client decision of 2026-09-03: the APK is
    company property and an open URL is a link anyone can forward. The
    cost is a bootstrap problem -- the FIRST install cannot come from
    inside the app, so it still arrives by cable or shared folder. Only
    updates flow through here.
    """
    svc = MobileReleaseService()
    release = None
    for r in svc.list("ANDROID"):
        if r.id == rid:
            release = r
            break
    if release is None:
        return jsonify({"error": "not_found",
                        "message": "Release not found."}), 404

    data = svc.bytes_for(rid)
    if data is None:
        # Only the latest APK is stored, so a device pointed at an old
        # download URL lands here. Say so plainly: an empty file would
        # install and then crash, which is miserable to diagnose from
        # another building.
        return jsonify({
            "error": "not_found",
            "message": ("That build is no longer available. Only the "
                        "latest release is kept — check for an update "
                        "and download the current version."),
        }), 404

    return send_file(
        BytesIO(data), as_attachment=True,
        download_name=release.file_name or f"fms-{release.version_name}.apk",
        mimetype="application/vnd.android.package-archive")
# ============================================================
# PUBLIC FIRST INSTALL
# ============================================================
#
# This endpoint is intentionally NOT authenticated.
#
# It is used by a phone that does not have FMS installed yet.
# The phone scans a QR code, opens this URL in its browser,
# and downloads the currently published Android APK.
#
# Existing installed-app updates continue using the
# authenticated /mobile/releases/<id>/download endpoint above.
#
@bp.route("/mobile/install", methods=["GET"])
def mobile_install():
    """Download the current Android APK for first-time installation."""

    svc = MobileReleaseService()

    # Only the published/current Android build is offered.
    release = svc.current("ANDROID")

    if release is None:
        return jsonify({
            "error": "not_found",
            "message": "No published Android APK is currently available."
        }), 404

    # Retrieve the actual APK binary.
    data = svc.bytes_for(release.id)

    if data is None:
        return jsonify({
            "error": "not_found",
            "message": (
                "The current Android release is available in the "
                "release list, but its APK file is no longer available."
            ),
        }), 404

    filename = (
        release.file_name
        or f"fms-field-{release.version_name}.apk"
    )

    response = send_file(
        BytesIO(data),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.android.package-archive",
    )

    # Prevent the phone/browser from reusing an old APK from cache.
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return response

@bp.route("/mobile/releases", methods=["GET"])
@api_auth_required("mobileapp.view")
def mobile_releases(api_user):
    rows = MobileReleaseService().list(request.args.get("platform", "ANDROID"))
    return jsonify({"items": [_row(r) for r in rows], "total": len(rows)})


@bp.route("/mobile/releases", methods=["POST"])
@api_auth_required("mobileapp.create")
def mobile_release_upload(api_user):
    try:
        release = MobileReleaseService().upload(
            request.files.get("file"),
            request.form.get("version_name"),
            request.form.get("version_code"),
            platform=request.form.get("platform", "ANDROID"),
            release_notes=request.form.get("release_notes"),
            user=api_user)
    except MobileReleaseError as e:
        # The service raises sentences an administrator can act on --
        # "version code 12 is not higher than 14" rather than a
        # constraint name.
        return jsonify({"error": "validation_error",
                        "message": str(e)}), 400
    return jsonify(_row(release)), 201


@bp.route("/mobile/releases/<int:rid>/publish", methods=["POST"])
@api_auth_required("mobileapp.update")
def mobile_release_publish(api_user, rid):
    try:
        release = MobileReleaseService().publish(rid, user=api_user)
    except MobileReleaseError as e:
        return jsonify({"error": "validation_error",
                        "message": str(e)}), 400
    return jsonify(_row(release))


@bp.route("/mobile/releases/<int:rid>/minimum", methods=["POST"])
@api_auth_required("mobileapp.update")
def mobile_release_minimum(api_user, rid):
    """Raise or clear the forced-update floor.

    Its own endpoint, not a field on publish, so that blocking every old
    device is always an explicit act someone chose to perform.
    """
    p = request.get_json(silent=True) or {}
    try:
        release = MobileReleaseService().set_minimum(
            rid, p.get("min_supported_version_code"), user=api_user)
    except MobileReleaseError as e:
        return jsonify({"error": "validation_error",
                        "message": str(e)}), 400
    return jsonify(_row(release))
