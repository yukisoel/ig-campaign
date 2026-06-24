"""Email notifications via Resend HTTP API.

ジョブ完了/エラー時にメール通知する。資格情報は Streamlit Secrets から読む。
失敗してもジョブ本体は止めない（ベストエフォート）。

Resend は HTTP API 経由なので、Workspace の SMTP/App Password 制限を回避できる。
"""
import base64
import os
from typing import Sequence

import requests
import streamlit as st


RESEND_URL = "https://api.resend.com/emails"
HTTP_TIMEOUT = 30


def _get_config() -> tuple[str, str]:
    """Returns (api_key, from_address)."""
    try:
        key = st.secrets.get("RESEND_API_KEY", "") or os.environ.get("RESEND_API_KEY", "")
        sender = st.secrets.get("RESEND_FROM", "") or os.environ.get("RESEND_FROM", "")
    except Exception:
        key = os.environ.get("RESEND_API_KEY", "")
        sender = os.environ.get("RESEND_FROM", "")
    return key, sender


def enabled() -> bool:
    key, sender = _get_config()
    return bool(key and sender)


def send(
    to: Sequence[str],
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> bool:
    """Resend API で送信。失敗時は print してFalseを返す（例外は投げない）。

    attachments: [(filename, content_bytes, mime_type)]
    mime_type は Resend 側でファイル名から推測されるため、API ペイロードには含めない。
    """
    key, sender = _get_config()
    if not (key and sender):
        print("[notify] RESEND_API_KEY / RESEND_FROM 未設定。通知スキップ。")
        return False
    if not to:
        return False

    payload: dict = {
        "from": sender,
        "to": list(to),
        "subject": subject,
        "text": body,
    }
    if attachments:
        payload["attachments"] = [
            {
                "filename": filename,
                "content": base64.b64encode(content).decode("ascii"),
            }
            for filename, content, _mime in attachments
        ]

    try:
        r = requests.post(
            RESEND_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code >= 300:
            print(f"[notify] resend failed: {r.status_code} {r.text}")
            return False
        print(f"[notify] sent to {to}: {subject}")
        return True
    except Exception as e:
        print(f"[notify] send failed: {e}")
        return False
