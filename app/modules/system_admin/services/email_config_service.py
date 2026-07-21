"""SMTP configuration and delivery — replaces the earlier stubbed-out
send_notification_email task (which only logged intent) with real email
sending, gated behind an admin-configurable enable switch and settings.
"""
import smtplib
import ssl
import time
import logging
from email.message import EmailMessage

from app.extensions import db
from app.modules.system_admin.models import EmailConfig

logger = logging.getLogger(__name__)

# Never let an unreachable/misconfigured SMTP server hang the request (or a
# Celery worker) indefinitely. Kept modest (not 20-30s) because
# socket.create_connection() applies this timeout PER resolved address, so
# a host with multiple DNS records (common: IPv4 + IPv6) can multiply the
# wait -- 10s keeps the worst case for a normal 1-2-address host bounded to
# ~10-20s instead of a minute+.
SMTP_TIMEOUT_SECONDS = 10


class EmailNotConfiguredError(Exception):
    pass


class EmailConfigService:
    def get(self) -> EmailConfig:
        config = EmailConfig.query.first()
        if config is None:
            config = EmailConfig()
            db.session.add(config)
            db.session.commit()
        return config

    def update(self, **kwargs) -> EmailConfig:
        config = self.get()
        for k, v in kwargs.items():
            setattr(config, k, v)
        db.session.commit()
        return config


class EmailSenderService:
    def send(self, *, to_email: str, subject: str, body_html: str,
             body_text: str = None, attach_files: list = None) -> None:
        """Send an email. `attach_files`, if given, is a list of dicts
        {"filepath": str, "filename": str, "mime_type": str|None} — used
        to carry a document's attachments (e.g. a comment's uploaded
        file) along with the notification, so the recipient doesn't have
        to log in just to see what was attached."""
        config = EmailConfigService().get()
        if not config.is_enabled:
            raise EmailNotConfiguredError(
                "Email sending is disabled. Enable it and configure SMTP "
                "settings under System Administration -> Email Configuration.")
        if not config.smtp_host or not config.from_email:
            raise EmailNotConfiguredError(
                "SMTP Host and From Email must be configured before emails "
                "can be sent. See System Administration -> Email Configuration.")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = (f"{config.from_name} <{config.from_email}>"
                       if config.from_name else config.from_email)
        msg["To"] = to_email
        msg.set_content(body_text or _strip_html(body_html))
        if body_html:
            msg.add_alternative(body_html, subtype="html")

        for f in (attach_files or []):
            try:
                if f.get("data") is not None:
                    # Raw bytes given directly (e.g. read from the
                    # database rather than a machine-local disk path) --
                    # the preferred path now that Attachment.file_data
                    # exists.
                    data = f["data"]
                else:
                    with open(f["filepath"], "rb") as fh:
                        data = fh.read()
                mime = f.get("mime_type") or "application/octet-stream"
                maintype, _, subtype = mime.partition("/")
                msg.add_attachment(
                    data, maintype=maintype or "application",
                    subtype=subtype or "octet-stream",
                    filename=f.get("filename") or "attachment")
            except OSError:
                # A missing/moved file on disk shouldn't block the whole
                # notification from being sent — the recipient still gets
                # the message and can open the document to find the file.
                continue

        port = int(config.smtp_port or 587)
        context = ssl.create_default_context()

        def _do_send():
            # Port 465 = implicit SSL ("SMTPS"): the connection is encrypted
            # from the very first byte, so we must use SMTP_SSL and must NOT
            # call starttls(). The previous code always used plain SMTP() +
            # starttls(), which hangs forever against a 465 endpoint
            # (Hostinger, Gmail SSL, etc.) because the server is waiting for
            # a TLS handshake, not a plain EHLO. This was the "browser just
            # keeps loading" symptom.
            if port == 465:
                with smtplib.SMTP_SSL(config.smtp_host, port,
                                      timeout=SMTP_TIMEOUT_SECONDS,
                                      context=context) as server:
                    if config.smtp_username:
                        server.login(config.smtp_username, config.smtp_password)
                    server.send_message(msg)
            else:
                # 587 (submission) or 25: start plaintext, optionally upgrade
                # with STARTTLS. use_tls drives whether we attempt the upgrade.
                with smtplib.SMTP(config.smtp_host, port,
                                  timeout=SMTP_TIMEOUT_SECONDS) as server:
                    server.ehlo()
                    if config.use_tls:
                        server.starttls(context=context)
                        server.ehlo()
                    if config.smtp_username:
                        server.login(config.smtp_username, config.smtp_password)
                    server.send_message(msg)

        # One retry with a short pause for exactly this failure pattern: a
        # timeout specifically on RCPT TO (i.e. connect/login already
        # succeeded fine) usually means the RECEIVING mail server for this
        # particular recipient was momentarily slow/greylisting the
        # connection -- not a real SMTP config problem, since a fresh
        # connection moments later commonly succeeds. Retrying the whole
        # connection (not just the one command) since a stale connection
        # after a mid-transaction timeout can't safely be reused.
        try:
            _do_send()
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
               TimeoutError, OSError) as e:
            logger.warning(
                "SMTP send failed (%s), retrying once after a short pause: %s",
                type(e).__name__, e)
            time.sleep(2)
            _do_send()


def _strip_html(html: str) -> str:
    """Crude fallback plain-text body when no explicit text version is
    given — good enough for a notification email, not meant to be a
    general-purpose HTML-to-text converter."""
    import re
    return re.sub(r"<[^>]+>", "", html or "").strip()
