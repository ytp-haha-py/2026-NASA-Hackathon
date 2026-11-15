"""Explicit AOI limits used by the engine job route (Issue #170, Goal D).

The canonical geometry contract in :mod:`analog_api.domain.planetary_aoi_geometry`
has conservative body defaults (25 km side / 400 km2 for Moon/Mars, 10 km / 25 km2
for Earth). The Issue #132 providers have tighter, already-tested caps. The engine
route must not validate with the geometry defaults and then fail inside the provider
with a different message, so it passes these explicit ``AoiLimits``:

- Earth: 600 m minimum side, 2,000 m maximum side, 4 km2 maximum area (``domain/aoi.py``).
- Moon/Mars: 1,000 m minimum side, 12,000 m maximum side, 144 km2 maximum area
  (``pipelines/src/analog_pipelines/planetary_aoi.py``).

Earth is included for the shared provider/limit contract even though the
``/api/v1/planetary/aoi`` request model only accepts Moon/Mars. Keeping the table
here makes the two code paths testable and prevents silent drift.
"""

from __future__ import annotations

from analog_api.domain.features import (
    ANALYSIS_RESOLUTION_M as EARTH_ANALYSIS_RESOLUTION_M,
)
from analog_api.domain.features import (
    AOI_MAX_AREA_KM2,
    AOI_MAX_SIDE_M,
    AOI_MIN_SIDE_M,
)
from analog_api.domain.planetary_aoi_geometry import (
    AoiGeometry,
    AoiLimits,
    validate_aoi_geometry,
)

PLANETARY_ANALYSIS_RESOLUTION_M = 250.0
# 12 km x 12 km is the maximum planar area admitted by the #132 side cap.
PLANETARY_MAX_AREA_KM2 = 144.0
MAX_PIXELS = 4_000_000

EARTH_PROVIDER_LIMITS = AoiLimits(
    min_side_m=AOI_MIN_SIDE_M,
    max_side_m=AOI_MAX_SIDE_M,
    max_area_km2=AOI_MAX_AREA_KM2,
    max_span_deg=180.0,
    max_pixels=MAX_PIXELS,
)
PLANETARY_PROVIDER_LIMITS = AoiLimits(
    min_side_m=1_000.0,
    max_side_m=12_000.0,
    max_area_km2=PLANETARY_MAX_AREA_KM2,
    max_span_deg=180.0,
    max_pixels=MAX_PIXELS,
)

BODY_PROVIDER_LIMITS = {
    "Earth": EARTH_PROVIDER_LIMITS,
    "Moon": PLANETARY_PROVIDER_LIMITS,
    "Mars": PLANETARY_PROVIDER_LIMITS,
}
BODY_PIXEL_SIZE_M = {
    "Earth": float(EARTH_ANALYSIS_RESOLUTION_M),
    "Moon": PLANETARY_ANALYSIS_RESOLUTION_M,
    "Mars": PLANETARY_ANALYSIS_RESOLUTION_M,
}


def provider_aoi_limits(body: str) -> AoiLimits:
    """Return the explicit safety caps for the configured provider of ``body``."""
    try:
        return BODY_PROVIDER_LIMITS[body]
    except KeyError as exc:
        raise KeyError(f"no provider AOI limits for body {body!r}") from exc


def provider_pixel_size_m(body: str) -> float:
    """Return the analysis pixel size used for the provider pixel budget."""
    try:
        return BODY_PIXEL_SIZE_M[body]
    except KeyError as exc:
        raise KeyError(f"no provider pixel size for body {body!r}") from exc


def validate_provider_geometry(
    body: str,
    bbox,
    *,
    lon_convention: str = "signed",
    latitude_kind: str | None = None,
) -> AoiGeometry:
    """Validate the full requested AOI against the provider's explicit caps."""
    return validate_aoi_geometry(
        body,
        bbox,
        lon_convention=lon_convention,
        latitude_kind=latitude_kind,
        limits=provider_aoi_limits(body),
        pixel_size_m=provider_pixel_size_m(body),
    )


__all__ = [
    "BODY_PIXEL_SIZE_M",
    "BODY_PROVIDER_LIMITS",
    "EARTH_PROVIDER_LIMITS",
    "PLANETARY_PROVIDER_LIMITS",
    "provider_aoi_limits",
    "provider_pixel_size_m",
    "validate_provider_geometry",
]
