"""Email notifications via Gmail SMTP.

ジョブ完了/エラー時にメール通知する。資格情報は Streamlit Secrets から読む。
失敗してもジョブ本体は止めない（ベストエフォート）。
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Sequence

import streamlit as st


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT = 30


def _get_creds() -> tuple[str, str]:
    try:
        user = st.secrets.get("GMAIL_USER", "") or os.environ.get("GMAIL_USER", "")
        pw = st.secrets.get("GMAIL_APP_PASSWORD", "") or os.environ.get("GMAIL_APP_PASSWORD", "")
    except Exception:
        user = os.environ.get("GMAIL_USER", "")
        pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    return user, pw


def enabled() -> bool:
    user, pw = _get_creds()
    return bool(user and pw)


def send(
    to: Sequence[str],
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> bool:
    """Gmail SMTP で送信。失敗時は print してFalseを返す（例外は投げない）。

    attachments: [(filename, content_bytes, mime_type)]
    """
    user, pw = _get_creds()
    if not (user and pw):
        print("[notify] GMAIL_USER / GMAIL_APP_PASSWORD 未設定。通知スキップ。")
        return False
    if not to:
        return False

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)

    if attachments:
        for filename, content, mime in attachments:
            maintype, _, subtype = mime.partition("/")
            if not subtype:
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.starttls(context=ctx)
            smtp.login(user, pw)
            smtp.send_message(msg)
        print(f"[notify] sent to {to}: {subject}")
        return True
    except Exception as e:
        print(f"[notify] send failed: {e}")
        return False
