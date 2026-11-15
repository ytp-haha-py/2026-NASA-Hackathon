#!/usr/bin/env python3
"""Availability and metadata check for the planetary basemap catalog (Issue #117).

The catalog under ``docs/data-sources/planetary-map-layers.json`` is the source of
truth. This script re-verifies, for each NASA Trek WMTS layer:

* GetCapabilities HTTP status, content type and XML structure;
* layer identifier, style, format, tile matrix set, supported CRS and bbox;
* zoom range, 256x256 tiles, doubling matrix dimensions and top-left corner;
* a min-zoom, mid-zoom and three max-zoom edge tiles, including the antimeridian;
* tile content type, JPEG/PNG magic bytes and decoded 256x256 dimensions;
* CORS for browser rendering;
* that one zoom beyond the published maximum returns 404 (the catalog is not stale).

It also checks the USGS Astrogeology WMS alternatives and the Trek API config, when
they are present in the catalog.

Tile probes use HTTP Range ``bytes=0-1023`` so no raster body beyond a few kilobytes
is transferred. The checker is stdlib-only and deterministic: it prints no timestamps
and makes no writes. Exit code 0 means every check passed; 1 means a regression.

Usage::

    python3 scripts/check_planetary_trek_sources.py
    python3 scripts/check_planetary_trek_sources.py --json
    python3 scripts/check_planetary_trek_sources.py --skip-alternatives
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "docs" / "data-sources" / "planetary-map-layers.json"
USER_AGENT = "planetary-analog-explorer-source-audit/1.0"
RANGE_BYTES = 1023
ORIGIN = "http://localhost:5173"


class Report:
    """Ordered pass/fail assertions."""

    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def expect(self, ok: bool, name: str, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [check for check in self.checks if not check["ok"]]


def http_request(
    url: str,
    *,
    method: str = "GET",
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Fetch a URL, capping the body size and retrying transport errors once."""
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, method=method, headers=request_headers)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = b""
                if method != "HEAD" and max_bytes is not None:
                    body = response.read(max_bytes + 1)
                return {
                    "status": response.status,
                    "content_type": response.headers.get("Content-Type", ""),
                    "content_length": response.headers.get("Content-Length"),
                    "cors": response.headers.get("Access-Control-Allow-Origin"),
                    "body": body,
                    "url": response.geturl(),
                }
        except urllib.error.HTTPError as exc:
            response_headers = exc.headers or {}
            return {
                "status": exc.code,
                "content_type": response_headers.get("Content-Type", ""),
                "content_length": response_headers.get("Content-Length"),
                "cors": response_headers.get("Access-Control-Allow-Origin"),
                "body": b"",
                "url": url,
            }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1.0)
    return {
        "status": 0,
        "content_type": "",
        "content_length": None,
        "cors": None,
        "body": b"",
        "url": url,
        "error": str(last_error),
    }


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(element: ET.Element, name: str) -> ET.Element | None:
    for candidate in list(element):
        if local_name(candidate.tag) == name:
            return candidate
    return None


def child_text(element: ET.Element, name: str) -> str | None:
    candidate = child(element, name)
    if candidate is None:
        return None
    return (candidate.text or "").strip()


def image_dimensions(body: bytes) -> tuple[int, int] | None:
    """Return (width, height) from a PNG IHDR or a JPEG SOF marker."""
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(body) >= 24 and body[12:16] == b"IHDR":
            return int.from_bytes(body[16:20], "big"), int.from_bytes(body[20:24], "big")
        return None
    if body[:3] != b"\xff\xd8\xff":
        return None
    index = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD}
    while index + 9 <= len(body):
        if body[index] != 0xFF:
            index += 1
            continue
        marker = body[index + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        segment_length = int.from_bytes(body[index + 2 : index + 4], "big")
        if marker in sof_markers:
            height = int.from_bytes(body[index + 5 : index + 7], "big")
            width = int.from_bytes(body[index + 7 : index + 9], "big")
            return width, height
        if segment_length < 2:
            return None
        index += 2 + segment_length
    return None


def tile_url(wmts: dict[str, Any], zoom: int, row: int, col: int) -> str:
    template = wmts["tile_template_normalized"]
    return (
        template.replace("{Style}", wmts["style"])
        .replace("{TileMatrixSet}", wmts["tile_matrix_set"])
        .replace("{TileMatrix}", str(zoom))
        .replace("{TileRow}", str(row))
        .replace("{TileCol}", str(col))
    )


def parse_capabilities(body: bytes) -> dict[str, Any]:
    root = ET.fromstring(body)
    layers: list[dict[str, Any]] = []
    matrix_sets: dict[str, dict[str, Any]] = {}
    for element in root.iter():
        name = local_name(element.tag)
        if name == "Layer":
            layers.append(
                {
                    "identifier": child_text(element, "Identifier"),
                    "format": child_text(element, "Format"),
                    "style": grandchild_text(element, "Style", "Identifier"),
                    "matrix_set": grandchild_text(element, "TileMatrixSetLink", "TileMatrixSet"),
                    "resource_url": (
                        (child(element, "ResourceURL").get("template") or "")
                        if child(element, "ResourceURL") is not None
                        else ""
                    ),
                    "lower": grandchild_text(element, "BoundingBox", "LowerCorner"),
                    "upper": grandchild_text(element, "BoundingBox", "UpperCorner"),
                }
            )
        elif name == "TileMatrixSet":
            identifier = child_text(element, "Identifier")
            if not identifier:
                continue
            matrices = []
            for candidate in list(element):
                if local_name(candidate.tag) != "TileMatrix":
                    continue
                ids = child_text(candidate, "Identifier")
                corner = child_text(candidate, "TopLeftCorner") or ""
                parts = corner.split()
                matrices.append(
                    {
                        "id": ids,
                        "scale": float(child_text(candidate, "ScaleDenominator") or "nan"),
                        "top_left": (float(parts[0]), float(parts[1])) if len(parts) == 2 else None,
                        "tile_width": int(child_text(candidate, "TileWidth") or 0),
                        "tile_height": int(child_text(candidate, "TileHeight") or 0),
                        "matrix_width": float(child_text(candidate, "MatrixWidth") or 0),
                        "matrix_height": float(child_text(candidate, "MatrixHeight") or 0),
                    }
                )
            matrix_sets[identifier] = {
                "crs": child_text(element, "SupportedCRS"),
                "matrices": matrices,
            }
    return {"layers": layers, "matrix_sets": matrix_sets}


def grandchild_text(element: ET.Element, parent_name: str, name: str) -> str | None:
    parent = child(element, parent_name)
    return child_text(parent, name) if parent is not None else None


def parse_pair(text: str | None) -> tuple[float, float] | None:
    parts = (text or "").split()
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def check_tile(
    report: Report,
    layer: dict[str, Any],
    zoom: int,
    row: int,
    col: int,
    kind: str,
    timeout: float,
    *,
    head: bool = False,
) -> None:
    wmts = layer["wmts"]
    label = f"{layer['id']}: {kind} z{zoom}/{row}/{col}"
    url = tile_url(wmts, zoom, row, col)
    if head:
        result = http_request(url, method="HEAD", timeout=timeout)
        report.expect(
            result["status"] == 200,
            f"{label} HTTP 200",
            f"status={result['status']}",
        )
        report.expect(
            result["content_type"].startswith("image/"),
            f"{label} image content type",
            f"content-type={result['content_type']}",
        )
        length = result["content_length"]
        report.expect(
            length is not None and length.isdigit() and int(length) > 1024,
            f"{label} non-empty tile",
            f"content-length={length}",
        )
        return

    result = http_request(
        url,
        timeout=timeout,
        headers={"Range": f"bytes=0-{RANGE_BYTES}", "Origin": ORIGIN},
        max_bytes=RANGE_BYTES,
    )
    report.expect(
        result["status"] in (200, 206),
        f"{label} HTTP 200/206",
        f"status={result['status']}",
    )
    report.expect(
        result["content_type"].startswith(wmts["format"]),
        f"{label} content type {wmts['format']}",
        f"content-type={result['content_type']}",
    )
    dimensions = image_dimensions(result["body"])
    report.expect(
        dimensions == (256, 256),
        f"{label} decodes as 256x256",
        f"dimensions={dimensions}",
    )
    report.expect(
        "*" in (result["cors"] or ""),
        f"{label} CORS wildcard",
        f"access-control-allow-origin={result['cors']}",
    )


def check_layer(report: Report, layer: dict[str, Any], timeout: float) -> None:
    wmts = layer["wmts"]
    identifier = layer["id"]
    # The WMTS layer identifier is the provider product label, not the catalog slug.
    expected_layer = layer.get("product_label", identifier)

    result = http_request(wmts["capabilities_url"], timeout=timeout, max_bytes=2_000_000)
    ok = report.expect(
        result["status"] == 200,
        f"{identifier}: capabilities HTTP 200",
        f"status={result['status']}",
    )
    report.expect(
        "xml" in result["content_type"].lower(),
        f"{identifier}: capabilities XML content type",
        f"content-type={result['content_type']}",
    )
    report.expect(
        "*" in (result["cors"] or ""),
        f"{identifier}: capabilities CORS wildcard",
        f"access-control-allow-origin={result['cors']}",
    )
    if not ok:
        return
    try:
        capabilities = parse_capabilities(result["body"])
    except ET.ParseError as exc:
        report.expect(False, f"{identifier}: capabilities parse", str(exc))
        return

    matching = [entry for entry in capabilities["layers"] if entry["identifier"] == expected_layer]
    report.expect(
        len(matching) == 1,
        f"{identifier}: layer identifier present once",
        f"expected={expected_layer} found={len(matching)}",
    )
    if not matching:
        return
    entry = matching[0]

    report.expect(
        entry["format"] == wmts["format"],
        f"{identifier}: advertised format",
        f"advertised={entry['format']} expected={wmts['format']}",
    )
    report.expect(
        entry["style"] == wmts["style"],
        f"{identifier}: default style",
        f"advertised={entry['style']} expected={wmts['style']}",
    )
    report.expect(
        entry["matrix_set"] == wmts["tile_matrix_set"],
        f"{identifier}: tile matrix set link",
        f"advertised={entry['matrix_set']} expected={wmts['tile_matrix_set']}",
    )
    lower = parse_pair(entry["lower"])
    upper = parse_pair(entry["upper"])
    bbox_ok = (
        lower is not None
        and upper is not None
        and abs(lower[0] + 180.0) <= 0.01
        and abs(lower[1] + 90.0) <= 0.01
        and abs(upper[0] - 180.0) <= 0.01
        and abs(upper[1] - 90.0) <= 0.01
    )
    report.expect(
        bbox_ok,
        f"{identifier}: bbox is -180,-90,180,90 (+/-0.01 deg)",
        f"lower={entry['lower']} upper={entry['upper']}",
    )
    resource_url = entry["resource_url"].replace("//{", "/{")
    report.expect(
        resource_url == wmts["tile_template_normalized"],
        f"{identifier}: REST tile template",
        f"advertised={resource_url}",
    )

    matrix_set = capabilities["matrix_sets"].get(wmts["tile_matrix_set"])
    report.expect(
        matrix_set is not None,
        f"{identifier}: tile matrix set exists",
        f"set={wmts['tile_matrix_set']}",
    )
    if matrix_set is None:
        return
    report.expect(
        matrix_set["crs"] == wmts["supported_crs"],
        f"{identifier}: supported CRS",
        f"advertised={matrix_set['crs']} expected={wmts['supported_crs']}",
    )

    matrices = matrix_set["matrices"]
    expected_ids = [str(zoom) for zoom in range(wmts["zoom_min"], wmts["zoom_max"] + 1)]
    ids_ok = report.expect(
        [matrix["id"] for matrix in matrices] == expected_ids,
        f"{identifier}: zoom range {wmts['zoom_min']}-{wmts['zoom_max']}",
        f"advertised={[matrix['id'] for matrix in matrices]}",
    )
    if not matrices or not ids_ok:
        return

    first = matrices[0]
    report.expect(
        (first["matrix_width"], first["matrix_height"]) == (2.0, 1.0),
        f"{identifier}: zoom 0 matrix is 2x1",
        f"advertised={first['matrix_width']}x{first['matrix_height']}",
    )
    corner_ok = all(matrix["top_left"] == (-180.0, 90.0) for matrix in matrices)
    report.expect(
        corner_ok,
        f"{identifier}: top-left corner is -180,90 at every zoom",
        f"corners={[matrix['top_left'] for matrix in matrices]}",
    )
    size_ok = all(
        (matrix["tile_width"], matrix["tile_height"]) == (256, 256) for matrix in matrices
    )
    report.expect(
        size_ok,
        f"{identifier}: 256x256 tiles at every zoom",
        f"sizes={[(matrix['tile_width'], matrix['tile_height']) for matrix in matrices]}",
    )
    grows_ok = all(
        matrices[index]["matrix_width"] == 2 * matrices[index - 1]["matrix_width"]
        and matrices[index]["matrix_height"] == 2 * matrices[index - 1]["matrix_height"]
        for index in range(1, len(matrices))
    )
    report.expect(
        grows_ok,
        f"{identifier}: matrix dimensions double per zoom",
        f"matrices={[(matrix['matrix_width'], matrix['matrix_height']) for matrix in matrices]}",
    )

    max_zoom = wmts["zoom_max"]
    max_matrix = matrices[-1]
    max_row = int(max_matrix["matrix_height"]) - 1
    max_col = int(max_matrix["matrix_width"]) - 1

    check_tile(report, layer, 0, 0, 0, "min zoom", timeout)
    mid_zoom = max_zoom // 2
    mid_matrix = matrices[mid_zoom - wmts["zoom_min"]]
    check_tile(
        report,
        layer,
        mid_zoom,
        int(mid_matrix["matrix_height"]) // 2,
        int(mid_matrix["matrix_width"]) // 2,
        "mid zoom",
        timeout,
    )
    check_tile(report, layer, max_zoom, 0, 0, "max zoom north-west", timeout)
    check_tile(report, layer, max_zoom, max_row, max_col, "max zoom south-east", timeout, head=True)
    check_tile(report, layer, max_zoom, 0, max_col, "max zoom antimeridian", timeout, head=True)

    beyond = tile_url(wmts, max_zoom + 1, 0, 0)
    result = http_request(beyond, method="HEAD", timeout=timeout)
    report.expect(
        result["status"] == 404,
        f"{identifier}: zoom {max_zoom + 1} is not published",
        f"status={result['status']}",
    )


def check_alternative(report: Report, alternative: dict[str, Any], timeout: float) -> None:
    identifier = alternative["id"]
    caps_url = alternative.get("capabilities_url")
    if caps_url:
        result = http_request(caps_url, timeout=timeout, max_bytes=2_000_000)
        report.expect(
            result["status"] == 200,
            f"{identifier}: WMS capabilities HTTP 200",
            f"status={result['status']}",
        )
        body = result["body"].decode("utf-8", errors="replace")
        verification = alternative["verification"]
        for crs in verification.get("supported_crs", []):
            if crs.startswith("IAU_"):
                report.expect(
                    crs in body,
                    f"{identifier}: advertises {crs}",
                    f"found={crs in body}",
                )
        for layer_name in alternative.get("layers", [])[:1]:
            report.expect(
                f"<Name>{layer_name}</Name>" in body,
                f"{identifier}: layer {layer_name} present",
                f"found={layer_name in body}",
            )
        getmap_url = verification.get("getmap_probe_url")
        if getmap_url:
            image = http_request(getmap_url, timeout=timeout, max_bytes=4096)
            report.expect(
                image["status"] == 200,
                f"{identifier}: GetMap HTTP 200",
                f"status={image['status']}",
            )
            report.expect(
                image["content_type"].startswith("image/"),
                f"{identifier}: GetMap image content type",
                f"content-type={image['content_type']}",
            )
            dims = image_dimensions(image["body"])
            expected = tuple(verification.get("getmap_dimensions", [64, 32]))
            report.expect(
                dims == expected,
                f"{identifier}: GetMap decodes as {expected[0]}x{expected[1]}",
                f"dimensions={dims}",
            )
        return

    config_url = alternative.get("urls", {}).get("config")
    if config_url:
        result = http_request(config_url, timeout=timeout, max_bytes=1_000_000)
        report.expect(
            result["status"] == 200,
            f"{identifier}: config HTTP 200",
            f"status={result['status']}",
        )
        try:
            config = json.loads(result["body"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            report.expect(False, f"{identifier}: config JSON parse", str(exc))
            return
        moon = config.get("moon", {}).get("projections", {}).get("equirectangular")
        mars = config.get("mars", {}).get("projections", {}).get("equirectangular")
        report.expect(
            moon == "urn:ogc:def:crs:EPSG::104903",
            f"{identifier}: Moon equirectangular projection",
            f"value={moon}",
        )
        report.expect(
            mars == "urn:ogc:def:crs:IAU2000::104905",
            f"{identifier}: Mars equirectangular projection",
            f"value={mars}",
        )
        sample = config.get("moon", {}).get("samples", {}).get("sample1", "")
        report.expect(
            "/default/default028mm/0/0/0." in sample,
            f"{identifier}: zoom-0 sample tile",
            f"sample={sample}",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--quiet", action="store_true", help="print failures and summary only")
    parser.add_argument(
        "--skip-alternatives",
        action="store_true",
        help="skip USGS WMS and Trek API documentation probes",
    )
    args = parser.parse_args(argv)

    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read catalog {args.catalog}: {exc}", file=sys.stderr)
        return 2

    report = Report()
    for layer in catalog.get("layers", []):
        check_layer(report, layer, args.timeout)
    if not args.skip_alternatives:
        for alternative in catalog.get("alternatives", []):
            check_alternative(report, alternative, args.timeout)

    failures = report.failures
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not failures,
                    "checked": len(report.checks),
                    "failed": len(failures),
                    "checks": report.checks,
                },
                indent=2,
                sort_keys=False,
            )
        )
    else:
        for check in report.checks:
            if check["ok"] and args.quiet:
                continue
            status = "PASS" if check["ok"] else "FAIL"
            detail = f"  ({check['detail']})" if check["detail"] and not check["ok"] else ""
            print(f"{status}  {check['name']}{detail}")
        print(f"\n{len(report.checks)} checks, {len(failures)} failed")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
