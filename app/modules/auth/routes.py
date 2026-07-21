"""Auth blueprint: login, logout, change password. No business logic here —
all decisions live in AuthService (spec: no logic in controllers)."""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.core.security.password import verify_password, hash_password
from app.modules.auth.forms import LoginForm, ChangePasswordForm
from app.modules.auth.service import AuthService, AccountLockedError

bp = Blueprint("auth", __name__, template_folder="templates")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = AuthService().authenticate(form.username.data, form.password.data)
        except AccountLockedError as exc:
            flash(str(exc), "danger")
            return render_template("auth/login.html", form=form)
        if user is None:
            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html", form=form)
        login_user(user, remember=form.remember_me.data)
        from app.core.security.password_policy import PasswordPolicyService
        policy = PasswordPolicyService()
        if user.must_change_password or policy.is_expired(user):
            if not user.must_change_password:
                flash("Your password has expired. Please set a new one "
                     "to continue.", "warning")
            return redirect(url_for("auth.change_password"))
        if policy.is_expiring_soon(user):
            days_left = policy.days_until_expiry(user)
            flash(f"Your password expires in {days_left} day"
                 f"{'s' if days_left != 1 else ''}. Consider changing it "
                 f"soon under your profile.", "warning")
        next_url = request.args.get("next")
        return redirect(next_url or url_for("main.dashboard"))
    return render_template("auth/login.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not verify_password(current_user.password_hash,
                               form.current_password.data):
            flash("Current password is incorrect.", "danger")
        else:
            current_user.password_hash = hash_password(form.new_password.data)
            current_user.must_change_password = False
            from app.core.security.password_policy import PasswordPolicyService
            PasswordPolicyService().record_password_change(
                current_user, current_user.password_hash)
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/change_password.html", form=form)
