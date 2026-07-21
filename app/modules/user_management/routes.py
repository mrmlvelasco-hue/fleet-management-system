"""User/Role/Permission admin blueprint. Thin controllers: parse input,
call service, render. Permission checks via @require_permission."""
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   jsonify, request)
from flask_login import login_required

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.modules.user_management.forms import UserForm, RoleForm
from app.modules.user_management.repository import (
    UserRepository, RoleRepository, PermissionRepository)
from app.modules.user_management.schemas import UserSchema
from app.modules.user_management.service import (
    UserService, RoleService, DuplicateUsernameError, SystemRoleError)

bp = Blueprint("user_management", __name__, url_prefix="/admin",
               template_folder="templates")

for _code, _desc in [
    ("user.view", "View users"), ("user.create", "Create users"),
    ("user.update", "Update users"), ("user.delete", "Deactivate users"),
    ("role.view", "View roles"), ("role.create", "Create roles"),
    ("role.update", "Update roles"), ("role.delete", "Delete roles"),
    ("permission.view", "View permissions"),
]:
    _module, _action = _code.split(".")
    registry.register(_code, _module, _action, _desc)


def _populate_user_form(form: UserForm) -> None:
    form.roles.choices = [(r.id, r.name)
                          for r in RoleRepository().list()]
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    form.branch_id.choices = [(0, "— None —")] + [
        (b.id, f"{b.code} — {b.name}") for b in BranchService().list()]
    form.department_id.choices = [(0, "— None —")] + [
        (d.id, f"{d.code} — {d.name}") for d in DepartmentService().list()]


def _populate_role_form(form: RoleForm) -> None:
    form.permissions.choices = [(p.id, p.code)
                                for p in PermissionRepository().list()]


def _grouped_permissions():
    """Return permissions grouped by module, sorted, for the Permission
    Picker UI (search + group + Select All/Clear All)."""
    perms = sorted(PermissionRepository().list(),
                  key=lambda p: (p.module, p.action))
    groups = {}
    for p in perms:
        groups.setdefault(p.module, []).append(p)
    return dict(sorted(groups.items()))


# ---------- Users ----------

@bp.route("/users")
@login_required
@require_permission("user.view")
def users_list():
    users = UserRepository().list(include_inactive=True)
    return render_template("user_management/users_list.html", users=users)


@bp.route("/users/data")
@login_required
@require_permission("user.view")
def users_data():
    users = UserRepository().list(include_inactive=True)
    return jsonify(data=UserSchema(many=True).dump(users))


@bp.route("/users/new", methods=["GET", "POST"])
@login_required
@require_permission("user.create")
def users_new():
    form = UserForm()
    _populate_user_form(form)
    if form.validate_on_submit():
        try:
            UserService().create_user(
                username=form.username.data, email=form.email.data,
                password=form.password.data or "ChangeMe123!",
                first_name=form.first_name.data, last_name=form.last_name.data,
                role_ids=form.roles.data,
                employee_id=form.employee_id.data or None,
                branch_id=form.branch_id.data or None,
                department_id=form.department_id.data or None,
                must_change_password=form.must_change_password.data or not form.password.data)
            flash("User created.", "success")
            return redirect(url_for("user_management.users_list"))
        except DuplicateUsernameError as exc:
            flash(str(exc), "danger")
    return render_template("user_management/user_form.html", form=form,
                           title="New User")


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("user.update")
def users_edit(user_id):
    user = UserRepository().get_by_id(user_id, include_inactive=True)
    if user is None:
        flash("User not found.", "warning")
        return redirect(url_for("user_management.users_list"))
    form = UserForm(obj=user)
    form._editing_user = user
    _populate_user_form(form)
    if form.validate_on_submit():
        UserService().update_user(
            user_id, email=form.email.data, first_name=form.first_name.data,
            last_name=form.last_name.data, role_ids=form.roles.data,
            employee_id=form.employee_id.data or None,
            branch_id=form.branch_id.data or None,
            department_id=form.department_id.data or None,
            password=form.password.data or None)
        flash("User updated.", "success")
        return redirect(url_for("user_management.users_list"))
    form.roles.data = [r.id for r in user.roles]
    form.branch_id.data = user.branch_id or 0
    form.department_id.data = user.department_id or 0
    return render_template("user_management/user_form.html", form=form,
                           title=f"Edit User — {user.username}")


@bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@login_required
@require_permission("user.delete")
def users_deactivate(user_id):
    UserService().deactivate_user(user_id)
    flash("User deactivated.", "info")
    return redirect(url_for("user_management.users_list"))


# ---------- Roles ----------

@bp.route("/roles")
@login_required
@require_permission("role.view")
def roles_list():
    roles = RoleRepository().list(include_inactive=True)
    return render_template("user_management/roles_list.html", roles=roles)


@bp.route("/roles/new", methods=["GET", "POST"])
@login_required
@require_permission("role.create")
def roles_new():
    form = RoleForm()
    _populate_role_form(form)
    if form.validate_on_submit():
        RoleService().create_role(name=form.name.data,
                                  description=form.description.data,
                                  permission_ids=form.permissions.data)
        flash("Role created.", "success")
        return redirect(url_for("user_management.roles_list"))
    return render_template("user_management/role_form.html", form=form,
                           grouped_permissions=_grouped_permissions(),
                           selected_ids=set(),
                           title="New Role")


@bp.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("role.update")
def roles_edit(role_id):
    role = RoleRepository().get_by_id(role_id)
    if role is None:
        flash("Role not found.", "warning")
        return redirect(url_for("user_management.roles_list"))
    form = RoleForm(obj=role)
    _populate_role_form(form)
    if form.validate_on_submit():
        RoleService().update_role(role_id, name=form.name.data,
                                  description=form.description.data,
                                  permission_ids=form.permissions.data)
        flash("Role updated.", "success")
        return redirect(url_for("user_management.roles_list"))
    form.permissions.data = [p.id for p in role.permissions]
    return render_template("user_management/role_form.html", form=form,
                           grouped_permissions=_grouped_permissions(),
                           selected_ids={p.id for p in role.permissions},
                           title=f"Edit Role — {role.name}")


@bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@login_required
@require_permission("role.delete")
def roles_delete(role_id):
    try:
        RoleService().delete_role(role_id)
        flash("Role deleted.", "info")
    except SystemRoleError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("user_management.roles_list"))


# ---------- Permissions (read-only; managed by PermissionRegistry) ----------

@bp.route("/permissions")
@login_required
@require_permission("permission.view")
def permissions_list():
    perms = PermissionRepository().list()
    return render_template("user_management/permissions_list.html",
                           permissions=perms)


# ---------- Organizational Scope (F1) ----------

@bp.route("/users/<int:user_id>/org-scope", methods=["GET", "POST"])
@login_required
@require_permission("user.update")
def user_org_scope(user_id):
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService, InvalidScopeError)
    from app.modules.master_data.org.service import (
        BranchService, BusinessUnitService)

    user = UserRepository().get_by_id(user_id, include_inactive=True)
    if user is None:
        flash("User not found.", "warning")
        return redirect(url_for("user_management.users_list"))

    svc = UserOrgScopeService()
    branches = BranchService().list()
    business_units = BusinessUnitService().list()

    if request.method == "POST":
        f = request.form
        try:
            svc.assign(
                user_id, scope_type=f["scope_type"],
                branch_id=int(f["branch_id"]) if f.get("branch_id") else None,
                business_unit_id=int(f["business_unit_id"])
                if f.get("business_unit_id") else None)
            flash("Organizational scope added.", "success")
        except InvalidScopeError as e:
            flash(str(e), "danger")
        return redirect(url_for("user_management.user_org_scope", user_id=user_id))

    scopes = svc.list_for_user(user_id)
    return render_template("user_management/user_org_scope.html",
                           target_user=user, scopes=scopes,
                           branches=branches, business_units=business_units)


@bp.route("/users/<int:user_id>/org-scope/<int:scope_id>/remove", methods=["POST"])
@login_required
@require_permission("user.update")
def user_org_scope_remove(user_id, scope_id):
    from app.modules.user_management.org_scope_service import UserOrgScopeService
    UserOrgScopeService().remove(scope_id)
    flash("Organizational scope removed.", "info")
    return redirect(url_for("user_management.user_org_scope", user_id=user_id))
