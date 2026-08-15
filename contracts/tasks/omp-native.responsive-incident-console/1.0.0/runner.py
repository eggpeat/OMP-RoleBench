#!/usr/bin/env python3
"""Static and rendered-layout verifier runner for the responsive UI task."""

from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
from typing import NoReturn
import urllib.error
import urllib.request

MAX_ARTIFACT_BYTES = 96 * 1024
MAX_HTML_BYTES = 56 * 1024
MAX_CSS_BYTES = 40 * 1024
SCHEMA_VERSION = "rolebench.ui-implementation/v1"
SNAPSHOT_VERSION = "rolebench.ui-runner-snapshot/v1"
FORBIDDEN = re.compile(r"(?is)<script\b|<iframe\b|\b(?:src|href)\s*=\s*[\"'](?:https?:|data:|javascript:)|@import\b|url\s*\(|\son[a-z]+\s*=")
URL_ATTRIBUTES = frozenset(
    {
        "action",
        "background",
        "cite",
        "formaction",
        "href",
        "manifest",
        "ping",
        "poster",
        "src",
        "srcset",
        "xlink:href",
    }
)
AUDIT_MARKER = "rolebench-audit"
AUDIT = r"""
(() => {
  const brief = __ROLEBENCH_BRIEF__;
  const visible = (node) => {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    let effectiveOpacity = 1;
    for (let current = node; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
      effectiveOpacity *= Number(style.opacity);
    }
    return (
      effectiveOpacity > 0 &&
      rect.width > 0 &&
      rect.height > 0 &&
      rect.right > 0 &&
      rect.bottom > 0 &&
      rect.left < window.innerWidth &&
      rect.top < window.innerHeight
    );
  };
  const rendered = (node) => {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    let effectiveOpacity = 1;
    for (let current = node; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
      effectiveOpacity *= Number(style.opacity);
    }
    return effectiveOpacity > 0 && rect.width > 0 && rect.height > 0;
  };
  const normalize = (node) => node ? node.innerText.replace(/\s+/g, ' ').trim() : '';
  const rgba = (value) => {
    const values = value.match(/[\d.]+/g);
    if (!values) return [0, 0, 0, 0];
    return [
      Number(values[0]),
      Number(values[1]),
      Number(values[2]),
      values.length > 3 ? Number(values[3]) : 1
    ];
  };
  const blend = (foreground, background) => {
    const alpha = foreground[3];
    return [
      foreground[0] * alpha + background[0] * (1 - alpha),
      foreground[1] * alpha + background[1] * (1 - alpha),
      foreground[2] * alpha + background[2] * (1 - alpha)
    ];
  };
  const effectiveBackground = (node) => {
    const layers = [];
    for (let current = node; current; current = current.parentElement) {
      layers.push(rgba(getComputedStyle(current).backgroundColor));
    }
    let color = [255, 255, 255];
    for (const layer of layers.reverse()) color = blend(layer, color);
    return color;
  };
  const luminance = (color) => {
    const channels = color.map((item) => {
      const normalized = item / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : Math.pow((normalized + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  };
  const contrast = (foreground, background) => {
    const first = luminance(foreground);
    const second = luminance(background);
    return Math.round(((Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)) * 100) / 100;
  };
  const nodeContrast = (node) => {
    const background = effectiveBackground(node);
    const foreground = blend(rgba(getComputedStyle(node).color), background);
    return contrast(foreground, background);
  };

  const sidebar = document.querySelector('[data-role="sidebar"]');
  const menu = document.querySelector('[data-role="mobile-menu"]');
  const grid = document.querySelector('[data-role="incident-grid"]');
  const primary = document.querySelector('[data-role="primary-action"]');
  const disclosures = [...document.querySelectorAll('details')];
  let primaryFocused = false;
  if (primary) {
    primary.focus();
    primaryFocused = document.activeElement === primary;
  }

  const primaryStyle = primary ? getComputedStyle(primary) : null;
  const primaryRect = primary ? primary.getBoundingClientRect() : {width: 0, height: 0};
  const primaryBackground = primary ? effectiveBackground(primary) : [255, 255, 255];
  const primaryForeground = primaryStyle ? blend(rgba(primaryStyle.color), primaryBackground) : [255, 255, 255];
  const surroundingBackground = primary && primary.parentElement
    ? effectiveBackground(primary.parentElement)
    : [255, 255, 255];
  const outline = primaryStyle ? rgba(primaryStyle.outlineColor) : [0, 0, 0, 0];
  const outlineContrast = contrast(blend(outline, surroundingBackground), surroundingBackground);

  const cards = grid ? [...grid.querySelectorAll('article')].filter(rendered) : [];
  const cardRects = cards.map((card) => card.getBoundingClientRect());
  const firstTop = cardRects.length ? Math.min(...cardRects.map((rect) => rect.top)) : 0;
  const columns = cardRects.filter((rect) => Math.abs(rect.top - firstTop) < 2).length;

  const navLabels = [...document.querySelectorAll('nav a')].map(normalize);
  const filterControls = [...document.querySelectorAll('select')].filter(visible);
  const filterOptions = filterControls.flatMap((control) =>
    [...control.querySelectorAll('option')].map(normalize)
  );
  const productRendered = normalize(document.querySelector('header')).includes(brief.product);
  const navigationRendered =
    navLabels.length === brief.navigation.length &&
    brief.navigation.every((label, index) => navLabels[index] === label);
  const filtersRendered =
    filterControls.length > 0 &&
    brief.filters.every((label) => filterOptions.includes(label));
  const incidentCardMatches = brief.incidents.map((incident) =>
    cards.filter((candidate) => normalize(candidate).includes(incident.id))
  );
  const matchedCards = incidentCardMatches.map((matches) => matches.length === 1 ? matches[0] : null);
  const incidentsRendered =
    cards.length === brief.incidents.length &&
    matchedCards.every(Boolean) &&
    new Set(matchedCards).size === brief.incidents.length &&
    brief.incidents.every((incident, index) => {
      const card = matchedCards[index];
      const text = normalize(card);
      const details = card ? card.querySelector('details') : null;
      return Boolean(
        card &&
        details &&
        text.includes(incident.title) &&
        text.toLowerCase().includes(String(incident.severity).toLowerCase()) &&
        text.includes(incident.service) &&
        text.includes(incident.age) &&
        normalize(details).includes(incident.timeline)
      );
    });
  const disclosureVisible =
    disclosures.length === brief.incidents.length &&
    disclosures.every((details) => {
      const summary = details.querySelector(':scope > summary');
      return (
        details.open &&
        rendered(details) &&
        rendered(summary) &&
        [...details.children].some((child) => child.tagName !== 'SUMMARY' && rendered(child))
      );
    });

  const textElements = new Set();
  const textWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (textWalker.nextNode()) {
    const parent = textWalker.currentNode.parentElement;
    if (parent && textWalker.currentNode.nodeValue.trim() && rendered(parent)) {
      textElements.add(parent);
    }
  }
  for (const control of document.querySelectorAll('button, input, select, textarea')) {
    if (
      rendered(control) &&
      (normalize(control) || String(control.value || '').trim() || String(control.placeholder || '').trim())
    ) {
      textElements.add(control);
    }
  }
  const textContrasts = [...textElements].map(nodeContrast);

  return {
    viewport_width: window.innerWidth,
    horizontal_overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    sidebar_visible: visible(sidebar),
    mobile_menu_visible: visible(menu),
    grid_columns: columns,
    primary_width: Math.round(primaryRect.width * 100) / 100,
    primary_height: Math.round(primaryRect.height * 100) / 100,
    primary_contrast: primaryStyle ? contrast(primaryForeground, primaryBackground) : 0,
    body_contrast: nodeContrast(document.body),
    minimum_text_contrast: textContrasts.length ? Math.min(...textContrasts) : 0,
    focus_indicator: Boolean(
      primaryFocused &&
      visible(primary) &&
      primaryStyle &&
      primaryStyle.outlineStyle !== 'none' &&
      parseFloat(primaryStyle.outlineWidth) >= 2 &&
      parseFloat(primaryStyle.outlineOffset) >= 0 &&
      outline[3] > 0 &&
      outlineContrast >= 3
    ),
    disclosure_visible_after_open: disclosureVisible,
    brief_content_rendered:
      productRendered && navigationRendered && filtersRendered && incidentsRendered
  };
})()
"""


class SubmissionError(ValueError):
    """Malformed or unsafe UI artifact."""


class StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: dict[str, int] = {}
        self.attributes: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag] = self.tags.get(tag, 0) + 1
        self.attributes.append((tag, dict(attrs)))

def _forbidden_asset_url(value: str | None) -> bool:
    if value is None:
        return False
    candidates = [value, *re.split(r"[\s,]+", value)]
    return any(
        compact.startswith("//")
        or re.match(r"^[a-z][a-z0-9+.-]*:", compact) is not None
        for candidate in candidates
        if (compact := re.sub(r"[\x00-\x20]+", "", candidate).casefold())
    )


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_brief() -> tuple[bytes, dict[str, object]]:
    path = Path("/opt/rolebench/task/public/workspace/brief.json")
    if not path.is_file():
        path = Path(__file__).resolve().parent / "public" / "workspace" / "brief.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SubmissionError("brief is malformed")
    return raw, value


def _required_content(brief: dict[str, object]) -> list[str]:
    values = [brief.get("product")]
    values.extend(brief.get("navigation", []))
    values.extend(brief.get("filters", []))
    for incident in brief.get("incidents", []):
        if isinstance(incident, dict):
            values.extend(incident.get(key) for key in ("id", "title", "severity", "service", "age", "timeline"))
    if any(not isinstance(value, str) for value in values):
        raise SubmissionError("brief content is malformed")
    return [str(value) for value in values]


def _validate_submission(value: object, brief: dict[str, object]) -> tuple[str, str, dict[str, bool]]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "files"} or value.get("schema_version") != SCHEMA_VERSION:
        raise SubmissionError("submission schema is invalid")
    files = value.get("files")
    if not isinstance(files, dict) or set(files) != {"index.html", "styles.css"}:
        raise SubmissionError("files must contain exactly index.html and styles.css")
    html = files.get("index.html")
    css = files.get("styles.css")
    if not isinstance(html, str) or not isinstance(css, str):
        raise SubmissionError("UI files must be UTF-8 strings")
    if not 1 <= len(html.encode("utf-8")) <= MAX_HTML_BYTES or not 1 <= len(css.encode("utf-8")) <= MAX_CSS_BYTES:
        raise SubmissionError("UI file exceeds its size bound")
    combined = html + "\n" + css
    if FORBIDDEN.search(combined) or AUDIT_MARKER in combined:
        raise SubmissionError("UI contains forbidden active or remote content")
    if not html.lstrip().lower().startswith("<!doctype html>"):
        raise SubmissionError("index.html must be a complete HTML document")

    parser = StructureParser()
    parser.feed(html)
    if (
        any(
            _forbidden_asset_url(value)
            for tag, attrs in parser.attributes
            for name, value in attrs.items()
            if name in URL_ATTRIBUTES or (tag == "object" and name == "data")
        )
        or any(
            FORBIDDEN.search(value) is not None
            for _, attrs in parser.attributes
            if (value := attrs.get("style")) is not None
        )
        or any(
            tag == "base"
            or (
                tag == "meta"
                and (attrs.get("http-equiv") or "").strip().casefold() == "refresh"
            )
            for tag, attrs in parser.attributes
        )
    ):
        raise SubmissionError("UI contains forbidden active or remote content")
    semantics = all(parser.tags.get(tag, 0) >= count for tag, count in {"header": 1, "nav": 1, "main": 1, "section": 1, "article": 3, "form": 1, "details": 3, "summary": 3, "button": 1}.items())
    linked_css = any(tag == "link" and attrs.get("rel") == "stylesheet" and attrs.get("href") == "styles.css" for tag, attrs in parser.attributes)
    active_nav = any(attrs.get("aria-current") == "page" for _, attrs in parser.attributes)
    mobile_label = any(attrs.get("data-role") == "mobile-menu" and bool(attrs.get("aria-label")) for _, attrs in parser.attributes)
    hooks = all(any(attrs.get("data-role") == role for _, attrs in parser.attributes) for role in ("sidebar", "mobile-menu", "incident-grid", "primary-action"))
    content_complete = all(item in html for item in _required_content(brief))
    checks = {
        "semantic_structure": semantics,
        "stylesheet_linked": linked_css,
        "active_navigation_labelled": active_nav,
        "mobile_menu_labelled": mobile_label,
        "audit_hooks_present": hooks,
        "brief_content_complete": content_complete,
        "responsive_rule_present": re.search(r"(?is)@media[^\{]*max-width\s*:\s*[0-9]+px", css) is not None,
        "focus_visible_rule_present": ":focus-visible" in css,
    }
    return html, css, checks


class CDPClient:
    """Minimal local Chrome DevTools Protocol client."""

    def __init__(self, websocket_url: str) -> None:
        match = re.fullmatch(r"ws://([^/:]+):([0-9]+)(/.*)", websocket_url)
        if match is None:
            raise SubmissionError("Chromium returned an invalid debugger URL")
        host, port, target = match.groups()
        self._socket = socket.create_connection((host, int(port)), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response and len(response) <= 16 * 1024:
            chunk = self._socket.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
        if not response.startswith(b"HTTP/1.1 101 "):
            self._socket.close()
            raise SubmissionError("Chromium debugger handshake failed")
        self._next_id = 0

    def close(self) -> None:
        self._socket.close()

    def _read_exact(self, count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            chunk = self._socket.recv(count - len(result))
            if not chunk:
                raise SubmissionError("Chromium debugger disconnected")
            result.extend(chunk)
        return bytes(result)

    def _send_frame(self, payload: bytes, *, opcode: int = 1) -> None:
        mask = os.urandom(4)
        size = len(payload)
        header = bytearray([0x80 | opcode])
        if size < 126:
            header.append(0x80 | size)
        elif size <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", size))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", size))
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self._socket.sendall(bytes(header) + mask + masked)

    def _receive_message(self) -> dict[str, object]:
        fragments = bytearray()
        while True:
            first, second = self._read_exact(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            size = second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._read_exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if second & 0x80 else None
            payload = self._read_exact(size)
            if mask is not None:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 8:
                raise SubmissionError("Chromium debugger closed unexpectedly")
            if opcode == 9:
                self._send_frame(payload, opcode=10)
                continue
            if opcode not in (0, 1):
                continue
            fragments.extend(payload)
            if not final:
                continue
            try:
                message = json.loads(fragments.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SubmissionError("Chromium debugger returned invalid JSON") from error
            if not isinstance(message, dict):
                raise SubmissionError("Chromium debugger returned a non-object message")
            return message

    def command(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self._next_id += 1
        command_id = self._next_id
        payload: dict[str, object] = {"id": command_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send_frame(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        while True:
            message = self._receive_message()
            if message.get("id") != command_id:
                continue
            if "error" in message:
                raise SubmissionError(f"Chromium debugger rejected {method}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise SubmissionError(f"Chromium debugger returned an invalid {method} result")
            return result


def _debugger_target() -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with opener.open("http://127.0.0.1:9222/json/list", timeout=1) as response:
                targets = json.load(response)
            if isinstance(targets, list):
                for target in targets:
                    if isinstance(target, dict) and target.get("type") == "page" and isinstance(target.get("webSocketDebuggerUrl"), str):
                        return target["webSocketDebuggerUrl"]
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(0.05)
    raise SubmissionError("Chromium debugger did not become ready")

def _runtime_value(client: CDPClient, expression: str) -> object:
    evaluated = client.command(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
    )
    remote_result = evaluated.get("result")
    if not isinstance(remote_result, dict) or "value" not in remote_result:
        raise SubmissionError("Chromium interaction returned an invalid value")
    return remote_result["value"]


def _exercise_disclosures(client: CDPClient, expected_count: int) -> None:
    count = _runtime_value(client, "document.querySelectorAll('details > summary').length")
    if isinstance(count, bool) or not isinstance(count, int) or count != expected_count:
        raise SubmissionError("UI disclosure count does not match the brief")
    for index in range(count):
        state = _runtime_value(
            client,
            f"""(() => {{
  const details = document.querySelectorAll('details')[{index}];
  const summary = details ? details.querySelector(':scope > summary') : null;
  if (!details || !summary) return null;
  summary.scrollIntoView({{block: 'center'}});
  summary.focus();
  return {{focused: document.activeElement === summary, open: details.open}};
}})()""",
        )
        if (
            not isinstance(state, dict)
            or set(state) != {"focused", "open"}
            or state.get("focused") is not True
            or state.get("open") is not False
        ):
            raise SubmissionError("UI disclosure summary is not keyboard-focusable and closed")
        client.command(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Enter",
                "code": "Enter",
                "text": "\r",
                "unmodifiedText": "\r",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            },
        )
        client.command(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            },
        )
        if (
            _runtime_value(
                client,
                f"document.querySelectorAll('details')[{index}].open",
            )
            is not True
        ):
            raise SubmissionError("UI disclosure did not open through its summary")
    _runtime_value(client, "scrollTo(0, 0); true")


def _render(work: Path, width: int, height: int, brief: dict[str, object]) -> dict[str, object]:
    profile = work / f"chromium-{width}"
    process = subprocess.Popen(
        [
            "/usr/bin/chromium",
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--remote-allow-origins=*",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9222",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={"HOME": "/workspace/rolebench-home", "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin", "TMPDIR": "/workspace"},
    )
    client: CDPClient | None = None
    try:
        client = CDPClient(_debugger_target())
        client.command("Page.enable")
        client.command("Runtime.enable")
        client.command(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": False,
                "screenWidth": width,
                "screenHeight": height,
            },
        )
        navigation = client.command("Page.navigate", {"url": (work / "index.html").as_uri()})
        if navigation.get("errorText"):
            raise SubmissionError("Chromium could not load the UI")
        client.command(
            "Runtime.evaluate",
            {
                "expression": "new Promise(resolve => document.readyState === 'complete' ? resolve(true) : addEventListener('load', () => resolve(true), {once:true}))",
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        incidents = brief.get("incidents")
        if not isinstance(incidents, list):
            raise SubmissionError("brief incidents are malformed")
        _exercise_disclosures(client, len(incidents))
        audit_expression = AUDIT.replace(
            "__ROLEBENCH_BRIEF__",
            json.dumps(brief, sort_keys=True, separators=(",", ":")),
        )
        evaluated = client.command(
            "Runtime.evaluate",
            {"expression": audit_expression, "returnByValue": True},
        )
        remote_result = evaluated.get("result")
        audit = remote_result.get("value") if isinstance(remote_result, dict) else None
        if not isinstance(audit, dict):
            raise SubmissionError("Chromium layout audit returned an invalid value")
        captured = client.command(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
        )
        encoded = captured.get("data")
        if not isinstance(encoded, str):
            raise SubmissionError("Chromium screenshot is missing")
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise SubmissionError("Chromium screenshot is invalid") from error
        audit["screenshot_bytes"] = len(data)
        audit["screenshot_sha256"] = hashlib.sha256(data).hexdigest()
        return audit
    finally:
        if client is not None:
            client.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _snapshot(*, status: str, error: str | None, brief_sha256: str | None, source_checks: object, desktop: object, mobile: object) -> str:
    return json.dumps({"schema_version": SNAPSHOT_VERSION, "status": status, "error": error, "brief_sha256": brief_sha256, "source_checks": source_checks, "desktop": desktop, "mobile": mobile}, sort_keys=True, separators=(",", ":"))


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        sys.stdout.write(_snapshot(status="rejected", error="artifact exceeds 96 KiB", brief_sha256=None, source_checks=None, desktop=None, mobile=None))
        return 0
    try:
        brief_bytes, brief = _load_brief()
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        html, css, checks = _validate_submission(parsed, brief)
        with tempfile.TemporaryDirectory(prefix="rolebench-ui-", dir="/workspace") as temporary:
            work = Path(temporary)
            (work / "index.html").write_text(html, encoding="utf-8")
            (work / "styles.css").write_text(css, encoding="utf-8")
            Path("/workspace/rolebench-home").mkdir(mode=0o700, exist_ok=True)
            desktop = _render(work, 1280, 800, brief)
            mobile = _render(work, 390, 844, brief)
        brief_sha256 = hashlib.sha256(brief_bytes).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, OSError, TypeError, RecursionError, subprocess.SubprocessError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), brief_sha256=None, source_checks=None, desktop=None, mobile=None))
        return 0
    sys.stdout.write(_snapshot(status="executed", error=None, brief_sha256=brief_sha256, source_checks=checks, desktop=desktop, mobile=mobile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
