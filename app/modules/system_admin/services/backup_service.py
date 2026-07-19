"""Backup service — configuration verification and MySQL backup execution.

Design decision (Senior Architect note): Restore is deliberately NOT exposed
as an in-app button. A one-click restore sitting one misclick away from
overwriting production is an enterprise anti-pattern — restore is an ops
procedure run deliberately from the server, documented in the README. What
the app DOES provide is:

  1. "Test Configuration" — non-destructive readiness checks so an admin can
     confirm the backup will work BEFORE relying on it (mysqldump reachable,
     destination writable, DB credentials valid).
  2. run_backup() — the actual mysqldump execution, invoked by the Celery
     Beat schedule (DAILY/WEEKLY) or a manual trigger, with gzip compression
     and retention purge.

SQLite dev databases don't use mysqldump; the checks and backup adapt to the
active engine so "Test Configuration" is meaningful in every environment.
"""
import os
import gzip
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, unquote

from flask import current_app


class BackupService:

    # ── Configuration test ────────────────────────────────────────────────

    def test_configuration(self, destination_path: str) -> list:
        """Run non-destructive readiness checks. Returns a list of
        {check, ok, detail} dicts so the UI can show a green/red line per
        check — the quickest way to confirm the backup will actually work
        before the client depends on it."""
        results = []
        uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        is_mysql = uri.startswith("mysql")

        # 1. Database connectivity — SELECT 1 through the app's own engine,
        #    so it exercises the exact credentials the app uses.
        results.append(self._check_db_connection())

        # 2. mysqldump availability (MySQL only).
        if is_mysql:
            results.append(self._check_mysqldump())
        else:
            results.append({
                "check": "Backup tool",
                "ok": True,
                "detail": "SQLite engine detected — backup copies the "
                          "database file directly; mysqldump not required."})

        # 3. Destination path writable.
        results.append(self._check_destination_writable(destination_path))

        return results

    def _check_db_connection(self) -> dict:
        from app.extensions import db
        from sqlalchemy import text
        try:
            db.session.execute(text("SELECT 1"))
            return {"check": "Database connection", "ok": True,
                    "detail": "Connected and SELECT 1 succeeded."}
        except Exception as e:
            return {"check": "Database connection", "ok": False,
                    "detail": f"Failed: {e}"}

    def _check_mysqldump(self) -> dict:
        exe = shutil.which("mysqldump")
        if not exe:
            return {"check": "mysqldump", "ok": False,
                    "detail": "mysqldump not found on PATH. Install MySQL "
                              "client tools on the server or set the full "
                              "path."}
        try:
            out = subprocess.run([exe, "--version"], capture_output=True,
                                 text=True, timeout=10)
            return {"check": "mysqldump", "ok": out.returncode == 0,
                    "detail": (out.stdout or out.stderr).strip()
                              or f"Found at {exe}"}
        except Exception as e:
            return {"check": "mysqldump", "ok": False,
                    "detail": f"Found at {exe} but failed to run: {e}"}

    def _check_destination_writable(self, destination_path: str) -> dict:
        path = destination_path or os.path.join(
            current_app.instance_path, "backups")
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return {"check": "Destination path", "ok": True,
                    "detail": f"Writable: {path}"}
        except Exception as e:
            return {"check": "Destination path", "ok": False,
                    "detail": f"Not writable ({path}): {e}"}

    # ── Backup execution ──────────────────────────────────────────────────

    def run_backup(self, destination_path: str = None,
                   retention_days: int = 30) -> dict:
        """Execute a backup of the active database. Returns
        {ok, file, size_bytes, error}. Called by the scheduled Celery task
        or a manual trigger."""
        uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        dest = destination_path or os.path.join(
            current_app.instance_path, "backups")
        os.makedirs(dest, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        try:
            if uri.startswith("mysql"):
                out_file = self._backup_mysql(uri, dest, stamp)
            elif uri.startswith("sqlite"):
                out_file = self._backup_sqlite(uri, dest, stamp)
            else:
                return {"ok": False, "error": f"Unsupported engine: {uri}"}
            size = os.path.getsize(out_file)
            self._purge_old(dest, retention_days)
            return {"ok": True, "file": out_file, "size_bytes": size,
                    "error": None}
        except Exception as e:
            current_app.logger.exception("Backup failed: %s", e)
            return {"ok": False, "error": str(e)}

    def _backup_mysql(self, uri: str, dest: str, stamp: str) -> str:
        p = urlparse(uri)
        db_name = p.path.lstrip("/").split("?")[0]
        out_file = os.path.join(dest, f"fms_{db_name}_{stamp}.sql.gz")
        cmd = [
            shutil.which("mysqldump") or "mysqldump",
            f"--host={p.hostname or '127.0.0.1'}",
            f"--port={p.port or 3306}",
            f"--user={unquote(p.username or 'root')}",
            "--single-transaction", "--routines", "--triggers", db_name,
        ]
        env = dict(os.environ)
        if p.password:
            env["MYSQL_PWD"] = unquote(p.password)  # avoids password on argv
        with gzip.open(out_file, "wb") as gz:
            proc = subprocess.run(cmd, stdout=gz,
                                  stderr=subprocess.PIPE, env=env, timeout=3600)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace"))
        return out_file

    def _backup_sqlite(self, uri: str, dest: str, stamp: str) -> str:
        src = uri.replace("sqlite:///", "").replace("sqlite://", "")
        if not src or not os.path.exists(src):
            raise FileNotFoundError(f"SQLite file not found: {src}")
        out_file = os.path.join(dest, f"fms_sqlite_{stamp}.db.gz")
        with open(src, "rb") as f_in, gzip.open(out_file, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        return out_file

    def _purge_old(self, dest: str, retention_days: int) -> None:
        if not retention_days or retention_days <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        for name in os.listdir(dest):
            if not name.startswith("fms_"):
                continue
            full = os.path.join(dest, name)
            try:
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(full), tz=timezone.utc)
                if mtime < cutoff:
                    os.remove(full)
            except OSError:
                pass

    def list_backups(self, destination_path: str = None) -> list:
        """Return existing backup files (newest first) for the history
        table on the config screen."""
        dest = destination_path or os.path.join(
            current_app.instance_path, "backups")
        if not os.path.isdir(dest):
            return []
        rows = []
        for name in sorted(os.listdir(dest), reverse=True):
            if not name.startswith("fms_"):
                continue
            full = os.path.join(dest, name)
            rows.append({
                "name": name,
                "size_bytes": os.path.getsize(full),
                "created_at": datetime.fromtimestamp(
                    os.path.getmtime(full), tz=timezone.utc),
            })
        return rows
