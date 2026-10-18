"""Experimental browser-based UI heuristics.

When Playwright and Chromium are installed, this module inspects a URL and flags
some simple alignment, spacing, and text/background contrast heuristics. These
checks are incomplete and may produce false positives; they are not a complete
accessibility or design audit. Visiting a URL makes an outbound request.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from typing import Optional

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - playwright optional at import time
    async_playwright = None  # type: ignore[assignment]


def parse_rgb(color_str: Optional[str]) -> Optional[tuple[int, int, int]]:
    """Parses 'rgb(r, g, b)' or 'rgba(r, g, b, a)' from JS computed styles."""
    if not color_str or color_str == "transparent":
        return None
    try:
        nums = (
            color_str.replace("rgb(", "")
            .replace("rgba(", "")
            .replace(")", "")
            .split(",")
        )
        return tuple(int(float(n.strip())) for n in nums[:3])
    except Exception:
        return None


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """Calculates WCAG relative luminance."""
    r, g, b = [x / 255.0 for x in rgb]
    r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
    g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
    b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    """Calculates WCAG contrast ratio."""
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


async def audit_ui_layout(url: str, viewport: dict | None = None) -> list[dict]:
    """
    Scans a URL and returns a mathematical UI Scorecard.
    Each entry lists element, position, and math_failures.
    """
    if async_playwright is None:
        raise ImportError(
            "playwright is required for ii_ui_heuristics. "
            "Install it: pip install playwright && playwright install chromium"
        )

    viewport = viewport or {"width": 1920, "height": 1080}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        await page.goto(url, wait_until="networkidle")

        elements_data = await page.evaluate(
            """
            () => {
                const targets = document.querySelectorAll('button, a, input, h1, h2, h3, p, div, img');
                return Array.from(targets).map(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const parentRect = el.parentElement ? el.parentElement.getBoundingClientRect() : null;

                    return {
                        tag: el.tagName,
                        text: (el.innerText || el.alt || '').substring(0, 30).trim(),
                        rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                        parentRect: parentRect ? { x: parentRect.x, y: parentRect.y, w: parentRect.width, h: parentRect.height } : null,
                        color: style.color,
                        bgColor: style.backgroundColor,
                        padding: parseInt(style.paddingTop) || 0
                    };
                }).filter(el => el.rect.w > 10 && el.rect.h > 10);
            }
            """
        )
        await browser.close()

    scorecard: list[dict] = []

    for el in elements_data:
        issues: list[str] = []

        # CHECK A: Alignment Math
        if el["parentRect"] and el["parentRect"]["w"] > 50:
            el_center_x = el["rect"]["x"] + (el["rect"]["w"] / 2)
            parent_center_x = el["parentRect"]["x"] + (el["parentRect"]["w"] / 2)
            deviation = abs(el_center_x - parent_center_x)

            if el["tag"] in ["BUTTON", "H1", "H2", "H3"] and deviation > 5.0:
                issues.append(
                    f"ALIGNMENT_FAIL: Off-center by {deviation:.1f}px"
                )

        # CHECK B: Spacing Math (8pt grid)
        if el["padding"] > 0 and el["padding"] % 8 != 0:
            issues.append(
                f"SPACING_FAIL: Padding is {el['padding']}px. Not on 8pt grid"
            )

        # CHECK C: WCAG Contrast Math
        fg_rgb = parse_rgb(el["color"])
        bg_rgb = parse_rgb(el["bgColor"])

        if fg_rgb and bg_rgb and el["text"]:
            ratio = contrast_ratio(fg_rgb, bg_rgb)
            if ratio < 4.5:
                issues.append(
                    f"CONTRAST_FAIL: Ratio is {ratio:.2f}:1. Fails WCAG AA"
                )

        if issues:
            scorecard.append(
                {
                    "element": f"<{el['tag'].lower()}> '{el['text']}'",
                    "position": f"x:{el['rect']['x']:.0f} y:{el['rect']['y']:.0f}",
                    "math_failures": issues,
                }
            )

    return scorecard


def audit_ui_layout_sync(url: str, viewport: dict | None = None) -> list[dict]:
    """Synchronous wrapper around audit_ui_layout."""
    return asyncio.run(audit_ui_layout(url, viewport))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    target = sys.argv[1] if len(sys.argv) > 1 else (
        "https://en.wikipedia.org/wiki/Artificial_intelligence"
    )
    print(f"[UI CHECK] Running heuristic scan for: {target}")
    ui_scorecard = audit_ui_layout_sync(target)
    print(f"\n[SCORECARD] UI HEURISTICS ({len(ui_scorecard)} issues found):")
    print(json.dumps(ui_scorecard[:5], indent=2))
