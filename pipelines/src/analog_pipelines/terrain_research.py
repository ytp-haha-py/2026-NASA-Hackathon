"""Experimental terrain math only: not a registered feature or production classifier.

Analytic tests verify units, sign symmetry and saddle sensitivity, not calibration
against observed DEM uncertainty. Buffered source reads and a pinned reference
geomorphon implementation are required before any production integration.

The functions here are intentionally storage-free. ``terrain_buffer`` owns the
buffer/validity bookkeeping and source-window reads; this module owns the local
surface mathematics. None of these values enter the production similarity score.
"""

from __future__ import annotations

import math

import numpy as np

LANDFORM_CLASSES = (
    "Flat",
    "Peak",
    "Ridge",
    "Shoulder",
    "Spur",
    "Slope",
    "Hollow",
    "Footslope",
    "Valley",
    "Pit",
)
CENTRAL_DERIVATIVE_BUFFER_CELLS = 1  # 3x3 stencil: one cell on every side.
CURVATURE_BUFFER_CELLS = 4  # Proposed: 3-sigma smoothing support plus derivative radius.
QUADRATIC_FIT_BUFFER_CELLS = 2  # 5x5 local least-squares quadratic fit.
GEOMORPHON_SEARCH_CELLS = 12  # Proposed: 1080 m at the common 90 m resolution.
GAUSSIAN_SIGMA_CELLS = 1.0  # 90 m at the common 90 m analysis resolution.
GAUSSIAN_TRUNCATE = 3.0  # Kernel radius = ceil(3 * sigma) = 3 cells.
PROFILE_GRADIENT_EPSILON = 1e-20  # (m/m)^2; below this the gradient is numerical zero.


def _validate_grid(z: np.ndarray, cell_m: float, minimum: int = 3) -> np.ndarray:
    z = np.asarray(z, dtype=float)
    if z.ndim != 2 or min(z.shape) < minimum:
        raise ValueError(f"Need a 2D {minimum}x{minimum}+ grid")
    if not np.isfinite(cell_m) or cell_m <= 0:
        raise ValueError("Need a positive finite metric cell size")
    return z


def _central_derivatives(
    z: np.ndarray, cell_m: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Central 3x3 derivatives p=z_x, q=z_y, r=z_xx, s=z_xy, t=z_yy.

    Returns full-shape arrays and a validity mask. The mask is False where any
    cell of the 3x3 neighbourhood is missing; no padding or smoothing is applied.
    """
    z = _validate_grid(z, cell_m)
    padded = np.pad(z, 1, constant_values=np.nan)
    nw, n, ne = padded[:-2, :-2], padded[:-2, 1:-1], padded[:-2, 2:]
    w, c, e = padded[1:-1, :-2], padded[1:-1, 1:-1], padded[1:-1, 2:]
    sw, s, se = padded[2:, :-2], padded[2:, 1:-1], padded[2:, 2:]
    valid = np.isfinite(nw + n + ne + w + c + e + sw + s + se)
    p = (e - w) / (2 * cell_m)
    q = (s - n) / (2 * cell_m)
    r = (e - 2 * c + w) / cell_m**2
    t = (s - 2 * c + n) / cell_m**2
    cross = (se - sw - ne + nw) / (4 * cell_m**2)
    return p, q, r, cross, t, valid


def _curvature_fields(
    p: np.ndarray,
    q: np.ndarray,
    r: np.ndarray,
    s: np.ndarray,
    t: np.ndarray,
    valid: np.ndarray,
) -> dict[str, np.ndarray]:
    """Curvature fields from metric first/second derivatives (m^-1 / m^-2).

    ``mean_curvature_m_inv`` is the graph mean curvature H, ``gaussian_curvature_m_inv2``
    is K, and ``surface_curvedness_m_inv`` is sqrt((k1^2+k2^2)/2) = sqrt(2H^2-K).
    Profile/tangential curvature follow the Mitasova/Hofierka form used by GRASS
    ``r.slope.aspect`` with a positive-convex sign convention; they are research
    comparisons and have not been cross-validated against that implementation.

    Missing-neighbourhood cells stay NaN. At exactly zero gradient the profile and
    tangential expressions are undefined, so they are reported as 0.0 (a plane has
    no curvature in either direction); all other fields remain 0/NaN as computed.
    """
    denominator = 1.0 + p * p + q * q
    mean = ((1 + q * q) * r - 2 * p * q * s + (1 + p * p) * t) / (2 * denominator**1.5)
    gaussian = (r * t - s * s) / denominator**2
    curvedness = np.sqrt(np.maximum(0.0, 2 * mean * mean - gaussian))
    grad2 = p * p + q * q
    positive_gradient = grad2 > PROFILE_GRADIENT_EPSILON
    safe_grad2 = np.where(positive_gradient, grad2, 1.0)
    profile = np.where(
        positive_gradient,
        -(r * p * p + 2 * p * q * s + t * q * q) / (safe_grad2 * denominator**1.5),
        0.0,
    )
    tangential = np.where(
        positive_gradient,
        -(r * q * q - 2 * p * q * s + t * p * p) / (safe_grad2 * np.sqrt(denominator)),
        0.0,
    )
    return {
        "mean_curvature_m_inv": np.where(valid, mean, np.nan),
        "gaussian_curvature_m_inv2": np.where(valid, gaussian, np.nan),
        "surface_curvedness_m_inv": np.where(valid, curvedness, np.nan),
        "profile_curvature_m_inv": np.where(valid, profile, np.nan),
        "tangential_curvature_m_inv": np.where(valid, tangential, np.nan),
    }


def surface_curvedness(z: np.ndarray, cell_m: float) -> np.ndarray:
    """Experimental C=sqrt((k1²+k2²)/2), in m^-1; incomplete 3x3 => NaN.

    Graph mean curvature H and Gaussian curvature K give C²=2H²-K. This
    distinguishes a saddle from a plane and cannot cancel convex with concave
    areas. The prototype has no smoothing; production smoothing/buffer choices
    remain unvalidated. Never feed its values into the production score.
    """
    p, q, r, s, t, valid = _central_derivatives(z, cell_m)
    return _curvature_fields(p, q, r, s, t, valid)["surface_curvedness_m_inv"]


def curvature_components(z: np.ndarray, cell_m: float) -> dict[str, np.ndarray]:
    """Central-difference H, K, curvedness and profile/tangential curvature.

    This is the unsmoothed 3x3 prototype. Every output has the input shape and is
    NaN wherever the 3x3 neighbourhood is incomplete.
    """
    return _curvature_fields(*_central_derivatives(z, cell_m))


def gaussian_smooth_strict(
    z: np.ndarray,
    sigma_cells: float = GAUSSIAN_SIGMA_CELLS,
    truncate: float = GAUSSIAN_TRUNCATE,
) -> np.ndarray:
    """Gaussian smoothing that never crosses missing data.

    The kernel is truncated at ``ceil(truncate * sigma)`` cells. A destination cell
    is NaN unless every non-zero kernel cell around it is finite; there is no
    renormalisation, nearest/mirror fill, or interpolation across nodata.
    """
    z = np.asarray(z, dtype=float)
    if z.ndim != 2:
        raise ValueError("Need a 2D grid")
    if not np.isfinite(sigma_cells) or sigma_cells <= 0:
        raise ValueError("sigma_cells must be positive and finite")
    if not np.isfinite(truncate) or truncate <= 0:
        raise ValueError("truncate must be positive and finite")
    radius = int(math.ceil(truncate * sigma_cells))
    offsets = np.arange(-radius, radius + 1, dtype=float)
    weights_1d = np.exp(-0.5 * (offsets / sigma_cells) ** 2)
    kernel = np.outer(weights_1d, weights_1d)
    kernel /= kernel.sum()

    padded = np.pad(z, radius, constant_values=np.nan)
    rows, cols = z.shape
    accumulated = np.zeros(z.shape, dtype=float)
    valid = np.ones(z.shape, dtype=bool)
    for i, dy in enumerate(range(-radius, radius + 1)):
        for j, dx in enumerate(range(-radius, radius + 1)):
            weight = kernel[i, j]
            if weight <= 0:
                continue
            shifted = padded[
                radius + dy : radius + dy + rows,
                radius + dx : radius + dx + cols,
            ]
            accumulated += weight * shifted
            valid &= np.isfinite(shifted)
    return np.where(valid, accumulated, np.nan)


def smoothed_curvature_components(
    z: np.ndarray,
    cell_m: float,
    sigma_cells: float = GAUSSIAN_SIGMA_CELLS,
    truncate: float = GAUSSIAN_TRUNCATE,
) -> dict[str, np.ndarray]:
    """Gaussian-smoothed central-difference curvature components.

    Support is ``ceil(truncate*sigma) + 1`` cells: the smoothing radius plus the
    one-cell derivative stencil. At the proposed sigma=1 cell, truncate=3 this is
    four cells (360 m at 90 m support).
    """
    return curvature_components(gaussian_smooth_strict(z, sigma_cells, truncate), cell_m)


def quadratic_curvature_components(
    z: np.ndarray, cell_m: float, radius_cells: int = QUADRATIC_FIT_BUFFER_CELLS
) -> dict[str, np.ndarray]:
    """Least-squares quadratic fit on a local ``(2r+1)^2`` window.

    Fits ``z = a x^2 + b y^2 + c xy + d x + e y + f`` and evaluates the derivatives
    at the window centre. This is an explicit-scale comparison for the smoothed
    central-difference prototype, not a production method. Windows containing any
    missing value are NaN; no fill is substituted.
    """
    z = _validate_grid(z, cell_m, minimum=3)
    if not isinstance(radius_cells, int) or radius_cells < 1:
        raise ValueError("radius_cells must be a positive integer")
    k = 2 * radius_cells + 1
    if min(z.shape) < k:
        raise ValueError(f"Grid is too small for a {k}x{k} quadratic fit")
    windows = np.lib.stride_tricks.sliding_window_view(z, (k, k))
    ny, nx = windows.shape[:2]
    # Fit in cell units, not metres: the quadratic design columns then have
    # comparable magnitude and the normal equations stay well conditioned.
    coords = np.arange(-radius_cells, radius_cells + 1, dtype=float)
    dy, dx = np.meshgrid(coords, coords, indexing="ij")
    design = np.column_stack(
        [
            (dx * dx).ravel(),
            (dy * dy).ravel(),
            (dx * dy).ravel(),
            dx.ravel(),
            dy.ravel(),
            np.ones(k * k),
        ]
    )
    pseudo_inverse = np.linalg.pinv(design)
    flat = windows.reshape(-1, k * k)
    finite = np.isfinite(flat).all(axis=1)
    values = np.where(finite[:, None], flat, 0.0)
    coefficients = values @ pseudo_inverse.T
    coefficients[~finite] = np.nan
    a, b, c, d, e, _ = coefficients.T
    shape = (ny, nx)
    fields = _curvature_fields(
        (d / cell_m).reshape(shape),
        (e / cell_m).reshape(shape),
        (2 * a / cell_m**2).reshape(shape),
        (c / cell_m**2).reshape(shape),
        (2 * b / cell_m**2).reshape(shape),
        finite.reshape(shape),
    )

    embedded: dict[str, np.ndarray] = {}
    for name, values_flat in fields.items():
        full = np.full(z.shape, np.nan, dtype=float)
        full[radius_cells : radius_cells + ny, radius_cells : radius_cells + nx] = (
            values_flat.reshape(ny, nx)
        )
        embedded[name] = full
    return embedded


def landform_hellinger(a, b) -> float:
    """Candidate whole-vector comparator; one contribution for the ten-class composition."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    for vector in (a, b):
        if (
            vector.shape != (len(LANDFORM_CLASSES),)
            or not np.isfinite(vector).all()
            or (vector < 0).any()
        ):
            raise ValueError("Need ten finite nonnegative landform fractions in canonical order")
        if not np.isclose(vector.sum(), 1, atol=1e-6, rtol=0):
            raise ValueError("Landform fractions must sum to one; do not fill missing classes")
    return float(np.linalg.norm(np.sqrt(a) - np.sqrt(b)) / np.sqrt(2))
