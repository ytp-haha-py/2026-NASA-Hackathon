"""SoilGrids 2.0 WCS provider for D06 Earth evidence-only soil properties.

SoilGrids values are global model predictions on a 250 m grid, not local soil
samples or rover measurements. Each observation keeps its own depth interval so
different depths are never averaged together or scored as one feature.
"""

from __future__ import annotations

import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio

from analog_pipelines.acquisition.models import (
    AcquisitionTask,
    LocalAsset,
    PlanArea,
    SourceMetadata,
    SourceQuery,
    SourceRecord,
    ValidationIssue,
    ValidationReport,
    atomic_write,
    file_sha256,
)
from analog_pipelines.typed_contracts import (
    DepthSupport,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
)

SOILGRIDS_METHOD_VERSION = "soilgrids-wcs-2.0.0"
SOILGRIDS_DATASET_ID = "soilgrids-250m-v2"
SOILGRIDS_PRODUCT_VERSION = "2.0"
SOILGRIDS_WCS_VERSION = "2.0.1"
SOILGRIDS_BASE = "https://maps.isric.org/mapserv"
SOILGRIDS_LICENSE = "CC BY 4.0; cite ISRIC SoilGrids 2.0"
SOILGRIDS_LICENSE_URL = "https://docs.isric.org/globaldata/soilgrids/"
SOILGRIDS_NATIVE_RESOLUTION_M = 250.0
SOILGRIDS_PROPERTIES = {
    "clay": ("g kg-1", "Clay content mass fraction"),
    "sand": ("g kg-1", "Sand content mass fraction"),
    "silt": ("g kg-1", "Silt content mass fraction"),
    "soc": ("dg kg-1", "Soil organic carbon"),
    "bdod": ("cg cm-3", "Bulk density of the fine earth fraction"),
}
# Standard SoilGrids 2.0 depth intervals, in centimetres. The public WCS endpoint
# also serves 60-100cm and 100-200cm; only these four bounded intervals are
# registered because they are the ones exercised by the D06 pilot.
SOILGRIDS_DEPTHS = {
    "0-5cm": (0, 5),
    "5-15cm": (5, 15),
    "15-30cm": (15, 30),
    "30-60cm": (30, 60),
}
SOILGRIDS_STATISTICS = {
    "mean": "mean",
    "Q0.05": "q05",
    "Q0.95": "q95",
}
# ISRIC asks clients to be considerate. Requests are serialized to at most one
# every 0.25 s per process, with exponential backoff on throttling/5xx errors.
SOILGRIDS_MIN_REQUEST_INTERVAL_S = 0.25
SOILGRIDS_MAX_ATTEMPTS = 4
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


def depth_interval_m(depth: str) -> tuple[float, float]:
    """Return the registered depth interval in metres for a SoilGrids depth label."""
    if depth not in SOILGRIDS_DEPTHS:
        raise ValueError(f"unsupported SoilGrids depth {depth!r}")
    start_cm, end_cm = SOILGRIDS_DEPTHS[depth]
    return start_cm / 100.0, end_cm / 100.0


def _wait_for_request_slot() -> None:
    global _LAST_REQUEST_AT
    with _RATE_LOCK:
        now = time.monotonic()
        wait = SOILGRIDS_MIN_REQUEST_INTERVAL_S - (now - _LAST_REQUEST_AT)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST_AT = time.monotonic()


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Exponential backoff, honouring a numeric Retry-After header when present."""
    if retry_after:
        try:
            return min(30.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return min(30.0, float(2 ** (attempt + 1)))


class SoilGridsError(RuntimeError):
    pass


def _wcs_url(property_id: str, coverage_id: str, bbox: dict[str, float]) -> str:
    params = [
        ("service", "WCS"),
        ("version", SOILGRIDS_WCS_VERSION),
        ("request", "GetCoverage"),
        ("CoverageId", coverage_id),
        ("subsettingCrs", "http://www.opengis.net/def/crs/EPSG/0/4326"),
        ("subset", f"Long({bbox['west']},{bbox['east']})"),
        ("subset", f"Lat({bbox['south']},{bbox['north']})"),
        ("format", "image/tiff"),
    ]
    return (
        SOILGRIDS_BASE + "?" + urllib.parse.urlencode([("map", f"/map/{property_id}.map"), *params])
    )


class SoilGridsProvider:
    provider_id = "soilgrids-wcs"

    def discover(self, query: SourceQuery) -> list[SourceRecord]:
        if query.body != "Earth" or query.dimension != "D06":
            return []
        return [
            SourceRecord(
                source_id="soilgrids-250m-v2",
                provider_id=self.provider_id,
                body="Earth",
                dimension="D06",
                product="SoilGrids 2.0 250 m WCS",
                version=SOILGRIDS_PRODUCT_VERSION,
                endpoint=SOILGRIDS_BASE,
                license=SOILGRIDS_LICENSE,
                license_url=SOILGRIDS_LICENSE_URL,
                native_resolution="250 m",
                observation_kinds=["numeric"],
                comparability="evidence_only",
                notes=(
                    "Model-derived soil property predictions at 250 m with explicit depth "
                    "intervals and Q0.05/Q0.95 prediction quantiles. Not a local sample, not a "
                    "rover measurement, and not a cross-body comparison."
                ),
                metadata={
                    "properties": sorted(SOILGRIDS_PROPERTIES),
                    "depths": sorted(SOILGRIDS_DEPTHS),
                    "statistics": sorted(SOILGRIDS_STATISTICS),
                },
            )
        ]

    def inspect(self, source: SourceRecord) -> SourceMetadata:
        return SourceMetadata(
            source_id=source.source_id,
            status="accessible",
            fields={
                "endpoint": SOILGRIDS_BASE,
                "version": source.version,
                "license": source.license,
                "native_resolution": source.native_resolution,
                "properties": sorted(SOILGRIDS_PROPERTIES),
            },
            blockers=[],
        )

    def plan(self, query: SourceQuery, areas: list[PlanArea]) -> list[AcquisitionTask]:
        if query.body != "Earth" or query.dimension != "D06":
            return []
        properties = query.parameters.get("properties", "clay,sand,silt,soc,bdod").split(",")
        depths = query.parameters.get("depths", "0-5cm").split(",")
        statistics = query.parameters.get("statistics", "mean,Q0.05,Q0.95").split(",")
        tasks: list[AcquisitionTask] = []
        for area in areas:
            if area.body != "Earth":
                continue
            if query.area_ids and area.area_id not in query.area_ids:
                continue
            for property_id in properties:
                property_id = property_id.strip()
                if property_id not in SOILGRIDS_PROPERTIES:
                    raise ValueError(f"unsupported SoilGrids property {property_id!r}")
                unit = SOILGRIDS_PROPERTIES[property_id][0]
                for depth in depths:
                    depth = depth.strip()
                    if depth not in SOILGRIDS_DEPTHS:
                        raise ValueError(f"unsupported SoilGrids depth {depth!r}")
                    for statistic in statistics:
                        statistic = statistic.strip()
                        if statistic not in SOILGRIDS_STATISTICS:
                            raise ValueError(f"unsupported SoilGrids statistic {statistic!r}")
                        coverage_id = f"{property_id}_{depth}_{statistic}"
                        task_id = (
                            f"soilgrids-D06-earth-{area.area_id}-{property_id}-{depth}-{statistic}"
                        )
                        tasks.append(
                            AcquisitionTask(
                                task_id=task_id,
                                provider_id=self.provider_id,
                                source_id="soilgrids-250m-v2",
                                body="Earth",
                                dimension="D06",
                                area=area,
                                endpoint=_wcs_url(property_id, coverage_id, area.bbox),
                                parameters={
                                    "property": property_id,
                                    "depth": depth,
                                    "statistic": statistic,
                                    "unit": unit,
                                    "coverage_id": coverage_id,
                                },
                                method_version=SOILGRIDS_METHOD_VERSION,
                                dataset_id=SOILGRIDS_DATASET_ID,
                                expected_bytes=80_000,
                                output_suffix=".tif",
                            )
                        )
        return tasks

    def acquire(self, task: AcquisitionTask, cache_dir: Path) -> LocalAsset:
        cache_dir.mkdir(parents=True, exist_ok=True)
        final = cache_dir / f"{task.task_id}.tif"
        if final.is_file() and final.read_bytes()[:2] in (b"II", b"MM"):
            return LocalAsset(
                task_id=task.task_id,
                path=str(final),
                sha256=file_sha256(final),
                bytes=final.stat().st_size,
                content_type="image/tiff",
            )
        last_error: Exception | None = None
        for attempt in range(SOILGRIDS_MAX_ATTEMPTS):
            try:
                _wait_for_request_slot()
                request = urllib.request.Request(
                    task.endpoint,
                    headers={"User-Agent": "planetary-analog/0.1 (+acquisition)"},
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    status = int(getattr(response, "status", 200))
                    data = response.read()
                if len(data) > 5_000_000:
                    raise SoilGridsError("SoilGrids response exceeded bounded size")
                if data[:2] not in (b"II", b"MM"):
                    raise SoilGridsError(
                        "SoilGrids WCS did not return TIFF: " + data[:300].decode(errors="replace")
                    )
                atomic_write(final, data)
                return LocalAsset(
                    task_id=task.task_id,
                    path=str(final),
                    sha256=file_sha256(final),
                    bytes=final.stat().st_size,
                    content_type="image/tiff",
                    http_status=status,
                )
            except urllib.error.HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == SOILGRIDS_MAX_ATTEMPTS - 1:
                    break
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = _retry_delay(attempt, retry_after)
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                if attempt < SOILGRIDS_MAX_ATTEMPTS - 1:
                    time.sleep(_retry_delay(attempt))
        raise SoilGridsError(f"SoilGrids acquisition failed: {last_error}")

    def process(self, asset: LocalAsset, task: AcquisitionTask) -> list[TypedObservation]:
        with rasterio.open(asset.path) as src:
            array = src.read(1, masked=True).astype("float64")
            nodata = src.nodata
            valid = ~np.ma.getmaskarray(array)
            values = np.asarray(array.filled(np.nan), dtype="float64")
            valid &= np.isfinite(values)
            if nodata is not None:
                valid &= values != float(nodata)
            sample = values[valid]
            mean = float(np.mean(sample)) if sample.size else None
            total = int(values.size)
            valid_fraction = float(sample.size / total) if total else 0.0
            # WCS subsets are 250 m source cells expressed on a geographic grid; src.res is
            # in degrees and must never be recorded as a metric resolution.
            resolution = SOILGRIDS_NATIVE_RESOLUTION_M
            depth = task.parameters["depth"]
            depth_min_m, depth_max_m = depth_interval_m(depth)
            property_id = task.parameters["property"]
            statistic = task.parameters["statistic"]
            unit = task.parameters["unit"]
        state = "detected" if mean is not None and math.isfinite(mean) else "no_coverage"
        return [
            TypedObservation(
                observation_id=f"soilgrids:{task.area.area_id}:{property_id}:{depth}:{STATISTICS_TO_CODE[statistic]}",
                area_id=task.area.area_id,
                body="Earth",
                dimension_code="D06",
                feature_id=f"soil_{property_id}_{depth.replace('-', '_')}_{STATISTICS_TO_CODE[statistic]}",
                value_kind="numeric",
                state=state,
                value=mean if state == "detected" else None,
                unit=unit,
                spatial_support=SpatialSupport(
                    kind="bbox",
                    crs="EPSG:4326",
                    bbox=task.area.bbox,
                    bbox_crs="EPSG:4326",
                    resolution_m=resolution,
                    source_resolution_m=resolution,
                    native_resolution_m=SOILGRIDS_NATIVE_RESOLUTION_M,
                    valid_fraction=valid_fraction,
                    notes=(
                        "Windowed SoilGrids 2.0 WCS subset at the AOI envelope; mean of native "
                        "250 m model cells. Not a local soil sample. The AOI mean is not a "
                        "SoilGrids product cell value."
                    ),
                ),
                temporal_support=TemporalSupport(kind="unknown"),
                depth_support=DepthSupport(
                    kind="depth_interval",
                    min_m=depth_min_m,
                    max_m=depth_max_m,
                ),
                method_version=SOILGRIDS_METHOD_VERSION,
                dataset_ids=[SOILGRIDS_DATASET_ID],
                source_asset_ids=[f"{task.provider_id}:{task.area.area_id}:{task.task_id}"],
                quality_flags=[
                    "model_derived_soil_property",
                    "model_prediction_not_measurement",
                    "250m_native_grid",
                    "global_model_not_local_sample",
                    f"depth_interval_{depth}",
                    f"statistic_{STATISTICS_TO_CODE[statistic]}",
                ],
                score_role="evidence_only",
                notes=(
                    f"{SOILGRIDS_PROPERTIES[property_id][1]} ({unit}); SoilGrids prediction for "
                    f"depth {depth}; statistic {statistic}. Model uncertainty is carried by the "
                    "separate Q0.05/Q0.95 prediction-quantile records at the same depth, not by a "
                    "confidence interval for this AOI mean. Depths are never pooled."
                ),
            )
        ]

    def validate(self, observations: list[TypedObservation]) -> ValidationReport:
        issues: list[ValidationIssue] = []
        for observation in observations:
            if observation.body != "Earth" or observation.dimension_code != "D06":
                issues.append(
                    ValidationIssue(
                        code="wrong_body_or_dimension",
                        message="SoilGrids is Earth D06 only",
                        observation_id=observation.observation_id,
                    )
                )
            if observation.score_role != "evidence_only":
                issues.append(
                    ValidationIssue(
                        code="wrong_score_role",
                        message="SoilGrids values are evidence only",
                        observation_id=observation.observation_id,
                    )
                )
            if observation.state == "detected":
                value = observation.value
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    issues.append(
                        ValidationIssue(
                            code="non_finite_value",
                            message="detected SoilGrids value must be finite",
                            observation_id=observation.observation_id,
                        )
                    )
                if not observation.unit:
                    issues.append(
                        ValidationIssue(
                            code="missing_unit",
                            message="SoilGrids observation requires a unit",
                            observation_id=observation.observation_id,
                        )
                    )
                if observation.depth_support.kind != "depth_interval":
                    issues.append(
                        ValidationIssue(
                            code="missing_depth",
                            message="SoilGrids observation requires a depth interval",
                            observation_id=observation.observation_id,
                        )
                    )
                elif (
                    observation.depth_support.min_m,
                    observation.depth_support.max_m,
                ) not in {depth_interval_m(depth) for depth in SOILGRIDS_DEPTHS}:
                    issues.append(
                        ValidationIssue(
                            code="unsupported_depth_interval",
                            message="SoilGrids depth interval is not a registered product interval",
                            observation_id=observation.observation_id,
                        )
                    )
        return ValidationReport.from_issues(len(observations), issues)


STATISTICS_TO_CODE = {"mean": "mean", "Q0.05": "q05", "Q0.95": "q95"}
