#!/usr/bin/env python3
"""Six-site LOLA expansion, official ROI centres first; source bounds centre otherwise."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analog_pipelines.features import extract_features  # noqa: E402
from analog_pipelines.paths import data_dir  # noqa: E402
from lola import (  # noqa: E402
    LolaReferenceProvider,
    build_manifest,
    default_cache_dir,
    default_dataset_info,
    default_schema_path,
    inspect_source,
    parse_source,
    validate_manifest,
    write_json_atomic,
)
from roi_centres import roi_centres  # noqa: E402

SITES = {
    "01": "Connecting Ridge",
    "04": "Shackleton Rim",
    "07": "Peak near Shackleton",
    "11": "de Gerlache Rim",
    "20": "Leibnitz Beta Plateau",
    "23": "Malapert Massif",
}
BASE = "https://pgda.gsfc.nasa.gov/data/LOLA_5mpp"


def plan(site, cache):
    source = f"{BASE}/Site{site}/Site{site}_final_adj_5mpp_surf.tif"
    if site in {"01", "04", "07", "11"}:
        url = f"{BASE}/Site{site}/Site{site}_ROIs.tgz"
        archive = cache / "roi" / f"Site{site}_ROIs.tgz"
        if not archive.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("ROI archive exceeds 1 MB budget")
            roi_centres(data)
            archive.write_bytes(data)
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        return source, [
            (
                f"roi{n}",
                (x, y),
                f"official {url}, record {n}: finite DBF X/Y, or corresponding SHP polygon bbox midpoint if DBF overflows; archive sha256:{sha}",
            )
            for n, x, y in roi_centres(archive.read_bytes())
        ]
    meta = inspect_source(parse_source(source), None, timeout=25, max_retries=1)
    left, bottom, right, top = meta.bounds
    return source, [
        (
            "bounds-centre",
            ((left + right) / 2, (bottom + top) / 2),
            "no ROI archive listed in official product78; centre of source raster bounds "
            f"{meta.bounds}; fixed 1980 m square, require >=80% valid after resampling",
        )
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", default=",".join(SITES))
    parser.add_argument("--limit-rois", type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.limit_rois <= 20 or any(s not in SITES for s in args.sites.split(",")):
        parser.error("Unsupported sites or ROI limit (1..20)")
    cache = default_cache_dir()
    report = data_dir("processed") / "lola-batch-status.json"
    statuses = []
    prior = json.loads(report.read_text()) if report.exists() else []
    for site in args.sites.split(","):
        output = Path("pipelines/planetary/manifests") / (
            "lola_moon_reference.json" if site == "04" else f"lola_site{site}_references.json"
        )
        original = json.loads(output.read_text()) if output.exists() else None
        refs = {r["reference_id"]: r for r in original["references"]} if original else {}
        try:
            source, windows = plan(site, cache)
        except Exception as exc:
            statuses.append(dict(site=site, provider="NASA LOLA", status="failed", error=str(exc)))
            print(f"FAILED Site{site}: {exc}", flush=True)
            windows = []
        for label, center, provenance in windows[: args.limit_rois]:
            refid = (
                f"moon-shackleton-rim-site04-{label}"
                if site == "04"
                else f"moon-lola-site{site}-{label}"
            )
            try:
                if site == "04" and label == "roi2" and refid in refs:
                    print(f"PRESERVED {refid}", flush=True)
                    statuses.append(dict(id=refid, provider="NASA LOLA", status="preserved"))
                    continue
                prepared = LolaReferenceProvider().prepare(
                    source=source,
                    center_xy=center,
                    cache_dir=cache,
                    timeout=25,
                    max_retries=1,
                    max_window_bytes=30 * 1024 * 1024,
                )
                result = extract_features(
                    str(prepared.cache_path), center_xy=center, body="Moon", source_label=source
                )
                dataset = default_dataset_info(
                    prepared,
                    dataset_id=f"nasa-lola-site{site}-5mpp-surf",
                    product_id=f"Site{site}_final_adj_5mpp_surf",
                    title=f"NASA LOLA Site{site} {SITES[site]} 5 m surface DEM",
                )
                doc = build_manifest(
                    prepared,
                    result,
                    dataset,
                    reference_id=refid,
                    display_name=f"{SITES[site]} (LOLA Site{site} {label})",
                    roi_name=label,
                    roi_provenance=provenance
                    + "; fixed windows may overlap other ROI-centred windows, not independent samples",
                    quality_flags=(
                        "lola_interpolated_dem",
                        "planetary_geographic_crs",
                        "roi_windows_may_overlap",
                    ),
                )
                refs[refid] = doc["references"][0]
                doc["references"] = sorted(refs.values(), key=lambda r: r["reference_id"])
                doc["datasets"][0]["checksum"] = (
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(doc["references"], sort_keys=True).encode()
                    ).hexdigest()
                )
                doc["datasets"][0]["notes"] += (
                    " Batch checksum covers canonical reference records with window checksums. Published error statistics are site/ROI dependent; not per-window uncertainty estimates."
                )
                validate_manifest(doc, default_schema_path())
                write_json_atomic(doc, output)
                statuses.append(
                    dict(
                        id=refid,
                        provider="NASA LOLA",
                        status="verified",
                        valid_fraction=result.valid_fraction,
                    )
                )
                print(f"OK {refid} valid={result.valid_fraction:.4f}", flush=True)
            except Exception as exc:
                statuses.append(
                    dict(id=refid, provider="NASA LOLA", status="failed", error=str(exc))
                )
                print(f"FAILED {refid}: {exc}", flush=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(prior + statuses, indent=2) + "\n")
    return int(any(row["status"] == "failed" for row in statuses))


if __name__ == "__main__":
    raise SystemExit(main())
