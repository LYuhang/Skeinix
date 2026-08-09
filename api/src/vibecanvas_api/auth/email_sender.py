"""Pluggable email sender. Dev prints to stderr (same UX as the old
dev-token bootstrap); prod wires SMTP at deploy time."""
from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage
from typing import Protocol

from vibecanvas_api.config import config
from vibecanvas_api.security.platform_secrets import platform_secret_resolver


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class DevEmailSender:
    """Prints the email to stderr — for local dev / tests."""
    def send(self, to: str, subject: str, body: str) -> None:
        print(f"📧 [dev-email] to={to} subject={subject!r}\n   {body}",
              file=sys.stderr)


class SmtpEmailSender:
    """Production SMTP with just-in-time managed-secret resolution."""
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        *,
        password_secret_id: str | None = None,
        development_password: str | None = None,
    ):
        self._host, self._port = host, port
        self._user = user
        self._password_secret_id = password_secret_id
        self._development_password = development_password

    def _password(self) -> str:
        if self._password_secret_id:
            return platform_secret_resolver().resolve(
                self._password_secret_id
            )
        if config.environment in {"development", "test"}:
            return self._development_password or ""
        raise RuntimeError("SMTP managed secret reference is required")

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self._user, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=10) as s:
            s.starttls()
            s.login(self._user, self._password())
            s.send_message(msg)


def get_email_sender() -> EmailSender:
    """Dev default. Production resolves the password from Secrets Manager."""
    if os.getenv("SMTP_HOST"):
        return SmtpEmailSender(
            os.environ["SMTP_HOST"],
            int(os.getenv("SMTP_PORT", "587")),
            os.environ["SMTP_USER"],
            password_secret_id=os.getenv("SMTP_PASSWORD_SECRET_ID"),
            development_password=os.getenv("SMTP_PASSWORD"),
        )
    return DevEmailSender()
