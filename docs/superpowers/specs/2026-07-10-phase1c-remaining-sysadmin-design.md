# Phase 1c — Remaining System Administration — Design Spec

**Date:** 2026-07-10
**Status:** Approved
**Phase:** 1 — sub-phase 1c of 3 (builds on 1a + 1b)

## Scope

All remaining System Administration modules:
1. System Parameters
2. Lookup Maintenance
3. Company Profile
4. Email Templates
5. Notification Rules + Notification Engine (wires 1b approval events → in-app + email)
6. Audit Trail Viewer
7. Dashboard Config (widget visibility toggles; KPI data comes Phase 4)
8. Backup Config (config record only; actual backup execution Phase 5+)
9. Report Config (config record only; rendering Phase 5+)

## Data Models

**SystemParameter**: code (unique), value (Text), data_type (STRING|INTEGER|BOOLEAN|DECIMAL),
description, group_name, is_editable. Read via SystemParameterService.get(code) → typed value.
First consumer: SESSION_TIMEOUT_MINUTES, MAX_FAILED_LOGIN_ATTEMPTS (Phase 1 hardcodes these;
1c moves them to the DB but keeps env-var fallback for fresh installs).

**Lookup**: lookup_type (index), code, description, sort_order, is_active. BaseModel.
LookupService.get_by_type(type) -> list. Types seeded at startup (same PermissionRegistry
pattern: LookupRegistry). Example types: FUEL_TYPE, VEHICLE_CATEGORY, MAINTENANCE_TYPE.

**CompanyProfile**: singleton row — company_name, address_line1/2, city, country,
phone, email, tin, logo_filename. Service enforces one active row.

**EmailTemplate**: event_code (unique, maps to ApprovalEngine events), name, subject,
body_html, body_text. Jinja2-rendered at send time with context dict.

**NotificationRule**: event_code, channel (IN_APP|EMAIL|BOTH), recipient_type
(SUBMITTER|CURRENT_APPROVER|ROLE|SPECIFIC_USER), role_id (nullable), user_id (nullable),
is_active. Multiple rules per event allowed (fan-out).

**InAppNotification**: user_id, title, message, event_code, reference_table,
reference_id, is_read, read_at. BaseModel (soft delete = dismiss).

**DashboardWidget**: code, label, icon, default_visible, sort_order.
**UserDashboardConfig**: user_id, widget_code, is_visible. Per-user overrides.

**BackupConfig**: schedule (DAILY|WEEKLY|MANUAL), retention_days, destination_path, is_active.
**ReportConfig**: report_code, name, description, template_path, is_active.

## Services

**SystemParameterService**
- get(code, default=None) → typed Python value (bool/int/str/Decimal)
- set(code, value) — admin only
- get_group(group_name) → dict
- Cached in app context (g) to avoid per-request DB hit

**LookupService**
- get_by_type(lookup_type) → list of active Lookup rows ordered by sort_order
- LookupRegistry: same pattern as PermissionRegistry — modules declare types at import,
  sync_lookups() upserts them at startup (idempotent)

**CompanyProfileService** — get() / save(**kwargs), enforces singleton

**EmailTemplateService** — get_by_event(event_code), render(template, context) via Jinja2

**NotificationEngine**
- Subscribes to ApprovalEngine events (the on_event hook from 1b)
- resolve_recipients(event_code, instance) → list of User objects from NotificationRule rows
- send_in_app(user, title, message, ...) — writes InAppNotification row
- send_email(user, subject, body_html) — Celery task (async); falls back gracefully if Redis
  not running (logs warning, doesn't crash)
- dispatch(event_name, instance) — called by ApprovalEngine hook; fans out to all matching rules

**InAppNotificationService** — list_for_user(user), mark_read(notification_id, user),
mark_all_read(user), unread_count(user)

## UI

All on 1a shell, DataTables, SweetAlert2, @require_permission, breadcrumbs.

- System Parameters: grouped list + inline edit (value only; code/type locked after creation)
- Lookup Maintenance: filterable by type, sortable rows, CRUD
- Company Profile: single-row form with logo upload placeholder
- Email Templates: list + form with textarea for HTML/text body + event_code select
- Notification Rules: list + form (event_code, channel, recipient_type with conditional
  role/user selects shown via JS)
- Audit Trail Viewer: read-only DataTables with server-side filters (table, action,
  user, date from/to); no edit/delete actions
- Dashboard Config: checkbox grid per widget (user-specific)
- Backup Config / Report Config: simple CRUD forms

Topnav bell: AJAX polling every 60s to `/notifications/unread-count`; badge shows count.
Notification dropdown shows 5 most recent in-app notifications with mark-read.

Permissions registered: sysparam.view/update, lookup.view/create/update/delete,
company.view/update, emailtemplate.view/create/update/delete,
notificationrule.view/create/update/delete, audittrail.view,
dashboardconfig.view/update, backupconfig.view/update, reportconfig.view/update.

## Testing

Unit: SystemParameterService typed casting (bool/int/Decimal/str), cached read, set persists;
LookupService filtered by type + sort, idempotent sync; CompanyProfile singleton enforcement;
EmailTemplate Jinja2 render with context; NotificationEngine recipient resolution (SUBMITTER,
CURRENT_APPROVER, ROLE), in-app write, email task queued; InAppNotification unread count +
mark-read; Audit viewer query with date/table/user filters.

Integration: all CRUD screens + permission 403s; bell endpoint returns JSON; end-to-end
approval submit triggers in-app notification row.

## Out of Scope (1c)

Actual email sending (SMTP config → Phase 5), actual backup execution, report rendering,
mobile API endpoints (Phase 6).
