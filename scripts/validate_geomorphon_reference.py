#!/usr/bin/env python
"""Validate geomorphon buffers against the independent WhiteboxTools reference.

The script builds a synthetic 90 m DEM with known peaks/pits/ridges/valleys,
runs WhiteboxTools ``Geomorphons`` on the full DEM, on the normal 22x22 crop,
and on a 46x46 crop with the required 12-cell halo.  It compares the center
22x22 core and writes a small evidence artifact.

This is a method/buffer validation, not a production geomorphon feature.
WhiteboxTools is an independent implementation of the Jasiewicz & Stepinski
(2013) geomorphon algorithm; it is not part of the production dependency set.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs/terrain-geomorphon-reference.json"
SEARCH = 12
THRESHOLD = 1.0
CORE = 22
HALO = SEARCH
SIZE = 80
NODATA = -32768


def _synthetic_dem() -> np.ndarray:
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    z = (x - 40) ** 2 * 0.02 + (y - 40) ** 2 * 0.01
    z -= 30 * np.exp(-((x - 20) ** 2 + (y - 20) ** 2) / 80)  # pit
    z += 30 * np.exp(-((x - 60) ** 2 + (y - 60) ** 2) / 80)  # peak
    z += 10 * np.exp(-((x - 40) ** 2) / 200)  # ridge
    z -= 10 * np.exp(-((y - 30) ** 2) / 200)  # valley
    return z.astype("float32")


def _write_dem(path: Path, data: np.ndarray, transform: Any) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:32633",
        transform=transform,
        nodata=-9999,
    ) as dataset:
        dataset.write(data, 1)


def _crop(source: Path, target: Path, row: int, col: int, size: int) -> None:
    with rasterio.open(source) as dataset:
        window = Window(col, row, size, size)
        data = dataset.read(1, window=window)
        transform = dataset.window_transform(window)
        crs = dataset.crs
    with rasterio.open(
        target,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=-9999,
    ) as dataset:
        dataset.write(data, 1)


def _run(tools: Any, dem: Path, output: Path, *, forms: bool) -> None:
    code = tools.geomorphons(
        str(dem),
        str(output),
        search=SEARCH,
        threshold=THRESHOLD,
        forms=forms,
    )
    if code != 0:
        raise RuntimeError(f"WhiteboxTools geomorphons failed: {code}")
    if not output.is_file():
        raise RuntimeError("WhiteboxTools did not write the output raster")


def _read(path: Path) -> np.ndarray:
    with rasterio.open(path) as dataset:
        return dataset.read(1)


def _agreement(left: np.ndarray, right: np.ndarray, mask: np.ndarray) -> tuple[int, int, float]:
    comparable = mask & (left != NODATA) & (right != NODATA)
    total = int(comparable.sum())
    if total == 0:
        return 0, 0, float("nan")
    equal = int((left[comparable] == right[comparable]).sum())
    return equal, total, equal / total


def run(output: Path) -> dict[str, Any]:
    try:
        from whitebox import WhiteboxTools
    except ImportError as exc:  # pragma: no cover - opt-in dependency
        raise SystemExit(
            "WhiteboxTools is not installed. Run with: uv run --with whitebox "
            "python scripts/validate_geomorphon_reference.py"
        ) from exc

    tools = WhiteboxTools()
    tools.verbose_mode = False
    version = "unknown"
    for attribute in ("version", "get_version"):
        value = getattr(tools, attribute, None)
        if value is not None:
            version = str(value() if callable(value) else value)
            break

    temporary = Path(tempfile.mkdtemp(prefix="geomorphon-reference-"))
    try:
        dem = temporary / "dem.tif"
        _write_dem(dem, _synthetic_dem(), from_origin(500000, 4000000, 90, 90))
        full = temporary / "full"
        normal = temporary / "normal"
        buffered = temporary / "buffered"
        core_row = (SIZE - CORE) // 2
        normal_row = core_row
        buffered_row = core_row - HALO

        results: dict[str, Any] = {}
        full_arrays: dict[str, np.ndarray] = {}
        for forms, key in ((True, "forms"), (False, "ternary")):
            out = full.with_name(f"full-{key}.tif")
            _run(tools, dem, out, forms=forms)
            full_arrays[key] = _read(out)

        core = (slice(core_row, core_row + CORE), slice(core_row, core_row + CORE))

        for name, output_dir, row, size in (
            ("normal_22x22", normal, normal_row, CORE),
            ("buffered_46x46", buffered, buffered_row, CORE + 2 * HALO),
        ):
            crop_dem = temporary / f"{name}.tif"
            _crop(dem, crop_dem, row, row, size)
            entry: dict[str, Any] = {"window_size": size, "core_size": CORE}
            for forms, key in ((True, "forms"), (False, "ternary")):
                out = output_dir.with_name(f"{name}-{key}.tif")
                _run(tools, crop_dem, out, forms=forms)
                array = _read(out)
                if key == "forms":
                    entry["valid_cells"] = int((array != NODATA).sum())
                    values, counts = np.unique(array[array != NODATA], return_counts=True)
                    entry["class_counts"] = {
                        str(int(value)): int(count)
                        for value, count in zip(values, counts, strict=False)
                    }
                if name == "normal_22x22":
                    left = array
                    right = full_arrays[key][core]
                    mask = np.ones_like(array, dtype=bool)
                else:
                    core_slice = (slice(HALO, HALO + CORE), slice(HALO, HALO + CORE))
                    left = array[core_slice]
                    right = full_arrays[key][core]
                    mask = np.ones_like(left, dtype=bool)
                equal, total, fraction = _agreement(left, right, mask)
                entry[f"{key}_equal"] = equal
                entry[f"{key}_comparable"] = total
                entry[f"{key}_agreement"] = fraction
            results[name] = entry

        payload = {
            "reference": {
                "implementation": "WhiteboxTools Geomorphons",
                "version": version,
                "algorithm": "Jasiewicz & Stepinski (2013) geomorphon",
                "parameters": {
                    "search": SEARCH,
                    "threshold_degrees": THRESHOLD,
                    "skip": 0,
                    "fdist": 0,
                    "forms": True,
                },
            },
            "dem": {
                "synthetic": True,
                "size": SIZE,
                "resolution_m": 90,
                "features": ["pit", "peak", "ridge", "valley", "slope", "flat"],
                "crs": "EPSG:32633",
            },
            "core": {
                "size": CORE,
                "full_core_row_col": [core_row, core_row],
                "buffered_halo_cells": HALO,
            },
            "results": results,
            "limitations": [
                "Synthetic DEM only; no Earth/Moon/Mars real-DEM geomorphon class validation.",
                "WhiteboxTools is an independent reference implementation, not a production dependency.",
                "No geomorphon feature is added to the production registry or similarity score.",
                "Class codes are WhiteboxTools' 10-class output; class-name equivalence is not asserted.",
            ],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return payload
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    payload = run(args.out)
    normal = payload["results"]["normal_22x22"]
    buffered = payload["results"]["buffered_46x46"]
    print(
        f"normal valid={normal['valid_cells']}/{normal['window_size'] ** 2} "
        f"forms_agreement={normal['forms_agreement']:.4f} "
        f"ternary_agreement={normal['ternary_agreement']:.4f}"
    )
    print(
        f"buffered valid={buffered['valid_cells']}/{buffered['window_size'] ** 2} "
        f"forms_agreement={buffered['forms_agreement']:.4f} "
        f"ternary_agreement={buffered['ternary_agreement']:.4f}"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
