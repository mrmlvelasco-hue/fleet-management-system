# Phase 4 — APK Release Management — Design Spec

**Date:** 2026-09-03
**Status:** Approved
**Phase:** 4 of 7

## Context

Nothing in the FMS knows what an APK is. Distribution today is entirely manual: build on the laptop,
copy the file to a phone by cable or shared folder, ask the person to install it. There is no version
history, no way to tell who is running what, and no way to stop someone using a build from three
months ago.

## Decisions Made

| Decision | Choice | Why not the alternative |
|---|---|---|
| Where the binary lives | **Its own tables**, not `AttachmentService` | The attachment allow-list is a *system parameter* shared by every attachment in the system. Adding `apk` to it would let anyone holding `vehicle.update` attach an executable to a vehicle record — turning the FMS into a malware distribution point inside the company network, reachable by a permission drivers hold. A typical signed APK also exceeds the 10 MB cap, which would have to rise for every attachment type at once. |
| Table split | **Two tables**: metadata and binary | A blob column on the metadata table means every `SELECT` that lists releases, or answers the version check, risks dragging 20 MB into memory. The version check runs on **every app start**, so this is the hot path. Splitting keeps metadata queries cheap and makes the blob a deliberate second fetch. |
| Download auth | **Login required** | The APK is company property and an open URL is a link anyone can forward. Cost: the *first* install cannot come from inside the app, so it still arrives by cable or shared folder. Only **updates** flow through the system. |
| Forced update | Supported, **manual only** | `min_supported_version_code` is never touched by publishing a release. Raising it is a separate, deliberate action, because it is the single most effective way to lock every driver out of a working app by accident. |
| Version numbers | Typed by the administrator, validated | Parsing them from the APK needs a binary-XML decoder as a new dependency. Server-side validation that `version_code` is strictly greater than the current highest catches the realistic mistake — re-uploading, or fat-fingering a digit — without new libraries. |
| Permission | New `mobileapp.*` codes | Deliberately not reusing `user.*` or `vehicle.*`. Publishing an executable that runs on company phones is its own authority and should be grantable to exactly the people who do it. |

## Data Model

```
mobile_app_releases
  id             PK
  version_name   VARCHAR(40)  NOT NULL   -- '1.4.0', shown to humans
  version_code   INTEGER      NOT NULL   -- monotonic, what the app compares
  platform       VARCHAR(20)  NOT NULL DEFAULT 'ANDROID'
  status         VARCHAR(20)  NOT NULL DEFAULT 'DRAFT'  -- DRAFT|PUBLISHED|ARCHIVED
  is_current     BOOLEAN      NOT NULL DEFAULT 0
  min_supported_version_code INTEGER NULL   -- set on the CURRENT release only
  release_notes  TEXT         NULL
  file_name      VARCHAR(255) NULL
  file_size      INTEGER      NULL
  checksum_sha256 VARCHAR(64) NULL
  released_at    DATETIME     NULL
  + BaseModel audit columns

mobile_app_release_files
  id          PK
  release_id  FK mobile_app_releases.id, NOT NULL, UNIQUE
  file_data   LargeBinary  NOT NULL
```

`version_code` is unique per platform. `is_current` is held to at most one PUBLISHED row per
platform by the service, for the same reason the assignment invariant is: a partial unique index is
not portable to MySQL or SQL Server.

**Checksum recorded at upload.** A truncated upload over a marginal office connection produces a file
that installs and then crashes, which is a miserable thing to debug remotely. The hash lets that be
ruled out in one step.

## Components

### `MobileReleaseService`

| Method | Behaviour |
|---|---|
| `upload(file, version_name, version_code, release_notes, user)` | Validates extension, size, and that `version_code` exceeds the current highest. Creates the metadata row DRAFT and the binary row. |
| `publish(release_id, user)` | DRAFT → PUBLISHED, clears `is_current` on the previous, sets it here, stamps `released_at`. |
| `set_minimum(release_id, version_code, user)` | The forced-update lever. **Never called by `publish`.** Refuses a value above the current release's own code — that would lock everyone out including people on the newest build. |
| `current(platform)` | The published current release, or None. |
| `bytes_for(release_id)` | The blob, fetched only here. |

### API

| Endpoint | Guard |
|---|---|
| `GET /api/v1/mobile/version` | any authenticated user |
| `GET /api/v1/mobile/releases/<id>/download` | any authenticated user |
| `GET/POST /api/v1/mobile/releases` | `mobileapp.view` / `mobileapp.create` |
| `POST /api/v1/mobile/releases/<id>/publish` | `mobileapp.update` |
| `POST /api/v1/mobile/releases/<id>/minimum` | `mobileapp.update` |

`GET /mobile/version` returns `{version_name, version_code, min_supported_version_code,
release_notes, download_url, file_size}` or nulls when nothing is published. It is guarded by
authentication but **no permission code** — every user of the app must be able to ask whether their
build is current, and gating that behind a permission would mean the people most likely to be on an
old build are the ones who cannot find out.

## Testing

- Upload rejects a non-APK extension and an oversized file
- Upload rejects a `version_code` not greater than the current highest
- Publishing moves `is_current` and leaves exactly one current release
- **Publishing does NOT change `min_supported_version_code`** — the forced-update guard
- `set_minimum` refuses a value above the current release's own code
- `/mobile/version` returns nulls, not an error, when nothing is published
- `/mobile/version` and download both refuse an unauthenticated caller
- Download returns the exact bytes uploaded, and the checksum matches
- Listing releases does not load blobs (asserted by querying the metadata table alone)

## Out of Scope for Phase 4

- The in-app update prompt and forced-update gate in React (→ Phase 5)
- `mobile_devices` fleet-version monitoring (→ Phase 7, optional)
- Release signing and the keystore — a build-process concern, not an application one
- Automatic APK building

## Risks

- **Blobs in MySQL.** At ~20 MB per release this is fine for the handful of builds this client will
  keep, and `max_allowed_packet` must be large enough — worth checking on the real server, since the
  SQLite suite will not catch it.
- **Retention decided (client, 2026-09-03): only the latest APK is stored.** Publishing deletes every
  superseded binary. The metadata row survives, marked `file_purged` — it is a few hundred bytes,
  it records which version was live when and who published it, and crucially it keeps the
  `version_code` claimed so nobody can re-upload 140 and produce two builds a phone cannot tell
  apart. DRAFT builds are spared: a draft is something somebody is still preparing, and deleting
  their upload because an unrelated release went live would be baffling and unrecoverable.
- A device pointed at a purged download URL gets a 404 saying the build is no longer available
  rather than an empty file, which would install and then crash.
