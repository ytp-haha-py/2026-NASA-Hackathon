"""Issue #160: real D01 + Earth D06 weighted search and legal query roles.

The fixture seeds the committed Earth manifests and typed-v2 pilots exactly like
``ANALOG_SEED_INCLUDE_TYPED=true uv run python -m analog_api.cli seed`` (the CLI uses the same
``seed_include_typed`` setting imported here). Nothing is synthesized in this module: every
target the tests send is read back from the seeded D01 store or the committed SoilGrids bundle.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]

REFERENCE_ID = "earth-wupatki-r3c3"
D01_FEATURE = "slope_mean_deg"
D06_FEATURE = "soil_clay_0_5cm_mean"
D06_DEEPER = "soil_clay_5_15cm_mean"
EARTH_POOL_SIZE = 113
DEEP_D06_GATED = 5


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("sixdim")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'sixdim.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _post(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/search/multidimensional", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _earth_reference(client: TestClient) -> dict[str, float | str]:
    """Read the reference vector back from the seeded index (no hand-written numbers)."""
    location = client.get(f"/api/v1/locations/{REFERENCE_ID}")
    assert location.status_code == 200, location.text
    d01 = {item["feature_id"]: item for item in location.json()["features"]}[D01_FEATURE]

    typed = client.get(
        "/api/v1/typed/observations",
        params={"area_id": REFERENCE_ID, "feature_id": D06_FEATURE},
    )
    assert typed.status_code == 200, typed.text
    observations = typed.json()["observations"]
    detected = [
        item
        for item in observations
        if item["state"] == "detected" and item["method_version"] == "soilgrids-wcs-2.0.0"
    ]
    assert len(detected) == 1, observations
    return {
        D01_FEATURE: d01["value"],
        D06_FEATURE: detected[0]["value"],
        "_d06_observation_id": detected[0]["observation_id"],
    }


def _find_result(client: TestClient, payload: dict, location_id: str) -> dict | None:
    """Locate one candidate across pages without assuming its rank."""
    for offset in (0, 50, 100):
        body = _post(client, {**payload, "limit": 50, "offset": offset})
        for row in body["results"]:
            if row["location_id"] == location_id:
                return row
        if body["next_offset"] is None:
            break
    return None


def _contributions(row: dict) -> dict[str, dict]:
    return {item["feature_id"]: item for item in row["contributions"]}


def _both_dimensions(weight_d01: float = 1.0, weight_d06: float = 1.0) -> dict:
    return {
        "D01": {"weight": weight_d01, "enabled": True},
        "D06": {"weight": weight_d06, "enabled": True},
    }


# ---------------------------------------------------------------------------
# 1. D01-only, D06-only and combined over one real Earth reference vector.
# ---------------------------------------------------------------------------


def test_earth_reference_d01_only_d06_only_and_combined(client: TestClient):
    reference = _earth_reference(client)
    targets = {
        D01_FEATURE: {"value": reference[D01_FEATURE], "weight": 1.0},
        D06_FEATURE: {"value": reference[D06_FEATURE], "weight": 1.0},
    }

    d01_only = _post(
        client,
        {
            "targets": targets,
            "dimension_weights": {
                "D01": {"weight": 1.0, "enabled": True},
                "D06": {"weight": 0.0, "enabled": False},
            },
            "limit": 5,
            "include_evidence": False,
        },
    )
    assert d01_only["scoring"]["version"] == "dimension-weighted-rms-v1"
    assert d01_only["scoring"]["active_dimensions"] == ["D01"]
    assert d01_only["scoring"]["dimension_controls"]["D01"]["active"] is True
    assert d01_only["scoring"]["dimension_controls"]["D06"] == {
        "weight": 0.0,
        "enabled": False,
        "active": False,
    }
    # The disabled D06 target is still declared, but never compared.
    assert set(d01_only["requested_features"]) == {D01_FEATURE, D06_FEATURE}
    assert d01_only["eligible_candidate_count"] == EARTH_POOL_SIZE
    assert d01_only["excluded_candidate_count"] == 0
    assert all(row["valid_features"] == [D01_FEATURE] for row in d01_only["results"])
    assert all(set(row["dimension_contributions"]) == {"D01"} for row in d01_only["results"])
    assert d01_only["coverage"]["features"][D01_FEATURE]["comparison"] == "active"
    assert (
        d01_only["coverage"]["features"][D06_FEATURE]["comparison"]
        == "dimension_disabled_not_compared"
    )
    leader = d01_only["results"][0]
    assert leader["location_id"] == REFERENCE_ID
    assert leader["distance"] == pytest.approx(0.0)
    assert _contributions(leader)[D01_FEATURE]["candidate"] == reference[D01_FEATURE]
    assert _contributions(leader)[D01_FEATURE]["observation_id"].startswith("d01:")

    d06_only = _post(
        client,
        {
            "targets": targets,
            "dimension_weights": {
                "D01": {"weight": 0.0, "enabled": False},
                "D06": {"weight": 1.0, "enabled": True},
            },
            "limit": 5,
            "include_evidence": False,
        },
    )
    assert d06_only["scoring"]["active_dimensions"] == ["D06"]
    assert d06_only["scoring"]["dimension_controls"]["D01"]["active"] is False
    assert d06_only["eligible_candidate_count"] == EARTH_POOL_SIZE
    assert d06_only["excluded_candidate_count"] == 0
    assert all(row["valid_features"] == [D06_FEATURE] for row in d06_only["results"])
    assert all(set(row["dimension_contributions"]) == {"D06"} for row in d06_only["results"])
    leader = d06_only["results"][0]
    assert leader["location_id"] == REFERENCE_ID
    assert leader["distance"] == pytest.approx(0.0)
    clay = _contributions(leader)[D06_FEATURE]
    assert clay["candidate"] == reference[D06_FEATURE]
    assert clay["observation_id"] == reference["_d06_observation_id"]
    assert clay["support"]["native_resolution_m"] == 250.0

    combined = _post(
        client,
        {
            "targets": targets,
            "dimension_weights": _both_dimensions(),
            "limit": 5,
            "include_evidence": False,
        },
    )
    assert combined["scoring"]["active_dimensions"] == ["D01", "D06"]
    assert combined["eligible_candidate_count"] == EARTH_POOL_SIZE
    leader = combined["results"][0]
    assert leader["location_id"] == REFERENCE_ID
    assert leader["distance"] == pytest.approx(0.0)
    assert set(leader["valid_features"]) == {D01_FEATURE, D06_FEATURE}
    assert set(leader["dimension_contributions"]) == {"D01", "D06"}
    assert leader["dimension_contributions"]["D01"]["dimension_weight"] == 1.0
    assert leader["dimension_contributions"]["D06"]["dimension_weight"] == 1.0
    assert leader["dimension_contributions"]["D01"][
        "mean_squared_normalized_delta"
    ] == pytest.approx(0.0)
    assert leader["dimension_contributions"]["D06"][
        "mean_squared_normalized_delta"
    ] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2. Feature weights and dimension weights are applied as documented.
# ---------------------------------------------------------------------------


def test_feature_and_dimension_weights_are_respected(client: TestClient):
    # Two D06 features from the same SoilGrids texture family make the feature weight observable:
    # with one feature per dimension the feature weight cancels inside q_d.
    targets = {
        D01_FEATURE: {"value": 10.0, "weight": 1.0},
        D06_FEATURE: {"value": 200.0, "weight": 1.0},
        "soil_sand_0_5cm_mean": {"value": 300.0, "weight": 1.0},
    }
    base_payload = {
        "targets": targets,
        "dimension_weights": _both_dimensions(),
        "include_evidence": False,
    }
    base = _find_result(client, base_payload, "earth-lanzarote")
    assert base is not None
    base_contrib = _contributions(base)
    z_clay = base_contrib[D06_FEATURE]["normalized_delta"]
    z_sand = base_contrib["soil_sand_0_5cm_mean"]["normalized_delta"]
    q_d01 = base["dimension_contributions"]["D01"]["mean_squared_normalized_delta"]
    q_d06 = base["dimension_contributions"]["D06"]["mean_squared_normalized_delta"]
    assert q_d01 == pytest.approx(base_contrib[D01_FEATURE]["normalized_delta"] ** 2)
    assert q_d06 == pytest.approx((z_clay**2 + z_sand**2) / 2.0)
    assert base["distance"] == pytest.approx(math.sqrt((q_d01 + q_d06) / 2.0))
    fractions = sum(item["contribution_fraction"] for item in base["contributions"])
    assert fractions == pytest.approx(1.0)
    assert sum(
        item["contribution"] for item in base["dimension_contributions"].values()
    ) == pytest.approx(base["distance"] ** 2 * 2.0)

    # Raise only the clay feature weight: q_D06 and the distance must follow the documented formula.
    heavier_clay = _find_result(
        client,
        {
            **base_payload,
            "targets": {
                **targets,
                D06_FEATURE: {"value": 200.0, "weight": 4.0},
            },
        },
        "earth-lanzarote",
    )
    assert heavier_clay is not None
    heavy_contrib = _contributions(heavier_clay)
    q_d06_heavy = heavier_clay["dimension_contributions"]["D06"]["mean_squared_normalized_delta"]
    assert q_d06_heavy == pytest.approx((4.0 * z_clay**2 + z_sand**2) / 5.0)
    assert q_d06_heavy != pytest.approx(q_d06)
    assert heavier_clay["distance"] == pytest.approx(math.sqrt((q_d01 + q_d06_heavy) / 2.0))
    assert heavier_clay["distance"] != pytest.approx(base["distance"])
    assert heavy_contrib[D06_FEATURE]["weight"] == 4.0
    assert heavier_clay["dimension_contributions"]["D06"]["contribution"] == pytest.approx(
        q_d06_heavy
    )

    # Raise only the D06 dimension weight: within-dimension q_D06 is unchanged, cross-dimension
    # aggregation changes.
    dim_heavy = _find_result(
        client,
        {
            **base_payload,
            "dimension_weights": _both_dimensions(weight_d06=3.0),
        },
        "earth-lanzarote",
    )
    assert dim_heavy is not None
    assert dim_heavy["dimension_contributions"]["D06"]["dimension_weight"] == 3.0
    q_d06_dim = dim_heavy["dimension_contributions"]["D06"]["mean_squared_normalized_delta"]
    assert q_d06_dim == pytest.approx(q_d06)
    assert dim_heavy["distance"] == pytest.approx(math.sqrt((q_d01 + 3.0 * q_d06) / 4.0))
    assert dim_heavy["distance"] != pytest.approx(base["distance"])

    # Zero feature weight: not compared, not excluded, and never silently scored as zero.
    zero_clay = _post(
        client,
        {
            "targets": {
                **targets,
                D06_FEATURE: {"value": 200.0, "weight": 0.0},
            },
            "dimension_weights": _both_dimensions(),
            "limit": 10,
            "include_evidence": False,
        },
    )
    assert zero_clay["eligible_candidate_count"] == EARTH_POOL_SIZE
    assert zero_clay["excluded_candidate_count"] == 0
    coverage = zero_clay["coverage"]["features"][D06_FEATURE]
    assert coverage["comparison"] == "zero_weight_not_compared"
    assert coverage["weight"] == 0.0
    assert all(D06_FEATURE not in row["valid_features"] for row in zero_clay["results"])
    assert all("soil_sand_0_5cm_mean" in row["valid_features"] for row in zero_clay["results"])


def test_dimension_rms_per_feature_contributions_decompose_the_dimension(client: TestClient):
    targets = {
        D01_FEATURE: {"value": 10.0, "weight": 1.0},
        D06_FEATURE: {"value": 200.0, "weight": 1.0},
        "soil_sand_0_5cm_mean": {"value": 300.0, "weight": 3.0},
    }
    row = _find_result(
        client,
        {
            "targets": targets,
            "dimension_weights": _both_dimensions(),
            "include_evidence": False,
        },
        "earth-lanzarote",
    )
    assert row is not None
    # The documented decomposition is: sum of feature contributions in d == dimension contribution.
    feature_sums = {"D01": 0.0, "D06": 0.0}
    for item in row["contributions"]:
        feature_sums[item["dimension_id"]] += item["contribution"]
    for dimension_id in ("D01", "D06"):
        assert feature_sums[dimension_id] == pytest.approx(
            row["dimension_contributions"][dimension_id]["contribution"]
        )
        assert feature_sums[dimension_id] == pytest.approx(
            row["dimension_contributions"][dimension_id]["dimension_weight"]
            * row["dimension_contributions"][dimension_id]["mean_squared_normalized_delta"]
        )


# ---------------------------------------------------------------------------
# 3. Changing the D06 dimension weight changes the actual top ranking.
# ---------------------------------------------------------------------------


def test_d06_dimension_weight_changes_the_top_ranking(client: TestClient):
    reference = _earth_reference(client)
    targets = {
        D01_FEATURE: {"value": reference[D01_FEATURE], "weight": 1.0},
        D06_FEATURE: {"value": reference[D06_FEATURE], "weight": 1.0},
    }
    # The reference window is excluded by a legal hard filter on its own D06 value range, so the
    # leader is decided by how the caller weights D01 against D06 instead of by self-distance 0.
    payload = {
        "targets": targets,
        "filters": {D06_FEATURE: {"min": 105.0}},
        "include_evidence": False,
        "limit": 5,
    }

    d06_light = _post(
        client,
        {**payload, "dimension_weights": _both_dimensions(weight_d06=0.05)},
    )
    d06_heavy = _post(
        client,
        {**payload, "dimension_weights": _both_dimensions(weight_d06=5.0)},
    )

    assert d06_light["filtered_candidate_count"] == 93
    assert d06_heavy["filtered_candidate_count"] == 93
    assert d06_light["eligible_candidate_count"] == 93
    assert d06_heavy["eligible_candidate_count"] == 93
    # The hard filter is enforced before ranking; those 20 rows are reported as excluded too.
    assert d06_light["excluded_candidate_count"] == EARTH_POOL_SIZE - 93
    assert d06_heavy["excluded_candidate_count"] == EARTH_POOL_SIZE - 93
    assert all(row["reasons"] == ["filter:" + D06_FEATURE] for row in d06_light["excluded"])
    assert d06_light["filters_applied"][D06_FEATURE]["min"] == 105.0

    light_leader = d06_light["results"][0]
    heavy_leader = d06_heavy["results"][0]
    assert light_leader["location_id"] == "earth-wupatki-r2c2"
    assert heavy_leader["location_id"] == "earth-iceland-r2c1"
    assert light_leader["location_id"] != heavy_leader["location_id"]
    assert light_leader["distance"] == pytest.approx(0.03296988475215127)
    assert heavy_leader["distance"] == pytest.approx(0.0495413503474468)

    # The flip is a real D01/D06 trade-off, not a tie-break artifact: the light-D06 leader is the
    # better D01 match, the heavy-D06 leader is the better D06 match.
    light_dims = light_leader["dimension_contributions"]
    heavy_dims = heavy_leader["dimension_contributions"]
    assert (
        light_dims["D01"]["mean_squared_normalized_delta"]
        < heavy_dims["D01"]["mean_squared_normalized_delta"]
    )
    assert (
        heavy_dims["D06"]["mean_squared_normalized_delta"]
        < light_dims["D06"]["mean_squared_normalized_delta"]
    )

    # Recompute both documented formulas from the returned contributions.
    for body, weight_d06 in ((d06_light, 0.05), (d06_heavy, 5.0)):
        leader = body["results"][0]
        q01 = leader["dimension_contributions"]["D01"]["mean_squared_normalized_delta"]
        q06 = leader["dimension_contributions"]["D06"]["mean_squared_normalized_delta"]
        assert leader["distance"] == pytest.approx(
            math.sqrt((1.0 * q01 + weight_d06 * q06) / (1.0 + weight_d06))
        )

    # With the reference window retained it still leads (self-distance 0), but the runner-up
    # order changes with the same D06 weight change.
    unfiltered = {**payload}
    unfiltered.pop("filters")
    light_unfiltered = _post(
        client,
        {**unfiltered, "dimension_weights": _both_dimensions(weight_d06=0.05)},
    )
    heavy_unfiltered = _post(
        client,
        {**unfiltered, "dimension_weights": _both_dimensions(weight_d06=5.0)},
    )
    assert light_unfiltered["results"][0]["location_id"] == REFERENCE_ID
    assert heavy_unfiltered["results"][0]["location_id"] == REFERENCE_ID
    assert light_unfiltered["results"][1]["location_id"] == "earth-wupatki-r2c2"
    assert heavy_unfiltered["results"][1]["location_id"] == "earth-iceland-r2c1"


# ---------------------------------------------------------------------------
# 4. Missing/incompatible values are excluded or not compared; never zero-filled.
# ---------------------------------------------------------------------------


def test_missing_and_incompatible_features_are_not_zero_scored(client: TestClient):
    # Only 5 of 113 Earth candidates carry the 5-15 cm SoilGrids depth.
    positive = _post(
        client,
        {
            "targets": {
                D01_FEATURE: {"value": 10.0, "weight": 1.0},
                D06_DEEPER: {"value": 200.0, "weight": 1.0},
            },
            "limit": 10,
            "include_evidence": False,
        },
    )
    assert positive["eligible_candidate_count"] == DEEP_D06_GATED
    assert positive["excluded_candidate_count"] == EARTH_POOL_SIZE - DEEP_D06_GATED
    assert (
        positive["eligible_candidate_count"] + positive["excluded_candidate_count"]
        == positive["candidate_pool_size"]
        == EARTH_POOL_SIZE
    )
    assert all(
        D01_FEATURE in row["valid_features"] and D06_DEEPER in row["valid_features"]
        for row in positive["results"]
    )
    coverage = positive["coverage"]["features"][D06_DEEPER]
    assert coverage["eligible_candidate_count"] == DEEP_D06_GATED
    assert coverage["comparison"] == "active"
    assert coverage["scored_result_count"] == DEEP_D06_GATED

    alps = next(row for row in positive["excluded"] if row["location_id"] == "earth-alps-r1c1")
    assert alps["missing_features"] == [D06_DEEPER]
    assert alps["incompatible_features"] == []
    assert "missing_feature:" + D06_DEEPER in alps["reasons"]
    # Every missing exclusion names the absent feature; no candidate is scored with a zero delta.
    missing_rows = [row for row in positive["excluded"] if row["missing_features"]]
    assert len(missing_rows) == EARTH_POOL_SIZE - DEEP_D06_GATED
    assert all(D06_DEEPER in row["missing_features"] for row in missing_rows)

    # Zero weight instead of a fabricated zero: the absence is not compared and cannot exclude.
    zero_weight = _post(
        client,
        {
            "targets": {
                D01_FEATURE: {"value": 10.0, "weight": 1.0},
                D06_DEEPER: {"value": 200.0, "weight": 0.0},
            },
            "limit": 10,
            "include_evidence": False,
        },
    )
    assert zero_weight["eligible_candidate_count"] == EARTH_POOL_SIZE
    assert zero_weight["excluded_candidate_count"] == 0
    assert zero_weight["coverage"]["features"][D06_DEEPER]["comparison"] == (
        "zero_weight_not_compared"
    )
    assert all(D06_DEEPER not in row["valid_features"] for row in zero_weight["results"])
    slope_only = _post(
        client,
        {
            "targets": {D01_FEATURE: {"value": 10.0, "weight": 1.0}},
            "limit": 10,
            "include_evidence": False,
        },
    )
    assert [row["distance"] for row in zero_weight["results"]] == [
        row["distance"] for row in slope_only["results"]
    ]

    # Cross-body D06 is rejected before ranking; it is never scored as zero or borrowed.
    moon = client.post(
        "/api/v1/search/multidimensional",
        json={
            "reference_id": "moon-lola-site01-roi1",
            "targets": {D06_FEATURE: {"value": 200.0, "weight": 1.0}},
        },
    )
    assert moon.status_code == 422, moon.text
    error = moon.json()["error"]
    assert error["code"] == "body_out_of_scope"
    assert error["details"][0]["reason"] == "cross_body_incompatible"
    assert error["details"][0]["body_scope"] == ["Earth"]
    assert "results" not in moon.json()


# ---------------------------------------------------------------------------
# 5. D02-D05 are evidence/context/uncertainty, never similarity or filters.
# ---------------------------------------------------------------------------


def test_d02_to_d05_roles_are_evidence_only_and_never_score(client: TestClient):
    registry_payload = client.get("/api/v1/search/features")
    assert registry_payload.status_code == 200, registry_payload.text
    features = {item["feature_id"]: item for item in registry_payload.json()["features"]}
    targeted_dimensions = {"D02", "D03", "D04", "D05"}
    typed = [item for item in features.values() if item["dimension_id"] in targeted_dimensions]
    assert {item["dimension_id"] for item in typed} == targeted_dimensions
    assert len(typed) == 15 + 6 + 8 + 4
    for item in typed:
        feature_id = item["feature_id"]
        assert item["roles"]["similarity"]["supported"] is False, feature_id
        assert item["roles"]["hard_filter"]["supported"] is False, feature_id
        assert item["roles"]["similarity"]["reason"], feature_id
        assert item["unsupported_reason"], feature_id
        legal = [role for role, support in item["roles"].items() if support["supported"]]
        assert legal, feature_id
        assert "similarity" not in legal
        assert "hard_filter" not in legal
        # Evidence-only features must declare one of the non-similarity roles explicitly.
        assert set(legal) & {"evidence", "uncertainty", "context"}, feature_id

    representatives = {
        "D02": "geologic_unit",
        "D03": "surface_brightness_temperature_k",
        "D04": "water_ice_detection",
        "D05": "solar_elevation_deg",
    }
    for dimension_id, feature_id in representatives.items():
        rejected = client.post(
            "/api/v1/search/multidimensional",
            json={"targets": {feature_id: {"value": 1.0, "weight": 1.0}}},
        )
        assert rejected.status_code == 422, (dimension_id, rejected.text)
        error = rejected.json()["error"]
        assert error["code"] == "feature_role_not_scoreable"
        detail = error["details"][0]
        assert detail["feature_id"] == feature_id
        assert detail["dimension_id"] == dimension_id
        assert "similarity" not in detail["legal_roles"]
        assert set(detail["legal_roles"]) & {"evidence", "uncertainty", "context"}

        not_filterable = client.post(
            "/api/v1/search/multidimensional",
            json={
                "targets": {D01_FEATURE: {"value": 10.0, "weight": 1.0}},
                "filters": {feature_id: {"min": 0.0}},
            },
        )
        assert not_filterable.status_code == 422, not_filterable.text
        assert not_filterable.json()["error"]["code"] == "feature_role_not_filterable"

    # SoilGrids prediction quantiles are uncertainty evidence, not filters or scores.
    quantile = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {D01_FEATURE: {"value": 10.0, "weight": 1.0}},
            "filters": {"soil_clay_0_5cm_q05": {"min": 0.0}},
        },
    )
    assert quantile.status_code == 422, quantile.text
    assert quantile.json()["error"]["code"] == "feature_role_not_filterable"


def test_evidence_records_are_annotations_not_scores(client: TestClient):
    # earth-lanzarote carries committed D02/D03/D04/D05 evidence alongside D01/D06.
    location = client.get("/api/v1/locations/earth-lanzarote")
    assert location.status_code == 200, location.text
    slope = {item["feature_id"]: item["value"] for item in location.json()["features"]}[D01_FEATURE]
    typed = client.get(
        "/api/v1/typed/observations",
        params={"area_id": "earth-lanzarote", "feature_id": D06_FEATURE},
    ).json()["observations"]
    clay = next(item["value"] for item in typed if item["state"] == "detected")

    payload = {
        "targets": {
            D01_FEATURE: {"value": slope, "weight": 1.0},
            D06_FEATURE: {"value": clay, "weight": 1.0},
        },
        "dimension_weights": _both_dimensions(),
        "limit": 3,
        "evidence_limit": 50,
    }
    with_evidence = _post(client, {**payload, "include_evidence": True})
    without_evidence = _post(client, {**payload, "include_evidence": False})
    winner = with_evidence["results"][0]
    assert winner["location_id"] == "earth-lanzarote"
    assert winner["distance"] == without_evidence["results"][0]["distance"]
    assert winner["distance"] == pytest.approx(0.0)

    records = winner["evidence_records"]
    assert records
    evidence_only = [
        record for record in records if record["dimension_id"] in {"D02", "D03", "D04", "D05"}
    ]
    assert {record["dimension_id"] for record in evidence_only} >= {"D03", "D04"}
    for record in evidence_only:
        assert record["stored_score_role"] in {"evidence_only", "context_only"}
        assert "similarity" not in record["legal_roles"]
        assert "hard_filter" not in record["legal_roles"]
        assert record["feature_id"] not in winner["valid_features"]
        assert "Evidence/context record" in record["note"]
    # Evidence rows are annotations only: they never enter the weighted distance.
    assert set(winner["valid_features"]) == {D01_FEATURE, D06_FEATURE}
    assert {item["feature_id"] for item in winner["contributions"]} == {
        D01_FEATURE,
        D06_FEATURE,
    }


# ---------------------------------------------------------------------------
# 6. Per-feature/per-dimension contributions and provenance are present.
# ---------------------------------------------------------------------------


def test_contributions_coverage_and_provenance(client: TestClient):
    reference = _earth_reference(client)
    body = _post(
        client,
        {
            "targets": {
                D01_FEATURE: {"value": reference[D01_FEATURE], "weight": 1.0},
                D06_FEATURE: {"value": reference[D06_FEATURE], "weight": 1.0},
            },
            "dimension_weights": _both_dimensions(),
            "limit": 5,
            "include_evidence": False,
        },
    )

    assert body["registry_version"] == "feature-eligibility-1.0.0"
    assert body["data_version"] == body["data_snapshot_id"]
    assert body["data_version"].startswith("dv-")
    assert body["candidate_body"] == "Earth"
    assert body["source_body"] == "Earth"
    assert body["cross_body_gate"]["reference_body"] is None
    assert "cross-body" in body["cross_body_gate"]["rule"]
    assert body["candidate_pool_size"] == EARTH_POOL_SIZE
    assert any("six-dimension scientific total" in note for note in body["limitations"])
    assert any("250 m" in note or "250 m model" in note for note in body["limitations"])
    assert "q_d = Σ(v_df * z_df^2) / Σ(v_df)" in body["scoring"]["formula"]
    assert "Feature-count invariant" in body["scoring"]["note"]

    targets = body["requested_targets"]
    assert targets[D01_FEATURE]["unit"] == "degree"
    assert targets[D01_FEATURE]["dimension_id"] == "D01"
    assert targets[D01_FEATURE]["body_scope"] == ["Earth", "Moon", "Mars"]
    assert targets[D06_FEATURE]["unit"] == "g kg-1"
    assert targets[D06_FEATURE]["dimension_id"] == "D06"
    assert targets[D06_FEATURE]["body_scope"] == ["Earth"]
    assert targets[D06_FEATURE]["measurement_family"] == "d06:soilgrids:clay:0_5"

    coverage = body["coverage"]
    assert coverage["candidate_pool_size"] == EARTH_POOL_SIZE
    assert coverage["requested_feature_count"] == 2
    assert "global coverage" in coverage["scope_note"]
    for feature_id in (D01_FEATURE, D06_FEATURE):
        item = coverage["features"][feature_id]
        assert item["candidate_pool_size"] == EARTH_POOL_SIZE
        assert item["eligible_candidate_count"] == EARTH_POOL_SIZE
        assert item["scored_result_count"] == EARTH_POOL_SIZE
        assert item["selector"]
        assert item["support"]

    # Use a non-zero-distance match so every contribution field is exercised.
    row = body["results"][1]
    assert row["distance"] > 0.0
    by_feature = _contributions(row)
    assert set(by_feature) == {D01_FEATURE, D06_FEATURE}
    for item in by_feature.values():
        assert item["dimension_id"] in {"D01", "D06"}
        assert item["unit"] in {"degree", "g kg-1"}
        assert item["weight"] == 1.0
        assert item["scale"] > 0.0
        assert item["observation_id"]
        assert item["selector"]
        assert item["support"]
        assert item["contribution"] >= 0.0
    assert by_feature[D01_FEATURE]["observation_id"].startswith("d01:")
    assert by_feature[D01_FEATURE]["selector"]["analysis_resolution_m"] == 90
    clay = by_feature[D06_FEATURE]
    assert clay["observation_id"].startswith("soilgrids:")
    assert clay["selector"]["depth_interval_m"] == [0.0, 0.05]
    assert clay["selector"]["statistic"] == "mean"
    assert clay["support"]["native_resolution_m"] == 250.0
    assert clay["uncertainty"]["kind"] == "prediction_quantile"
    assert clay["uncertainty"]["q05"] is not None
    assert clay["uncertainty"]["q95"] is not None

    dims = row["dimension_contributions"]
    assert set(dims) == {"D01", "D06"}
    for item in dims.values():
        assert item["dimension_weight"] == 1.0
        assert item["feature_weight_sum"] == 1.0
        assert item["feature_count"] == 1
        assert item["mean_squared_normalized_delta"] >= 0.0
        assert item["distance"] == pytest.approx(math.sqrt(item["mean_squared_normalized_delta"]))
        assert item["contribution"] == pytest.approx(
            item["dimension_weight"] * item["mean_squared_normalized_delta"]
        )
    assert row["distance"] == pytest.approx(
        math.sqrt(sum(item["contribution"] for item in dims.values()) / 2.0)
    )
    assert [item["rank"] for item in body["results"]] == list(range(1, len(body["results"]) + 1))
    assert [item["distance"] for item in body["results"]] == sorted(
        item["distance"] for item in body["results"]
    )


def test_registry_exposes_exactly_25_scoreable_features(client: TestClient):
    body = client.get("/api/v1/search/features")
    assert body.status_code == 200, body.text
    payload = body.json()
    summary = payload["audit_summary"]
    assert summary["registered_feature_count"] == 104
    assert summary["similarity_feature_count"] == 25
    assert summary["by_dimension"]["D01"]["similarity"] == 5
    assert summary["by_dimension"]["D06"]["similarity"] == 20
    for dimension_id in ("D02", "D03", "D04", "D05"):
        assert summary["by_dimension"][dimension_id]["similarity"] == 0
        assert summary["by_dimension"][dimension_id]["hard_filter"] == 0
        assert (
            summary["by_dimension"][dimension_id]["unsupported_for_scoring"]
            == summary["by_dimension"][dimension_id]["features"]
        )
    scoreable = [item for item in payload["features"] if item["roles"]["similarity"]["supported"]]
    assert {item["dimension_id"] for item in scoreable} == {"D01", "D06"}
    assert payload["join_audit"]["proximity_join_used"] is False
    assert payload["join_audit"]["typed_areas_exactly_matching_fine_candidate"] == (EARTH_POOL_SIZE)
