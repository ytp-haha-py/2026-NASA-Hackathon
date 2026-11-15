"""Offline checks for the default global Moon/Mars D01 provider (#170)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from analog_api.adapters import planetary_global
from analog_api.adapters.planetary_aoi import PlanetaryAoiProvider
from analog_api.domain.aoi import ValidatedAoi
from analog_api.domain.entities import BBox
from analog_api.domain.errors import AppError


def _request(body: str = "Moon", features: tuple[str, ...] = ("slope_mean_deg",)) -> ValidatedAoi:
    return ValidatedAoi(
        body=body,
        bbox=BBox(west=20.0, south=-10.2, east=20.2, north=-10.0),
        geometry=None,
        area_km2=36.2,
        requested_features=features,
        reference_id=None,
        idempotency_key=None,
        area_id=f"planet-aoi-{body.lower()}-test",
        cache_key="test-cache-key",
        width_m=6000.0,
        height_m=6000.0,
    )


def test_feature_values_normalise_lola_flat_and_mola_records() -> None:
    assert planetary_global._feature_values(
        {"features": {"slope_mean_deg": 1.5, "flat_terrain_fraction": 0.0}}
    ) == {"slope_mean_deg": 1.5, "flat_terrain_fraction": 0.0}
    assert planetary_global._feature_values(
        {"features": {"slope_mean_deg": {"value": 2.5}, "roughness_std_m": {"value": None}}}
    ) == {"slope_mean_deg": 2.5}


def test_global_provider_dispatches_fine_d01_when_network_enabled(monkeypatch) -> None:
    called: dict[str, object] = {}
    sentinel = object()

    def fake_global(request: ValidatedAoi) -> object:
        called["request"] = request
        return sentinel

    monkeypatch.setattr(planetary_global, "analyze_global_planetary_aoi", fake_global)
    provider = PlanetaryAoiProvider(allow_network=True)
    assert provider.analyze(_request()) is sentinel
    assert called["request"].body == "Moon"


def test_global_no_data_is_explicit_not_zero_filled(monkeypatch) -> None:
    fake_module = SimpleNamespace(
        analyze_lola_aoi=lambda bbox: SimpleNamespace(
            to_payload=lambda: {"status": "no_data", "features": None, "warnings": ["all nodata"]}
        )
    )
    monkeypatch.setattr(planetary_global, "_module", lambda name: fake_module)
    with pytest.raises(AppError) as exc:
        planetary_global.analyze_global_planetary_aoi(_request())
    assert exc.value.code == "no_data"
    assert exc.value.status_code == 422


def test_global_source_failure_is_explicit_503(monkeypatch) -> None:
    def boom(bbox):
        raise OSError("network down")

    fake_module = SimpleNamespace(analyze_lola_aoi=boom)
    monkeypatch.setattr(planetary_global, "_module", lambda name: fake_module)
    with pytest.raises(AppError) as exc:
        planetary_global.analyze_global_planetary_aoi(_request())
    assert exc.value.code == "source_read_failed"
    assert exc.value.status_code == 503
