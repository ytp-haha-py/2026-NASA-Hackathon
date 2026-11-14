"""Conservative mineral observations; spectral parameters remain context only."""

from __future__ import annotations

from urllib.parse import urlencode

import numpy as np

from analog_pipelines.geology.io import Cache, artifact
from analog_pipelines.geology.models import Analysis, Area, Context, Dataset, Mineral
from analog_pipelines.geology.providers import base_analysis
from analog_pipelines.geology.spatial import area_shape, transform_geometry

LUNAR_METADATA = "https://zenodo.org/api/records/20725024"
LUNAR_RGB_METADATA = "https://zenodo.org/api/records/15549419"
CMR = "https://cmr.earthdata.nasa.gov/search"
ODE = "https://oderest.rsl.wustl.edu/live2/"


def unavailable_minerals(names: list[str], reason: str, state: str = "unknown") -> list[Mineral]:
    return [
        Mineral(
            evidence_id=f"unavailable:{name}",
            mineral=name,
            state=state,
            measurement="unverified",
            method="not_analyzed",
            quality="unverified",
            limitations=[reason],
        )
        for name in names
    ]


def summarize_identification(
    mineral_id: int,
    ids: np.ndarray,
    depth: np.ndarray,
    uncertainty: np.ndarray,
    inside_aoi: np.ndarray,
    quality_valid: np.ndarray,
) -> dict:
    """Native geolocated pixels only; denominator is sampled AOI pixels, not scene bbox.

    Caller must decode product-specific nodata, lookup names, quality bits and
    geolocation before calling. False quality is never a valid non-detection.
    """
    arrays = [ids, depth, uncertainty, inside_aoi, quality_valid]
    if any(a.shape != ids.shape for a in arrays):
        raise ValueError("geolocation, quality and mineral arrays must share native pixel support")
    total = np.count_nonzero(inside_aoi)
    if not total:
        return {"state": "no_coverage", "value": None, "uncertainty": None, "valid_fraction": 0.0}
    valid = inside_aoi & quality_valid & np.isfinite(ids) & (ids >= 0)
    # Missing depth/uncertainty invalidates a positive mineral observation.
    positive = ids > 0
    valid &= ~positive | (np.isfinite(depth) & np.isfinite(uncertainty) & (uncertainty >= 0))
    fraction = np.count_nonzero(valid) / total
    if not np.any(valid):
        return {"state": "unknown", "value": None, "uncertainty": None, "valid_fraction": 0.0}
    detected = valid & (ids == mineral_id)
    if not np.any(detected):
        return {
            "state": "not_detected_under_valid_observation",
            "value": None,
            "uncertainty": None,
            "valid_fraction": fraction,
        }
    return {
        "state": "detected",
        "value": float(np.mean(depth[detected])),
        # Keep source per-pixel uncertainty, do not pretend it is the error on the AOI mean.
        "uncertainty": float(np.mean(uncertainty[detected])),
        "valid_fraction": fraction,
        "detected_pixel_count": int(np.count_nonzero(detected)),
        "valid_pixel_count": int(np.count_nonzero(valid)),
    }


def emit_audit(area: Area, dataset: Dataset, cache: Cache) -> tuple[Analysis, dict]:
    geometry = transform_geometry(area_shape(area), area.crs, "EPSG:4326")
    bbox = ",".join(str(v) for v in geometry.bounds)
    collection_data, collection_receipt = cache.json(
        CMR + "/collections.json?" + urlencode({"short_name": "EMITL2BMIN", "page_size": 30}),
        "emit-collections.json",
    )
    collection = next(
        (r for r in collection_data["feed"]["entry"] if r["version_id"] == dataset.version), None
    )
    if collection is None:
        return base_analysis(
            area, dataset, status="unverified", reason="Requested version absent from live CMR"
        ), {"version": dataset.version, "hits": None}
    url = (
        CMR
        + "/granules.umm_json?"
        + urlencode(
            {
                "collection_concept_id": collection["id"],
                "bounding_box": bbox,
                "page_size": 2,
                "sort_key": "-start_date",
            }
        )
    )
    data, receipt = cache.json(url)
    hits = data["hits"]
    # A CMR footprint hit is potential coverage only, never valid mineral coverage.
    status = "no_coverage" if hits == 0 else "auth_required"
    reason = (
        "CMR bbox query returned no granules"
        if hits == 0
        else (
            "CMR metadata candidates only; Earthdata download + native geolocation, mask and uncertainty validation required."
        )
    )
    record = base_analysis(area, dataset, status=status, reason=reason)
    record.artifacts = [collection_receipt, receipt]
    record.mineralogy = unavailable_minerals(
        ["clay_minerals", "hydrated_sulfates", "carbonates", "iron_oxides"],
        reason,
        "no_coverage" if hits == 0 else "unknown",
    )
    record.quality_flags = [
        "cmr_bbox_hits_are_not_valid_pixel_coverage",
        "native_swath_not_orthorectified",
    ]
    return record, {
        "area_id": area.area_id,
        "version": dataset.version,
        "collection_id": collection["id"],
        "bbox_candidate_count": hits,
        "metadata_sample_count": len(data.get("items", [])),
        "pixel_valid_fraction": None,
        "status": status,
        "query_url": url,
        "estimated_size_mb_per_granule": 97.4 if dataset.version == "002" else 95.5,
    }


def lunar_audit(area: Area, dataset: Dataset, cache: Cache) -> tuple[Analysis, dict]:
    import h5py
    import rasterio

    metadata, receipt = cache.json(LUNAR_METADATA, "zenodo-v4.json")
    metadata_rgb, receipt_rgb = cache.json(LUNAR_RGB_METADATA, "zenodo-v3.json")
    raw = metadata["files"][0]
    matrix = cache.fetch(raw["links"]["self"], "lunar.mat", raw["checksum"])
    rgb = next(r for r in metadata_rgb["files"] if r["key"] == "Oli.tif")
    rgb_path = cache.fetch(rgb["links"]["self"], "lunar-oli.tif", rgb["checksum"])
    with h5py.File(matrix) as f:
        cube = f["AbcubeNew"]
        if cube.shape != (5, 2201, 2201) or cube.dtype.kind != "f":
            raise ValueError("unexpected lunar MAT layout")
        values = cube[()]
        inspection = {
            "variable": "AbcubeNew",
            "hdf5_shape": list(cube.shape),
            "matlab_shape": [2201, 2201, 5],
            "dtype": str(cube.dtype),
            "band_order_from_record": ["Oli", "Plag", "Opx", "Cpx", "SmFe"],
            "finite_min": float(np.nanmin(values)),
            "finite_max": float(np.nanmax(values)),
            "nonfinite_count": int(np.count_nonzero(~np.isfinite(values))),
            "values_outside_0_100": int(
                np.count_nonzero(np.isfinite(values) & ((values < 0) | (values > 100)))
            ),
            "embedded_crs": None,
            "embedded_transform": None,
            "unit": "not_verified",
            "valid_range": "not_verified",
        }
    with rasterio.open(rgb_path) as src:
        inspection["legacy_tiff"] = {
            "dtype": src.dtypes[0],
            "bands": src.count,
            "color_interpretation": [c.name for c in src.colorinterp],
            "crs": src.crs.to_wkt(),
            "transform": list(src.transform),
        }
    reason = (
        "Research-derived MAT has no embedded CRS, pixel registration, unit, valid-range or QA/uncertainty metadata. "
        "Legacy TIFF is RGB; its colors cannot supply abundance or validate v4 matrix registration. "
        "No AOI mineral numbers or detection claims enabled."
    )
    record = base_analysis(area, dataset, status="unverified", reason=reason)
    record.artifacts = [
        receipt,
        receipt_rgb,
        artifact(matrix, raw["links"]["self"], "research_derived_matrix", raw["checksum"]),
        artifact(rgb_path, rgb["links"]["self"], "legacy_visualization_only", rgb["checksum"]),
    ]
    record.mineralogy = unavailable_minerals(
        ["olivine", "plagioclase", "low_calcium_pyroxene", "high_calcium_pyroxene"],
        reason,
    )
    record.quality_flags = [
        "research_derived_product",
        "rgb_abundance_rejected",
        "spatial_registration_unverified",
        "units_unverified",
        "uncertainty_not_supplied",
    ]
    return record, inspection


def crism_context(area: Area, dataset: Dataset, cache: Cache) -> tuple[Analysis, dict]:
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import Window, from_bounds
    from shapely.geometry import mapping

    aoi = transform_geometry(area_shape(area), area.crs, "+proj=longlat +R=3396190 +type=crs")
    west, south, east, north = aoi.bounds
    if west < 0 or east < 0:
        raise ValueError("ODE adapter currently requires a split AOI in 0..180 east longitude")
    query = (
        ODE
        + "?"
        + urlencode(
            {
                "query": "product",
                "results": "fp",
                "output": "JSON",
                "limit": 5,
                "target": "mars",
                "ihid": "MRO",
                "iid": "CRISM",
                "pt": "MTRDR",
                "minlat": south,
                "maxlat": north,
                "westernlon": west,
                "easternlon": east,
            }
        )
    )
    data, receipt = cache.json(query)
    products = data["ODEResults"].get("Products", {}).get("Product", [])
    if isinstance(products, dict):
        products = [products]
    candidates = []
    for product in products:
        files = product["Product_files"]["Product_file"]
        for file in files:
            if file["Description"] == "REFINED SPECTRAL SUMMARY PAR. FILE":
                candidates.append((float(file["KBytes"]), product, file))
    if not candidates:
        return base_analysis(
            area,
            dataset,
            status="no_coverage",
            reason="No MTRDR summary candidate in bounded ODE query",
        ), {"candidate_count": 0}
    _, product, selected = min(candidates, key=lambda r: (r[0], r[1]["pdsid"]))
    files = product["Product_files"]["Product_file"]
    artifacts = [receipt]
    paths = {}
    for role, description in [
        ("summary", "REFINED SPECTRAL SUMMARY PAR. FILE"),
        ("information", "DP INFORMATION MAPS FILE"),
    ]:
        file = next(f for f in files if f["Description"] == description)
        url = file["URL"]
        # Keep real source basename so GDAL finds the paired ENVI header.
        for suffix in ("img", "hdr", "lbl"):
            source_url = url.rsplit(".", 1)[0] + "." + suffix
            path = cache.fetch(source_url, source_url.rsplit("/", 1)[1])
            artifacts.append(artifact(path, source_url, f"mtrdr_{role}_{suffix}"))
            if suffix == "img":
                paths[role] = path
    record = base_analysis(
        area,
        dataset,
        status="context_only",
        reason="Spectral parameters are not validated mineral identification or mass fraction.",
    )
    record.artifacts = artifacts
    with rasterio.open(paths["summary"]) as src, rasterio.open(paths["information"]) as info:
        if src.crs != info.crs or src.transform != info.transform or src.shape != info.shape:
            raise ValueError("MTRDR quality and summary grids differ")
        projected = transform_geometry(area_shape(area), area.crs, src.crs)
        try:
            window = from_bounds(*projected.bounds, src.transform).round_offsets().round_lengths()
            window = window.intersection(Window(0, 0, src.width, src.height))
        except rasterio.errors.WindowError:
            record.status, record.reason = (
                "no_coverage",
                "ODE bbox hit but raster does not intersect AOI",
            )
            return record, {
                "candidate_count": len(candidates),
                "selected_product": product["pdsid"],
            }
        inside = ~geometry_mask(
            [mapping(projected)],
            out_shape=(int(window.height), int(window.width)),
            transform=src.window_transform(window),
            invert=False,
        )
        # DP VNIR/IR Mask is retained as context; no undocumented quality bit threshold.
        quality = info.read(9, window=window, masked=True)
        summary = src.read(window=window, masked=True)
        total = int(np.count_nonzero(inside))
        native_valid = inside & ~np.ma.getmaskarray(quality) & np.isfinite(quality.filled(np.nan))
        fractions = []
        for name in (
            "OLINDEX3",
            "LCPINDEX2",
            "HCPINDEX2",
            "D2300",
            "BD1900_2",
            "SINDEX2",
            "CINDEX2",
        ):
            if name not in src.descriptions:
                continue
            band = summary[src.descriptions.index(name)]
            valid = native_valid & ~np.ma.getmaskarray(band) & np.isfinite(band.filled(np.nan))
            fraction = float(np.count_nonzero(valid) / total) if total else 0.0
            fractions.append(fraction)
            record.geological_context.append(
                Context(
                    evidence_id=f"crism:{product['pdsid']}:{name}",
                    name=name,
                    value=float(np.median(band.data[valid])) if np.any(valid) else None,
                    unit="dimensionless_spectral_parameter",
                    valid_fraction=fraction,
                    method="native_pixel_center_AOI_median_nonfill_finite",
                    meaning="Published MTRDR refined spectral summary parameter; band name from ENVI/PDS label. No mineral identification threshold applied.",
                    limitations=[
                        "Coverage describes finite, non-fill source pixels, not validated mineral detections.",
                        "DP quality mask values are not interpreted as a mineral detection algorithm.",
                        "No uncertainty product or calibrated abundance model applied.",
                    ],
                )
            )
        record.valid_fraction = min(fractions) if fractions else None
        record.analysis_crs = src.crs.to_wkt()
        record.source_crs = src.crs.to_wkt()
        record.native_resolution_m = src.res[0]
        inspection = {
            "candidate_count_in_first_5_products": len(candidates),
            "selected_product": product["pdsid"],
            "native_resolution_m": list(src.res),
            "source_crs": src.crs.to_wkt(),
            "nodata": src.nodata,
            "raster_bounds": list(src.bounds),
            "aoi_native_pixel_count": total,
            "information_mask_unique_values": np.unique(quality.compressed()).tolist(),
            "min_finite_summary_fraction": record.valid_fraction,
            "coverage_semantics": "AOI pixel-center, nonfill finite summary + DP mask pixels; no validated mineral coverage",
        }
    record.mineralogy = unavailable_minerals(
        ["olivine", "pyroxene", "clay_minerals", "hydrated_sulfates", "carbonates"],
        "Spectral parameter evidence retained in geological_context only; mineral detector not validated.",
    )
    record.quality_flags = [
        "context_only",
        "spectral_indices_are_not_abundances",
        "mineral_detection_not_validated",
    ]
    return record, inspection
