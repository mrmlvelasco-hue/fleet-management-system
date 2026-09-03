"""Publishing builds of the field app.

The single writer for mobile_app_releases, for the same reason
VehicleAssignmentService is: two invariants here are enforced in code
rather than by constraints (one current release per platform; a
monotonic version_code), and an invariant is only as good as the
funnelling.
"""
import hashlib
from datetime import datetime, timezone

from app.extensions import db
from app.modules.system_admin.mobile_release_models import (
    MobileAppRelease, MobileAppReleaseFile)

#: 150 MB. Generous next to a ~20 MB release, because the cost of being
#: wrong is asymmetric: a limit that is too tight blocks a legitimate
#: build at the worst moment, while one that is too loose costs disk on
#: a table only administrators can write to.
MAX_APK_MB = 150

ALLOWED_EXTENSIONS = {"apk"}


class MobileReleaseError(Exception):
    """Something an administrator can read and act on."""


class MobileReleaseService:

    # ── writes ──────────────────────────────────────────────────────

    def upload(self, file, version_name, version_code, platform="ANDROID",
               release_notes=None, user=None):
        """Store a build as DRAFT.

        Uploading is deliberately NOT releasing. A build can be put in
        place, its checksum checked and its notes written before any
        phone is told it exists.
        """
        if file is None or not getattr(file, "filename", ""):
            raise MobileReleaseError("Choose an APK file to upload.")

        name = file.filename
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise MobileReleaseError(
                "That file is not an APK. Only .apk files can be published "
                "as a mobile release.")

        if not version_name or not str(version_name).strip():
            raise MobileReleaseError("Version name is required (e.g. 1.4.0).")
        try:
            version_code = int(version_code)
        except (TypeError, ValueError):
            raise MobileReleaseError(
                "Version code must be a whole number matching the "
                "versionCode in the Android build.")

        # Strictly greater than every existing code, not merely greater
        # than the current release. Re-uploading a number already used
        # by an archived build would make two rows indistinguishable to
        # a phone, which compares codes and nothing else.
        highest = (db.session.query(db.func.max(MobileAppRelease.version_code))
                   .filter(MobileAppRelease.platform == platform).scalar())
        if highest is not None and version_code <= highest:
            raise MobileReleaseError(
                f"Version code {version_code} is not higher than the "
                f"highest already uploaded ({highest}). Android will not "
                f"treat it as an update.")

        data = file.read()
        if not data:
            raise MobileReleaseError("That file is empty.")
        if len(data) > MAX_APK_MB * 1024 * 1024:
            raise MobileReleaseError(
                f"That file is larger than the {MAX_APK_MB} MB limit.")

        release = MobileAppRelease(
            version_name=str(version_name).strip(),
            version_code=version_code,
            platform=platform,
            status="DRAFT",
            is_current=False,
            release_notes=release_notes,
            file_name=name,
            file_size=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
        )
        db.session.add(release)
        db.session.flush()
        db.session.add(MobileAppReleaseFile(release_id=release.id,
                                            file_data=data))
        db.session.commit()
        return release

    def publish(self, release_id, user=None):
        """DRAFT → PUBLISHED, and make it the current build.

        Does NOT touch min_supported_version_code. Publishing tells
        phones a newer build exists; blocking the old one is a separate
        decision with a separate blast radius, and coupling them would
        mean every routine release risked locking out whoever had not
        updated yet.
        """
        release = db.session.get(MobileAppRelease, release_id)
        if release is None:
            raise MobileReleaseError("That release does not exist.")
        if release.status == "PUBLISHED":
            return release

        previous = self.current(release.platform)
        if previous is not None and previous.id != release.id:
            previous.is_current = False
            previous.status = "ARCHIVED"

        release.status = "PUBLISHED"
        release.is_current = True
        release.released_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
        return release

    def set_minimum(self, release_id, min_code, user=None):
        """Raise the forced-update floor. The deliberate action.

        Refuses a floor above the current release's own version_code:
        that would lock out every device including those on the newest
        build, leaving nobody with a working app and no way to publish a
        fix that anyone could reach.
        """
        release = db.session.get(MobileAppRelease, release_id)
        if release is None:
            raise MobileReleaseError("That release does not exist.")
        if min_code in (None, "", 0):
            release.min_supported_version_code = None
            db.session.commit()
            return release
        try:
            min_code = int(min_code)
        except (TypeError, ValueError):
            raise MobileReleaseError("Minimum version must be a whole number.")
        if min_code > release.version_code:
            raise MobileReleaseError(
                f"A minimum of {min_code} is higher than this release "
                f"({release.version_code}), which would lock out every "
                f"device including those already up to date.")
        release.min_supported_version_code = min_code
        db.session.commit()
        return release

    # ── reads ───────────────────────────────────────────────────────

    def current(self, platform="ANDROID"):
        return (MobileAppRelease.query
                .filter_by(platform=platform, is_current=True,
                           status="PUBLISHED")
                .order_by(MobileAppRelease.version_code.desc())
                .first())

    def list(self, platform="ANDROID"):
        """Metadata only. Never touches MobileAppReleaseFile -- that is
        the whole reason the tables are separate."""
        return (MobileAppRelease.query
                .filter_by(platform=platform)
                .order_by(MobileAppRelease.version_code.desc()).all())

    def bytes_for(self, release_id):
        row = (MobileAppReleaseFile.query
               .filter_by(release_id=release_id).first())
        return row.file_data if row else None
