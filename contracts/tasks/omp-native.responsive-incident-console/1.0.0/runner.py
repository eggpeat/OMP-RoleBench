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
    let coverage = 0;
    for (let current = node; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (coverage < 0.999 && style.backgroundImage !== 'none') return null;
      const layer = rgba(style.backgroundColor);
      layers.push(layer);
      coverage += (1 - coverage) * layer[3];
      if (coverage >= 0.999) break;
    }
    let color = [255, 255, 255];
    for (const layer of layers.reverse()) {
      const a = layer[3];
      color = [
        layer[0] * a + color[0] * (1 - a),
        layer[1] * a + color[1] * (1 - a),
        layer[2] * a + color[2] * (1 - a),
      ];
    }
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
    const hi = Math.max(first, second);
    const lo = Math.min(first, second);
    return (hi + 0.05) / (lo + 0.05);
  };
  const effectiveOpacity = (node) => {
    let opacity = 1;
    for (let current = node; current; current = current.parentElement) {
      opacity *= Number(getComputedStyle(current).opacity || 1);
    }
    return opacity;
  };
  const nodeContrast = (node) => {
    if (!node) return 0;
    const background = effectiveBackground(node);
    return background
      ? contrast(blend(rgba(getComputedStyle(node).color), background), background)
      : 0;
  };
  const focusStyle = (node) => {
    if (!node) return null;
    const style = getComputedStyle(node);
    return {
      background: effectiveBackground(node),
      boxShadow: style.boxShadow,
      outlineColor: style.outlineColor,
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      borders: ['Top', 'Right', 'Bottom', 'Left'].map((side) => ({
        color: style[`border${side}Color`],
        style: style[`border${side}Style`],
        width: Number.parseFloat(style[`border${side}Width`]),
      })),
    };
  };
  const focusIndicatorFor = (node, before) => {
    if (!node || !before || document.activeElement !== node || !visible(node)) return false;
    const after = focusStyle(node);
    const background = after.background
      || effectiveBackground(node.parentElement || node)
      || [255, 255, 255];
    const outline = rgba(after.outlineColor);
    const outlineChanged =
      after.outlineColor !== before.outlineColor
      || after.outlineStyle !== before.outlineStyle
      || after.outlineWidth !== before.outlineWidth;
    const outlineSignal =
      outlineChanged
      && after.outlineStyle !== 'none'
      && after.outlineWidth >= 2
      && outline[3] > 0
      && contrast(blend(outline, background), background) >= 3;
    const borderSignal = after.borders.some((border, index) => {
      const previous = before.borders[index];
      const changed =
        border.color !== previous.color
        || border.style !== previous.style
        || border.width !== previous.width;
      const color = rgba(border.color);
      return (
        changed
        && !['none', 'hidden'].includes(border.style)
        && border.width >= 2
        && color[3] > 0
        && contrast(blend(color, background), background) >= 3
      );
    });
    const backgroundSignal =
      before.background !== null
      && after.background !== null
      && contrast(before.background, after.background) >= 3;
    const shadowItems = (after.boxShadow || '')
      .split(/,(?![^(]*\))/)
      .map((s) => s.trim())
      .filter(Boolean);
    const shadowSignal =
      after.boxShadow !== before.boxShadow
      && after.boxShadow !== 'none'
      && shadowItems.some((shadow) => {
        const colorMatch = shadow.match(/rgba?\([^)]+\)/);
        if (!colorMatch) return false;
        const color = rgba(colorMatch[0]);
        if (color[3] <= 0 || contrast(blend(color, background), background) < 3) return false;
        const lengths = shadow.replace(colorMatch[0], '').match(/-?[\d.]+(?:px)?/g);
        if (!lengths) return false;
        const nums = lengths.map((l) => Number.parseFloat(l) || 0);
        const [x = 0, y = 0, blur = 0, spread = 0] = nums;
        const extent = Math.max(Math.abs(spread), Math.abs(blur), Math.hypot(x, y));
        return extent >= 1.5;
      });
    return outlineSignal || borderSignal || backgroundSignal || shadowSignal;
  };

  const sidebar = document.querySelector('[data-role="sidebar"]');
  const menu = document.querySelector('[data-role="mobile-menu"]');
  const grid = document.querySelector('[data-role="incident-grid"]');
  const primary = document.querySelector('[data-role="primary-action"]');
  const navLinks = [...document.querySelectorAll('nav a')];
  const currentNavLinks = navLinks.filter((node) => node.getAttribute('aria-current') === 'page');
  const mobileMenuButton = menu instanceof HTMLButtonElement
    && Boolean((menu.getAttribute('aria-label') || '').trim())
    && !menu.disabled;
  const disclosures = [...document.querySelectorAll('details')];
  const primaryUnfocusedStyle = focusStyle(primary);
  const menuUnfocusedStyle = focusStyle(menu);
  let primaryFocused = false;
  if (primary) {
    primary.focus();
    primaryFocused = document.activeElement === primary;
  }

  const primaryRect = primary ? primary.getBoundingClientRect() : {width: 0, height: 0};
  const focusIndicator =
    primaryFocused && focusIndicatorFor(primary, primaryUnfocusedStyle);

  const cards = grid ? [...grid.querySelectorAll('article')].filter(rendered) : [];
  const cardRects = cards.map((card) => card.getBoundingClientRect());
  const firstTop = cardRects.length ? Math.min(...cardRects.map((rect) => rect.top)) : 0;
  const columns = cardRects.filter((rect) => Math.abs(rect.top - firstTop) < 2).length;
  const cardsHorizontallyInView = cardRects.length > 0
    && cardRects.every((rect) => rect.right > 0 && rect.left < window.innerWidth);

  const navLabels = navLinks.map((node) => normalize(node));
  const filterControls = [...document.querySelectorAll('select')].filter(visible);
  const primaryIsFilter = Boolean(primary && (primary instanceof HTMLSelectElement || filterControls.includes(primary)));
  const filterOptions = filterControls.flatMap((control) =>
    [...control.querySelectorAll('option')].map(normalize)
  );
  let mobileMenuFocusable = false;
  let mobileMenuFocusIndicator = false;
  if (mobileMenuButton && visible(menu)) {
    menu.focus({preventScroll: true});
    mobileMenuFocusable = document.activeElement === menu && menu.tabIndex >= 0;
    mobileMenuFocusIndicator =
      mobileMenuFocusable && focusIndicatorFor(menu, menuUnfocusedStyle);
  }
  const productRendered = normalize(document.querySelector('header')).includes(brief.product);
  const navigationRendered =
    navLabels.length === brief.navigation.length &&
    (visible(sidebar)
      ? navLinks.every((node) => {
          const rect = node.getBoundingClientRect();
          return rendered(node) && rect.right > 0 && rect.left < window.innerWidth;
        })
      : true) &&
    brief.navigation.every((label, index) => navLabels[index] === label);
  const filtersRendered =
    primaryIsFilter &&
    filterControls.length > 0 &&
    filterControls.includes(primary) &&
    brief.filters.every((label) => filterOptions.includes(label));
  const sidebarRect = sidebar ? sidebar.getBoundingClientRect() : null;
  const gridRect = grid ? grid.getBoundingClientRect() : null;
  const sidebarLeftOfGrid = Boolean(
    sidebarRect && gridRect && visible(sidebar) && rendered(grid)
    && sidebarRect.left < gridRect.left
    && sidebarRect.right <= gridRect.left + 1
    && sidebarRect.top <= Math.max(0, gridRect.top) + 1
    && sidebarRect.bottom >= Math.min(window.innerHeight, gridRect.bottom) - 1
  );
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
  let requiredTextPainted = true;
  const textWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (textWalker.nextNode()) {
    const textNode = textWalker.currentNode;
    const parent = textNode.parentElement;
    if (parent && textNode.nodeValue.trim() && rendered(parent)) {
      const range = document.createRange();
      range.selectNodeContents(textNode);
      const textRect = range.getBoundingClientRect();
      const fontSize = Number.parseFloat(getComputedStyle(parent).fontSize);
      requiredTextPainted = requiredTextPainted
        && fontSize > 0
        && textRect.width > 0
        && textRect.height > 0
        && textRect.right > 0
        && textRect.left < window.innerWidth;
      textElements.add(parent);
    }
  }
  for (const control of document.querySelectorAll('button, input, select, textarea')) {
    if (
      rendered(control) &&
      (normalize(control) || String(control.value || '').trim() || String(control.placeholder || '').trim())
    ) {
      const rect = control.getBoundingClientRect();
      requiredTextPainted =
        requiredTextPainted
        && Number.parseFloat(getComputedStyle(control).fontSize) > 0
        && rect.width > 0
        && rect.height > 0
        && rect.right > 0
        && rect.left < window.innerWidth;
      textElements.add(control);
    }
  }
  const textMetrics = [...textElements].map((node) => {
    const style = getComputedStyle(node);
    const fontSize = Number.parseFloat(style.fontSize);
    const fontWeight = style.fontWeight === 'bold' ? 700 : Number.parseFloat(style.fontWeight);
    const threshold = fontSize >= 24 || (fontSize >= (14 * 96 / 72) && fontWeight >= 700) ? 3 : 4.5;
    const background = effectiveBackground(node);
    const solid = background !== null && effectiveOpacity(node) >= 0.999;
    return {
      ratio: solid ? contrast(blend(rgba(style.color), background), background) : 0,
      solid,
      threshold,
    };
  });
  const unsupportedPaintEffectsAbsent = [
    document.documentElement,
    ...document.querySelectorAll('body, body *'),
  ].filter(rendered).every((node) => {
    const styles = [
      getComputedStyle(node),
      getComputedStyle(node, '::before'),
      getComputedStyle(node, '::after'),
    ];
    return styles.every((style) =>
      style.backgroundImage === 'none'
      && style.filter === 'none'
      && (style.backdropFilter || 'none') === 'none'
      && (style.mixBlendMode || 'normal') === 'normal'
      && (style.backgroundBlendMode || 'normal') === 'normal'
    );
  });
  const h1 = document.querySelector('h1');
  const h1Style = h1 ? getComputedStyle(h1) : null;
  const h1FontSize = h1Style ? Number.parseFloat(h1Style.fontSize) : 0;
  const h2s = cards.map((c) => c.querySelector('h2')).filter(Boolean);
  const h2FontSizes = h2s.map((h) => Number.parseFloat(getComputedStyle(h).fontSize));
  const avgH2FontSize = h2FontSizes.length ? h2FontSizes.reduce((a, b) => a + b, 0) / h2FontSizes.length : 0;
  const bodyFontSize = Number.parseFloat(getComputedStyle(document.body).fontSize) || 16;
  const visualHierarchy = Boolean(
    h1FontSize >= 20 &&
    avgH2FontSize >= 14 &&
    h1FontSize > avgH2FontSize &&
    avgH2FontSize >= bodyFontSize * 0.95
  );
  const cardPaddingValid = cards.length > 0 && cards.every((c) => {
    const style = getComputedStyle(c);
    return (
      Number.parseFloat(style.paddingTop) >= 8 &&
      Number.parseFloat(style.paddingBottom) >= 8 &&
      Number.parseFloat(style.paddingLeft) >= 8 &&
      Number.parseFloat(style.paddingRight) >= 8
    );
  });
  const cardSpacingValid = cardPaddingValid && (
    columns === 3
      ? (cardRects.length >= 3 && cardRects[1].left >= cardRects[0].right + 4 && cardRects[2].left >= cardRects[1].right + 4)
      : (cardRects.length >= 2 ? cardRects[1].top >= cardRects[0].bottom + 4 : true)
  );
  const cardStyles = cards.map((c) => {
    const s = getComputedStyle(c);
    const b = c.querySelector('.severity, [class*="severity"]') || c;
    const bs = getComputedStyle(b);
    return s.borderTopColor + s.borderColor + s.backgroundColor + bs.color + bs.backgroundColor;
  });
  const severityDistinct = cards.length === brief.incidents.length && new Set(cardStyles).size >= 2;
  const desktopAlignmentValid = columns === 3
    ? cardRects.length >= 3 &&
      Math.abs(cardRects[0].top - cardRects[1].top) < 3 &&
      Math.abs(cardRects[1].top - cardRects[2].top) < 3 &&
      Math.abs(cardRects[0].width - cardRects[1].width) < 4 &&
      Math.abs(cardRects[1].width - cardRects[2].width) < 4
    : true;
  const visualDesignRubric = Boolean(
    visualHierarchy &&
    cardSpacingValid &&
    severityDistinct &&
    desktopAlignmentValid
  );
  const textContrasts = textMetrics.map((metric) => metric.ratio);
  const minimumTextContrast = textContrasts.length ? Math.min(...textContrasts) : 0;
  const solidTextBackgrounds = unsupportedPaintEffectsAbsent
    && textMetrics.every((metric) => metric.solid);
  const textContrastAa = textMetrics.length > 0
    && textMetrics.every((metric) => metric.ratio >= metric.threshold);
  return {
    viewport_width: innerWidth,
    horizontal_overflow: document.documentElement.scrollWidth > innerWidth + 1,
    sidebar_visible: visible(sidebar),
    sidebar_left_of_grid: sidebarLeftOfGrid,
    mobile_menu_visible: visible(menu),
    mobile_menu_button: mobileMenuButton,
    mobile_menu_focusable: mobileMenuFocusable,
    mobile_menu_focus_indicator: mobileMenuFocusIndicator,
    cards_horizontally_in_view: cardsHorizontallyInView,
    grid_columns: columns,
    primary_width: Math.round(primaryRect.width),
    primary_height: Math.round(primaryRect.height),
    primary_contrast: nodeContrast(primary),
    body_contrast: nodeContrast(document.body),
    minimum_text_contrast: minimumTextContrast,
    solid_text_backgrounds: solidTextBackgrounds,
    text_contrast_aa: textContrastAa,
    required_text_painted: requiredTextPainted && textElements.size > 0,
    visual_design_rubric: visualDesignRubric,
    focus_indicator: focusIndicator,
    disclosure_visible_after_open: disclosureVisible,
    navigation_rendered: navigationRendered,
    active_navigation_current: currentNavLinks.length === 1,
    brief_content_rendered:
      productRendered && navigationRendered && filtersRendered && incidentsRendered,
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
        self.navigation_links: list[dict[str, str | None]] = []
        self._navigation_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        self.tags[tag] = self.tags.get(tag, 0) + 1
        self.attributes.append((tag, values))
        if tag == "nav":
            self._navigation_depth += 1
        elif tag == "a" and self._navigation_depth:
            self.navigation_links.append(values)

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._navigation_depth:
            self._navigation_depth -= 1

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
    active_nav = (
        sum(
            attrs.get("aria-current", "").casefold() == "page"
            for attrs in parser.navigation_links
        )
        == 1
    )
    mobile_hooks = [
        (tag, attrs)
        for tag, attrs in parser.attributes
        if attrs.get("data-role") == "mobile-menu"
    ]
    mobile_label = (
        len(mobile_hooks) == 1
        and mobile_hooks[0][0] == "button"
        and bool((mobile_hooks[0][1].get("aria-label") or "").strip())
        and "disabled" not in mobile_hooks[0][1]
    )
    primary_hooks = [
        (tag, attrs)
        for tag, attrs in parser.attributes
        if attrs.get("data-role") == "primary-action"
    ]
    primary_is_filter = (
        len(primary_hooks) == 1
        and primary_hooks[0][0] == "select"
    )
    hook_counts = {
        role: sum(
            attrs.get("data-role") == role
            for _, attrs in parser.attributes
        )
        for role in {"mobile-menu", "incident-grid", "primary-action"}
    }
    hooks = all(count == 1 for count in hook_counts.values()) and primary_is_filter
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
            or not isinstance(state.get("open"), bool)
        ):
            raise SubmissionError("UI disclosure summary is not keyboard-focusable")
        initially_open = state["open"]
        for event_type in ("keyDown", "keyUp"):
            event = {
                "type": event_type,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            }
            if event_type == "keyDown":
                event.update({"text": "\r", "unmodifiedText": "\r"})
            client.command("Input.dispatchKeyEvent", event)
        toggled_open = _runtime_value(
            client,
            f"document.querySelectorAll('details')[{index}].open",
        )
        if toggled_open is initially_open or not isinstance(toggled_open, bool):
            raise SubmissionError("UI disclosure did not toggle through its summary")
        if not toggled_open:
            for event_type in ("keyDown", "keyUp"):
                event = {
                    "type": event_type,
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                }
                if event_type == "keyDown":
                    event.update({"text": "\r", "unmodifiedText": "\r"})
                client.command("Input.dispatchKeyEvent", event)
            if (
                _runtime_value(
                    client,
                    f"document.querySelectorAll('details')[{index}].open",
                )
                is not True
            ):
                raise SubmissionError("UI disclosure did not reopen through its summary")
    _runtime_value(client, "scrollTo(0, 0); true")


def _render(work: Path, width: int, height: int, brief: dict[str, object], *, home_dir: Path, tmp_dir: Path) -> dict[str, object]:
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
        env={"HOME": str(home_dir), "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_dir)},
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
        workspace = Path("/workspace") if Path("/workspace").is_dir() else Path(tempfile.gettempdir())
        home_dir = workspace / "rolebench-home"
        home_dir.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rolebench-ui-", dir=str(workspace)) as temporary:
            work = Path(temporary)
            (work / "index.html").write_text(html, encoding="utf-8")
            (work / "styles.css").write_text(css, encoding="utf-8")
            desktop = _render(work, 1280, 800, brief, home_dir=home_dir, tmp_dir=workspace)
            mobile = _render(work, 390, 844, brief, home_dir=home_dir, tmp_dir=workspace)
        brief_sha256 = hashlib.sha256(brief_bytes).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, OSError, TypeError, RecursionError, subprocess.SubprocessError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), brief_sha256=None, source_checks=None, desktop=None, mobile=None))
        return 0
    sys.stdout.write(_snapshot(status="executed", error=None, brief_sha256=brief_sha256, source_checks=checks, desktop=desktop, mobile=mobile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
