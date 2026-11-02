"""Gmail STARTTLS digest delivery using only Python's standard library."""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

from normalizers.models import Job

MAX_LISTED_JOBS = 30


def gmail_settings() -> tuple[str, str, str]:
    values = {}
    for key in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NOTIFY_TO"):
        value = os.environ.get(key, "").strip()
        if not value:
            raise ValueError(f"{key} must be configured")
        values[key] = value
    for key in ("GMAIL_ADDRESS", "NOTIFY_TO"):
        value = values[key]
        if "\r" in value or "\n" in value or parseaddr(value)[1] != value or "@" not in value:
            raise ValueError(f"{key} must contain a single bare email address")
    password = "".join(values["GMAIL_APP_PASSWORD"].split())
    return values["GMAIL_ADDRESS"], password, values["NOTIFY_TO"]


def send_email(jobs: list[Job]) -> None:
    if not jobs:
        return
    address, password, recipient = gmail_settings()
    lines = [f"今日新增 {len(jobs)} 个岗位\n"]
    for job in jobs[:MAX_LISTED_JOBS]:
        lines.extend([
            f"• {job.company} — {job.title}",
            f"  {job.location}",
            f"  {job.apply_url}\n",
        ])
    if len(jobs) > MAX_LISTED_JOBS:
        lines.append(
            f"邮件仅展示前 {MAX_LISTED_JOBS} 个；其余 {len(jobs) - MAX_LISTED_JOBS} 个"
            "请查看本次扫描的完整 JSON 输出（GitHub Actions artifact）。"
        )
    msg = EmailMessage()
    msg["Subject"] = f"[Job Scan] 新增 {len(jobs)} 个岗位"
    msg["From"] = address
    msg["To"] = recipient
    msg.set_content("\n".join(lines))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(address, password)
        refused = smtp.send_message(msg)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
