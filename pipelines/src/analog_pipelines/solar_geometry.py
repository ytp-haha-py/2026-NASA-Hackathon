"""Pure solar-geometry helpers and an optional NAIF SPICE provider (Issue #45).

The model computes the Sun incidence/elevation angle for a point on a body
ellipsoid in a documented body-fixed frame. It is **solar geometry only**:

- no observed irradiance or radiance,
- no shadow fraction or terrain horizon raster,
- no atmospheric/cloud correction,
- no ionizing radiation or dose.

The SPICE dependency is optional and imported lazily so offline tests can exercise
the pure helpers without installing ``spiceypy``. Kernels are never committed; the
builder downloads pinned public NAIF kernels into an external cache and verifies
their SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.request import urlopen

if TYPE_CHECKING:  # pragma: no cover - typing only
    from types import TracebackType

DEG = math.pi / 180.0
SPICE_METHOD_VERSION = "spice-solar-geometry-1.0.0"
SPICE_DATASET_ID = "naif-spice-generic-kernels"
NAIF_BASE = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels"

BODY_FRAME = {"Moon": "MOON_ME", "Mars": "IAU_MARS"}
BODY_NAIF_ID = {"Moon": "MOON", "Mars": "MARS"}
COMMON_KERNEL_FILENAMES = ("naif0012.tls", "pck00011.tpc", "de421.bsp")
BODY_KERNEL_FILENAMES = {
    "Moon": (*COMMON_KERNEL_FILENAMES, "moon_pa_de421_1900-2050.bpc", "moon_080317.tf"),
    "Mars": COMMON_KERNEL_FILENAMES,
}


class SolarGeometryError(RuntimeError):
    """Base error for the model/provider boundary."""


class KernelIntegrityError(SolarGeometryError):
    """A cached kernel is missing its pinned digest or has the wrong bytes."""


class SpiceUnavailable(SolarGeometryError):
    """The optional ``spiceypy`` dependency is not installed."""


@dataclass(frozen=True, slots=True)
class KernelSpec:
    """A pinned, public NAIF kernel used by the pilot."""

    filename: str
    url: str
    sha256: str
    bytes: int
    purpose: str

    @property
    def asset_id(self) -> str:
        return f"naif-kernel:{self.filename}"


# Pinned generic SPICE kernels. DE421 is used because the existing lunar LOLA
# reference products document a MOON_ME frame realized with DE421, and because it
# is materially smaller than DE440 while covering the pilot epoch (1900-2050).
KERNEL_SPECS: tuple[KernelSpec, ...] = (
    KernelSpec(
        filename="naif0012.tls",
        url=f"{NAIF_BASE}/lsk/naif0012.tls",
        sha256="678e32bdb5a744117a467cd9601cd6b373f0e9bc9bbde1371d5eee39600a039b",
        bytes=5257,
        purpose="Leap-second kernel for UTC -> ephemeris time conversion.",
    ),
    KernelSpec(
        filename="pck00011.tpc",
        url=f"{NAIF_BASE}/pck/pck00011.tpc",
        sha256="3dff7b1dbeceaa01f25467767d3fa25816051c85d162d1edf04acb310ee28bb1",
        bytes=131226,
        purpose="Text planetary constants: body radii and IAU_MARS orientation.",
    ),
    KernelSpec(
        filename="de421.bsp",
        url=f"{NAIF_BASE}/spk/planets/a_old_versions/de421.bsp",
        sha256="08b20db2ae22488650641c5a9033e5bfda4b1c4b440cfeaf20f621cfa18ecdb3",
        bytes=16790528,
        purpose="JPL DE421 planetary ephemeris (1900-2050), Sun/Moon/Mars states.",
    ),
    KernelSpec(
        filename="moon_pa_de421_1900-2050.bpc",
        url=f"{NAIF_BASE}/pck/moon_pa_de421_1900-2050.bpc",
        sha256="656f90616403d75a75f0cd6c8830fc5b44f8cb4facb5ccb8915e752b397520cf",
        bytes=1770496,
        purpose="DE421 lunar principal-axis orientation (binary PCK).",
    ),
    KernelSpec(
        filename="moon_080317.tf",
        url=f"{NAIF_BASE}/fk/satellites/moon_080317.tf",
        sha256="78732477b96f9863e7b0d65bcee3c22b8707ca5ed0db56d1173319cb2e8c7993",
        bytes=21437,
        purpose="Lunar frame definitions: MOON_ME / MOON_PA realized from DE421.",
    ),
)
KERNEL_BY_FILENAME = {spec.filename: spec for spec in KERNEL_SPECS}


@dataclass(frozen=True, slots=True)
class SolarGeometryResult:
    """Angles and Sun geometry for one point at one instant."""

    incidence_deg: float
    elevation_deg: float
    sun_distance_m: float
    subsolar_latitude_deg: float
    subsolar_longitude_deg: float
    local_solar_time_hours: float
    sun_direction: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class SpiceGeometryRecord:
    """A computed record plus the vectors/frame facts needed for provenance."""

    body: str
    frame: str
    epoch_utc: str
    et_seconds: float
    site_latitude_deg: float
    site_longitude_deg: float
    site_radius_m: float
    site_xyz_m: tuple[float, float, float]
    surface_normal: tuple[float, float, float]
    sun_xyz_m: tuple[float, float, float]
    geometry: SolarGeometryResult
    frame_validation: str
    spice_version: str


def _finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise SolarGeometryError(f"{name} must be finite")
    return float(value)


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(math.fsum(component * component for component in vector))


def _unit(vector: Sequence[float]) -> tuple[float, float, float]:
    length = _norm(vector)
    if length <= 0.0 or not math.isfinite(length):
        raise SolarGeometryError("cannot normalize a zero/non-finite vector")
    return (float(vector[0] / length), float(vector[1] / length), float(vector[2] / length))


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


def _cross(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return (
        float(left[1] * right[2] - left[2] * right[1]),
        float(left[2] * right[0] - left[0] * right[2]),
        float(left[0] * right[1] - left[1] * right[0]),
    )


def _subtract(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return (
        float(left[0] - right[0]),
        float(left[1] - right[1]),
        float(left[2] - right[2]),
    )


def ellipsoid_surface_radius_m(
    latitude_deg: float,
    equatorial_radius_m: float,
    polar_radius_m: float,
) -> float:
    """Planetocentric radius on a biaxial ellipsoid at a planetocentric latitude."""
    lat = _finite(latitude_deg, "latitude_deg") * DEG
    equatorial = _finite(equatorial_radius_m, "equatorial_radius_m")
    polar = _finite(polar_radius_m, "polar_radius_m")
    if equatorial <= 0.0 or polar <= 0.0:
        raise SolarGeometryError("body radii must be positive")
    denominator = math.sqrt(
        (math.cos(lat) ** 2) / (equatorial**2) + (math.sin(lat) ** 2) / (polar**2)
    )
    return 1.0 / denominator


def body_fixed_site_vector(
    latitude_deg: float,
    longitude_deg: float,
    equatorial_radius_m: float,
    polar_radius_m: float | None = None,
) -> tuple[float, float, float]:
    """Body-fixed Cartesian site vector from planetocentric lat/lon (east-positive)."""
    lat = _finite(latitude_deg, "latitude_deg") * DEG
    lon = _finite(longitude_deg, "longitude_deg") * DEG
    if not -90.0 <= latitude_deg <= 90.0:
        raise SolarGeometryError("latitude_deg must be within [-90, 90]")
    equatorial = _finite(equatorial_radius_m, "equatorial_radius_m")
    polar = _finite(
        polar_radius_m if polar_radius_m is not None else equatorial_radius_m,
        "polar_radius_m",
    )
    radius = ellipsoid_surface_radius_m(latitude_deg, equatorial, polar)
    return (
        radius * math.cos(lat) * math.cos(lon),
        radius * math.cos(lat) * math.sin(lon),
        radius * math.sin(lat),
    )


def ellipsoid_surface_normal(
    site_xyz_m: Sequence[float],
    equatorial_radius_m: float,
    polar_radius_m: float,
) -> tuple[float, float, float]:
    """Outward unit normal of a biaxial ellipsoid at a body-fixed point."""
    equatorial = _finite(equatorial_radius_m, "equatorial_radius_m")
    polar = _finite(polar_radius_m, "polar_radius_m")
    if equatorial <= 0.0 or polar <= 0.0:
        raise SolarGeometryError("body radii must be positive")
    gradient = (
        site_xyz_m[0] / (equatorial**2),
        site_xyz_m[1] / (equatorial**2),
        site_xyz_m[2] / (polar**2),
    )
    return _unit(gradient)


def local_solar_time_hours(site_longitude_deg: float, subsolar_longitude_deg: float) -> float:
    """Local solar time from east-positive body-fixed longitudes, wrapped to [0, 24)."""
    site = _finite(site_longitude_deg, "site_longitude_deg")
    subsolar = _finite(subsolar_longitude_deg, "subsolar_longitude_deg")
    delta = (site - subsolar + 180.0) % 360.0 - 180.0
    return (12.0 + delta / 15.0) % 24.0


def local_east_vector(
    longitude_deg: float,
    surface_normal: Sequence[float],
) -> tuple[float, float, float]:
    """Body-fixed east direction projected into the local tangent plane."""
    lon = _finite(longitude_deg, "longitude_deg") * DEG
    raw = (-math.sin(lon), math.cos(lon), 0.0)
    normal = _unit(surface_normal)
    projected = _subtract(raw, [component * _dot(raw, normal) for component in normal])
    if _norm(projected) < 1e-12:  # pragma: no cover - only for a degenerate normal
        projected = _cross((0.0, 0.0, 1.0), normal)
    return _unit(projected)


def local_north_vector(
    surface_normal: Sequence[float],
    east_vector: Sequence[float],
) -> tuple[float, float, float]:
    """Local north from a right-handed ENU frame: east x north = up."""
    return _unit(_cross(surface_normal, east_vector))


def compute_solar_geometry(
    *,
    site_xyz_m: Sequence[float],
    sun_xyz_m: Sequence[float],
    surface_normal: Sequence[float],
    site_longitude_deg: float,
) -> SolarGeometryResult:
    """Compute Sun incidence/elevation for a flat ellipsoid tangent plane.

    ``sun_xyz_m`` is the Sun position relative to the body centre in the same
    body-fixed frame as ``site_xyz_m``. Incidence is the angle between the local
    outward normal and the site-to-Sun vector; elevation is ``90 - incidence``.
    A negative elevation means the Sun is below the local tangent plane.
    """
    sun_from_center = tuple(_finite(value, "sun_xyz_m") for value in sun_xyz_m)
    if len(sun_from_center) != 3:
        raise SolarGeometryError("sun_xyz_m must have three components")
    site = tuple(_finite(value, "site_xyz_m") for value in site_xyz_m)
    if len(site) != 3:
        raise SolarGeometryError("site_xyz_m must have three components")
    normal = _unit(surface_normal)
    to_sun = _subtract(sun_from_center, site)
    distance = _norm(to_sun)
    sun_direction = _unit(to_sun)
    cosine = max(-1.0, min(1.0, _dot(sun_direction, normal)))
    incidence = math.degrees(math.acos(cosine))
    center_distance = _norm(sun_from_center)
    subsolar_latitude = math.degrees(math.asin(sun_from_center[2] / center_distance))
    subsolar_longitude = math.degrees(math.atan2(sun_from_center[1], sun_from_center[0]))
    return SolarGeometryResult(
        incidence_deg=incidence,
        elevation_deg=90.0 - incidence,
        sun_distance_m=distance,
        subsolar_latitude_deg=subsolar_latitude,
        subsolar_longitude_deg=subsolar_longitude,
        local_solar_time_hours=local_solar_time_hours(site_longitude_deg, subsolar_longitude),
        sun_direction=sun_direction,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_kernel(path: str | Path, spec: KernelSpec) -> Path:
    """Verify a cached kernel's size and pinned SHA-256; never accept silent drift."""
    resolved = Path(path)
    if not resolved.is_file():
        raise KernelIntegrityError(f"kernel not found: {resolved}")
    size = resolved.stat().st_size
    if size != spec.bytes:
        raise KernelIntegrityError(
            f"{spec.filename}: size {size} != pinned {spec.bytes}; refusing to use it"
        )
    digest = _sha256(resolved)
    if digest != spec.sha256:
        raise KernelIntegrityError(
            f"{spec.filename}: sha256 {digest} != pinned {spec.sha256}; "
            "refusing to use an unpinned kernel"
        )
    return resolved


def ensure_kernel(spec: KernelSpec, cache_dir: str | Path, *, download: bool = False) -> Path:
    """Return a verified cached kernel, optionally downloading the pinned bytes."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / spec.filename
    if destination.is_file():
        return verify_kernel(destination, spec)
    if not download:
        raise KernelIntegrityError(
            f"{spec.filename} is missing from {cache}; re-run with --download to fetch "
            "the pinned public NAIF kernel"
        )
    partial = destination.with_name(destination.name + ".part")
    with urlopen(spec.url, timeout=120) as response, partial.open("wb") as handle:
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
    try:
        verify_kernel(partial, spec)
    except KernelIntegrityError:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(destination)
    return destination


def ensure_kernels(cache_dir: str | Path, *, download: bool = False) -> dict[str, Path]:
    """Ensure every pinned kernel is present and verified."""
    return {
        spec.filename: ensure_kernel(spec, cache_dir, download=download) for spec in KERNEL_SPECS
    }


def spice_available() -> bool:
    """Whether the optional SPICE dependency can be imported."""
    return importlib.util.find_spec("spiceypy") is not None


def _import_spice() -> Any:
    try:
        import spiceypy  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SpiceUnavailable(
            "spiceypy is not installed; run with `uv run --extra spice` or "
            "`uv run --with 'spiceypy>=6,<7'`"
        ) from exc
    return spiceypy


def spice_epoch_text(epoch_utc: str) -> str:
    """Convert a documented ISO-8601 UTC instant to a SPICE-parsable UTC string."""
    text = epoch_utc.strip()
    if not text:
        raise SolarGeometryError("epoch_utc must not be empty")
    if text.endswith("Z"):
        text = text[:-1] + " UTC"
    if "T" in text:
        text = text.replace("T", " ", 1)
    return text


class SpiceSolarGeometryProvider:
    """Thin, explicitly scoped wrapper around NAIF SPICE geometry calls."""

    def __init__(self, kernels: Mapping[str, str | Path]) -> None:
        missing = [filename for filename in KERNEL_BY_FILENAME if filename not in kernels]
        if missing:
            raise SolarGeometryError(f"missing required kernels: {missing}")
        self._kernels = {filename: Path(path) for filename, path in kernels.items()}
        self._spice: Any | None = None
        self._loaded = False

    def __enter__(self) -> SpiceSolarGeometryProvider:
        spice = _import_spice()
        for spec in KERNEL_SPECS:
            spice.furnsh(str(self._kernels[spec.filename]))
        self._spice = spice
        self._loaded = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._loaded and self._spice is not None:
            self._spice.kclear()
        self._loaded = False
        self._spice = None

    def _require_spice(self) -> Any:
        if self._spice is None:
            raise SolarGeometryError("provider must be used as a context manager")
        return self._spice

    def frame_id(self, frame: str) -> int | None:
        spice = self._require_spice()
        try:
            return int(spice.namfrm(frame))
        except Exception:  # noqa: BLE001 - metadata only; never fabricate an ID
            return None

    def validate_frame(self, frame: str, et_seconds: float) -> str:
        """Validate a frame by rotating it to J2000; raise instead of guessing."""
        spice = self._require_spice()
        try:
            spice.pxform(frame, "J2000", et_seconds)
        except Exception as exc:  # noqa: BLE001 - SPICE raises toolkit exceptions
            raise SolarGeometryError(
                f"frame {frame!r} is not available at the requested epoch: {exc}"
            ) from exc
        return f"pxform({frame!r}, 'J2000') succeeded"

    def compute_site(
        self,
        *,
        body: str,
        latitude_deg: float,
        longitude_deg: float,
        epoch_utc: str,
    ) -> SpiceGeometryRecord:
        """Compute one point record in the body's documented body-fixed frame."""
        if body not in BODY_FRAME:
            raise SolarGeometryError(f"unsupported body for this pilot: {body!r}")
        spice = self._require_spice()
        frame = BODY_FRAME[body]
        naif_body = BODY_NAIF_ID[body]
        et = float(spice.str2et(spice_epoch_text(epoch_utc)))
        frame_validation = self.validate_frame(frame, et)
        _, radii_km = spice.bodvrd(naif_body, "RADII", 3)
        equatorial_radius = float(radii_km[0]) * 1000.0
        polar_radius = float(radii_km[1]) * 1000.0
        site = body_fixed_site_vector(latitude_deg, longitude_deg, equatorial_radius, polar_radius)
        normal = ellipsoid_surface_normal(site, equatorial_radius, polar_radius)
        # SPICE state vectors are in km; the pure geometry helpers use metres.
        sun_km, _light_time = spice.spkpos("SUN", et, frame, "LT", naif_body)
        sun_tuple = (
            float(sun_km[0]) * 1000.0,
            float(sun_km[1]) * 1000.0,
            float(sun_km[2]) * 1000.0,
        )
        geometry = compute_solar_geometry(
            site_xyz_m=site,
            sun_xyz_m=sun_tuple,
            surface_normal=normal,
            site_longitude_deg=longitude_deg,
        )
        return SpiceGeometryRecord(
            body=body,
            frame=frame,
            epoch_utc=epoch_utc,
            et_seconds=et,
            site_latitude_deg=float(latitude_deg),
            site_longitude_deg=float(longitude_deg),
            site_radius_m=_norm(site),
            site_xyz_m=site,
            surface_normal=normal,
            sun_xyz_m=sun_tuple,
            geometry=geometry,
            frame_validation=frame_validation,
            spice_version=str(getattr(spice, "__version__", "unknown")),
        )


def body_kernel_asset_ids(body: str) -> list[str]:
    """Kernel asset IDs actually required by one body's computation."""
    return [KERNEL_BY_FILENAME[filename].asset_id for filename in BODY_KERNEL_FILENAMES[body]]
