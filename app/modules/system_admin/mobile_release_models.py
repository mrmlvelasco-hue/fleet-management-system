"""APK release metadata and binaries.

Deliberately NOT routed through AttachmentService. That service reads
its allowed extensions from a SYSTEM PARAMETER shared by every
attachment in the system, so adding "apk" to it would let anyone holding
vehicle.update attach an executable to a vehicle record -- turning the
FMS into a malware distribution point inside the company network,
reachable by a permission drivers hold. A signed APK also exceeds the
10 MB attachment cap, which would have had to rise for every attachment
type at once.

TWO TABLES, not one with a blob column.

The version check runs on EVERY app start, and the release list is an
admin screen someone opens while wondering which build is live. A blob
column on the metadata table means both of those risk dragging 20 MB per
row into memory -- SQLAlchemy loads columns eagerly unless told
otherwise, and the first person to write `MobileAppRelease.query.all()`
would not notice until the office wifi did. Splitting makes the binary a
deliberate second fetch that only bytes_for() performs.
"""
from app.core.models.base import BaseModel
from app.extensions import db


class MobileAppRelease(db.Model, BaseModel):
    """One build of the field app."""

    __tablename__ = "mobile_app_releases"

    #: Shown to humans: "1.4.0". Never compared -- string ordering says
    #: "1.10.0" < "1.9.0", which is wrong and would silently offer a
    #: downgrade as an update.
    version_name = db.Column(db.String(40), nullable=False)

    #: What the app actually compares. Monotonic integer, the same
    #: value as Android's versionCode, so the phone can decide "am I
    #: behind" with one comparison and no parsing.
    version_code = db.Column(db.Integer, nullable=False, index=True)

    platform = db.Column(db.String(20), nullable=False, default="ANDROID")

    #: DRAFT | PUBLISHED | ARCHIVED. Uploading does not release: a build
    #: can be put in place and checked before any phone is told about
    #: it.
    status = db.Column(db.String(20), nullable=False, default="DRAFT")

    #: At most one PUBLISHED current row per platform, enforced in the
    #: service. A partial unique index is the natural expression and is
    #: not portable to MySQL or SQL Server, and a constraint that only
    #: existed on one engine would give false confidence on the others.
    is_current = db.Column(db.Boolean, nullable=False, default=False)

    #: The forced-update lever. NEVER set by publish() -- raising it is
    #: a separate deliberate action, because it is the most effective
    #: way to lock every driver out of a working app by accident.
    min_supported_version_code = db.Column(db.Integer, nullable=True)

    release_notes = db.Column(db.Text, nullable=True)

    file_name = db.Column(db.String(255), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)

    #: Recorded at upload. A truncated upload over a marginal office
    #: connection yields a file that installs and then crashes, which is
    #: miserable to debug from another building. The hash rules that out
    #: in one step.
    checksum_sha256 = db.Column(db.String(64), nullable=True)

    released_at = db.Column(db.DateTime, nullable=True)


class MobileAppReleaseFile(db.Model, BaseModel):
    """The APK bytes. One row per release, fetched only on download."""

    __tablename__ = "mobile_app_release_files"

    release_id = db.Column(db.Integer,
                           db.ForeignKey("mobile_app_releases.id"),
                           nullable=False, unique=True)
    file_data = db.Column(db.LargeBinary, nullable=False)

    # No backref onto MobileAppRelease. A relationship there would be
    # one autoflush or one lazy access away from undoing the whole
    # reason these are separate tables.
