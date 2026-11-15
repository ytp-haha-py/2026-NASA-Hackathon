"""Source-provider protocol and a real NASA POWER daily-point adapter."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

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
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
    Uncertainty,
)

NASA_POWER_ENDPOINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
NASA_POWER_METHOD_VERSION = "nasa-power-daily-1.0.0"
NASA_POWER_DATASET_ID = "nasa-power-daily-point"
NASA_POWER_PARAMETERS = {
    "ALLSKY_SFC_SW_DWN": (
        "surface_shortwave_irradiance_kwh_m2_day",
        "kWh m-2 day-1",
    ),
    "ALLSKY_SFC_LW_DWN": (
        "surface_longwave_irradiance_kwh_m2_day",
        "kWh m-2 day-1",
    ),
}


class AcquisitionError(RuntimeError):
    pass


class SourceProvider(Protocol):
    provider_id: str

    def discover(self, query: SourceQuery) -> list[SourceRecord]: ...

    def inspect(self, source: SourceRecord) -> SourceMetadata: ...

    def plan(self, query: SourceQuery, areas: list[PlanArea]) -> list[AcquisitionTask]: ...

    def acquire(self, task: AcquisitionTask, cache_dir: Path) -> LocalAsset: ...

    def process(self, asset: LocalAsset, task: AcquisitionTask) -> list[TypedObservation]: ...

    def validate(self, observations: list[TypedObservation]) -> ValidationReport: ...


def _center(area: PlanArea) -> tuple[float, float]:
    bbox = area.bbox
    return (
        (float(bbox["south"]) + float(bbox["north"])) / 2,
        (float(bbox["west"]) + float(bbox["east"])) / 2,
    )


def _iso_date(value: str) -> str:
    return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC).date().isoformat()


class NasaPowerProvider:
    """NASA POWER daily point values for D05 Earth context.

    These are coarse reanalysis/model values, not 1.98 km irradiance
    measurements. They are always emitted with ``context_only`` score role.
    """

    provider_id = "nasa-power"

    def discover(self, query: SourceQuery) -> list[SourceRecord]:
        if query.body != "Earth" or query.dimension != "D05":
            return []
        return [
            SourceRecord(
                source_id="nasa-power-daily-point",
                provider_id=self.provider_id,
                body="Earth",
                dimension="D05",
                product="NASA POWER Daily Point API",
                version="temporal/daily/point",
                endpoint=NASA_POWER_ENDPOINT,
                license="NASA POWER data are free and open; cite NASA POWER",
                license_url="https://power.larc.nasa.gov/docs/services/api/",
                native_resolution="approximately 0.5 x 0.625 degree",
                observation_kinds=["timeseries"],
                comparability="context_only",
                notes=(
                    "Coarse reanalysis/model context for solar illumination. Not a local "
                    "1.98 km measurement and not ionizing radiation."
                ),
                metadata={"parameters": sorted(NASA_POWER_PARAMETERS)},
            )
        ]

    def inspect(self, source: SourceRecord) -> SourceMetadata:
        return SourceMetadata(
            source_id=source.source_id,
            status="metadata_verified",
            fields={
                "endpoint": source.endpoint,
                "version": source.version,
                "license": source.license,
                "native_resolution": source.native_resolution,
                "parameters": sorted(NASA_POWER_PARAMETERS),
            },
            blockers=[],
        )

    def plan(self, query: SourceQuery, areas: list[PlanArea]) -> list[AcquisitionTask]:
        sources = self.discover(query)
        if not sources:
            return []
        source = sources[0]
        start = query.start or "2024-01-01"
        end = query.end or "2024-01-07"
        parameters = query.parameters.get("parameters") or ",".join(NASA_POWER_PARAMETERS)
        tasks: list[AcquisitionTask] = []
        for area in areas:
            if area.body != "Earth":
                continue
            if query.area_ids and area.area_id not in query.area_ids:
                continue
            lat, lon = _center(area)
            params = {
                "parameters": parameters,
                "community": "RE",
                "longitude": f"{lon:.6f}",
                "latitude": f"{lat:.6f}",
                "start": start.replace("-", ""),
                "end": end.replace("-", ""),
                "format": "JSON",
            }
            tasks.append(
                AcquisitionTask(
                    task_id=f"nasa-power-D05-earth-{area.area_id}-{start}-{end}",
                    provider_id=self.provider_id,
                    source_id=source.source_id,
                    body="Earth",
                    dimension="D05",
                    area=area,
                    endpoint=source.endpoint + "?" + urllib.parse.urlencode(params),
                    parameters=params,
                    method_version=NASA_POWER_METHOD_VERSION,
                    dataset_id=NASA_POWER_DATASET_ID,
                    expected_bytes=50_000,
                )
            )
        return tasks

    def acquire(self, task: AcquisitionTask, cache_dir: Path) -> LocalAsset:
        cache_dir.mkdir(parents=True, exist_ok=True)
        final = cache_dir / f"{task.task_id}.json"
        if final.is_file():
            return LocalAsset(
                task_id=task.task_id,
                path=str(final),
                sha256=file_sha256(final),
                bytes=final.stat().st_size,
                content_type="application/json",
            )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    task.endpoint,
                    headers={"User-Agent": "planetary-analog/0.1 (+acquisition)"},
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    data = response.read()
                if len(data) > 10_000_000:
                    raise AcquisitionError("POWER response exceeded bounded size")
                json.loads(data)
                atomic_write(final, data)
                return LocalAsset(
                    task_id=task.task_id,
                    path=str(final),
                    sha256=file_sha256(final),
                    bytes=final.stat().st_size,
                    content_type="application/json",
                )
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(min(8, 2**attempt))
        raise AcquisitionError(f"NASA POWER acquisition failed: {last_error}")

    def process(self, asset: LocalAsset, task: AcquisitionTask) -> list[TypedObservation]:
        payload = json.loads(Path(asset.path).read_text(encoding="utf-8"))
        parameter_payload = payload.get("properties", {}).get("parameter")
        if not isinstance(parameter_payload, dict):
            raise AcquisitionError("POWER response has no properties.parameter object")
        observations: list[TypedObservation] = []
        for parameter, series in parameter_payload.items():
            mapped = NASA_POWER_PARAMETERS.get(parameter)
            if mapped is None or not isinstance(series, dict):
                continue
            feature_id, unit = mapped
            dates: list[str] = []
            values: list[float] = []
            missing = 0
            for date, raw in sorted(series.items()):
                if raw is None or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                    missing += 1
                    continue
                if float(raw) <= -900:  # POWER fill/nodata sentinel
                    missing += 1
                    continue
                dates.append(_iso_date(date))
                values.append(float(raw))
            state = "detected" if values else "no_coverage"
            observations.append(
                TypedObservation(
                    observation_id=(
                        f"nasa-power:{task.area.area_id}:{feature_id}:"
                        f"{task.parameters['start']}-{task.parameters['end']}"
                    ),
                    area_id=task.area.area_id,
                    body="Earth",
                    dimension_code="D05",
                    feature_id=feature_id,
                    value_kind="timeseries",
                    state=state,
                    value=(
                        {
                            "timestamps": dates,
                            "values": values,
                            "unit": unit,
                            "missing_count": missing,
                            "parameter": parameter,
                            "community": "RE",
                        }
                        if state == "detected"
                        else None
                    ),
                    unit=unit,
                    uncertainty=Uncertainty(
                        kind="unknown",
                        notes="POWER supplies model/reanalysis values without per-point uncertainty.",
                    ),
                    spatial_support=SpatialSupport(
                        kind="point",
                        crs="EPSG:4326",
                        bbox=task.area.bbox,
                        bbox_crs="EPSG:4326",
                        source_resolution_m=50_000.0,
                        native_resolution_m=50_000.0,
                        notes=(
                            "NASA POWER coarse grid point at the AOI centre; approximately "
                            "0.5 x 0.625 degree. Not a 1.98 km local measurement."
                        ),
                    ),
                    temporal_support=TemporalSupport(
                        kind="interval",
                        start=dates[0] if dates else task.parameters["start"],
                        end=dates[-1] if dates else task.parameters["end"],
                        notes="Daily POWER values; interval covers the requested dates.",
                    ),
                    method_version=NASA_POWER_METHOD_VERSION,
                    dataset_ids=[NASA_POWER_DATASET_ID],
                    source_asset_ids=[f"{task.provider_id}:{task.area.area_id}:{task.task_id}"],
                    quality_flags=[
                        "model_or_reanalysis_context",
                        "coarse_resolution",
                        "not_a_local_irradiance_measurement",
                    ],
                    score_role="context_only",
                    notes=(
                        "D05 Earth solar illumination context only. Do not present as a local "
                        "measurement or combine with ionizing radiation."
                    ),
                )
            )
        if not observations:
            raise AcquisitionError("POWER response contained no supported parameters")
        return observations

    def validate(self, observations: list[TypedObservation]) -> ValidationReport:
        issues: list[ValidationIssue] = []
        for observation in observations:
            if observation.body != "Earth" or observation.dimension_code != "D05":
                issues.append(
                    ValidationIssue(
                        code="wrong_body_or_dimension",
                        message="POWER outputs must be Earth D05",
                        observation_id=observation.observation_id,
                    )
                )
            if observation.score_role != "context_only":
                issues.append(
                    ValidationIssue(
                        code="wrong_score_role",
                        message="POWER values are context only",
                        observation_id=observation.observation_id,
                    )
                )
            if observation.state == "detected":
                value = observation.value
                if not isinstance(value, dict):
                    issues.append(
                        ValidationIssue(
                            code="invalid_timeseries",
                            message="detected POWER value must be a timeseries object",
                            observation_id=observation.observation_id,
                        )
                    )
                    continue
                timestamps = value.get("timestamps")
                values = value.get("values")
                if not isinstance(timestamps, list) or not isinstance(values, list):
                    issues.append(
                        ValidationIssue(
                            code="invalid_timeseries",
                            message="timeseries requires dates and values lists",
                            observation_id=observation.observation_id,
                        )
                    )
                elif len(timestamps) != len(values) or not values:
                    issues.append(
                        ValidationIssue(
                            code="timeseries_length_mismatch",
                            message="dates and values must be non-empty and equal length",
                            observation_id=observation.observation_id,
                        )
                    )
                elif not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                    issues.append(
                        ValidationIssue(
                            code="non_finite_value",
                            message="POWER values must be finite",
                            observation_id=observation.observation_id,
                        )
                    )
        return ValidationReport.from_issues(len(observations), issues)


def default_providers() -> dict[str, SourceProvider]:
    from analog_pipelines.acquisition.providers_soilgrids import SoilGridsProvider

    providers: list[SourceProvider] = [NasaPowerProvider(), SoilGridsProvider()]
    return {provider.provider_id: provider for provider in providers}
