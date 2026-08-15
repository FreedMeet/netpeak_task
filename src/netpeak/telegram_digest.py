import logging
import os
from pathlib import Path
import re

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"
MAX_MESSAGE_LENGTH = 4096


def _markdown_to_telegram_html(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if line.startswith("## "):
            line = f"<b>{line[3:]}</b>"
        elif line.startswith("# "):
            line = f"<b>{line[2:]}</b>"
        else:
            line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        lines.append(line)
    return "\n".join(lines)


def _send_text(token: str, chat_id: str, text: str) -> None:
    url = f"{TELEGRAM_API_BASE.format(token=token)}/sendMessage"
    html_text = _markdown_to_telegram_html(text)
    if len(html_text) > MAX_MESSAGE_LENGTH:
        html_text = html_text[: MAX_MESSAGE_LENGTH - 20] + "\n\n... (обрізано)"
    response = httpx.post(
        url,
        data={"chat_id": chat_id, "text": html_text, "parse_mode": "HTML"},
        timeout=15,
    )
    response.raise_for_status()

def _send_document(token: str, chat_id: str, file_path: Path, caption: str) -> None:
    url = f"{TELEGRAM_API_BASE.format(token=token)}/sendDocument"
    with open(file_path, "rb") as f:
        response = httpx.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (file_path.name, f, "text/markdown")},
            timeout=15,
        )
    response.raise_for_status()


def send_report(report_path: Path, summary_line: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.info("Telegram not configured, skipping digest")
        return

    report_text = report_path.read_text(encoding="utf-8")

    try:
        _send_text(token, chat_id, report_text)
        _send_document(token, chat_id, report_path, summary_line)
        logger.info("Digest sent to Telegram (text + file)")
    except Exception as e:
        logger.warning("Failed to send Telegram digest: %s", e)