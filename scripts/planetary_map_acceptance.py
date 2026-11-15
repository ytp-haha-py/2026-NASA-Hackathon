#!/usr/bin/env python
"""Live HTTP check for the Issue #117 planetary basemap catalog.

Network access is required. This is intentionally not part of CI; run it before publishing a map
catalog change and record the output in the PR/issue evidence.

    uv run python scripts/planetary_map_acceptance.py
    uv run python scripts/planetary_map_acceptance.py --api-base https://api.example/api/v1
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "planetary-analog-explorer-map-acceptance/1.0"


def fetch(url: str, timeout: float) -> tuple[int, str, int]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(64)
            return response.status, response.headers.get("content-type", ""), len(payload)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("content-type", "") if exc.headers else "", 0
    except urllib.error.URLError as exc:
        return 0, f"URLError: {exc.reason}", 0


def check_layers(timeout: float) -> list[dict[str, Any]]:
    from analog_api.domain.planetary import PLANETARY_MAP_LAYERS

    results: list[dict[str, Any]] = []
    for layer in PLANETARY_MAP_LAYERS:
        checks = [
            ("capabilities", layer.capabilities_url, "xml"),
            ("sample_tile", layer.verification["sample_tile_url"], layer.format),
        ]
        zoom, width, height = layer.matrix_sizes[-1]
        max_tile = (
            layer.tile_template.replace("{TileMatrix}", str(zoom))
            .replace("{TileRow}", str(height - 1))
            .replace("{TileCol}", str(width - 1))
        )
        checks.append(("max_zoom_tile", max_tile, layer.format))
        for name, url, expected_kind in checks:
            status, content_type, size = fetch(url, timeout)
            expected = (
                "xml" in content_type
                if expected_kind == "xml"
                else ("image/" in content_type and size > 0)
            )
            results.append(
                {
                    "layer_id": layer.layer_id,
                    "check": name,
                    "url": url,
                    "status": status,
                    "content_type": content_type,
                    "bytes_read": size,
                    "ok": status == 200 and expected,
                }
            )
    return results


def check_api(api_base: str, timeout: float) -> list[dict[str, Any]]:
    base = api_base.rstrip("/")
    results: list[dict[str, Any]] = []
    for path, expected_key in (
        ("/planetary/maps?body=Moon", "layers"),
        ("/planetary/maps?body=Mars", "layers"),
        ("/planetary/map-features?body=Mars&bbox=76,17,79,20", "features"),
        ("/planetary/map-features?body=Moon&bbox=120,-90,-150,-85", "features"),
        ("/planetary/coordinate-contract", "contract_version"),
    ):
        url = base + path
        status, content_type, size = fetch(url, timeout)
        ok = status == 200 and "json" in content_type
        payload: Any = None
        if ok:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout
            ) as response:
                payload = json.loads(response.read())
            ok = expected_key in payload
        results.append(
            {
                "check": path,
                "url": url,
                "status": status,
                "content_type": content_type,
                "bytes_read": size,
                "ok": ok,
                "total": payload.get("total") if isinstance(payload, dict) else None,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--api-base",
        help="Optional deployed API base ending in /api/v1. Checks catalog/overlay responses too.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    results = check_layers(args.timeout)
    if args.api_base:
        results.extend(check_api(args.api_base, args.timeout))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for item in results:
            label = item.get("layer_id") or "api"
            check = item.get("check")
            print(
                f"{label:50} {check:20} HTTP {item['status']} "
                f"{item['content_type']:24} ok={item['ok']}"
            )
    failures = [item for item in results if not item["ok"]]
    if failures:
        print(f"\nFAIL: {len(failures)} check(s) failed", file=sys.stderr)
        return 1
    print(f"\nPASS: {len(results)} live planetary map check(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
