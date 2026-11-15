#!/usr/bin/env python
"""Bounded D02 official-footprint discovery and AOI evidence for Issue #170 Goal E.

The module deliberately keeps two phases separate:

* ``discover`` reaches the public NASA PDS Geosciences ODE REST service and stores a
  bounded set of query responses plus small PDS labels.
* ``build`` is offline and turns those committed responses into portable evidence.

Nothing in this module reads a spectral raster, identifies a mineral, converts a band
depth to an abundance, or writes to the shared geology typed export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer
from shapely import make_valid
from shapely import transform as shapely_transform
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
QUERIES = HERE / "queries"
LABELS = QUERIES / "labels"
AREA_SOURCE = ROOT / "pipelines/geology/areas.json"
AREA_CONFIG = ROOT / "configs/areas.yaml"
TYPED_MANIFEST = ROOT / "pipelines/geology/typed/manifest.json"

ODE_ENDPOINT = "https://oderest.rsl.wustl.edu/live2/"
ODE_SERVICE_VERSION = "ODE V2.1.5 (live2)"
USER_AGENT = "AnalogX-D02-AOI-170/1.0"

MOON_RADIUS_M = 1_737_400.0
MARS_RADIUS_M = 3_396_190.0

SELECTED_AREAS = (
    "moon-shackleton-rim-site04-roi2",
    "mars-test-jezero-floor",
    "mars-test-jezero-western-delta",
)

M3_DATASET_ID = "moon-m3-l2-reflectance"
M3_COLLECTION = "CH1-ORB-L-M3-4-L2-REFLECTANCE-V1.0"
M3_LICENSE = "NASA Planetary Data System archived data (public domain)"
M3_LICENSE_URL = "https://pds.nasa.gov/"
M3_DOI = "10.17189/f8xf-6a29"

CRISM_DATASET_ID = "mars-crism-mtrdr"
CRISM_COLLECTION = "MRO-M-CRISM-5-RDR-MPTARGETED-V1.0"
CRISM_LICENSE = "PDS archived data are in the public domain (NASA PDS Geosciences Node)"
CRISM_LICENSE_URL = "https://pds.nasa.gov/"

TIO2_DATASET_ID = "moon-lroc-wac-tio2"
TIO2_COLLECTION = "LRO-L-LROC-5-RDR-V1.0"
TIO2_LICENSE = "NASA Planetary Data System archived data (public domain)"
TIO2_LICENSE_URL = "https://pds.nasa.gov/"
TIO2_DOI = "10.1016/j.icarus.2017.06.013"

COMPLETE_TOL = 1e-6
MAX_MOON_COMPLETE = 3
MAX_MOON_PARTIAL = 1
MAX_STORED_PRODUCTS = 12

SQL_KEYS = (
    "PRODUCT_ID",
    "DATA_SET_ID",
    "START_TIME",
    "STOP_TIME",
    "ORBIT_NUMBER",
    "INSTRUMENT_MODE_ID",
    "PRODUCT_VERSION_TYPE",
    "PRODUCT_CREATION_TIME",
    "CH1:SWATH_WIDTH",
    "CH1:SWATH_LENGTH",
    "SOURCE_PRODUCT_ID",
)


def sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _http_get(url: str, *, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def _http_json(url: str, *, timeout: int = 120) -> tuple[dict[str, Any], bytes]:
    payload = _http_get(url, timeout=timeout)
    return json.loads(payload), payload


def _ode_url(params: dict[str, Any]) -> str:
    return ODE_ENDPOINT + "?" + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# AOI preparation


def _densify(points: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    dense: list[tuple[float, float]] = []
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        pieces = max(2, int(math.hypot(x2 - x1, y2 - y1) / step))
        for part in range(pieces):
            t = part / pieces
            dense.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    dense.append(points[-1])
    return dense


def _lonlat(area: dict[str, Any], body: str) -> tuple[list[tuple[float, float]], float]:
    ring = [tuple(point) for point in area["geometry"]["coordinates"][0]]
    if body == "Mars":
        dense = _densify(ring, 0.001)
        return dense, MARS_RADIUS_M
    source = CRS.from_user_input(area["crs"])
    target = CRS.from_proj4(f"+proj=longlat +R={MOON_RADIUS_M} +no_defs")
    transform = Transformer.from_crs(source, target, always_xy=True)
    dense = _densify(ring, 100.0)
    return [transform.transform(x, y) for x, y in dense], MOON_RADIUS_M


def _aoi_record(area: dict[str, Any]) -> dict[str, Any]:
    body = str(area["body"])
    dense, radius = _lonlat(area, body)
    lons = [point[0] for point in dense]
    lats = [point[1] for point in dense]
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    if body == "Moon":
        # ODE expects 0..360 east longitude for the Moon.
        west_ode = west % 360.0
        east_ode = east % 360.0
        if west_ode > east_ode:
            raise ValueError("Moon AOI crosses the ODE 0/360 seam; split it before use")
    else:
        west_ode, east_ode = west, east
    center_lon = (west + east) / 2.0
    center_lon_ode = (west_ode + east_ode) / 2.0
    center_lat = (south + north) / 2.0
    analysis_crs = (
        f"+proj=laea +lat_0={center_lat:.10f} +lon_0={center_lon:.10f} "
        f"+R={radius} +units=m +type=crs"
    )
    area_shape = _area_shape(area, dense, analysis_crs)
    return {
        "area_id": area["area_id"],
        "display_name": area.get("display_name") or area["area_id"],
        "body": body,
        "origin": area.get("origin") or "unknown",
        "source": str(AREA_SOURCE.relative_to(ROOT)),
        "geometry_source": area.get("geometry_source"),
        "geometry_fingerprint": area.get("geometry_fingerprint"),
        "geometry_crs": area["crs"],
        "geometry": area["geometry"],
        "geometry_lonlat": {
            "type": "Polygon",
            "coordinates": [[[x, y] for x, y in dense]],
        },
        "bbox_lonlat": {
            "west": west,
            "south": south,
            "east": east,
            "north": north,
        },
        "bbox_ode": {
            "minlat": south,
            "maxlat": north,
            "westernlon": west_ode,
            "easternlon": east_ode,
        },
        "center_lonlat": {"lon": center_lon, "lat": center_lat},
        "center_lon_ode": center_lon_ode,
        "analysis_crs": analysis_crs,
        "area_m2": area_shape.area,
        "area_km2": area_shape.area / 1_000_000.0,
        "limitations": area.get("limitations") or [],
    }


def _area_shape(
    area: dict[str, Any], lonlat: list[tuple[float, float]], analysis_crs: str
) -> Polygon:
    shape_lonlat = Polygon(lonlat)
    if not shape_lonlat.is_valid or shape_lonlat.area <= 0:
        raise ValueError(f"invalid AOI geometry for {area['area_id']}")
    target = CRS.from_user_input(analysis_crs)
    if area["body"] == "Mars":
        source = CRS.from_proj4(f"+proj=longlat +R={MARS_RADIUS_M} +type=crs")
    else:
        source = CRS.from_proj4(f"+proj=longlat +R={MOON_RADIUS_M} +no_defs")
    transform = Transformer.from_crs(source, target, always_xy=True)
    projected = shapely_transform(shape_lonlat, transform.transform, interleaved=False)
    if projected.geom_type != "Polygon":
        raise ValueError(f"AOI did not project to a polygon: {area['area_id']}")
    return projected


def _area_by_id() -> dict[str, dict[str, Any]]:
    payload = json.loads(AREA_SOURCE.read_text(encoding="utf-8"))
    return {str(area["area_id"]): area for area in payload["areas"]}


# ---------------------------------------------------------------------------
# Footprint + coverage helpers


def _wkt_to_geojson(text: str) -> dict[str, Any] | None:
    match = re.search(r"POLYGON\s*\(\((.*?)\)\)", text, re.S)
    if not match:
        return None
    ring = []
    for pair in match.group(1).split(","):
        lon, lat = (float(value) for value in pair.split())
        ring.append([lon, lat])
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _footprint_shape(geometry: dict[str, Any]) -> BaseGeometry:
    candidate = shape(geometry)
    if candidate.is_empty or not candidate.is_valid:
        candidate = make_valid(candidate)
    if candidate.geom_type == "Polygon":
        return candidate
    if candidate.geom_type == "MultiPolygon":
        return candidate
    if candidate.geom_type == "GeometryCollection":
        polygons = [part for part in candidate.geoms if part.geom_type == "Polygon"]
        if polygons:
            return MultiPolygon(polygons)
    raise ValueError(f"footprint is not polygonal: {candidate.geom_type}")


def _coverage(
    aoi_lonlat: dict[str, Any],
    footprint_lonlat: dict[str, Any],
    body: str,
    analysis_crs: str,
) -> tuple[float | None, str, str | None]:
    """Return (fraction, state, error) using an equal-area intersection."""

    if body == "Mars":
        source_crs = f"+proj=longlat +R={MARS_RADIUS_M} +type=crs"
    else:
        source_crs = f"+proj=longlat +R={MOON_RADIUS_M} +no_defs"
    target = CRS.from_user_input(analysis_crs)
    transform = Transformer.from_crs(source_crs, target, always_xy=True)
    try:
        aoi = shapely_transform(
            _footprint_shape(aoi_lonlat), transform.transform, interleaved=False
        )
        footprint = shapely_transform(
            _footprint_shape(footprint_lonlat), transform.transform, interleaved=False
        )
        if aoi.area <= 0:
            return None, "unknown", "AOI has no positive area in the analysis CRS"
        fraction = aoi.intersection(footprint).area / aoi.area
    except Exception as error:  # pragma: no cover - defensive, recorded not hidden
        return None, "unknown", f"{type(error).__name__}: {error}"
    fraction = max(0.0, min(1.0, float(fraction)))
    if fraction >= 1.0 - COMPLETE_TOL:
        return 1.0, "complete", None
    if fraction <= COMPLETE_TOL:
        return 0.0, "no_coverage", None
    return fraction, "partial", None


# ---------------------------------------------------------------------------
# Label parsing


def _parse_pds3_label(text: str) -> dict[str, Any]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("/*") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        key, raw = key.strip(), raw.strip()
        if key in values:
            continue
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        values[key] = raw
    bands_match = re.search(r"^\s*BANDS\s*=\s*(\d+)", text, re.M)
    values["BANDS"] = bands_match.group(1) if bands_match else None
    return {
        "product_id": values.get("PRODUCT_ID"),
        "data_set_id": values.get("DATA_SET_ID"),
        "start_time": values.get("START_TIME"),
        "stop_time": values.get("STOP_TIME"),
        "orbit_number": values.get("ORBIT_NUMBER"),
        "instrument_mode": values.get("INSTRUMENT_MODE_ID"),
        "product_version_type": values.get("PRODUCT_VERSION_TYPE"),
        "product_creation_time": values.get("PRODUCT_CREATION_TIME"),
        "swath_width_px": values.get("CH1:SWATH_WIDTH"),
        "swath_length_px": values.get("CH1:SWATH_LENGTH"),
        "source_product_id": values.get("SOURCE_PRODUCT_ID"),
        "bands": values.get("BANDS"),
        "label_kind": "PDS3",
    }


def _xml_value(text: str, tag: str) -> str | None:
    match = re.search(rf"<{re.escape(tag)}(?:\s[^>]*)?>([^<]*)</{re.escape(tag)}>", text)
    return match.group(1).strip() if match and match.group(1).strip() else None


def _xml_values(text: str, tag: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(rf"<{re.escape(tag)}(?:\s[^>]*)?>([^<]*)</{re.escape(tag)}>", text)
        if match.group(1).strip()
    ]


def _parse_pds4_label(text: str) -> dict[str, Any]:
    return {
        "logical_identifier": _xml_value(text, "logical_identifier"),
        "version_id": _xml_value(text, "version_id"),
        "version_ids": _xml_values(text, "version_id"),
        "title": _xml_value(text, "title"),
        "start_time": _xml_value(text, "start_date_time"),
        "stop_time": _xml_value(text, "stop_date_time"),
        "solar_longitude": _xml_value(text, "solar_longitude"),
        "product_version_id": _xml_value(text, "mro:product_version_id")
        or _xml_value(text, "lro:product_version_id"),
        "orbit_number": _xml_value(text, "mro:orbit_number")
        or _xml_value(text, "lro:orbit_number"),
        "pixel_scale_x": _xml_value(text, "cart:pixel_scale_x"),
        "pixel_scale_unit": _xml_unit(text, "cart:pixel_scale_x"),
        "latitude_type": _xml_value(text, "cart:latitude_type"),
        "label_kind": "PDS4",
    }


def _xml_unit(text: str, tag: str) -> str | None:
    match = re.search(rf"<{re.escape(tag)}\s+unit=\"([^\"]+)\"", text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Discovery (network, bounded)


def _query_specs(aoi: dict[str, Any], body: str) -> list[dict[str, Any]]:
    bbox = aoi["bbox_ode"]
    base = {
        "target": body.lower(),
        "results": "cm",
        "output": "JSON",
        "minlat": f"{bbox['minlat']:.4f}",
        "maxlat": f"{bbox['maxlat']:.4f}",
        "westernlon": f"{bbox['westernlon']:.4f}",
        "easternlon": f"{bbox['easternlon']:.4f}",
        "loc": "b",
    }
    if body == "Moon":
        return [
            {**base, "ihid": "CH1-ORB", "iid": "M3", "pt": "REFIMG"},
            {**base, "ihid": "LRO", "iid": "LROC", "pt": "SDWTIO"},
        ]
    return [{**base, "ihid": "MRO", "iid": "CRISM", "pt": "MTRDR"}]


def _store_label(url: str, name: str) -> dict[str, Any]:
    LABELS.mkdir(parents=True, exist_ok=True)
    path = LABELS / name
    try:
        raw = _http_get(url)
    except Exception as error:
        return {
            "status": "failed",
            "url": url,
            "error": f"{type(error).__name__}: {error}",
            "file": None,
            "sha256": None,
        }
    payload = raw.replace(b"\r\n", b"\n")
    path.write_bytes(payload)
    return {
        "status": "stored",
        "url": url,
        "file": str(path.relative_to(HERE)),
        "sha256": sha256_bytes(payload),
        "raw_source_sha256": sha256_bytes(raw),
        "bytes": len(payload),
        "raw_source_bytes": len(raw),
        "line_endings": "LF normalized from CRLF" if payload != raw else "unchanged",
    }


def _moon_selection(
    products: list[dict[str, Any]], aoi: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranked: list[tuple[float, str, float, str, dict[str, Any]]] = []
    for product in products:
        footprint = _wkt_to_geojson(product.get("Footprint_C0_geometry") or "")
        if footprint is None:
            continue
        fraction, state, _error = _coverage(
            aoi["geometry_lonlat"], footprint, "Moon", aoi["analysis_crs"]
        )
        product_url = str(product.get("ProductURL") or "")
        match = re.search(r"product_id=([^&]+)", product_url)
        product_id = match.group(1) if match else str(product.get("Product_name") or "")
        center_lon = float(product.get("Center_longitude") or 0.0)
        observation_time = str(product.get("Observation_time") or "")
        ranked.append((fraction or 0.0, state, center_lon, product_id, product))
        product["_coverage_fraction"] = fraction
        product["_coverage_state"] = state
        product["_product_id"] = product_id
        product["_observation_time"] = observation_time
    complete = sorted(
        [row for row in ranked if row[1] == "complete"],
        key=lambda row: (
            abs(row[2] - aoi["center_lon_ode"]),
            row[3],
        ),
    )
    partial = sorted(
        [row for row in ranked if row[1] == "partial"],
        key=lambda row: (-(row[0] or 0.0), row[3]),
    )
    selected = complete[:MAX_MOON_COMPLETE] + partial[:MAX_MOON_PARTIAL]
    summary = {
        "candidates_returned": len(products),
        "complete": sum(1 for row in ranked if row[1] == "complete"),
        "partial": sum(1 for row in ranked if row[1] == "partial"),
        "no_coverage": sum(1 for row in ranked if row[1] == "no_coverage"),
        "unknown": sum(1 for row in ranked if row[1] == "unknown"),
        "selected_product_ids": [row[3] for row in selected],
        "selection_rule": (
            "Complete-coverage candidates ranked by absolute difference between the "
            "ODE swath centre longitude and the AOI centre longitude, then product id; "
            "up to three are stored. All partial-coverage candidates are ranked by "
            "decreasing overlap; up to one is stored. Non-covering candidates are not "
            "stored because the LROC TiO2 search supplies the explicit no-coverage case."
        ),
    }
    for _fraction, _state, _center, _pid, product in selected:
        product.pop("_coverage_fraction", None)
        product.pop("_coverage_state", None)
        product.pop("_product_id", None)
        product.pop("_observation_time", None)
    return [row[4] for row in selected], summary


def discover() -> dict[str, Any]:
    areas = _area_by_id()
    aois = {area_id: _aoi_record(areas[area_id]) for area_id in SELECTED_AREAS}
    QUERIES.mkdir(parents=True, exist_ok=True)
    LABELS.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "service": "NASA PDS Geosciences ODE REST",
        "service_version": ODE_SERVICE_VERSION,
        "endpoint": ODE_ENDPOINT,
        "concurrency": 1,
        "notes": [
            "Only JSON metadata and PDS labels are downloaded; no raster bytes.",
            "AOI queries are bounded bounding-box searches. One additional family-extent "
            "query over 70 S to 70 N documents that the LROC TiO2 product family has no "
            "polar tiles; only its count and latitude range are stored.",
        ],
        "queries": [],
    }

    for area_id in SELECTED_AREAS:
        aoi = aois[area_id]
        body = aoi["body"]
        for params in _query_specs(aoi, body):
            query_id = f"{area_id}-{params['iid'].lower()}-{params['pt'].lower()}"
            url = _ode_url(params)
            retrieved_at = utc_now()
            payload, raw = _http_json(url)
            response = payload.get("ODEResults", {})
            count = int(response.get("Count") or 0)
            products = []
            if isinstance(response.get("Products"), dict):
                products = response["Products"].get("Product") or []
            stored = products
            selection: dict[str, Any] | None = None
            count_info: dict[str, Any] | None = None
            if body == "Moon" and params["iid"] == "M3":
                stored, selection = _moon_selection(products, aoi)
                count_params = {**params, "results": "c"}
                count_payload, count_raw = _http_json(_ode_url(count_params))
                count_summary = count_payload.get("ODEResults", {})
                count_info = {
                    "request_url": _ode_url(count_params),
                    "count": int(count_summary.get("Count") or 0),
                    "status": count_summary.get("Status") or "unknown",
                    "raw_response_bytes": len(count_raw),
                    "raw_response_sha256": sha256_bytes(count_raw),
                }
                if selection is not None:
                    selection["metadata_query_count"] = count_info["count"]
            record = {
                "query_id": query_id,
                "area_id": area_id,
                "body": body,
                "dataset_id": (
                    TIO2_DATASET_ID
                    if params["iid"] == "LROC"
                    else M3_DATASET_ID
                    if params["iid"] == "M3"
                    else CRISM_DATASET_ID
                ),
                "service": "NASA PDS Geosciences ODE REST",
                "service_version": ODE_SERVICE_VERSION,
                "endpoint": ODE_ENDPOINT,
                "method": "GET",
                "params": params,
                "request_url": url,
                "retrieved_at": retrieved_at,
                "status": response.get("Status") or "unknown",
                "count": count,
                "products_returned": len(products),
                "products_stored": len(stored),
                "trimmed": len(stored) != len(products),
                "raw_response_bytes": len(raw),
                "raw_response_sha256": sha256_bytes(raw),
                "selection": selection,
                "count_query": count_info,
                "products": stored,
            }
            write_json(QUERIES / f"ode_{query_id}.json", record)
            result["queries"].append(
                {
                    "query_id": query_id,
                    "file": f"queries/ode_{query_id}.json",
                    "request_url": url,
                    "status": record["status"],
                    "count": count,
                    "products_returned": len(products),
                    "products_stored": len(stored),
                    "retrieved_at": retrieved_at,
                }
            )

    # A separate family-level query documents why the lunar polar AOI has no TiO2 tile.
    params = {
        "query": "products",
        "target": "moon",
        "results": "cm",
        "output": "JSON",
        "ihid": "LRO",
        "iid": "LROC",
        "pt": "SDWTIO",
        "minlat": "-70.0",
        "maxlat": "70.0",
        "westernlon": "0.0",
        "easternlon": "360.0",
        "loc": "b",
    }
    url = _ode_url(params)
    retrieved_at = utc_now()
    payload, raw = _http_json(url)
    response = payload.get("ODEResults", {})
    products = []
    if isinstance(response.get("Products"), dict):
        products = response["Products"].get("Product") or []
    lat_min = min(float(item["Minimum_latitude"]) for item in products) if products else None
    lat_max = max(float(item["Maximum_latitude"]) for item in products) if products else None
    write_json(
        QUERIES / "ode_moon_lroc_tio2_family.json",
        {
            "query_id": "moon-lroc-tio2-family-extent",
            "area_id": None,
            "body": "Moon",
            "dataset_id": TIO2_DATASET_ID,
            "service": "NASA PDS Geosciences ODE REST",
            "service_version": ODE_SERVICE_VERSION,
            "endpoint": ODE_ENDPOINT,
            "method": "GET",
            "params": params,
            "request_url": url,
            "retrieved_at": retrieved_at,
            "status": response.get("Status") or "unknown",
            "count": int(response.get("Count") or 0),
            "products_returned": len(products),
            "products_stored": 0,
            "trimmed": True,
            "raw_response_bytes": len(raw),
            "raw_response_sha256": sha256_bytes(raw),
            "tile_summary": {
                "tile_count": len(products),
                "latitude_min": lat_min,
                "latitude_max": lat_max,
                "longitude_min": 0.0,
                "longitude_max": 360.0,
                "tile_names": [item.get("Product_name") for item in products],
            },
            "selection": {
                "selection_rule": (
                    "Family-level extent summary only; per-tile footprints are not stored."
                )
            },
            "products": [],
        },
    )
    result["queries"].append(
        {
            "query_id": "moon-lroc-tio2-family-extent",
            "file": "queries/ode_moon_lroc_tio2_family.json",
            "request_url": url,
            "status": response.get("Status") or "unknown",
            "count": int(response.get("Count") or 0),
            "products_returned": len(products),
            "products_stored": 0,
            "retrieved_at": retrieved_at,
        }
    )

    # Store small labels for every selected product and one LROC family sample.
    label_index: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for query_file in sorted(QUERIES.glob("ode_*.json")):
        if query_file.name == "ode_moon_lroc_tio2_family.json":
            continue
        record = json.loads(query_file.read_text(encoding="utf-8"))
        for product in record.get("products") or []:
            label_url = product.get("LabelURL") or product.get("External_url")
            if not label_url:
                continue
            product_id = _product_id(product)
            if product_id in seen_labels:
                continue
            seen_labels.add(product_id)
            suffix = ".xml" if label_url.lower().endswith(".xml") else ".lbl"
            label_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", product_id) + suffix
            info = _store_label(label_url.replace("http://", "https://"), label_name)
            info["product_id"] = product_id
            info["dataset_id"] = record["dataset_id"]
            label_index.append(info)
    sample = _store_label(
        "https://pds.lroc.im-ldi.com/data/LRO-L-LROC-5-RDR-V1.0/LROLRC_2001/"
        "DATA/SDP/WAC_TIO2/WAC_TIO2_E350N0450.xml",
        "lroc_wac_tio2_sample.xml",
    )
    sample["product_id"] = "sdp.wac_tio2.wac_tio2_e350n0450"
    sample["dataset_id"] = TIO2_DATASET_ID
    label_index.append(sample)
    write_json(QUERIES / "label_index.json", {"labels": label_index})

    result["labels"] = label_index
    result["downloaded_label_bytes"] = sum(
        item.get("bytes") or 0 for item in label_index if item.get("status") == "stored"
    )
    write_json(QUERIES / "discovery.json", result)
    return result


def _product_id(product: dict[str, Any]) -> str:
    match = re.search(r"product_id=([^&]+)", str(product.get("ProductURL") or ""))
    if match:
        return match.group(1)
    name = str(product.get("Product_name") or "")
    return re.sub(r"\.(img|lbl|xml)$", "", name, flags=re.I)


# ---------------------------------------------------------------------------
# Offline evidence build


def _load_query(name: str) -> dict[str, Any]:
    return json.loads((QUERIES / name).read_text(encoding="utf-8"))


def _label_lookup() -> dict[str, dict[str, Any]]:
    index = json.loads((QUERIES / "label_index.json").read_text(encoding="utf-8"))
    return {item["product_id"]: item for item in index["labels"] if item.get("status") == "stored"}


def _label_parsed(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    path = HERE / item["file"]
    text = path.read_text(encoding="utf-8", errors="replace")
    parsed = _parse_pds4_label(text) if path.suffix.lower() == ".xml" else _parse_pds3_label(text)
    parsed["file"] = item["file"]
    parsed["source_url"] = item.get("url")
    parsed["bytes"] = len(text.encode("utf-8"))
    parsed["sha256"] = sha256_file(path)
    return parsed


def _utc(value: str | None) -> str | None:
    if not value:
        return value
    if value.endswith("Z"):
        return value
    return value + "Z"


def _product_record(
    product: dict[str, Any],
    dataset_id: str,
    body: str,
    label: dict[str, Any] | None,
) -> dict[str, Any]:
    product_id = _product_id(product)
    if body == "Moon":
        footprint_source = "ODE Footprint_C0_geometry"
        footprint = _wkt_to_geojson(product.get("Footprint_C0_geometry") or "")
        footprint_crs = (
            "IAU Moon 2000 planetocentric longitude/latitude, degrees, R=1737400 m (-180..180 east)"
        )
        collection = M3_COLLECTION
        license_text = M3_LICENSE
        license_url = M3_LICENSE_URL
        native_m = None
        map_scale = None
        pixel_scale = None
        resolution_status = "not_stated_in_product_label"
        resolution_notes = (
            "The L2 REFIMG label is a pixel-located pushbroom swath (304 samples, "
            "85 bands) and does not state a map scale or a per-product pixel size. "
            "No resolution was inferred from the swath width."
        )
    else:
        footprint_source = "ODE Footprint_geometry"
        footprint = _wkt_to_geojson(product.get("Footprint_geometry") or "")
        footprint_crs = (
            "Mars 2000 planetocentric longitude/latitude, degrees, R=3396190 m (positive east)"
        )
        collection = CRISM_COLLECTION
        license_text = CRISM_LICENSE
        license_url = CRISM_LICENSE_URL
        native_m = float(product.get("Map_scale") or 0.0) or None
        map_scale = native_m
        pixel_scale = float(product.get("Map_resolution") or 0.0) or None
        resolution_status = "verified_from_ode_metadata_and_pds4_label"
        resolution_notes = (
            "ODE Map_scale is metres per pixel; ODE Map_resolution and the label "
            "cart:pixel_scale_x are pixels per degree. Both are retained without "
            "resampling."
        )
    if footprint is None:
        raise ValueError(f"no footprint for {product_id}")
    observed = {
        "start": _utc(product.get("UTC_start_time") or (label or {}).get("start_time")),
        "end": _utc(product.get("UTC_stop_time") or (label or {}).get("stop_time")),
    }
    if body == "Moon":
        version = "V1.0 collection; product V01"
        if (label or {}).get("product_version_type"):
            version += f"; label version type {(label or {})['product_version_type']}"
    else:
        version = "V1.0 collection; MTRDR; PDS4 label 1.0"
        if (label or {}).get("product_version_id"):
            version += f"; product version {(label or {})['product_version_id']}"
    return {
        "product_id": product_id,
        "dataset_id": dataset_id,
        "body": body,
        "provider": (
            "NASA PDS Imaging Node / Chandrayaan-1 M3 team"
            if body == "Moon"
            else "NASA PDS Geosciences Node / MRO CRISM"
        ),
        "instrument": "Moon Mineralogy Mapper (M3)" if body == "Moon" else "CRISM",
        "collection_id": collection,
        "version": version,
        "license": license_text,
        "license_url": license_url,
        "doi": M3_DOI if body == "Moon" else None,
        "access_status": "open",
        "url": product.get("LabelURL")
        or str(product.get("External_url") or "").replace("http://", "https://"),
        "ode_product_page": product.get("ProductURL"),
        "footprint": footprint,
        "footprint_source": footprint_source,
        "footprint_crs": footprint_crs,
        "footprint_bbox": {
            "west": float(product.get("Westernmost_longitude") or 0.0),
            "south": float(product.get("Minimum_latitude") or 0.0),
            "east": float(product.get("Easternmost_longitude") or 0.0),
            "north": float(product.get("Maximum_latitude") or 0.0),
        },
        "temporal_support": {
            "kind": "observation",
            "start": observed["start"],
            "end": observed["end"],
            "notes": "PDS label and ODE observation metadata agree.",
        },
        "depth_support": {"kind": "surface", "min_m": None, "max_m": None},
        "resolution": {
            "native_m": native_m,
            "map_scale_m_per_pixel": map_scale,
            "pixel_scale_px_per_deg": pixel_scale,
            "status": resolution_status,
            "notes": resolution_notes,
        },
        "label": label,
        "verification_status": (
            "ode_metadata_and_pds_label_verified"
            if label
            else "ode_metadata_only_label_unavailable"
        ),
        "quality_flags": [
            "official_metadata_only",
            "no_raster_bytes_read",
            "no_spectral_values_read",
            "no_mineral_detection",
            "footprint_evidence_only",
        ],
        "limitations": [
            "A product footprint is not a valid observation and is not a mineral detection.",
            "The footprint is intersected with the AOI; no pixel values or valid-data mask were read.",
            "The product is not representative of the whole body or of all available observations.",
        ]
        + (
            [
                "M3 registration/geolocation in this repository is not independently validated.",
                "Polar illumination and shadowing were not assessed.",
            ]
            if body == "Moon"
            else [
                "CRISM summary-parameter thresholds and DP mask bits are not validated here.",
                "One targeted observation is not a survey of Jezero crater or Mars.",
            ]
        ),
    }


def _dataset_records(
    m3_summary: dict[str, Any],
    m3_products: list[dict[str, Any]],
    crism_products: list[dict[str, Any]],
    tio2_family: dict[str, Any],
    tio2_label: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    tiles = tio2_family.get("tile_summary") or {}
    m3_starts = [
        product["temporal_support"]["start"]
        for product in m3_products
        if product["temporal_support"]["start"]
    ]
    m3_ends = [
        product["temporal_support"]["end"]
        for product in m3_products
        if product["temporal_support"]["end"]
    ]
    crism_starts = [
        product["temporal_support"]["start"]
        for product in crism_products
        if product["temporal_support"]["start"]
    ]
    crism_ends = [
        product["temporal_support"]["end"]
        for product in crism_products
        if product["temporal_support"]["end"]
    ]
    crism_resolutions = sorted(
        {
            int(product["resolution"]["native_m"])
            for product in crism_products
            if product["resolution"]["native_m"]
        }
    )
    return [
        {
            "dataset_id": M3_DATASET_ID,
            "body": "Moon",
            "provider": "NASA PDS Imaging Node / Chandrayaan-1 M3 team",
            "instrument": "Moon Mineralogy Mapper (M3)",
            "collection_id": M3_COLLECTION,
            "version": "V1.0",
            "doi": M3_DOI,
            "license": M3_LICENSE,
            "license_url": M3_LICENSE_URL,
            "access_status": "open",
            "product_type": "L2 REFIMG (pixel-located reflectance)",
            "depth_support": {"kind": "surface", "min_m": None, "max_m": None},
            "temporal_support": {
                "kind": "observation_sample",
                "start": min(m3_starts) if m3_starts else None,
                "end": max(m3_ends) if m3_ends else None,
                "notes": (
                    "Range of the bounded selected M3 observations; the mission operated "
                    "2008-11 to 2009-08."
                ),
            },
            "resolution": {
                "native_m": None,
                "map_scale_m_per_pixel": None,
                "status": "not_stated_in_product_label",
                "notes": "No per-product map scale or pixel size is stated in the L2 REFIMG label.",
            },
            "verification_status": "metadata_and_label_verified",
            "selection": m3_summary,
            "limitations": [
                "Reflectance is not an abundance; no continuum removal or band-depth estimator is applied.",
                "Only a bounded set of candidates returned by one ODE bbox query is stored.",
            ],
        },
        {
            "dataset_id": CRISM_DATASET_ID,
            "body": "Mars",
            "provider": "NASA PDS Geosciences Node / MRO CRISM",
            "instrument": "Compact Reconnaissance Imaging Spectrometer for Mars (CRISM)",
            "collection_id": CRISM_COLLECTION,
            "version": "V1.0 collection; MTRDR; PDS4 label version 1.0",
            "doi": None,
            "license": CRISM_LICENSE,
            "license_url": CRISM_LICENSE_URL,
            "access_status": "open",
            "product_type": "MTRDR map-projected targeted observation",
            "depth_support": {"kind": "surface", "min_m": None, "max_m": None},
            "temporal_support": {
                "kind": "observation_sample",
                "start": min(crism_starts) if crism_starts else None,
                "end": max(crism_ends) if crism_ends else None,
                "notes": "Range of the bounded selected CRISM MTRDR observations.",
            },
            "resolution": {
                "native_m": crism_resolutions[0] if len(crism_resolutions) == 1 else None,
                "native_m_range": crism_resolutions,
                "map_scale_m_per_pixel": crism_resolutions,
                "pixel_scale_px_per_deg": sorted(
                    {
                        product["resolution"]["pixel_scale_px_per_deg"]
                        for product in crism_products
                        if product["resolution"]["pixel_scale_px_per_deg"]
                    }
                ),
                "status": "verified_from_ode_metadata_and_pds4_label",
                "notes": (
                    "Per-product map scale is retained in products.json; selected products "
                    "span 18 m and 36 m per pixel."
                ),
            },
            "verification_status": "metadata_and_label_verified",
            "selection": {
                "query_counts": {"mars-test-jezero-floor": 5, "mars-test-jezero-western-delta": 3},
                "selection_rule": (
                    "Every product returned by each bounded per-AOI ODE bbox query is "
                    "retained; no candidate is discarded or borrowed across AOIs."
                ),
            },
            "limitations": [
                "Footprints and labels only; no spectral values, summary parameters or DP mask were read.",
                "MTRDR summary-parameter thresholds are not validated for mineral detection.",
                "Two analyst-selected Jezero test AOIs are not production Mars references.",
            ],
        },
        {
            "dataset_id": TIO2_DATASET_ID,
            "body": "Moon",
            "provider": "NASA PDS Imaging Node / LROC",
            "instrument": "LRO Wide Angle Camera (WAC)",
            "collection_id": TIO2_COLLECTION,
            "version": (tio2_label or {}).get("version_id") or "2.0",
            "doi": TIO2_DOI,
            "license": TIO2_LICENSE,
            "license_url": TIO2_LICENSE_URL,
            "access_status": "open",
            "product_type": "SDWTIO WAC TiO2 abundance map (400 m/px)",
            "depth_support": {"kind": "surface", "min_m": None, "max_m": None},
            "temporal_support": {
                "kind": "cumulative",
                "start": None,
                "end": None,
                "notes": "Cumulative WAC product; per-tile acquisition times are not in the queried metadata.",
            },
            "resolution": {
                "native_m": 400.0,
                "map_scale_m_per_pixel": 398.991452949,
                "status": "verified_from_pds4_sample_label",
                "notes": "Sample label tile WAC_TIO2_E350N0450 states 400 m/px.",
            },
            "footprint": {
                "tile_count": tiles.get("tile_count"),
                "latitude_min": tiles.get("latitude_min"),
                "latitude_max": tiles.get("latitude_max"),
                "longitude_min": tiles.get("longitude_min"),
                "longitude_max": tiles.get("longitude_max"),
            },
            "verification_status": "family_extent_and_sample_label_verified",
            "limitations": [
                "The 16 WAC TiO2 tiles cover 70 S to 70 N only; the lunar polar AOI is outside the product family.",
                "TiO2 abundance is a derived oxide map, not a mineral detection in this evidence.",
                "No raster tile was downloaded.",
            ],
        },
    ]


def _tio2_record(
    aoi: dict[str, Any],
    area_query: dict[str, Any],
    family: dict[str, Any],
) -> dict[str, Any]:
    tiles = family.get("tile_summary") or {}
    lat_min = tiles.get("latitude_min")
    lat_max = tiles.get("latitude_max")
    if lat_min is None or lat_max is None:
        raise ValueError("LROC TiO2 family extent is missing")
    outside = aoi["bbox_lonlat"]["south"] < float(lat_min) or aoi["bbox_lonlat"]["north"] > float(
        lat_max
    )
    if not outside:
        raise ValueError("LROC TiO2 family unexpectedly reaches the selected lunar AOI")
    return {
        "record_id": f"d02-aoi:{aoi['area_id']}:{TIO2_DATASET_ID}:no-coverage",
        "area_id": aoi["area_id"],
        "body": "Moon",
        "dataset_id": TIO2_DATASET_ID,
        "product_id": None,
        "feature_id": "mineral_oxide_abundance_coverage",
        "observation_class": "mineral",
        "state": "no_coverage",
        "value": None,
        "unit": None,
        "value_kind": "category",
        "valid_fraction": None,
        "coverage": {
            "state": "no_coverage",
            "fraction": None,
            "method": (
                "Exact ODE bbox query for the AOI returned count=0; the separately "
                "queried product-family extent is 70 S to 70 N, outside the AOI."
            ),
            "analysis_crs": None,
            "aoi_area_m2": aoi["area_m2"],
            "covered_area_m2": None,
            "source_footprint": None,
        },
        "depth_support": {"kind": "surface", "min_m": None, "max_m": None},
        "temporal_support": {
            "kind": "cumulative",
            "start": None,
            "end": None,
            "notes": "No tile covers the AOI; no observation time applies.",
        },
        "resolution": {
            "native_m": 400.0,
            "map_scale_m_per_pixel": 398.991452949,
            "pixel_scale_px_per_deg": None,
            "status": "product_family_only_no_aoi_coverage",
            "notes": "Resolution describes the product family, not an AOI observation.",
        },
        "provenance": {
            "query_file": "queries/ode_moon-shackleton-rim-site04-roi2-lroc-sdwtio.json",
            "query_url": area_query["request_url"],
            "family_query_file": "queries/ode_moon_lroc_tio2_family.json",
            "family_query_url": family["request_url"],
            "query_count": area_query["count"],
            "query_retrieved_at": area_query["retrieved_at"],
            "response_sha256": area_query["raw_response_sha256"],
            "family_tile_latitude_min": lat_min,
            "family_tile_latitude_max": lat_max,
            "metadata_status": "official_query_count_and_family_extent_verified",
        },
        "quality_flags": [
            "official_query_count_zero",
            "no_product_footprint",
            "no_coverage_is_not_absence",
            "no_raster_bytes_read",
        ],
        "limitations": [
            "No WAC TiO2 tile intersects the AOI; this is not evidence that TiO2 is absent.",
            "The family extent is 70 S to 70 N; polar coverage is not available in this product.",
        ],
        "score_role": "evidence_only",
    }


def _coverage_record(
    aoi: dict[str, Any],
    product: dict[str, Any],
    label: dict[str, Any] | None,
) -> dict[str, Any]:
    body = str(product["body"])
    fraction, state, error = _coverage(
        aoi["geometry_lonlat"],
        product["footprint"],
        body,
        aoi["analysis_crs"],
    )
    covered = None if fraction is None else fraction * aoi["area_m2"]
    if state == "no_coverage":
        top_state = "no_coverage"
        covered = None
    elif state == "unknown":
        top_state = "unknown"
    else:
        top_state = "unknown"
    flags = [
        "official_metadata_only",
        "no_spectral_values_read",
        "no_mineral_detection",
        "no_abundance_exported",
        "footprint_evidence_only",
    ]
    if state == "no_coverage":
        flags.append("no_coverage_is_not_absence")
    if error:
        flags.append("geometry_error_recorded")
    return {
        "record_id": (
            f"d02-aoi:{aoi['area_id']}:{product['dataset_id']}:{product['product_id']}:coverage"
        ),
        "area_id": aoi["area_id"],
        "body": body,
        "dataset_id": product["dataset_id"],
        "product_id": product["product_id"],
        "feature_id": (
            "mineral_spectral_coverage" if body == "Mars" else "mineral_reflectance_coverage"
        ),
        "observation_class": "mineral",
        "state": top_state,
        "value": None,
        "unit": None,
        "value_kind": "category",
        "valid_fraction": None,
        "coverage": {
            "state": state,
            "fraction": None if state == "no_coverage" else fraction,
            "method": (
                "Equal-area LAEA intersection of the official ODE product footprint and "
                "the AOI; no raster or valid-data mask was read."
            ),
            "analysis_crs": aoi["analysis_crs"],
            "aoi_area_m2": aoi["area_m2"],
            "covered_area_m2": covered,
            "source_footprint": product["footprint_source"],
            "error": error,
        },
        "depth_support": product["depth_support"],
        "temporal_support": product["temporal_support"],
        "resolution": product["resolution"],
        "provenance": {
            "query_file": product["query_file"],
            "query_url": product["query_url"],
            "query_retrieved_at": product["query_retrieved_at"],
            "response_sha256": product["response_sha256"],
            "product_label_file": (label or {}).get("file"),
            "product_label_sha256": (label or {}).get("sha256"),
            "product_url": product["url"],
            "metadata_status": product["verification_status"],
            "coverage_state_derivation": "equal-area polygon intersection",
        },
        "quality_flags": flags,
        "limitations": list(product["limitations"])
        + (
            [
                "The official footprint states spatial extent, not valid-data coverage.",
                "No mineral state may be inferred from footprint coverage alone.",
            ]
            if state != "no_coverage"
            else [
                "The AOI does not overlap this product footprint; no values are borrowed "
                "from another AOI or product.",
            ]
        ),
        "score_role": "evidence_only",
    }


def _typed_geology_index() -> dict[str, dict[str, Any]]:
    """Read-only index of the existing D02 typed geology map evidence."""

    path = ROOT / "pipelines/geology/typed/observations.jsonl"
    index: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        dataset_ids = [
            item
            for item in row.get("dataset_ids") or []
            if item in {"usgs-moon-geology-v2", "usgs-jezero-sim3464"}
        ]
        if not dataset_ids:
            continue
        entry = index.setdefault(
            row["area_id"],
            {"dataset_ids": set(), "states": {}, "records": 0},
        )
        entry["dataset_ids"].update(dataset_ids)
        entry["states"][row["state"]] = entry["states"].get(row["state"], 0) + 1
        entry["records"] += 1
    return {
        area_id: {
            "dataset_ids": sorted(entry["dataset_ids"]),
            "states": dict(sorted(entry["states"].items())),
            "records": entry["records"],
        }
        for area_id, entry in index.items()
    }


def build(*, check: bool = False) -> dict[str, Any]:
    areas = _area_by_id()
    aois = {area_id: _aoi_record(areas[area_id]) for area_id in SELECTED_AREAS}
    aoi_payload = {
        "schema": "d02-aoi-selection-1.0.0",
        "scope": "Bounded Moon/Mars AOIs for Issue #170 Goal E D02 evidence.",
        "source": str(AREA_SOURCE.relative_to(ROOT)),
        "source_sha256": sha256_file(AREA_SOURCE),
        "config_source": str(AREA_CONFIG.relative_to(ROOT)),
        "config_sha256": sha256_file(AREA_CONFIG),
        "generated_from": "pipelines/d02_d04_aoi/geology/build.py",
        "areas": [aois[area_id] for area_id in SELECTED_AREAS],
    }
    write_json(HERE / "aoi.json", aoi_payload)

    labels = _label_lookup()
    queries = sorted(QUERIES.glob("ode_*.json"))
    query_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in queries]
    generated_at = max(
        (query["retrieved_at"] for query in query_payloads if query.get("retrieved_at")),
        default="1970-01-01T00:00:00Z",
    )

    products: list[dict[str, Any]] = []
    product_lookup: dict[str, dict[str, Any]] = {}
    for query in query_payloads:
        if not query.get("area_id"):
            continue
        for raw_product in query.get("products") or []:
            product_id = _product_id(raw_product)
            if product_id in product_lookup:
                continue
            label_item = labels.get(product_id)
            label = _label_parsed(label_item)
            record = _product_record(raw_product, query["dataset_id"], query["body"], label)
            record["query_file"] = str(
                (QUERIES / f"ode_{query['query_id']}.json").relative_to(HERE)
            )
            record["query_url"] = query["request_url"]
            record["query_retrieved_at"] = query["retrieved_at"]
            record["response_sha256"] = query["raw_response_sha256"]
            product_lookup[product_id] = record
            products.append(record)

    records: list[dict[str, Any]] = []
    for area_id in SELECTED_AREAS:
        aoi = aois[area_id]
        for query in query_payloads:
            if query.get("area_id") != area_id:
                continue
            for raw_product in query.get("products") or []:
                product = product_lookup[_product_id(raw_product)]
                records.append(_coverage_record(aoi, product, product["label"]))
    tio2 = _load_query("ode_moon_lroc_tio2_family.json")
    tio2_area_query = _load_query("ode_moon-shackleton-rim-site04-roi2-lroc-sdwtio.json")
    records.append(_tio2_record(aois["moon-shackleton-rim-site04-roi2"], tio2_area_query, tio2))
    records.sort(key=lambda row: (row["body"], row["area_id"], row["dataset_id"], row["record_id"]))

    m3_query = _load_query("ode_moon-shackleton-rim-site04-roi2-m3-refimg.json")
    m3_summary = m3_query.get("selection") or {}
    tio2_label_item = labels.get("sdp.wac_tio2.wac_tio2_e350n0450")
    tio2_label = _label_parsed(tio2_label_item)
    datasets = _dataset_records(
        m3_summary,
        [product for product in products if product["dataset_id"] == M3_DATASET_ID],
        [product for product in products if product["dataset_id"] == CRISM_DATASET_ID],
        tio2,
        tio2_label,
    )

    summaries = []
    typed_geology = _typed_geology_index()
    for area_id in SELECTED_AREAS:
        aoi = aois[area_id]
        area_records = [row for row in records if row["area_id"] == area_id]
        by_dataset = []
        for dataset in datasets:
            if dataset["body"] != aoi["body"]:
                continue
            subset = [row for row in area_records if row["dataset_id"] == dataset["dataset_id"]]
            if not subset:
                continue
            by_dataset.append(
                {
                    "dataset_id": dataset["dataset_id"],
                    "records": len(subset),
                    "coverage_states": _counts(row["coverage"]["state"] for row in subset),
                    "mineral_states": _counts(row["state"] for row in subset),
                    "product_ids": sorted(
                        {row["product_id"] for row in subset if row["product_id"]}
                    ),
                    "max_coverage_fraction": max(
                        (
                            row["coverage"]["fraction"]
                            for row in subset
                            if row["coverage"]["fraction"] is not None
                        ),
                        default=None,
                    ),
                    "any_valid_mineral_observation": False,
                    "notes": (
                        "Footprint evidence only; no spectral value, mineral detection or "
                        "abundance was ingested."
                    ),
                }
            )
        summaries.append(
            {
                "area_id": area_id,
                "display_name": aoi["display_name"],
                "body": aoi["body"],
                "origin": aoi["origin"],
                "area_km2": aoi["area_km2"],
                "geometry_fingerprint": aoi["geometry_fingerprint"],
                "datasets": by_dataset,
                "existing_geology_evidence": {
                    "source": "pipelines/geology/typed/observations.jsonl",
                    "rewritten": False,
                    **typed_geology.get(area_id, {}),
                    "note": (
                        "Read-only link to existing USGS geologic-map evidence. This "
                        "slice does not duplicate, rewrite or re-score those records."
                    ),
                    "state_semantics": (
                        "Existing typed D02 mapped-geologic-unit presence; not a mineral "
                        "detection and not a spectral observation."
                    ),
                },
                "observed_mineral_detection": False,
                "abundance_exported": False,
            }
        )

    evidence = {
        "schema": "d02-aoi-geology-evidence-1.0.0",
        "export_version": "d02-aoi-geology-1.0.0",
        "dimension": "D02",
        "dimension_name": "Geology & Mineralogy",
        "generated_at": generated_at,
        "scope": {
            "bounded_pilot": True,
            "bodies": ["Moon", "Mars"],
            "areas": list(SELECTED_AREAS),
            "global_coverage_claimed": False,
            "exhaustive_survey_claimed": False,
            "similarity_score_claimed": False,
            "raster_bytes_downloaded": 0,
            "official_footprint_service": "NASA PDS Geosciences ODE REST",
            "service_version": ODE_SERVICE_VERSION,
            "concurrency": 1,
            "notes": [
                "All records are official product footprints and labels, not spectral retrievals.",
                "No mineral was detected and no abundance was exported.",
                "no_coverage is explicit and is not evidence of absence.",
                "Existing D02 typed bundles were read for context and were not rewritten.",
            ],
        },
        "state_definitions": {
            "unknown": (
                "Official product metadata exist and the footprint may cover the AOI, but no "
                "validated mineral retrieval was performed. value and valid_fraction stay null."
            ),
            "no_coverage": (
                "No official product footprint intersects the AOI, or the official query "
                "returned zero products. value, unit and fraction stay null; this is not zero "
                "concentration and not evidence of absence."
            ),
        },
        "coverage_state_definitions": {
            "complete": "equal-area footprint/AOI intersection fraction is 1 within 1e-6",
            "partial": "0 < intersection fraction < 1 - 1e-6",
            "no_coverage": "intersection fraction is 0 within 1e-6; fraction is exported as null",
            "unknown": "the footprint could not be intersected; an error is recorded",
        },
        "datasets": datasets,
        "products": products,
        "aoi_summaries": summaries,
        "records": records,
        "limitations": [
            "A product footprint is an extent, not a valid-data mask or a mineral observation.",
            "M3 and CRISM spectral values were not read; no detection threshold was applied.",
            "The Moon M3 sample is a bounded subset of the candidates returned by one ODE query.",
            "The lunar LROC TiO2 family does not cover the polar AOI; that is no_coverage, not zero TiO2.",
            "Mars test AOIs are analyst-selected validation windows, not production references.",
            "ODE footprint coordinates were used as published; independent registration was not performed.",
        ],
        "warnings": [
            "One Moon AOI and two Mars test AOIs only; not a global or exhaustive survey.",
            "Existing `pipelines/geology/typed/` records are referenced, not modified.",
        ],
    }
    write_json(HERE / "evidence.json", evidence)
    write_jsonl(HERE / "evidence.jsonl", records)

    files = {}
    for name in (
        "aoi.json",
        "products.json",
        "evidence.json",
        "evidence.jsonl",
    ):
        path = HERE / name
        if path.exists():
            files[name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    # products.json is written now so the manifest and file stay consistent.
    products_payload = {
        "schema": "d02-aoi-products-1.0.0",
        "generated_at": generated_at,
        "products": products,
    }
    write_json(HERE / "products.json", products_payload)
    files["products.json"] = {
        "bytes": (HERE / "products.json").stat().st_size,
        "sha256": sha256_file(HERE / "products.json"),
    }
    query_files = {}
    for path in sorted(QUERIES.glob("*.json")):
        query_files[str(path.relative_to(HERE))] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    label_files = {}
    for path in sorted(LABELS.glob("*")):
        label_files[str(path.relative_to(HERE))] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    manifest = {
        "schema": "d02-aoi-geology-manifest-1.0.0",
        "export_version": "d02-aoi-geology-1.0.0",
        "generated_at": generated_at,
        "record_count": len(records),
        "product_count": len(products),
        "dataset_count": len(datasets),
        "area_count": len(SELECTED_AREAS),
        "body_counts": _counts(row["body"] for row in records),
        "state_counts": _counts(row["state"] for row in records),
        "coverage_state_counts": _counts(row["coverage"]["state"] for row in records),
        "files": files,
        "queries": query_files,
        "labels": label_files,
        "source_area_sha256": sha256_file(AREA_SOURCE),
        "typed_manifest": str(TYPED_MANIFEST.relative_to(ROOT)),
        "typed_manifest_sha256": sha256_file(TYPED_MANIFEST),
        "scope_note": (
            "D02 official-footprint evidence only. No mineral detection, abundance, "
            "similarity or combined score. no_coverage is never zero-filled."
        ),
    }
    write_json(HERE / "manifest.json", manifest)
    if check:
        _verify_manifest(manifest)
    return manifest


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _verify_manifest(manifest: dict[str, Any]) -> None:
    groups = ("files", "queries", "labels")
    for group in groups:
        for name, expected in manifest[group].items():
            path = HERE / name
            if not path.exists():
                raise SystemExit(f"missing file: {name}")
            if sha256_file(path) != expected["sha256"]:
                raise SystemExit(f"checksum mismatch: {name}")
            if path.stat().st_size != expected["bytes"]:
                raise SystemExit(f"size mismatch: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("discover", "build", "check", "all"),
        help="discover (network), build (offline), check, or both",
    )
    args = parser.parse_args()
    if args.command in {"discover", "all"}:
        discover()
    if args.command in {"build", "all"}:
        build()
    if args.command == "check":
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        _verify_manifest(manifest)
        print(f"verified {manifest['record_count']} records and all listed files")


if __name__ == "__main__":
    main()
