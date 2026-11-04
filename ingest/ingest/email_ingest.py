"""Mailpit webhook -> EventRow.

Mailpit's webhook (MP_WEBHOOK_URL) POSTs the message *summary* (ID, MessageID, From,
To, Subject, Snippet, Created, ...). We fetch the full message and headers from the
Mailpit API so campaign ids in headers or tracked links can be extracted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .campaign import extract_email_campaign
from .store import EventRow


def _addresses(summary: dict[str, Any]) -> list[str]:
    out = []
    for key in ("To", "Cc", "Bcc"):
        for a in summary.get(key) or []:
            if isinstance(a, dict) and a.get("Address"):
                out.append(a["Address"])
    return out


def fetch_full(mailpit_url: str, message_id: str, client: httpx.Client | None = None) -> tuple[dict, dict]:
    """Returns (message, headers). Empty dicts if Mailpit can't be reached."""
    own = client is None
    client = client or httpx.Client(timeout=5)
    try:
        msg = client.get(f"{mailpit_url}/api/v1/message/{message_id}")
        hdr = client.get(f"{mailpit_url}/api/v1/message/{message_id}/headers")
        return (msg.json() if msg.status_code == 200 else {}), (hdr.json() if hdr.status_code == 200 else {})
    except httpx.HTTPError:
        return {}, {}
    finally:
        if own:
            client.close()


def _parse_time(headers: dict, summary: dict) -> datetime:
    date_hdr = next((v[0] for k, v in headers.items() if k.lower() == "date" and v), None)
    if date_hdr:
        try:
            return parsedate_to_datetime(date_hdr)
        except (TypeError, ValueError):
            pass
    created = summary.get("Created")
    if created:
        return datetime.fromisoformat(created.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def build_email_event(instance_id: str, summary: dict, message: dict, headers: dict) -> EventRow:
    body_text = message.get("Text") or summary.get("Snippet") or ""
    html = message.get("HTML") or ""
    hit = extract_email_campaign(headers, html or body_text)
    sender = (summary.get("From") or {}).get("Address")
    msgid = summary.get("MessageID") or summary.get("ID")
    return EventRow(
        instance_id=instance_id,
        channel="email",
        local_time=_parse_time(headers, summary),
        title=summary.get("Subject"),
        body=summary.get("Snippet") or body_text[:500],
        big_text=body_text or None,
        campaign_id=hit.campaign_id if hit else None,
        crm_vendor=hit.vendor if hit else None,
        extras={"from": sender, "mailpit_id": summary.get("ID"), "message_id": summary.get("MessageID"),
                "headers": headers, "tags": summary.get("Tags") or []},
        action="received",
        raw={"summary": summary},
        dedupe_key=f"msg:{msgid}",
        dedupe_window=None,
    )


addresses = _addresses


def list_messages(mailpit_url: str, client: httpx.Client, page_size: int = 200, max_pages: int = 50):
    """Yield message summaries from Mailpit, newest first."""
    for page in range(max_pages):
        r = client.get(f"{mailpit_url}/api/v1/messages", params={"start": page * page_size, "limit": page_size})
        r.raise_for_status()
        msgs = r.json().get("messages") or []
        yield from msgs
        if len(msgs) < page_size:
            return
