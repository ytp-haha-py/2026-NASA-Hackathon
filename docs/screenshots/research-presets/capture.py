"""Capture the local Research Presets UI using an isolated, real-data preview API.

Optional tooling: uv run --no-project --with playwright==1.65.1 python capture.py
No source ingestion or AOI analysis is triggered; external map/font requests are blocked.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5177")
    parser.add_argument("--api-url", default="http://127.0.0.1:8137")
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    observations = []
    with sync_playwright() as playwright:
        options = {"executable_path": str(args.browser)} if args.browser else {}
        browser = playwright.chromium.launch(headless=True, **options)
        for locale, width, height, preset in [
            ("zh-TW", 1440, 1100, "rover-mobility"),
            ("zh-TW", 390, 844, "precision-landing"),
            ("en", 1440, 1100, "geological-fieldwork"),
        ]:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.add_init_script(
                f"localStorage.setItem('planetary-analog-explorer.locale', {json.dumps(locale)})"
            )
            page.route("https://**/*", lambda route: route.abort())

            def proxy_api(route):
                parts = urlsplit(route.request.url)
                url = args.api_url.rstrip("/") + parts.path
                if parts.query:
                    url += "?" + parts.query
                route.fulfill(response=route.fetch(url=url))

            page.route("**/api/**", proxy_api)
            page.goto(args.base_url, wait_until="domcontentloaded")
            select = page.locator("#research-preset-select")
            select.wait_for(state="visible")
            select.select_option(preset)
            page.locator("#metric-slope_mean_deg-weight").wait_for()
            layout = page.locator(".board-head").evaluate(
                """header => {
                    const box = selector => {
                        const r = header.querySelector(selector).getBoundingClientRect();
                        return {x: r.x, y: r.y, width: r.width, height: r.height};
                    };
                    return {viewport: innerWidth, width: header.clientWidth,
                        scrollWidth: header.scrollWidth, title: box('h2'),
                        select: box('select'), limit: box('.limit-control')};
                }"""
            )
            assert layout["scrollWidth"] <= layout["width"] + 1, layout
            assert layout["select"]["x"] + layout["select"]["width"] <= width + 1, layout
            filename = f"{'desktop' if width > 600 else 'mobile'}-{locale}.png"
            page.locator(".dimension-board").screenshot(path=args.output / filename)
            observations.append({"file": filename, "locale": locale, "preset": preset, **layout})
            page.close()
        browser.close()
    (args.output / "layout.json").write_text(json.dumps(observations, indent=2) + "\n")
    print(json.dumps(observations, indent=2))


if __name__ == "__main__":
    main()
