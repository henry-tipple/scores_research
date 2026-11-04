import json

from ingest.campaign import (
    PUSH_EXTRACTORS,
    extract_deep_link,
    extract_email_campaign,
    extract_push_campaign,
    push_extractor,
)
from ingest.store import dedupe_key_for_push


def test_braze_cid():
    hit = extract_push_campaign({"cid": "abc123", "t": "x"})
    assert (hit.vendor, hit.campaign_id) == ("braze", "abc123")


def test_onesignal_custom_json():
    hit = extract_push_campaign({"custom": json.dumps({"i": "os-1", "u": "app://x"})})
    assert (hit.vendor, hit.campaign_id) == ("onesignal", "os-1")
    assert extract_deep_link({"custom": json.dumps({"i": "os-1", "u": "app://x"})}) == "app://x"


def test_onesignal_custom_already_dict():
    assert extract_push_campaign({"custom": {"i": "os-2"}}).campaign_id == "os-2"


def test_no_campaign():
    assert extract_push_campaign({"android.title": "Goal"}) is None
    assert extract_push_campaign({}) is None
    assert extract_push_campaign({"custom": "not json"}) is None


def test_other_vendors():
    assert extract_push_campaign({"google.c.a.c_id": "fb-9"}).vendor == "firebase"
    assert extract_push_campaign({"wzrk_id": "ct-1", "wzrk_pn": "true"}).vendor == "clevertap"
    assert extract_push_campaign({"itbl": json.dumps({"campaignId": 42})}).campaign_id == "42"


def test_extractor_is_pluggable():
    @push_extractor("acme")
    def _acme(extras):
        return extras.get("acme_campaign")

    try:
        assert extract_push_campaign({"acme_campaign": "A-1"}).vendor == "acme"
    finally:
        PUSH_EXTRACTORS.pop()


def test_broken_extractor_does_not_break_ingest():
    @push_extractor("broken")
    def _broken(extras):
        raise RuntimeError("boom")

    try:
        assert extract_push_campaign({"cid": "still-works"}).campaign_id == "still-works"
        assert extract_push_campaign({}) is None
    finally:
        PUSH_EXTRACTORS.pop()


def test_email_header_then_utm():
    assert extract_email_campaign({"x-campaign-id": ["wk-1"]}, "").campaign_id == "wk-1"
    hit = extract_email_campaign({}, '<a href="https://x.test/a?utm_source=crm&utm_campaign=winback">')
    assert (hit.vendor, hit.campaign_id) == ("utm", "winback")
    assert extract_email_campaign({}, "no links") is None


def test_dedupe_keys():
    assert dedupe_key_for_push("push", "c1", "k", None, "t", "b", None) == "cid:c1"
    a = dedupe_key_for_push("live_update", "c1", "k", None, "t", "1-0", None)
    b = dedupe_key_for_push("live_update", "c1", "k", None, "t", "2-0", None)
    assert a != b and a.startswith("nk:k:")   # live cards key on content, not campaign
    assert dedupe_key_for_push("push", None, "k", "ch", "t", "b", None) == \
        dedupe_key_for_push("push", None, "k", "ch", "t", "b", None)
