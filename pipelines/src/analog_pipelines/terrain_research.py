"""Experimental math only: not a registered feature or production classifier.

Analytic tests verify units, sign symmetry and saddle sensitivity, not calibration
against observed DEM uncertainty. Buffered source reads and a pinned reference
geomorphon implementation are required before any production integration.
"""

from __future__ import annotations

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
CURVATURE_BUFFER_CELLS = 4  # Proposed: 3-sigma smoothing support plus derivative radius.
GEOMORPHON_SEARCH_CELLS = 12  # Proposed: 1080 m at the common 90 m resolution.


def surface_curvedness(z: np.ndarray, cell_m: float) -> np.ndarray:
    """Experimental C=sqrt((k1²+k2²)/2), in m^-1; incomplete 3x3 => NaN.

    Graph mean curvature H and Gaussian curvature K give C²=2H²-K. This
    distinguishes a saddle from a plane and cannot cancel convex with concave
    areas. The prototype has no smoothing; production smoothing/buffer choices
    remain unvalidated. Never feed its values into the production score.
    """
    z = np.asarray(z, dtype=float)
    if z.ndim != 2 or min(z.shape) < 3 or not np.isfinite(cell_m) or cell_m <= 0:
        raise ValueError("Need a 2D 3x3+ grid and positive metric cell size")
    padded = np.pad(z, 1, constant_values=np.nan)
    nw, n, ne = padded[:-2, :-2], padded[:-2, 1:-1], padded[:-2, 2:]
    w, c, e = padded[1:-1, :-2], padded[1:-1, 1:-1], padded[1:-1, 2:]
    sw, s, se = padded[2:, :-2], padded[2:, 1:-1], padded[2:, 2:]
    valid = np.isfinite(nw + n + ne + w + c + e + sw + s + se)
    p, q = (e - w) / (2 * cell_m), (s - n) / (2 * cell_m)
    r, t = (e - 2 * c + w) / cell_m**2, (s - 2 * c + n) / cell_m**2
    cross = (se - sw - ne + nw) / (4 * cell_m**2)
    denominator = 1 + p * p + q * q
    mean = ((1 + q * q) * r - 2 * p * q * cross + (1 + p * p) * t) / (2 * denominator**1.5)
    gaussian = (r * t - cross * cross) / denominator**2
    return np.where(valid, np.sqrt(np.maximum(0, 2 * mean * mean - gaussian)), np.nan)


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
