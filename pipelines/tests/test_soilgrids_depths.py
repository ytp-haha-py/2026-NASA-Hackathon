"""Offline D06 SoilGrids depth, state and release-integrity tests (Issue #46)."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import urllib.error
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_api.adapters.release_import import import_release
from analog_api.adapters.seed import make_engine
from analog_api.adapters.typed_db import TypedObservationStore, init_typed_db
from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    LocalAsset,
    PlanArea,
    SourceQuery,
    TaskCheckpoint,
    write_json,
)
from analog_pipelines.acquisition.planner import build_plan
from analog_pipelines.acquisition.providers_regolith import (
    NO_SAMPLE_BLOCKERS,
    SparseRegolithProvider,
)
from analog_pipelines.acquisition.providers_soilgrids import (
    SOILGRIDS_DEPTHS,
    SOILGRIDS_MIN_REQUEST_INTERVAL_S,
    SoilGridsProvider,
    depth_interval_m,
)
from analog_pipelines.acquisition.release import (
    ReleaseIntegrityError,
    build_release,
    verify_release_integrity,
)
from analog_pipelines.typed_contracts import TypedObservation
from rasterio.transform import from_origin
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
DEPTH_RELEASE = ROOT / "pipelines/pilots/d06_earth_soilgrids_depths"
BASE_RELEASE = ROOT / "pipelines/pilots/d06_earth_soilgrids"


def _area() -> PlanArea:
    return PlanArea(
        area_id="earth-test",
        display_name="earth-test",
        body="Earth",
        bbox={"west": 10.0, "south": 45.0, "east": 10.02, "north": 45.02},
        crs="EPSG:4326",
        coordinate_frame="earth_wgs84",
    )


def _plan(**parameters: str) -> AcquisitionPlan:
    return build_plan(
        scope="test",
        areas=[_area()],
        dimensions=["D06"],
        bodies=["Earth"],
        provider_ids=["soilgrids-wcs"],
        parameters=parameters,
    )


def _write_tif(path: Path, values: list[list[float]], nodata: float | None = None) -> Path:
    array = np.array(values, dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(10.0, 45.02, 0.01, 0.01),
        nodata=nodata,
    ) as dst:
        dst.write(array, 1)
    return path


def _asset(task_id: str, path: Path) -> LocalAsset:
    return LocalAsset(
        task_id=task_id,
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        bytes=path.stat().st_size,
        content_type="image/tiff",
        http_status=200,
    )


def test_plan_covers_all_registered_depths_without_mixing(tmp_path: Path):
    plan = _plan(
        properties="clay",
        depths="0-5cm,5-15cm,15-30cm,30-60cm",
        statistics="mean",
    )
    assert len(plan.tasks) == 4
    provider = SoilGridsProvider()
    tif = _write_tif(tmp_path / "clay.tif", [[131, 132], [133, 134]])
    observations: list[TypedObservation] = []
    for task in plan.tasks:
        observation = provider.process(_asset(task.task_id, tif), task)[0]
        assert observation.depth_support.kind == "depth_interval"
        assert (
            observation.depth_support.min_m,
            observation.depth_support.max_m,
        ) == depth_interval_m(task.parameters["depth"])
        assert provider.validate([observation]).ok
        observations.append(observation)
    assert len({observation.observation_id for observation in observations}) == 4
    assert len({observation.feature_id for observation in observations}) == 4
    assert len(
        {
            (observation.depth_support.min_m, observation.depth_support.max_m)
            for observation in observations
        }
    ) == len(SOILGRIDS_DEPTHS)


def test_depth_and_statistic_never_collide_in_one_release():
    rows = [
        json.loads(line)
        for line in (DEPTH_RELEASE / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 300
    assert len({row["observation_id"] for row in rows}) == len(rows)
    assert len({row["feature_id"] for row in rows}) == 5 * len(SOILGRIDS_DEPTHS) * 3
    depth_counts = Counter(
        (row["depth_support"]["min_m"], row["depth_support"]["max_m"]) for row in rows
    )
    assert depth_counts == {
        (0.0, 0.05): 75,
        (0.05, 0.15): 75,
        (0.15, 0.3): 75,
        (0.3, 0.6): 75,
    }
    assert {row["state"] for row in rows} == {"detected"}
    assert {row["score_role"] for row in rows} == {"evidence_only"}
    assert {row["spatial_support"]["native_resolution_m"] for row in rows} == {250.0}

    requests = [
        json.loads(line)
        for line in (DEPTH_RELEASE / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(requests) == 300
    assert {request["status"] for request in requests} == {"succeeded"}
    assert {request["http_status"] for request in requests} <= {200, None}
    assert all(request["sha256"] for request in requests)
    assets = json.loads((DEPTH_RELEASE / "assets.json").read_text(encoding="utf-8"))
    checksums = {asset["asset_id"]: asset["checksum"] for asset in assets}
    for request in requests:
        asset_id = f"soilgrids-wcs:{request['area_id']}:{request['task_id']}"
        assert checksums[asset_id] == request["sha256"]


def test_physical_zero_is_a_value_and_nodata_is_no_coverage(tmp_path: Path):
    provider = SoilGridsProvider()
    task = next(
        task for task in _plan(properties="silt", depths="15-30cm", statistics="mean").tasks
    )
    zero_tif = _write_tif(tmp_path / "zero.tif", [[0, 0], [0, 0]])
    zero = provider.process(_asset(task.task_id, zero_tif), task)[0]
    assert zero.state == "detected"
    assert zero.value == 0.0
    assert zero.unit == "g kg-1"

    nodata_tif = _write_tif(tmp_path / "nodata.tif", [[-9999, -9999]], nodata=-9999)
    missing = provider.process(_asset(task.task_id, nodata_tif), task)[0]
    assert missing.state == "no_coverage"
    assert missing.value is None
    assert missing.spatial_support.valid_fraction == 0.0

    with pytest.raises(ValueError):
        TypedObservation.model_validate(
            {
                **missing.model_dump(mode="json"),
                "state": "no_coverage",
                "value": 0.0,
            }
        )


def test_retry_and_backoff_are_bounded_and_record_http_status(tmp_path: Path, monkeypatch):
    provider = SoilGridsProvider()
    task = _plan(properties="clay", depths="30-60cm", statistics="mean").tasks[0]
    tif = _write_tif(tmp_path / "ok.tif", [[1, 2]])
    payload = tif.read_bytes()
    calls = {"n": 0}
    delays: list[float] = []

    class _Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()
            return False

    def fake_urlopen(request, timeout=60):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(
                request.full_url, 429, "Throttled", {"Retry-After": "0"}, None
            )
        return _Response(payload)

    monkeypatch.setattr(
        "analog_pipelines.acquisition.providers_soilgrids.urllib.request.urlopen",
        fake_urlopen,
    )
    monkeypatch.setattr(
        "analog_pipelines.acquisition.providers_soilgrids.time.sleep",
        lambda seconds: delays.append(seconds),
    )
    asset = provider.acquire(task, tmp_path)
    assert calls["n"] == 3
    assert asset.http_status == 200
    assert asset.sha256 == hashlib.sha256(payload).hexdigest()
    assert SOILGRIDS_MIN_REQUEST_INTERVAL_S >= 0.0
    assert len(delays) >= 2


def test_moon_and_mars_regolith_are_explicit_no_coverage_without_tasks():
    provider = SparseRegolithProvider()
    areas = [
        PlanArea(
            area_id=f"{body.lower()}-test",
            body=body,
            bbox={"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0},
            crs="unknown",
            coordinate_frame=f"{body.lower()}_unknown",
        )
        for body in ("Moon", "Mars")
    ]
    plan = build_plan(
        scope="test",
        areas=areas,
        dimensions=["D06"],
        bodies=["Moon", "Mars"],
        provider_ids=["sparse-regolith-samples"],
    )
    assert plan.tasks == []
    assert any("no tasks" in note for note in plan.notes)
    for body in ("Moon", "Mars"):
        sources = provider.discover(SourceQuery(body=body, dimension="D06"))
        assert len(sources) == 1
        assert sources[0].body == body
        metadata = provider.inspect(sources[0])
        assert metadata.status == "blocked"
        assert metadata.blockers == [NO_SAMPLE_BLOCKERS[body]]
        assert "DEM" in sources[0].metadata["forbidden_derivations"][0]


def test_release_checksums_reject_tampered_observations(tmp_path: Path):
    release = tmp_path / "tampered-observations"
    shutil.copytree(DEPTH_RELEASE, release)
    lines = (release / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["value"] = 0.0
    lines[0] = json.dumps(payload, sort_keys=True)
    (release / "observations.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ReleaseIntegrityError, match="observations checksum mismatch"):
        verify_release_integrity(release)


def test_release_checksums_reject_tampered_declared_file(tmp_path: Path):
    release = tmp_path / "tampered-qa"
    shutil.copytree(DEPTH_RELEASE, release)
    qa = json.loads((release / "qa.json").read_text(encoding="utf-8"))
    qa["completeness"]["complete_groups"] = 0
    (release / "qa.json").write_text(
        json.dumps(qa, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(ReleaseIntegrityError, match="release file checksum mismatch"):
        verify_release_integrity(release)


def test_release_checksum_rejects_tampered_raw_asset(tmp_path: Path):
    plan = _plan(properties="clay", depths="0-5cm", statistics="mean")
    task = plan.tasks[0]
    provider = SoilGridsProvider()
    tif = _write_tif(tmp_path / "raw-source.tif", [[131, 132]])
    asset = _asset(task.task_id, tif)
    observations = provider.process(asset, task)
    run_dir = tmp_path / "run"
    (run_dir / "observations").mkdir(parents=True)
    (run_dir / "checkpoints").mkdir(parents=True)
    write_json(run_dir / "plan.json", plan.model_dump(mode="json"))
    write_json(
        run_dir / "checkpoints" / f"{task.task_id}.json",
        TaskCheckpoint(
            task_id=task.task_id,
            fingerprint=task.fingerprint(),
            status="succeeded",
            asset=asset,
            observations_path=str(run_dir / "observations" / f"{task.task_id}.jsonl"),
        ).model_dump(mode="json"),
    )
    (run_dir / "observations" / f"{task.task_id}.jsonl").write_text(
        "".join(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True) + "\n"
            for observation in observations
        ),
        encoding="utf-8",
    )
    release = tmp_path / "release"
    build_release(
        plan_path=run_dir / "plan.json",
        run_dir=run_dir,
        output_dir=release,
        include_raw=True,
    )
    assert verify_release_integrity(release)["verified_raw"] == 1
    raw_file = next((release / "raw").glob("*.tif"))
    raw_file.write_bytes(raw_file.read_bytes() + b"tamper")
    with pytest.raises(ReleaseIntegrityError, match="raw asset checksum mismatch"):
        verify_release_integrity(release)


def test_import_preserves_depths_and_upserts_overlapping_zero_to_five(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'd06.db'}"
    engine = make_engine(database_url)
    init_typed_db(engine)
    with Session(engine) as session:
        depth_result = import_release(session, DEPTH_RELEASE)
        session.commit()
        assert depth_result["observations"] == 300
        assert depth_result["integrity"]["observations"] == 300
        store = TypedObservationStore(session)
        rows = store.list_observations(dimension="D06", body="Earth")
        assert len(rows) == 300
        assert Counter(
            (row["depth_support"]["min_m"], row["depth_support"]["max_m"]) for row in rows
        ) == {
            (0.0, 0.05): 75,
            (0.05, 0.15): 75,
            (0.15, 0.3): 75,
            (0.3, 0.6): 75,
        }
        # The earlier 0-5 cm pilot uses the same observation IDs, so importing it must
        # upsert the existing rows instead of doubling the count.
        base_result = import_release(session, BASE_RELEASE)
        session.commit()
        assert base_result["observations"] == 75
        assert len(store.list_observations(dimension="D06", body="Earth")) == 300
