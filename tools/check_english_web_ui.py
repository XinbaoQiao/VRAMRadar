"""Render the main window headlessly in English and flag any CJK text.

Copies ``src/vram_radar/web`` into a temporary folder, injects the synthetic
pywebview API from ``tools/web_ui_stub.js`` (no real hosts, no network) and
loads several states (healthy, stale/error, offline, first connection) with a
headless Chromium-family browser (Edge or Chrome).  Every visible text node
and localized attribute (aria-label, title, placeholder, alt, data-label) in
the resulting DOM, including closed dialogs and collapsed sections, must be
free of CJK characters.

Usage: python tools/check_english_web_ui.py [--browser PATH] [--zh]
Prints JSON; exit 1 on CJK hits, 2 when no browser is available.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "vram_radar" / "web"
STUB = ROOT / "tools" / "web_ui_stub.js"
CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")
ATTRIBUTES = {"aria-label", "title", "placeholder", "alt", "data-label"}
# Language names are intentionally shown in their own language.
ALLOWED = {"简体中文"}
# English count agreement and repeated list items (e.g. "Data is stale, Data is stale").
REPEAT_EN = re.compile(r"(?:^|, )([^,]{3,}), \1(?:,|$)")
GRAMMAR = (
    re.compile(r"(?<![\d.,/])1 GPUs\b"),
    re.compile(r"(?<![\d.,/])(?!1 )\d[\d,.]* GPU\b(?!s)"),
    re.compile(r"(?<![\d.,/])(?!1/)\d[\d,.]*/1 GPUs\b"),
    REPEAT_EN,
    re.compile(r"(?<![\d.,/\u2013-])1 (?:servers|items|days|hours|minutes|seconds|nodes|jobs|users|matches)\b"),
)
# The same repeated-item check for the Chinese UI (e.g. 数据已过期，数据已过期).
REPEAT_ZH = re.compile(r"(?:^|，)([^，]{2,})，\1(?:，|$)")
SCENARIOS = ("full", "clean", "offline", "connecting", "single")
# Collapsed sections are rendered again with every <details> opened.  Cluster
# node modules are excluded: they page through get_cluster_nodes, which keeps the
# headless virtual clock busy without adding new strings.
EXPANSIONS = ("0", "details:not(.cluster-module)")


def find_browser(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return next((path for path in candidates if path and Path(path).is_file()), None)


class TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.hits: list[str] = []
        self.grammar: list[str] = []
        self.repeats: list[str] = []

    def inert(self) -> bool:
        # <template> content is not part of the live document; clones are
        # localized when inserted and are covered by the expanded renders.
        return "template" in self.stack

    def handle_starttag(self, tag, attrs):
        if self.inert():
            if tag == "template":
                self.stack.append(tag)
            return
        if tag not in {"br", "img", "input", "meta", "link", "hr", "source", "wbr"}:
            self.stack.append(tag)
        for key, value in attrs:
            if key in ATTRIBUTES and value and CJK.search(value) and value.strip() not in ALLOWED:
                self.hits.append(f"[{tag} {key}] {value.strip()}")
            if key in ATTRIBUTES and value and any(rule.search(value) for rule in GRAMMAR):
                self.grammar.append(f"[{tag} {key}] {value.strip()}")
            if key in ATTRIBUTES and value and REPEAT_ZH.search(value):
                self.repeats.append(f"[{tag} {key}] {value.strip()}")

    def handle_endtag(self, tag):
        if self.inert() and tag != "template":
            return
        while self.stack:
            if self.stack.pop() == tag:
                break

    def handle_data(self, data):
        if self.inert() or (self.stack and self.stack[-1] in {"script", "style"}):
            return
        text = data.strip()
        if text and CJK.search(text) and text not in ALLOWED:
            self.hits.append(text)
        if text and any(rule.search(text) for rule in GRAMMAR):
            self.grammar.append(text)
        if text and REPEAT_ZH.search(text):
            self.repeats.append(text)


def prepare(folder: Path) -> Path:
    site = folder / "web"
    shutil.copytree(WEB, site)
    shutil.copy2(STUB, site / "web_ui_stub.js")
    index = site / "index.html"
    markup = index.read_text(encoding="utf-8")
    first_script = markup.index("<script")
    markup = markup[:first_script] + '<script src="web_ui_stub.js"></script>\n  ' + markup[first_script:]
    index.write_text(markup, encoding="utf-8")
    return index


def render(browser: str, index: Path, profile: Path, query: str) -> str:
    url = index.resolve().as_uri() + "?" + query
    command = [
        browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
        "--disable-extensions", "--allow-file-access-from-files", f"--user-data-dir={profile}",
        "--window-size=1280,2400", "--virtual-time-budget=8000", "--dump-dom", url,
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(command, capture_output=True, timeout=180, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        return (exc.stdout or b"").decode("utf-8", "replace")
    return result.stdout.decode("utf-8", "replace")


def run(language: str = "en", browser: str | None = None) -> dict:
    """Render every scenario; ``ok`` is None when no browser is available."""
    browser = find_browser(browser)
    if not browser:
        return {"ok": None, "language": language, "skipped": "no Chromium-family browser found"}
    report: dict = {"language": language, "scenarios": {}}
    failed = False
    with tempfile.TemporaryDirectory(prefix="vram-radar-web-ui-") as temp:
        folder = Path(temp)
        index = prepare(folder)
        for scenario in SCENARIOS:
            for number, expand in enumerate(EXPANSIONS):
                key = f"{scenario}/{number}"
                dom = render(browser, index, folder / f"profile-{scenario}-{number}",
                             f"lang={language}&v={scenario}&expand={quote(expand)}")
                if "A100 Cluster" not in dom:
                    report["scenarios"][key] = {"error": "page did not render"}
                    failed = True
                    continue
                collector = TextCollector()
                collector.feed(dom)
                hits = sorted(set(collector.hits))
                report["scenarios"][key] = {"dom_bytes": len(dom), "cjk": hits}
                # In Chinese mode the check is inverted: CJK text must be present.
                failed = failed or (not hits if language != "en" else bool(hits))
                grammar = sorted(set(collector.grammar if language == "en" else collector.repeats))
                report["scenarios"][key]["grammar"] = grammar
                failed = failed or bool(grammar)
    report["ok"] = not failed
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--browser")
    parser.add_argument("--zh", action="store_true", help="render Chinese instead (sanity check: expects CJK)")
    args = parser.parse_args(argv)
    report = run("zh-CN" if args.zh else "en", args.browser)
    # Keep CJK readable on UTF-8 consoles; escape it on legacy code pages (GBK).
    utf8 = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") == "utf8"
    print(json.dumps(report, ensure_ascii=not utf8, indent=1))
    if report["ok"] is None:
        return 2
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
