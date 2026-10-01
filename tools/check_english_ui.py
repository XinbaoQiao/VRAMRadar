"""Collect every user-visible native string in English mode and flag CJK.

Builds the real strip + right-click menu (hidden webview host, synthetic
Codex quota, real local provider probes plus synthetic edge states:
not installed, signed out, exhausted, pending), switches to English, ticks,
and walks every menu item (text, status column, tooltip), strip label,
tooltip/accessible text, plus the dialog/toast/notice specs.  Nothing is
sent anywhere; consent is never changed.

Usage: python tools/check_english_ui.py [--zh]   -> JSON; exit 1 on CJK hits
"""
from __future__ import annotations

import copy
import json
import logging
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def provider_overview(variant: int) -> dict:
    from vram_radar.providers import PROVIDERS
    from vram_radar.providers.base import Environment
    env = Environment()
    states = {}
    for spec in PROVIDERS:
        try:
            states[spec.id] = spec.probe(env)
        except Exception:
            states[spec.id] = {"id": spec.id, "installed": None}
    edits = [
        {},
        {"grok": {"installed": False, "running": False}, "kimi": {"signed_in": False}},
        {"grok": {"session_relogin": True}, "kimi": {"code": "exhausted"}},
    ][variant % 3]
    for pid, patch in edits.items():
        states[pid] = {**states.get(pid, {}), **patch}
    selected = ["codex", "grok", "deepseek", "kimi"]
    return {"enabled": True, "selected": selected, "providers": states,
            "session_consent": ["grok"] if variant != 1 else []}


def dialog_strings(english: bool) -> list[str]:
    from vram_radar import ui_dialogs as ui
    from vram_radar.usage_surface import (consent_precheck, limit_hint_text, limit_menu_text, auto_read_status,
                                          AUTO_READ_FOOTER, CONSENT_ETA_SECONDS)
    lang = "en" if english else "zh-CN"
    out = []
    for name in ("Grok", "Kimi", "Yuanbao" if english else "腾讯元宝"):
        for spec in (ui.consent_spec(name, name, CONSENT_ETA_SECONDS, language=lang), ui.revoke_spec(name, language=lang),
                     ui.notice_spec(consent_precheck({"installed": False}, name, english), name, language=lang),
                     ui.notice_spec(consent_precheck({"installed": True, "signed_in": False}, name, english), name, language=lang)):
            out += [spec["headline"], *spec["lines"], spec.get("note", ""), *(b[1] for b in spec["buttons"])]
    out += [limit_menu_text(4, lang), limit_hint_text(4, lang), AUTO_READ_FOOTER[1 if english else 0]]
    for st in ({"installed": False}, {"signed_in": False}, {"session_relogin": True}, {}):
        out.append(auto_read_status(st, bool(st.get("session_relogin")), english)[0])
    return out


def run(language: str = "en") -> dict:
    import webview
    from benchmark_webview_ui import FakeApi, _wait_until_ready
    from vram_radar.usage_surface import CodexUsageSurface
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    english = language == "en"
    window = webview.create_window("VRAM Radar string audit", width=600, height=400,
                                  url=(ROOT / "src/vram_radar/web/index.html").as_uri(),
                                  js_api=FakeApi(), hidden=True, focus=False)
    state = {"enabled": True, "state": "ready", "windows": [
        {"window_minutes": 300, "remaining_percent": 0, "resets_at": time.time() + 3600 * 47},
        {"window_minutes": 10080, "remaining_percent": 55, "resets_at": time.time() + 86400 * 3}]}
    variant = {"n": 0}
    display = {"codex_time_format": "decimal", "usage_labels": "text", "usage_background": "transparent"}
    surface = CodexUsageSurface(window, lambda: copy.deepcopy(state), language=lambda: language,
                                open_settings=lambda: None, open_home=lambda: None, refresh=lambda: None,
                                display_options=lambda: dict(display), save_display=lambda k, v: {"ok": True},
                                disable=lambda: None, quit_application=lambda: None,
                                providers=lambda: provider_overview(variant["n"]),
                                save_providers=lambda ids: {"ok": True},
                                save_consent=lambda pid, granted: {"ok": True})
    surface.confirm_consent = lambda *a: False          # never open a modal here
    result = {"ok": False, "language": language, "strings": 0, "hits": []}
    timeout = threading.Timer(60, window.destroy)

    def run_inner():
        try:
            from System import Action
            _wait_until_ready(window, time.monotonic() + 20)
            surface.start()
            invoke = lambda cb: window.native.Invoke(Action(cb))
            seen: dict[str, str] = {}

            def walk(items, path):
                for i in range(items.Count):
                    item = items[i]
                    for attr in ("Text", "ShortcutKeyDisplayString", "ToolTipText"):
                        value = str(getattr(item, attr, "") or "")
                        if value:
                            seen.setdefault(value, f"{path}/{i}.{attr}")
                    sub = getattr(item, "DropDownItems", None)
                    if sub is not None and sub.Count:
                        walk(sub, f"{path}/{i}")

            separator_checks = []

            def collect():
                # The greyed "max 4" note sits under its own separator.
                items = surface._models_menu.DropDownItems
                index = items.IndexOf(surface._limit_item)
                if surface._limit_item.Available:
                    separator_checks.append(index > 0 and items[index - 1].Equals(surface._limit_sep)
                                            and bool(surface._limit_sep.Available))
                walk(surface._menu.Items, "menu")
                form = surface.form
                for c in form.Controls:
                    if str(c.Text or ""):
                        seen.setdefault(str(c.Text), "strip.label")
                for value in (form.AccessibleName, form.AccessibleDescription, form.Text):
                    if value:
                        seen.setdefault(str(value), "strip.accessible")

            for n in range(3):
                for labels in ("text", "icons"):
                    variant["n"] = n
                    display["usage_labels"] = labels
                    surface._pending = {"kimi": time.time()} if n == 2 else {}
                    invoke(surface._tick)
                    invoke(surface._tick)
                    invoke(collect)
            for value in dialog_strings(english):
                if value:
                    seen.setdefault(value, "dialog")
            hits = [{"where": where, "text": text[:120]} for text, where in seen.items()
                    if (CJK.search(text) if english else False)]
            result["limit_separator"] = bool(separator_checks) and all(separator_checks)
            result.update(ok=not hits, strings=len(seen), hits=hits[:40],
                          sample=sorted(t[:40] for t in seen) if "--sample" in sys.argv else [])
            if not english:
                result["latin_only"] = [t[:60] for t in seen if not CJK.search(t) and re.search(r"[A-Za-z]{4,}", t)
                                        and t not in ("Codex", "Grok", "DeepSeek", "Kimi", "Claude", "VRAM Radar")][:40]
        except Exception as error:
            result.update(error=f"{type(error).__name__}: {error}")
        finally:
            try:
                surface.stop()
            finally:
                timeout.cancel()
                window.destroy()

    with tempfile.TemporaryDirectory(prefix="string-audit-") as temporary:
        timeout.start()
        try:
            webview.start(run_inner, private_mode=True, storage_path=temporary)
        finally:
            logging.disable(previous_disable)   # in-process callers (tests) keep their logging
    return result


if __name__ == "__main__":
    out = run("zh-CN" if "--zh" in sys.argv else "en")
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(0 if out["ok"] else 1)