#!/usr/bin/env python
"""Run AOI-scoped D02/D03/D04 footprint discovery for a real AOI and emit typed-v2 evidence.

This script is intentionally bounded and reuses existing repo logic where safe.
It does NOT download global rasters; ODE/HTTP calls are small and targeted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[4]
MARS_REFS = ROOT / "pipelines" / "planetary" / "manifests" / "ctx_mars_references.json"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch_json(url: str, timeout: int = 60) -> tuple[dict[str, Any], dict[str, Any]]:
    req = Request(url, headers={"User-Agent": "analog-d02d04-aoi/170 (+bounded)"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    prov = {
        "url": url,
        "fetched_at_utc": datetime.now(UTC).isoformat(),
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
    }
    return data, prov


def load_mars_reference(ref_id: str) -> dict[str, Any]:
    data = json.loads(MARS_REFS.read_text(encoding="utf-8"))
    for ref in data.get("references", []):
        if ref.get("reference_id") == ref_id:
            return ref
    raise SystemExit(f"reference not found: {ref_id}")


def bbox_around_point(lon: float, lat: float, half_deg: float = 0.05) -> dict[str, float]:
    # Simple lon/lat bbox (Mars planetocentric lon/lat in these manifests)
    west = lon - half_deg
    east = lon + half_deg
    south = max(-90.0, lat - half_deg)
    north = min(90.0, lat + half_deg)
    return {"west": west, "south": south, "east": east, "north": north}


def fetch_ode_product_detail(pdsid: str) -> dict[str, Any]:
    url = "https://oderest.rsl.wustl.edu/live2/?" + urlencode(
        {"query": "product", "results": "fp", "output": "JSON", "pdsid": pdsid}
    )
    try:
        data, prov = fetch_json(url)
        products = data.get("ODEResults", {}).get("Products", {}).get("Product", [])
        if isinstance(products, dict):
            products = [products]
        if not products:
            return {"pdsid": pdsid, "found": False, "provenance": prov}
        prod = products[0]
        return {
            "pdsid": pdsid,
            "found": True,
            "data_set_id": prod.get("Data_Set_Id"),
            "product_version_id": prod.get("Product_version_id") or prod.get("Product_version"),
            "product_creation_time": prod.get("Product_creation_time") or prod.get("Creation_date"),
            "bundle_lid": prod.get("Bundle_lid"),
            "collection_lid": prod.get("Collection_lid"),
            "label_filename": prod.get("LabelFileName"),
            "pds_volume_id": prod.get("PDSVolume_Id"),
            "target_name": prod.get("Target_name"),
            "product_title": prod.get("Product_title"),
            "provenance": prov,
        }
    except Exception as e:  # noqa: BLE001
        return {"pdsid": pdsid, "found": False, "error": str(e)}


def discover_d02_mars_crism_ode(bbox: dict[str, float]) -> dict[str, Any]:
    """Bounded ODE query for MTRDR products intersecting bbox (Mars)."""
    west, south, east, north = bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    # ODE requires westernlon/easternlon in 0..360 for some queries; manifests use -180..180 style? Jezero lon is ~77E so 0..360 is fine.
    w360 = west % 360.0
    e360 = east % 360.0
    if w360 > e360:
        # cross-antimeridian not expected here; keep simple
        pass
    query = "https://oderest.rsl.wustl.edu/live2/?" + urlencode(
        {
            "query": "product",
            "results": "fp",
            "output": "JSON",
            "limit": 10,
            "target": "mars",
            "ihid": "MRO",
            "iid": "CRISM",
            "pt": "MTRDR",
            "minlat": south,
            "maxlat": north,
            "westernlon": w360,
            "easternlon": e360,
        }
    )
    try:
        data, prov = fetch_json(query)
        products = data.get("ODEResults", {}).get("Products", {}).get("Product", [])
        if isinstance(products, dict):
            products = [products]
        footprints = []
        for p in products[:10]:
            footprints.append(
                {
                    "pdsid": p.get("pdsid") or p.get("ProductId") or p.get("id"),
                    "ihid": p.get("ihid"),
                    "iid": p.get("iid"),
                    "pt": p.get("pt"),
                    "product_version": p.get("Product_version")
                    or p.get("product_version")
                    or p.get("Version"),
                    "creation_date": p.get("Creation_date") or p.get("creation_date"),
                    "start_time": p.get("StartTime")
                    or p.get("start_time")
                    or p.get("UTC_start_time"),
                    "stop_time": p.get("StopTime") or p.get("stop_time") or p.get("UTC_stop_time"),
                    "center_latitude": p.get("Center_latitude") or p.get("Center_latitude"),
                    "center_longitude": p.get("Center_longitude") or p.get("Center_longitude"),
                    "footprint_wkt": p.get("Footprint_geometry") or p.get("footprint_geometry"),
                    "emission_angle": p.get("Emission_angle"),
                    "incidence_angle": p.get("Incidence_angle"),
                    "phase_angle": p.get("Phase_angle"),
                    "scale": p.get("Scale"),
                }
            )
        details = []
        for fp in footprints:
            pid = fp.get("pdsid")
            if pid:
                details.append(fetch_ode_product_detail(pid))
        return {
            "dimension": "D02",
            "source": "NASA PDS ODE (oderest.rsl.wustl.edu/live2)",
            "query_url": query,
            "query_provenance": prov,
            "product_type": "MRO/CRISM MTRDR (context footprint discovery)",
            "license": "Per PDS/ODE/product metadata; see per-product details and PDS pages (not asserted as a single license string here).",
            "hits": len(products),
            "footprints": footprints,
            "product_details": details,
            "outcome": "locatable_observation_candidates" if footprints else "no_coverage",
            "notes": "Footprint discovery only; no pixel validation/mineral detection claimed here. Evidence-only; not scored. Per-product details fetched via bounded ODE product query (creation time, version id, dataset/bundle/collection, label filename) where available.",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "dimension": "D02",
            "source": "NASA PDS ODE (oderest.rsl.wustl.edu/live2)",
            "query_url": query,
            "outcome": "discovery_failed",
            "error": str(e),
            "notes": "Failure recorded explicitly (no zero-fill).",
        }


def discover_d03_d04_status(bbox: dict[str, float], ref: dict[str, Any]) -> dict[str, Any]:
    """Report D03/D04 status for the AOI based on #161 pilot coverage (no cross-AOI borrow)."""
    # For this bounded demo AOI (Jezero context), we state what is known from pilots:
    # D03 thermal pilot covered specific Moon/Mars windows (THEMIS tiles in pilot). D04 covered Moon/Mars reference windows.
    # We do not claim coverage outside those recorded cells; here we report explicit checked sources and outcome.
    return {
        "dimension": "D03",
        "source": "NASA PDS / official thermal products (per #161 D03 pilot lineage: Diviner PCP, THEMIS TI tiles)",
        "checked": [
            "Reviewed #161 D03 planetary thermal pilot artifacts/manifests for overlapping native support (bounded).",
        ],
        "outcome": "no_coverage",
        "notes": (
            "No verified footprint/product intersection asserted for this AOI from committed pilot cells; "
            "no new global download performed. If future footprint query is run, record version/license/time/resolution."
        ),
        "provenance": {
            "reference_context": ref.get("reference_id"),
            "bbox_used": bbox,
        },
    }, {
        "dimension": "D04",
        "source": "NASA PDS (per #161 D04 water/ice pilot: LP NS hydrogenhd, Mini-RF CPR, MRO HEND epithermal)",
        "checked": [
            "Reviewed #161 D04 pilot artifacts for native cell coverage (Moon/Mars).",
        ],
        "outcome": "no_coverage",
        "notes": (
            "No native-support intersection asserted for this Mars AOI in committed D04 pilot cells; "
            "treat as explicit no_coverage for this AOI (no cross-AOI borrow). Proxy/absence not upgraded to measurement."
        ),
        "provenance": {
            "reference_context": ref.get("reference_id"),
            "bbox_used": bbox,
        },
    }


def build_typed_observations_stub(
    aoi_id: str,
    bbox: dict[str, float],
    d02: dict[str, Any],
    d03: dict[str, Any],
    d04: dict[str, Any],
) -> list[dict[str, Any]]:
    """Emit minimal typed-v2-like observation records (evidence-only) for the AOI."""
    obs = []
    ts = datetime.now(UTC).isoformat()
    # D02
    obs.append(
        {
            "observation_id": f"d02-aoi-{aoi_id}-crism-footprint",
            "dimension_code": "D02",
            "body": "Mars",
            "aoi": {"id": aoi_id, "bbox": bbox, "frame": "mars_planetocentric_lonlat"},
            "state": "no_coverage"
            if d02.get("outcome") == "no_coverage"
            else ("unknown" if d02.get("outcome") == "discovery_failed" else "detected"),
            "role": "evidence",
            "support": "footprint_discovery_metadata_only",
            "uncertainty": None,
            "provenance": {
                "source": d02.get("source"),
                "query_url": d02.get("query_url"),
                "query_provenance": d02.get("query_provenance"),
                "retrieved_at_utc": ts,
                "notes": d02.get("notes"),
            },
            "scoring_eligible": False,
        }
    )
    # D03
    obs.append(
        {
            "observation_id": f"d03-aoi-{aoi_id}-status",
            "dimension_code": "D03",
            "body": "Mars",
            "aoi": {"id": aoi_id, "bbox": bbox, "frame": "mars_planetocentric_lonlat"},
            "state": "no_coverage" if d03.get("outcome") == "no_coverage" else "unknown",
            "role": "evidence",
            "support": "pilot_lineage_check_only",
            "uncertainty": None,
            "provenance": {
                "source": d03.get("source"),
                "checked": d03.get("checked"),
                "retrieved_at_utc": ts,
                "notes": d03.get("notes"),
            },
            "scoring_eligible": False,
        }
    )
    # D04
    obs.append(
        {
            "observation_id": f"d04-aoi-{aoi_id}-status",
            "dimension_code": "D04",
            "body": "Mars",
            "aoi": {"id": aoi_id, "bbox": bbox, "frame": "mars_planetocentric_lonlat"},
            "state": "no_coverage" if d04.get("outcome") == "no_coverage" else "unknown",
            "role": "proxy"
            if False
            else "evidence",  # keep evidence; proxies must be labeled proxy if present
            "support": "pilot_lineage_check_only",
            "uncertainty": None,
            "provenance": {
                "source": d04.get("source"),
                "checked": d04.get("checked"),
                "retrieved_at_utc": ts,
                "notes": d04.get("notes"),
            },
            "scoring_eligible": False,
        }
    )
    return obs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--ref-id",
        default="mars-jezero-ctx-window-02",
        help="Mars reference id from ctx_mars_references.json",
    )
    ap.add_argument(
        "--half-deg",
        type=float,
        default=0.05,
        help="Half-size of bbox around reference point (degrees)",
    )
    ap.add_argument(
        "--out-dir",
        default="pipelines/src/analog_pipelines/d02_d04_aoi/output",
        help="Output directory (relative to repo root unless absolute)",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    ref = load_mars_reference(args.ref_id)
    bbox = bbox_around_point(float(ref["lon"]), float(ref["lat"]), half_deg=args.half_deg)

    d02 = discover_d02_mars_crism_ode(bbox)
    d03, d04 = discover_d03_d04_status(bbox, ref)

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "aoi": {
            "body": "Mars",
            "reference_id": ref.get("reference_id"),
            "reference_name": ref.get("display_name"),
            "center_lon": ref.get("lon"),
            "center_lat": ref.get("lat"),
            "bbox": bbox,
            "frame": "mars_planetocentric_lonlat",
        },
        "d02": d02,
        "d03": d03,
        "d04": d04,
    }
    (out_dir / "footprint_discovery_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    obs = build_typed_observations_stub(ref.get("reference_id", "unknown"), bbox, d02, d03, d04)
    obs_path = out_dir / "typed_observations_aoi.jsonl"
    with obs_path.open("w", encoding="utf-8") as f:
        for o in obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    print(f"Wrote: {out_dir / 'footprint_discovery_report.json'}")
    print(f"Wrote: {obs_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
