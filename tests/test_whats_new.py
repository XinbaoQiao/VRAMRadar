"""What's-new marker parsing and once-per-version dismiss store."""
from __future__ import annotations

from pathlib import Path

from vram_radar.storage import storage_paths
from vram_radar.whats_new import (
    WhatsNewStore,
    dialog_spec,
    lines_for_language,
    parse_whats_new,
)


FEATURE_BODY = """
## Highlights

- quota strip

<!-- vram-radar:whats-new
{"show_whats_new": true,
 "en": ["Pin the AI quota strip on the left.", "Show reset countdowns beside quotas."],
 "zh": ["额度条可固定在任务栏左侧。", "额度旁显示重置倒计时。"]}
-->
"""


def test_parse_requires_opt_in_flag() -> None:
    assert parse_whats_new("## fixes only", version="1.2.3") is None
    body = FEATURE_BODY.replace("true", "false")
    assert parse_whats_new(body, version="1.2.3") is None


def test_parse_feature_marker() -> None:
    notes = parse_whats_new(FEATURE_BODY, version="v1.2.3")
    assert notes is not None
    assert notes["show"] is True
    assert notes["version"] == "1.2.3"
    assert notes["en"][0].startswith("Pin the AI")
    assert "额度" in notes["zh"][0]


def test_parse_rejects_english_with_cjk() -> None:
    body = """<!-- vram-radar:whats-new
{"show_whats_new": true, "en": ["修复了崩溃"], "zh": ["修复了崩溃"]}
-->"""
    assert parse_whats_new(body, version="1.2.3") is None


def test_parse_rejects_missing_language() -> None:
    body = """<!-- vram-radar:whats-new
{"show_whats_new": true, "en": ["Only English"], "zh": []}
-->"""
    assert parse_whats_new(body, version="1.2.3") is None


def test_lines_and_dialog_spec_language() -> None:
    notes = parse_whats_new(FEATURE_BODY, version="1.2.3")
    assert lines_for_language(notes, "en") == notes["en"]
    assert lines_for_language(notes, "zh-CN") == notes["zh"]
    en = dialog_spec(notes, "en")
    zh = dialog_spec(notes, "zh-CN")
    assert en["headline"] == "What's new"
    assert zh["headline"] == "新功能"
    assert en["buttons"][0][1] == "Got it"
    assert zh["buttons"][0][1] == "知道了"


def test_store_offer_once_per_version(tmp_path: Path) -> None:
    paths = storage_paths(tmp_path)
    store = WhatsNewStore(paths, "default")
    notes = parse_whats_new(FEATURE_BODY, version="9.9.9")
    assert store.should_offer(notes)
    store.set_pending(notes)
    pending = store.take_pending_for("v9.9.9")
    assert pending is not None
    assert pending["version"] == "9.9.9"
    store.dismiss("9.9.9")
    assert not store.should_offer(notes)
    assert store.take_pending_for("v9.9.9") is None


def test_store_pending_ignores_other_version(tmp_path: Path) -> None:
    paths = storage_paths(tmp_path)
    store = WhatsNewStore(paths, "default")
    notes = parse_whats_new(FEATURE_BODY, version="1.0.0")
    store.set_pending(notes)
    assert store.take_pending_for("v1.0.1") is None
    assert store.take_pending_for("v1.0.0") is not None


def test_bugfix_body_without_marker_is_silent() -> None:
    body = "## v1.2.4\n\n- Fix SSH reconnect\n- Fix tray flicker\n"
    assert parse_whats_new(body, version="1.2.4") is None
