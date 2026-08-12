"""E-Mail-Alarme per SMTP.

Gedacht für Störungen, die sonst niemand mitbekommt: die Macs laufen unbeaufsichtigt
und ins Dashboard schaut erst jemand, wenn Zahlen fehlen. Der Versand ist bewusst
fehlertolerant — er läuft immer in einem Fehlerpfad, in dem er nichts verschlimmern darf.
"""

import logging
import smtplib
import socket
from email.message import EmailMessage

from config import get_config

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 20


def is_configured() -> bool:
    """True if enough is configured to even attempt a send."""
    config = get_config()
    return bool((config.alert_smtp_host or "").strip() and (config.alert_mail_to or "").strip())


def _sender(config) -> str:
    """From-Adresse: explizit, sonst der SMTP-Benutzer, sonst der Rechnername."""
    explicit = (config.alert_mail_from or "").strip()
    if explicit:
        return explicit
    user = (config.alert_smtp_user or "").strip()
    if "@" in user:
        return user
    return f"mactool@{socket.gethostname()}"


def _connect(host: str, port: int, security: str) -> smtplib.SMTP:
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT)
    return smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)


def send_alert(subject: str, body: str) -> dict:
    """Send one alert mail. Never raises.

    Returns {"status": "sent" | "not_configured" | "error", ...}.
    """
    config = get_config()
    host = (config.alert_smtp_host or "").strip()
    recipient = (config.alert_mail_to or "").strip()

    if not host or not recipient:
        logger.error(
            "E-Mail-Alarm nicht konfiguriert (alert_smtp_host/alert_mail_to fehlen) — "
            f"Meldung bleibt nur im Log: {subject}"
        )
        return {
            "status": "not_configured",
            "error": "alert_smtp_host oder alert_mail_to ist leer",
        }

    security = (config.alert_smtp_security or "starttls").strip().lower()
    if security not in ("starttls", "ssl", "none"):
        logger.warning(f"Unbekannte alert_smtp_security '{security}' — nutze starttls")
        security = "starttls"

    port = int(config.alert_smtp_port or 0) or (465 if security == "ssl" else 587)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _sender(config)
    message["To"] = recipient
    message.set_content(body)

    try:
        with _connect(host, port, security) as server:
            server.ehlo()
            if security == "starttls":
                server.starttls()
                server.ehlo()
            user = (config.alert_smtp_user or "").strip()
            if user:
                server.login(user, config.alert_smtp_password or "")
            server.send_message(message)
    except Exception as e:
        # Kein exc_info: das Passwort steckt nicht im Trace, aber der Login-String
        # mancher Server schon — die Klartextmeldung reicht zur Diagnose.
        detail = f"{type(e).__name__}: {e}"
        logger.error(f"Alarm-Mail an {recipient} fehlgeschlagen ({host}:{port}): {detail}")
        return {"status": "error", "to": recipient, "error": detail}

    logger.info(f"Alarm-Mail an {recipient} verschickt: {subject}")
    return {"status": "sent", "to": recipient}
