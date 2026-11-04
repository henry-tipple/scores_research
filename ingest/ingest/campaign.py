"""Pluggable CRM campaign-id extraction.

Each extractor looks at a push extras bundle (or email headers/body) and returns a
CampaignHit or None. Extractors run in registration order; the first hit wins.
Add a vendor with:

    @push_extractor("myvendor")
    def _myvendor(extras): ...

Keys marked `verified: false` are educated guesses from vendor SDK docs and must be
confirmed against real payloads once the apps can be observed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class CampaignHit:
    vendor: str
    campaign_id: str


PushExtractor = Callable[[Mapping[str, Any]], "str | None"]
EmailExtractor = Callable[[Mapping[str, list[str]], str], "str | None"]

PUSH_EXTRACTORS: list[tuple[str, PushExtractor]] = []
EMAIL_EXTRACTORS: list[tuple[str, EmailExtractor]] = []


def push_extractor(vendor: str):
    def deco(fn: PushExtractor) -> PushExtractor:
        PUSH_EXTRACTORS.append((vendor, fn))
        return fn
    return deco


def email_extractor(vendor: str):
    def deco(fn: EmailExtractor) -> EmailExtractor:
        EMAIL_EXTRACTORS.append((vendor, fn))
        return fn
    return deco


def _maybe_json(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _s(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


# ---------------------------------------------------------------- push vendors

@push_extractor("braze")          # verified in brief: Braze puts the campaign id in `cid`
def _braze(extras):
    return _s(extras.get("cid"))


@push_extractor("onesignal")      # verified in brief: OneSignal `custom` JSON carries `i`
def _onesignal(extras):
    custom = _maybe_json(extras.get("custom"))
    if isinstance(custom, dict) and custom.get("i"):
        return _s(custom["i"])
    return None


@push_extractor("airship")        # verified: false
def _airship(extras):
    return _s(extras.get("com.urbanairship.push.PUSH_ID") or extras.get("_uamid"))


@push_extractor("clevertap")      # verified: false
def _clevertap(extras):
    return _s(extras.get("wzrk_id")) if extras.get("wzrk_pn") is not None or "wzrk_id" in extras else None


@push_extractor("iterable")       # verified: false
def _iterable(extras):
    itbl = _maybe_json(extras.get("itbl"))
    return _s(itbl.get("campaignId")) if isinstance(itbl, dict) else None


@push_extractor("firebase")       # verified: false - Firebase console campaigns
def _firebase(extras):
    return _s(extras.get("google.c.a.c_id"))


def extract_push_campaign(extras: Mapping[str, Any]) -> CampaignHit | None:
    for vendor, fn in PUSH_EXTRACTORS:
        try:
            cid = fn(extras)
        except Exception:  # an extractor must never break ingest
            cid = None
        if cid:
            return CampaignHit(vendor, cid)
    return None


# ---------------------------------------------------------------- email vendors

def _header(headers: Mapping[str, list[str]], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name.lower() and v:
            return v[0]
    return None


@email_extractor("header")        # generic X-Campaign-Id style headers; verified: false
def _email_header(headers, body):
    for name in ("X-Campaign-Id", "X-Campaign", "X-Braze-Campaign-Id", "X-Mailgun-Campaign-Id",
                 "X-SFMC-Campaign", "X-Mailchimp-Campaign"):
        if v := _header(headers, name):
            return v
    return None


@email_extractor("utm")           # fall back to utm_campaign in the first tracked link
def _email_utm(headers, body):
    for url in re.findall(r"https?://[^\s\"'<>]+", body or ""):
        q = parse_qs(urlparse(url).query)
        if q.get("utm_campaign"):
            return q["utm_campaign"][0]
    return None


def extract_email_campaign(headers: Mapping[str, list[str]], body: str) -> CampaignHit | None:
    for vendor, fn in EMAIL_EXTRACTORS:
        try:
            cid = fn(headers, body)
        except Exception:
            cid = None
        if cid:
            return CampaignHit(vendor, cid)
    return None


DEEP_LINK_KEYS = ("deep_link", "deeplink", "deepLink", "uri", "ab_uri", "url", "link", "u")


def extract_deep_link(extras: Mapping[str, Any]) -> str | None:
    for k in DEEP_LINK_KEYS:
        if v := extras.get(k):
            return str(v)
    custom = _maybe_json(extras.get("custom"))
    if isinstance(custom, dict) and custom.get("u"):
        return str(custom["u"])
    return None
