# Phase 1a — Mobile Access Flag & Assignee ↔ System Account Link — Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` (inline) or
> `superpowers:subagent-driven-development` to implement task-by-task. Steps use `- [ ]` for tracking.

**Goal:** Give the FMS an authoritative link between a login and a vehicle assignee, plus an explicit,
revocable switch controlling whether that login may obtain a token from the Android field app.

**Architecture:** Two additive columns — `users.mobile_access` and `drivers.user_id`. The flag is
enforced in exactly one place, the native branch of `/api/v1/auth/token` and `/api/v1/auth/refresh`,
so the web channel and the permission model are untouched. The link is owned by `DriverService`,
which enforces one-account-one-assignee.

**Tech Stack:** Flask 3, SQLAlchemy 2, Flask-Migrate/Alembic, WTForms, PyJWT, pytest, Jinja2,
Bootstrap 5, Select2.

**Spec:** `docs/specs/2026-09-03-phase1a-mobile-access-and-assignee-link-design.md`

---

### Task 1: Schema — both columns and the migration

**Files:**
- Modify: `app/modules/user_management/models.py`
- Modify: `app/modules/master_data/driver/models.py`
- Create: `migrations/versions/b1a7c0d93e21_add_mobile_access_and_assignee_user_link.py`
- Test: `tests/unit/test_mobile_access_link_models.py`

- [ ] **Step 1: Write the failing test**

```python
def test_user_defaults_to_no_mobile_access(app):
    with app.app_context():
        u = User(username="d1", email="d1@x.com", password_hash="x")
        db.session.add(u); db.session.commit()
        assert u.mobile_access is False

def test_driver_user_link_defaults_to_none(app):
    ...
    assert driver.user_id is None
    assert driver.user_account is None
```

- [ ] **Step 2: Run and confirm it fails** — `pytest tests/unit/test_mobile_access_link_models.py -v`,
      expect `AttributeError: 'User' object has no attribute 'mobile_access'`.

- [ ] **Step 3: Add the columns.** `mobile_access = db.Column(db.Boolean, default=False,
      nullable=False)` on `User`; `user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
      nullable=True, unique=True)` plus `user_account = db.relationship("User")` on `Driver`.

- [ ] **Step 4: Write the Alembic migration**, `down_revision = "f8c1a2b3c4d5"` (current head).
      Follow the defensive style of `f8c1a2b3c4d5_add_vehicle_checklists.py`: inspect existing
      columns before adding, so a partially-applied database can still be upgraded. Name the unique
      constraint explicitly (`uq_drivers_user_id`) — an unnamed one cannot be dropped on MySQL.

- [ ] **Step 5: Run the test — expect PASS. Commit.**

---

### Task 2: The channel gate on `/auth/token`

**Files:**
- Modify: `app/modules/api/routes.py` (`auth_token`)
- Test: `tests/unit/test_api_mobile_access_gate.py`

- [ ] **Step 1: Write four failing tests** — native+flag-off → 403 `mobile_access_denied`;
      native+flag-on → 200 with `refresh_token` in body; web (no `client` key) + flag off → 200;
      wrong password + flag on → 401 (the gate must not become an oracle that distinguishes a bad
      password from a disabled phone).

- [ ] **Step 2: Run — expect the 403 test to fail with 200.**

- [ ] **Step 3: Implement.** In `auth_token`, after credentials verify and before
      `issue_refresh_token`, add: if `_is_native_client(payload)` and not `user.mobile_access`,
      return 403. Ordering matters — after the password check so the response cannot be used to
      enumerate which accounts have mobile access without knowing the password.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Mutation-check.** Temporarily invert the condition to `if not
      _is_native_client(payload)`. At least one test must fail. Revert. Then delete the check
      entirely; the 403 test must fail. Revert. **Commit only after both mutations were caught.**

---

### Task 3: The gate on `/auth/refresh` — what makes it a kill switch

**Files:**
- Modify: `app/modules/api/routes.py` (`auth_refresh`)
- Test: `tests/unit/test_api_mobile_access_gate.py`

- [ ] **Step 1: Write the failing test** — obtain a native refresh token with the flag on, clear
      `mobile_access`, POST the token in the body → 401. Plus: the cookie path is unaffected by the
      flag.

- [ ] **Step 2: Run — expect 200 (the gap this task closes).**

- [ ] **Step 3: Implement.** In `auth_refresh`, after `user_from_refresh_token` resolves the user:
      if `supplied` (body token = native) and not `user.mobile_access`, return 401 with the same
      "Please sign in again." message used for an invalid token.

- [ ] **Step 4: Run — expect PASS. Mutation-check as in Task 2. Commit.**

---

### Task 4: `DriverService.link_user`

**Files:**
- Modify: `app/modules/master_data/driver/service.py`
- Test: `tests/unit/test_driver_user_link.py`

- [ ] **Step 1: Write five failing tests** — links; `link_user(driver_id, None)` clears; a user
      already linked elsewhere raises `DuplicateAssigneeLinkError`; an inactive user raises
      `InvalidAssigneeError`; linking leaves `user.mobile_access` untouched.

- [ ] **Step 2: Run — expect `AttributeError: 'DriverService' object has no attribute 'link_user'`.**

- [ ] **Step 3: Implement** `link_user(self, record_id, user_id)` and add
      `DuplicateAssigneeLinkError`. Re-linking a driver to the account it already holds must be a
      no-op, not a duplicate error — otherwise saving the assignee form twice fails the second time.

- [ ] **Step 4: Run — expect PASS. Commit.**

---

### Task 5: `GET /api/v1/me` exposes both facts

**Files:**
- Modify: `app/modules/api/routes.py` (`me`)
- Test: `tests/unit/test_api_me_assignee.py`

- [ ] **Step 1: Write the failing tests** — unlinked user returns `mobile_access` and
      `"assignee": None`; linked user returns `{id, person_id, employee_number, full_name}`.

- [ ] **Step 2: Run — expect `KeyError: 'assignee'`.**

- [ ] **Step 3: Implement** — query `Driver` by `user_id`, add both keys.

- [ ] **Step 4: Run — expect PASS. Commit.**

---

### Task 6: Driver API payloads carry the link

**Files:**
- Modify: `app/modules/api/drivers.py`
- Test: `tests/unit/test_api_drivers.py` (extend)

- [ ] **Step 1: Write the failing test** — list rows and detail both carry `user_id` and `username`
      (`None` when unlinked).

- [ ] **Step 2: Run — expect `KeyError`.**

- [ ] **Step 3: Implement** in the row and detail serializers.

- [ ] **Step 4: Run — expect PASS. Commit.**

---

### Task 7: User Maintenance UI

**Files:**
- Modify: `app/modules/user_management/forms.py`, `service.py`, `routes.py`
- Modify: `app/modules/user_management/templates/user_management/user_form.html`, `users_list.html`
- Test: `tests/integration/test_mobile_access_ui.py`

- [ ] **Step 1: Write the failing test** — POST the user form with `mobile_access=y`, assert the
      column persisted; assert the list page renders the badge.

- [ ] **Step 2: Run — expect the assertion on `user.mobile_access` to fail.**

- [ ] **Step 3: Implement** — `BooleanField` on `UserForm` with help text; thread through
      `create_user`/`update_user` using the existing `is_lockout_exempt` pattern (`None` means
      "unchanged" on update); checkbox in the form template; column in the list template.

- [ ] **Step 4: Run — expect PASS. Commit.**

---

### Task 8: Assignee UI — the System Account picker and column

**Files:**
- Modify: `app/modules/master_data/routes.py`
- Modify: `app/modules/master_data/templates/master_data/driver_form.html`, `driver_list.html`
- Test: `tests/integration/test_assignee_user_link_ui.py`

- [ ] **Step 1: Write the failing test** — POST the assignee form with `user_id`, assert persisted;
      POST with blank, assert cleared; assert the list renders the username.

- [ ] **Step 2: Run — expect the link to be `None`.**

- [ ] **Step 3: Implement** — populate the choices from active users, call
      `DriverService().link_user`, catch `DuplicateAssigneeLinkError` into a flash message rather
      than a 500, render the Select2 and the column.

- [ ] **Step 4: Run — expect PASS. Commit.**

---

### Task 9: Full regression and bundle

- [ ] **Step 1:** Run the unit suite in batches (~57 files each) and the integration suite in
      batches, to avoid timeouts.
- [ ] **Step 2:** All green.
- [ ] **Step 3:** `git bundle create fms_fixes_v229.bundle HEAD main --tags` — `HEAD` is required or
      the bundle clones with an empty working tree.
- [ ] **Step 4:** Write `APPLY_GUIDE_v229.md` including the deployment note from the spec: every
      field phone stops logging in until `mobile_access` is ticked.
