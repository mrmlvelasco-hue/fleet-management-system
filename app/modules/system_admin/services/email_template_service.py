"""Email Template service — Jinja2-rendered notification templates."""
from jinja2 import Template

from app.extensions import db
from app.modules.system_admin.models import EmailTemplate


class EmailTemplateService:
    def get_by_event(self, event_code: str) -> EmailTemplate | None:
        return EmailTemplate.query.filter_by(
            event_code=event_code, is_active=True).first()

    def create(self, event_code: str, name: str, subject: str,
               body_html: str = "", body_text: str = "") -> EmailTemplate:
        tmpl = EmailTemplate(event_code=event_code, name=name,
                             subject=subject, body_html=body_html,
                             body_text=body_text)
        db.session.add(tmpl)
        db.session.commit()
        return tmpl

    def update(self, template_id: int, **kwargs) -> EmailTemplate | None:
        tmpl = db.session.get(EmailTemplate, template_id)
        if tmpl is None:
            return None
        for k, v in kwargs.items():
            setattr(tmpl, k, v)
        db.session.commit()
        return tmpl

    def deactivate(self, template_id: int) -> None:
        tmpl = db.session.get(EmailTemplate, template_id)
        if tmpl:
            tmpl.is_active = False
            db.session.commit()

    @staticmethod
    def render(template: EmailTemplate, context: dict) -> dict:
        """Return rendered {subject, body_html, body_text} dict."""
        return {
            "subject": Template(template.subject).render(**context),
            "body_html": Template(template.body_html).render(**context),
            "body_text": Template(template.body_text).render(**context),
        }
