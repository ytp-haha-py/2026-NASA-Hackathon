"""Versioned feature-eligibility registry for D01-D06 observations (Issue #116).

The registry is deliberately separate from the stored typed observations. Source records keep
their acquisition ``score_role`` (for example ``evidence_only``); this module decides whether a
stored observation may legally answer a *query* role such as weighted similarity, a hard filter,
evidence, or uncertainty. A role is never inferred from the dimension code alone.

Nothing here zero-fills, interpolates, or joins by proximity. A typed observation may only be
materialised for a fine candidate when all of the following match exactly and reproducibly:

* ``typed_areas.area_id`` == ``candidates.location_id`` (exact string, never nearest point);
* same body;
* registered method version and unit;
* same selector (depth interval and statistic);
* ``state == "detected"`` and a finite numeric value;
* the recorded spatial bbox equals the candidate bbox within a fixed tolerance.

Every rejected observation is reported with a machine-readable reason so the API can explain why a
feature did not participate.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from analog_api.domain.features import (
    CONTEXT_FILTER_BY_ID,
    CONTEXT_FILTER_FEATURES,
    CONTEXT_FILTER_VERSION,
    FEATURES,
    ContextFilterDefinition,
    FeatureDefinition,
)

REGISTRY_VERSION = "feature-eligibility-1.0.0"
D06_METHOD_VERSION = "soilgrids-wcs-2.0.0"
D06_DATASET_ID = "soilgrids-250m-v2"
D06_SOURCE_URL = "https://docs.isric.org/globaldata/soilgrids/"
D06_LICENSE = "CC BY 4.0; cite ISRIC SoilGrids 2.0"
D06_LICENSE_URL = "https://docs.isric.org/globaldata/soilgrids/"
SPATIAL_BBOX_TOLERANCE_DEG = 1.0e-6

ROLE_SIMILARITY = "similarity"
ROLE_HARD_FILTER = "hard_filter"
ROLE_CATEGORY_MATCH = "category_match"
ROLE_EVIDENCE = "evidence"
ROLE_UNCERTAINTY = "uncertainty"
ROLE_CONTEXT = "context"
# Read-only alias used by /search/features for "any active filter role" (hard_filter or
# category_match). It is never stored and never a similarity role.
ROLE_FILTER = "filter"
QUERY_ROLES = (
    ROLE_SIMILARITY,
    ROLE_HARD_FILTER,
    ROLE_CATEGORY_MATCH,
    ROLE_FILTER,
    ROLE_EVIDENCE,
    ROLE_UNCERTAINTY,
    ROLE_CONTEXT,
)

DIMENSION_LABELS = {
    "D01": "Terrain & Landforms",
    "D02": "Geology & Mineralogy",
    "D03": "Thermal Environment",
    "D04": "Water & Ice",
    "D05": "Solar Illumination & Radiation",
    "D06": "Regolith & Surface Properties",
    "land_cover_context": "Earth land-cover context (not D07)",
}

MISSING_POLICY = (
    "A missing or gated-out value excludes a candidate only when the feature carries a positive "
    "weight. A zero-weight feature is never compared and never excludes a candidate. Missing is "
    "reported as missing; it is never imputed as zero."
)

_D06_PATTERN = re.compile(
    r"^soil_(?P<property>bdod|clay|sand|silt|soc)_"
    r"(?P<depth>0_5|5_15|15_30|30_60)cm_(?P<statistic>mean|q05|q95)$"
)

_D06_PROPERTIES: dict[str, dict[str, Any]] = {
    "clay": {
        "label": "Soil clay content",
        "unit": "g kg-1",
        "min_value": 0.0,
        "max_value": 1000.0,
        "scale": 200.0,
        "scale_rationale": (
            "Fixed a-priori contrast of 200 g kg-1, separating sandy from clay-rich surface "
            "material at the 0-5 cm SoilGrids prediction depth. Not calibrated to ecological "
            "equivalence."
        ),
    },
    "sand": {
        "label": "Soil sand content",
        "unit": "g kg-1",
        "min_value": 0.0,
        "max_value": 1000.0,
        "scale": 200.0,
        "scale_rationale": (
            "Fixed a-priori contrast of 200 g kg-1 for the sand fraction. Sand, silt and clay "
            "are compositional components, not independent measurements."
        ),
    },
    "silt": {
        "label": "Soil silt content",
        "unit": "g kg-1",
        "min_value": 0.0,
        "max_value": 1000.0,
        "scale": 200.0,
        "scale_rationale": (
            "Fixed a-priori contrast of 200 g kg-1 for the silt fraction. Sand, silt and clay "
            "are compositional components, not independent measurements."
        ),
    },
    "soc": {
        "label": "Soil organic carbon",
        "unit": "dg kg-1",
        "min_value": 0.0,
        "max_value": 1000.0,
        "scale": 100.0,
        "scale_rationale": (
            "Fixed a-priori contrast of 100 dg kg-1 (10 g kg-1) for surface organic carbon. "
            "The scale is an engineering query contrast, not a calibrated similarity percentage."
        ),
    },
    "bdod": {
        "label": "Bulk density of fine earth",
        "unit": "cg cm-3",
        "min_value": 0.0,
        "max_value": 300.0,
        "scale": 50.0,
        "scale_rationale": (
            "Fixed a-priori contrast of 50 cg cm-3 (0.5 g cm-3) for surface bulk density."
        ),
    },
}

_DEPTHS: dict[str, tuple[float, float, str]] = {
    "0_5": (0.0, 0.05, "0-5 cm"),
    "5_15": (0.05, 0.15, "5-15 cm"),
    "15_30": (0.15, 0.30, "15-30 cm"),
    "30_60": (0.30, 0.60, "30-60 cm"),
}

_D06_QUALITY_RULES = (
    "state must be detected; unknown/no_coverage are not scored",
    "finite numeric value required; null is missing, not zero",
    "mean, q05 and q95 belong to one measurement family and are never treated as three "
    "independent observations",
    "depths are never pooled; the requested feature id fixes one depth interval",
    "the AOI mean is not a SoilGrids product cell value and not a local soil sample",
)


@dataclass(frozen=True, slots=True)
class RoleSupport:
    supported: bool
    reason: str | None = None
    constraints: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "reason": self.reason,
            "constraints": list(self.constraints),
        }


def _roles(
    *,
    similarity: RoleSupport,
    hard_filter: RoleSupport,
    evidence: RoleSupport,
    uncertainty: RoleSupport | None = None,
    context: RoleSupport | None = None,
    category_match: RoleSupport | None = None,
) -> dict[str, RoleSupport]:
    return {
        ROLE_SIMILARITY: similarity,
        ROLE_HARD_FILTER: hard_filter,
        ROLE_CATEGORY_MATCH: category_match
        or RoleSupport(
            False,
            "No validated taxonomy/crosswalk is registered for this feature.",
            ("Categorical similarity is disabled until a versioned crosswalk exists.",),
        ),
        ROLE_EVIDENCE: evidence,
        ROLE_UNCERTAINTY: uncertainty
        or RoleSupport(False, "No separate uncertainty records are registered for this feature."),
        ROLE_CONTEXT: context or RoleSupport(False, "Not registered as Earth land-cover context."),
    }


@dataclass(frozen=True, slots=True)
class FeatureEligibility:
    feature_id: str
    dimension_id: str
    label: str
    unit: str
    value_kind: str
    body_scope: tuple[str, ...]
    score_role: str
    roles: dict[str, RoleSupport]
    min_value: float | None
    max_value: float | None
    scale: float | None
    scale_rationale: str | None
    selector: dict[str, Any]
    method_versions: tuple[str, ...]
    support: dict[str, Any]
    missing_policy: str
    qa_rules: tuple[str, ...]
    comparability_gates: tuple[str, ...]
    source: dict[str, Any]
    unsupported_reason: str | None = None
    measurement_family: str | None = None
    uncertainty_feature_ids: tuple[str, ...] = ()
    filter_kind: str | None = None
    filter_statistic: str | None = None

    @property
    def similarity_supported(self) -> bool:
        return self.roles[ROLE_SIMILARITY].supported

    @property
    def hard_filter_supported(self) -> bool:
        return self.roles[ROLE_HARD_FILTER].supported

    @property
    def category_match_supported(self) -> bool:
        return self.roles[ROLE_CATEGORY_MATCH].supported

    @property
    def materializable(self) -> bool:
        return (
            self.similarity_supported or self.hard_filter_supported or self.category_match_supported
        )

    @property
    def depth_interval(self) -> tuple[float, float] | None:
        raw = self.selector.get("depth_interval_m")
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            return float(raw[0]), float(raw[1])
        return None

    def to_payload(self) -> dict[str, object]:
        roles = {role: support.to_payload() for role, support in self.roles.items()}
        # ``filter`` is an additive, read-only alias for whichever filter role is active
        # (range -> hard_filter, category -> category_match). ``hard_filter`` stays canonical
        # for the range-filter path so existing clients keep working.
        active_filter = (
            self.roles[ROLE_HARD_FILTER]
            if self.hard_filter_supported
            else self.roles[ROLE_CATEGORY_MATCH]
            if self.category_match_supported
            else None
        )
        if active_filter is not None:
            roles[ROLE_FILTER] = active_filter.to_payload()
        return {
            "feature_id": self.feature_id,
            "dimension_id": self.dimension_id,
            "dimension_label": DIMENSION_LABELS.get(self.dimension_id, self.dimension_id),
            "label": self.label,
            "unit": self.unit,
            "value_kind": self.value_kind,
            "body_scope": list(self.body_scope),
            "score_role": self.score_role,
            "roles": roles,
            "filter_kind": self.filter_kind,
            "filter_statistic": self.filter_statistic,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "scale": self.scale,
            "scale_rationale": self.scale_rationale,
            "selector": self.selector,
            "method_versions": list(self.method_versions),
            "support": self.support,
            "missing_policy": self.missing_policy,
            "qa_rules": list(self.qa_rules),
            "comparability_gates": list(self.comparability_gates),
            "source": self.source,
            "unsupported_reason": self.unsupported_reason,
            "measurement_family": self.measurement_family,
            "uncertainty_feature_ids": list(self.uncertainty_feature_ids),
            "context_filter_version": CONTEXT_FILTER_VERSION if self.filter_kind else None,
            "registry_version": REGISTRY_VERSION,
        }


@dataclass(slots=True)
class ObservedFeature:
    feature_id: str
    dimensions: set[str] = field(default_factory=set)
    bodies: Counter[str] = field(default_factory=Counter)
    states: Counter[str] = field(default_factory=Counter)
    value_kinds: set[str] = field(default_factory=set)
    units: set[str] = field(default_factory=set)
    method_versions: set[str] = field(default_factory=set)
    dataset_ids: set[str] = field(default_factory=set)
    area_ids: set[str] = field(default_factory=set)
    observation_ids: list[str] = field(default_factory=list)
    numeric_count: int = 0
    numeric_min: float | None = None
    numeric_max: float | None = None

    @property
    def observation_count(self) -> int:
        return len(self.observation_ids)

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]]) -> dict[str, ObservedFeature]:
        observed: dict[str, ObservedFeature] = {}
        for row in rows:
            feature_id = str(row["feature_id"])
            item = observed.setdefault(feature_id, ObservedFeature(feature_id=feature_id))
            if row.get("dimension_code"):
                item.dimensions.add(str(row["dimension_code"]))
            item.bodies[str(row.get("body") or "unknown")] += 1
            item.states[str(row.get("state") or "unknown")] += 1
            if row.get("value_kind"):
                item.value_kinds.add(str(row["value_kind"]))
            if row.get("unit"):
                item.units.add(str(row["unit"]))
            if row.get("method_version"):
                item.method_versions.add(str(row["method_version"]))
            for dataset_id in row.get("dataset_ids") or []:
                item.dataset_ids.add(str(dataset_id))
            item.area_ids.add(str(row["area_id"]))
            item.observation_ids.append(str(row["observation_id"]))
            value = row.get("value")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            ):
                numeric = float(value)
                item.numeric_count += 1
                item.numeric_min = (
                    numeric if item.numeric_min is None else min(item.numeric_min, numeric)
                )
                item.numeric_max = (
                    numeric if item.numeric_max is None else max(item.numeric_max, numeric)
                )
        return observed

    def to_payload(self) -> dict[str, object]:
        return {
            "observation_count": self.observation_count,
            "area_count": len(self.area_ids),
            "by_body": dict(sorted(self.bodies.items())),
            "by_state": dict(sorted(self.states.items())),
            "value_kinds": sorted(self.value_kinds),
            "units": sorted(self.units),
            "method_versions": sorted(self.method_versions),
            "dataset_ids": sorted(self.dataset_ids),
            "numeric_observation_count": self.numeric_count,
            "observed_numeric_min": self.numeric_min,
            "observed_numeric_max": self.numeric_max,
            "area_ids": sorted(self.area_ids),
        }


def _d01_entry(feature: FeatureDefinition) -> FeatureEligibility:
    return FeatureEligibility(
        feature_id=feature.id,
        dimension_id="D01",
        label=feature.label,
        unit=feature.unit,
        value_kind=feature.value_kind,
        body_scope=("Earth", "Moon", "Mars"),
        score_role=ROLE_SIMILARITY,
        roles=_roles(
            similarity=RoleSupport(
                True,
                None,
                (
                    "Same method_version, 90 m analysis grid and finite value.",
                    "Comparable within the registered terrain method only; not environmental "
                    "equivalence.",
                ),
            ),
            hard_filter=RoleSupport(True, None, ("Inclusive bounds inside the registry domain.",)),
            evidence=RoleSupport(True),
        ),
        min_value=feature.min_value,
        max_value=feature.max_value,
        scale=feature.scale,
        scale_rationale=feature.scale_rationale,
        selector={
            "kind": "local_window",
            "statistic": "registry_metric",
            "analysis_resolution_m": feature.extraction_scale_m,
        },
        method_versions=(feature.method_version,),
        support={
            "kind": "fixed_window",
            "window_size_m": 1980.0,
            "analysis_resolution_m": 90.0,
            "spatial_support": feature.spatial_support,
            "temporal_support": feature.temporal_support,
        },
        missing_policy=MISSING_POLICY,
        qa_rules=(
            "finite numeric value",
            "method_version must equal the registry version",
            "recommended valid_fraction >= 0.8 at the source; quality flags are surfaced",
        ),
        comparability_gates=(
            "same method_version",
            "same projected metric CRS and 90 m analysis grid",
            "same fixed 1.98 km window",
            "terrain-shape similarity does not imply environmental equivalence",
        ),
        source={
            "provider": "Copernicus DEM GLO-90",
            "dataset_ids": ["copernicus-dem-glo90"],
            "license": "Copernicus DEM licence; see /api/v1/datasets",
        },
        measurement_family=f"d01:{feature.method_version}:{feature.id}",
        filter_kind="range",
    )


def _d06_entry(
    feature_id: str, match: re.Match[str], observed: ObservedFeature
) -> FeatureEligibility:
    prop = match.group("property")
    depth_key = match.group("depth")
    statistic = match.group("statistic")
    spec = _D06_PROPERTIES[prop]
    depth_min, depth_max, depth_label = _DEPTHS[depth_key]
    family = f"d06:soilgrids:{prop}:{depth_key}"
    is_mean = statistic == "mean"
    label = f"{spec['label']} {depth_label} {'mean' if is_mean else statistic.upper()}"
    selector = {
        "kind": "depth_interval",
        "depth_interval_m": [depth_min, depth_max],
        "depth_label": depth_label,
        "statistic": statistic,
        "statistic_role": (
            "central estimate used by similarity/hard filters"
            if is_mean
            else "prediction quantile used as uncertainty evidence, never a separate observation"
        ),
    }
    support = {
        "kind": "area_envelope_mean",
        "native_resolution_m": 250.0,
        "analysis_resolution_m": None,
        "aggregation": "mean of native 250 m model cells over the AOI envelope",
        "not_a_local_sample": True,
        "value_origin": "model_prediction",
        "spatial_support": (
            "AOI envelope at SoilGrids 2.0 native 250 m model cells; the AOI mean is not a "
            "SoilGrids product cell value."
        ),
        "temporal_support": "SoilGrids 2.0 static model release used by the pilot",
    }
    if prop in {"clay", "sand", "silt"}:
        selector["compositional_group"] = f"soil_texture_{depth_key}cm"
        support["compositional_note"] = (
            "Clay, silt and sand are compositional fractions of the same SoilGrids prediction "
            "and are not independent measurements. Requesting several of them double-weights "
            "the same texture information."
        )
    if is_mean:
        roles = _roles(
            similarity=RoleSupport(
                True,
                None,
                (
                    "Earth only; exact area_id join to a fine candidate.",
                    "Same SoilGrids method version and 0-5/5-15/15-30/30-60 cm depth interval.",
                    "Value kind must be numeric and state detected.",
                    "Area-level 250 m model mean is not a local measurement and not the same "
                    "spatial support as a 90 m terrain window.",
                ),
            ),
            hard_filter=RoleSupport(
                True, None, ("Inclusive bounds inside the registered physical domain.",)
            ),
            evidence=RoleSupport(True),
            uncertainty=RoleSupport(
                True,
                None,
                (
                    "Q05/Q95 are separate prediction-quantile records at the same depth; they "
                    "are not a confidence interval for the AOI mean.",
                ),
            ),
        )
        unsupported = None
    else:
        reason = (
            "Prediction quantile of the same SoilGrids model prediction. It is uncertainty "
            "evidence for the matching mean feature, not an independent observation and not a "
            "similarity feature."
        )
        roles = _roles(
            similarity=RoleSupport(False, reason),
            hard_filter=RoleSupport(False, reason),
            evidence=RoleSupport(True, reason),
            uncertainty=RoleSupport(True, reason),
        )
        unsupported = reason
    return FeatureEligibility(
        feature_id=feature_id,
        dimension_id="D06",
        label=label,
        unit=spec["unit"],
        value_kind="numeric",
        body_scope=("Earth",),
        score_role=ROLE_SIMILARITY if is_mean else ROLE_UNCERTAINTY,
        roles=roles,
        min_value=float(spec["min_value"]),
        max_value=float(spec["max_value"]),
        scale=float(spec["scale"]),
        scale_rationale=spec["scale_rationale"],
        selector=selector,
        method_versions=(D06_METHOD_VERSION,),
        support=support,
        missing_policy=MISSING_POLICY,
        qa_rules=_D06_QUALITY_RULES,
        comparability_gates=(
            "body Earth only",
            "exact area_id == fine candidate location_id",
            "method_version soilgrids-wcs-2.0.0",
            "same depth interval and statistic",
            "unit exactly equal to the registry unit",
            "finite numeric value; state detected",
            "recorded AOI bbox equals the candidate bbox within 1e-6 degree",
            "250 m model support is not interchangeable with 90 m terrain support",
        ),
        source={
            "provider": "ISRIC SoilGrids 2.0",
            "dataset_ids": [D06_DATASET_ID],
            "url": D06_SOURCE_URL,
            "license": D06_LICENSE,
            "license_url": D06_LICENSE_URL,
            "value_origin": "model_prediction",
        },
        unsupported_reason=unsupported,
        measurement_family=family,
        uncertainty_feature_ids=(
            f"soil_{prop}_{depth_key}cm_q05",
            f"soil_{prop}_{depth_key}cm_q95",
        )
        if is_mean
        else (),
        filter_kind="range" if is_mean else None,
    )


def _evidence_only_entry(
    feature_id: str,
    observed: ObservedFeature,
    *,
    dimension_id: str,
    label: str,
    score_role: str,
    reason: str,
    role: str = ROLE_EVIDENCE,
) -> FeatureEligibility:
    bodies = tuple(sorted(observed.bodies)) or ("unknown",)
    units = sorted(observed.units)
    methods = tuple(sorted(observed.method_versions))
    role_support = RoleSupport(True, reason)
    rejected = RoleSupport(False, reason)
    return FeatureEligibility(
        feature_id=feature_id,
        dimension_id=dimension_id,
        label=label,
        unit=units[0] if len(units) == 1 else "mixed",
        value_kind=next(iter(sorted(observed.value_kinds)), "unknown"),
        body_scope=bodies,
        score_role=score_role,
        roles=_roles(
            similarity=rejected,
            hard_filter=rejected,
            evidence=role_support,
            uncertainty=RoleSupport(
                True,
                "Stored state/quality records are exposed as evidence; they do not alter the "
                "weighted distance.",
            )
            if "unknown" in observed.states or "no_coverage" in observed.states
            else None,
            context=role_support if role == ROLE_CONTEXT else None,
        ),
        min_value=observed.numeric_min if observed.numeric_count else None,
        max_value=observed.numeric_max if observed.numeric_count else None,
        scale=None,
        scale_rationale=None,
        selector={
            "kind": "stored_observation",
            "value_kinds": sorted(observed.value_kinds),
            "states": sorted(observed.states),
        },
        method_versions=methods,
        support={
            "kind": "source_defined",
            "method_versions": list(methods),
            "units": units,
            "observed_by_body": dict(sorted(observed.bodies.items())),
        },
        missing_policy=MISSING_POLICY,
        qa_rules=(
            "state and quality flags are exposed verbatim",
            "unknown/no_coverage is never converted to zero or a negative detection",
        ),
        comparability_gates=(
            "not registered for weighted similarity",
            "no validated same-quantity/same-method/same-support counterpart",
        ),
        source={
            "provider": "typed observation bundle",
            "dataset_ids": sorted(observed.dataset_ids),
            "value_origin": "stored_observation",
        },
        unsupported_reason=reason,
    )


def category_labels(value: Any) -> tuple[str, ...]:
    """Return stored, source-native category labels without normalising or inventing any.

    The committed D02 ``geologic_unit`` payload is a point-query object. Its meaningful
    labels are the source ``code``/``name`` plus every returned map-unit name under
    ``units[].name``. Blank values are dropped; no crosswalk, casing or taxonomy is applied.
    """

    labels: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if text:
            labels.append(text)
    elif isinstance(value, Mapping):
        for key in ("code", "name"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                labels.append(raw.strip())
        units = value.get("units")
        if isinstance(units, (list, tuple)):
            for unit in units:
                if not isinstance(unit, Mapping):
                    continue
                raw = unit.get("name")
                if isinstance(raw, str) and raw.strip():
                    labels.append(raw.strip())
    return tuple(dict.fromkeys(labels))


def _geologic_unit_entry(
    feature_id: str,
    observed: ObservedFeature,
    labels_by_area: Mapping[str, tuple[str, ...]],
) -> FeatureEligibility:
    taxonomy_reason = (
        "Geologic map-unit names are dataset-specific free text. No versioned taxonomy "
        "crosswalk exists, so map units cannot enter categorical or weighted similarity."
    )
    bodies = tuple(sorted(observed.bodies)) or ("Earth",)
    methods = tuple(sorted(observed.method_versions))
    if labels_by_area:
        category_role = RoleSupport(
            True,
            "Exact match against the stored, source-native map-unit label. This is a dropdown "
            "lookup, not a taxonomy crosswalk and not an equivalence claim.",
            (
                "Earth body only; Moon/Mars map units remain evidence and are never matched "
                "across bodies.",
                "Labels are stored verbatim (value.code / value.name / value.units[].name); "
                "no casing, spelling or taxonomy normalisation is applied.",
                "A candidate with no stored label is excluded as missing, never filled.",
                "Category selection changes only the candidate set; it never enters distance.",
            ),
        )
        category_unsupported = None
    else:
        category_role = RoleSupport(
            False,
            "No Earth geologic_unit observations with a stored label are present in this "
            "database, so the category control has no real coverage.",
        )
        category_unsupported = category_role.reason
    return FeatureEligibility(
        feature_id=feature_id,
        dimension_id="D02",
        label="Geologic map unit (source-native label)",
        unit="category",
        value_kind="category",
        body_scope=bodies,
        score_role=ROLE_EVIDENCE,
        roles=_roles(
            similarity=RoleSupport(False, taxonomy_reason),
            hard_filter=RoleSupport(
                False,
                "Numeric range filtering is undefined for a categorical map-unit label; use "
                "category_filters instead.",
            ),
            evidence=RoleSupport(True, taxonomy_reason),
            category_match=category_role,
        ),
        min_value=None,
        max_value=None,
        scale=None,
        scale_rationale=None,
        selector={
            "kind": "source_native_category",
            "category_body": "Earth",
            "match": "exact stored label equality",
            "labels_from": ["value.code", "value.name", "value.units[].name"],
            "normalisation": "none",
        },
        method_versions=methods,
        support={
            "kind": "point_query",
            "spatial_support": (
                "Macrostrat API v2 point query at the AOI centre; not a 1.98 km area fraction."
            ),
            "temporal_support": "static compilation, age depends on the source map",
            "taxonomy_crosswalk": False,
            "earth_only_control": True,
            "value_origin": "third_party_map_compilation",
        },
        missing_policy=MISSING_POLICY,
        qa_rules=(
            "state must be detected",
            "body must be Earth for the category control",
            "the stored label must be non-blank",
            "missing category is excluded, never imputed",
        ),
        comparability_gates=(
            "body Earth only for the category control",
            "no taxonomy/crosswalk; exact source label match only",
            "third-party map compilation with partial global coverage",
            "not a weighted-similarity feature",
        ),
        source={
            "provider": "Macrostrat API v2",
            "dataset_ids": sorted(observed.dataset_ids) or ["macrostrat-api"],
            "value_origin": "third_party_map_compilation",
        },
        unsupported_reason=category_unsupported or taxonomy_reason,
        measurement_family=None,
        filter_kind="category",
    )


def _context_filter_entry(
    feature: ContextFilterDefinition, observed: ObservedFeature
) -> FeatureEligibility:
    support_kind = "coarse_grid_point"
    selector = {
        "kind": "derived_period_mean",
        "statistic": feature.filter_statistic,
        "source_value_kind": "timeseries",
        "temporal_interval": {
            "start": "2024-01-01",
            "end": "2024-01-07",
        },
        "derivation": (
            "arithmetic mean of the stored finite daily values; missing_count must be 0 and "
            "every sample must be finite (no zero-fill, no interpolation)"
        ),
        "filter_kind": feature.filter_kind,
    }
    support = {
        "kind": support_kind,
        "native_resolution_m": 50000.0,
        "spatial_support": feature.spatial_support,
        "temporal_support": feature.temporal_support,
        "requires_bbox_match": True,
        "filter_value_semantics": "period_mean_of_stored_timeseries",
        "value_origin": "model_reanalysis_context",
        "not_a_local_measurement": True,
    }
    reason = (
        "Registered as a coarse Earth range filter only. NASA POWER reanalysis support is "
        "not shared with the 90 m terrain window, so this feature is never a similarity input."
    )
    return FeatureEligibility(
        feature_id=feature.feature_id,
        dimension_id=feature.dimension_id,
        label=feature.label,
        unit=feature.unit,
        value_kind="numeric",
        body_scope=("Earth",),
        score_role=feature.score_role,
        roles=_roles(
            similarity=RoleSupport(False, reason),
            hard_filter=RoleSupport(
                True,
                None,
                (
                    "Earth only; inclusive min/max inside the registered physical domain.",
                    "Exact area_id join to a fine candidate and matching stored pilot bbox.",
                    "Filter value is the registered 2024-01-01..2024-01-07 period mean; "
                    "missing samples reject the observation.",
                    "Range filtering selects candidates and never changes the weighted distance.",
                ),
            ),
            evidence=RoleSupport(True, reason),
            context=RoleSupport(True, reason),
        ),
        min_value=feature.min_value,
        max_value=feature.max_value,
        scale=feature.scale,
        scale_rationale=feature.scale_rationale,
        selector=selector,
        method_versions=(feature.method_version,),
        support=support,
        missing_policy=MISSING_POLICY,
        qa_rules=(
            "body Earth only",
            "state must be detected",
            "method_version must equal nasa-power-daily-1.0.0",
            "unit must equal the registry unit",
            "temporal_support must be the exact 2024-01-01..2024-01-07 interval",
            "every stored daily sample must be finite and missing_count must be 0",
            "recorded AOI bbox must equal the candidate bbox within 1e-6 degree",
        ),
        comparability_gates=(
            "body Earth only",
            "coarse ~0.5 x 0.625 degree reanalysis support; not a 1.98 km measurement",
            "filter-only role; never weighted similarity",
            "no cross-body or same-quantity equivalence",
            "derived period mean, not a new observed value",
        ),
        source={
            "provider": "NASA POWER",
            "dataset_ids": ["nasa-power-daily-point"],
            "url": feature.reference,
            "license": "NASA POWER free and open data; citation requested",
            "value_origin": "model_reanalysis_context",
        },
        unsupported_reason=reason,
        measurement_family=(f"{feature.dimension_id.lower()}:nasa-power:{feature.method_version}"),
        filter_kind=feature.filter_kind,
        filter_statistic=feature.filter_statistic,
    )


def _typed_entry(
    feature_id: str,
    observed: ObservedFeature,
    category_labels_by_area: Mapping[str, tuple[str, ...]] | None = None,
) -> FeatureEligibility:
    dimension = next(iter(sorted(observed.dimensions)), "unregistered")
    body_label = "/".join(sorted(observed.bodies)) or "unknown"
    if dimension == "D02":
        if feature_id == "geologic_unit":
            return _geologic_unit_entry(feature_id, observed, category_labels_by_area or {})
        if feature_id == "geology_analysis_availability":
            reason = (
                "Coverage/availability record (unknown or no_coverage). It is a quality/evidence "
                "field, not a mineralogical measurement."
            )
            return _evidence_only_entry(
                feature_id,
                observed,
                dimension_id="D02",
                label="Geology analysis availability",
                score_role=ROLE_EVIDENCE,
                reason=reason,
            )
        if feature_id.startswith("mineral_") or feature_id.startswith("context_"):
            reason = (
                f"D02 {body_label} evidence record from a single provider. Detection states are "
                "unknown/no_coverage or the spectral index support is not validated against the "
                "fine candidate pool; it remains evidence/quality only."
            )
            return _evidence_only_entry(
                feature_id,
                observed,
                dimension_id="D02",
                label=feature_id.replace("_", " "),
                score_role=ROLE_EVIDENCE,
                reason=reason,
            )
        reason = (
            "D02 process evidence is descriptive and has no validated numeric or categorical "
            "crosswalk for search."
        )
        return _evidence_only_entry(
            feature_id,
            observed,
            dimension_id="D02",
            label=feature_id.replace("_", " "),
            score_role=ROLE_EVIDENCE,
            reason=reason,
        )
    if dimension == "D03":
        reason = (
            f"D03 {body_label} remote-sensing/reanalysis record. The method, native support and "
            "temporal selector are not shared with the fine Earth 90 m candidate pool; it is "
            "context/evidence and never a cross-body temperature equivalence."
        )
        return _evidence_only_entry(
            feature_id,
            observed,
            dimension_id="D03",
            label=feature_id.replace("_", " "),
            score_role=ROLE_CONTEXT if "Earth" in observed.bodies else ROLE_EVIDENCE,
            reason=reason,
            role=ROLE_CONTEXT if "Earth" in observed.bodies else ROLE_EVIDENCE,
        )
    if dimension == "D04":
        reason = (
            f"D04 {body_label} water/ice record. Hydrogen proxies, radar CPR, reanalysis soil "
            "wetness and snow depth are different physical quantities with different supports; "
            "no validated cross-body or same-quantity mapping is registered."
        )
        return _evidence_only_entry(
            feature_id,
            observed,
            dimension_id="D04",
            label=feature_id.replace("_", " "),
            score_role=ROLE_EVIDENCE,
            reason=reason,
        )
    if dimension == "D05":
        reason = (
            f"D05 {body_label} illumination/radiation record. Solar geometry and irradiance "
            "depend on a time selector and body-specific ephemerides; they are context/evidence, "
            "not surface-property similarity."
        )
        return _evidence_only_entry(
            feature_id,
            observed,
            dimension_id="D05",
            label=feature_id.replace("_", " "),
            score_role=ROLE_CONTEXT,
            reason=reason,
            role=ROLE_CONTEXT,
        )
    reason = (
        "Observed feature has no registered eligibility rule. It is exposed as evidence only and "
        "cannot be used as a similarity target or hard filter."
    )
    return _evidence_only_entry(
        feature_id,
        observed,
        dimension_id=dimension,
        label=feature_id.replace("_", " "),
        score_role=ROLE_EVIDENCE,
        reason=reason,
    )


def _earth_category_labels_by_area(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    labels: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("feature_id") != "geologic_unit":
            continue
        if str(row.get("body") or "") != "Earth":
            continue
        if row.get("state") != "detected":
            continue
        found = category_labels(row.get("value"))
        if found:
            labels[str(row["area_id"])].update(found)
    return {area_id: tuple(sorted(values)) for area_id, values in labels.items() if values}


def build_registry(rows: Sequence[Mapping[str, Any]]) -> dict[str, FeatureEligibility]:
    """Build the registry for all D01 features plus every observed typed feature.

    The Earth scalar context range filters are registered even when a database has zero
    observations, so ``/search/features`` can truthfully report 0 coverage instead of hiding
    the control. They never gain a similarity role.
    """
    observed = ObservedFeature.from_rows(rows)
    category_by_area = _earth_category_labels_by_area(rows)
    registry: dict[str, FeatureEligibility] = {
        feature.id: _d01_entry(feature) for feature in FEATURES
    }
    for feature in CONTEXT_FILTER_FEATURES:
        registry[feature.feature_id] = _context_filter_entry(
            feature,
            observed.get(feature.feature_id) or ObservedFeature(feature_id=feature.feature_id),
        )
    for feature_id, item in sorted(observed.items()):
        if feature_id in CONTEXT_FILTER_BY_ID:
            continue
        match = _D06_PATTERN.match(feature_id)
        if match and "D06" in item.dimensions:
            registry[feature_id] = _d06_entry(feature_id, match, item)
        else:
            registry[feature_id] = _typed_entry(
                feature_id, item, category_labels_by_area=category_by_area
            )
    return registry


def observed_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, ObservedFeature]:
    return ObservedFeature.from_rows(rows)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _bbox_close(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    if not left or not right:
        return False
    try:
        return all(
            abs(float(left[key]) - float(right[key])) <= SPATIAL_BBOX_TOLERANCE_DEG
            for key in ("west", "south", "east", "north")
        )
    except (KeyError, TypeError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class MaterializedFeature:
    feature_id: str
    area_id: str
    body: str
    value: float
    unit: str
    method_version: str
    observation_id: str
    dimension_id: str
    selector: dict[str, Any]
    support: dict[str, Any]
    dataset_ids: tuple[str, ...]
    quality_flags: tuple[str, ...]
    uncertainty: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "area_id": self.area_id,
            "body": self.body,
            "value": self.value,
            "unit": self.unit,
            "method_version": self.method_version,
            "observation_id": self.observation_id,
            "dimension_id": self.dimension_id,
            "selector": self.selector,
            "support": self.support,
            "dataset_ids": list(self.dataset_ids),
            "quality_flags": list(self.quality_flags),
            "uncertainty": self.uncertainty,
        }


@dataclass(frozen=True, slots=True)
class MaterializedCategoryFeature:
    feature_id: str
    area_id: str
    body: str
    labels: tuple[str, ...]
    observation_id: str
    dimension_id: str
    selector: dict[str, Any]
    support: dict[str, Any]
    dataset_ids: tuple[str, ...]
    quality_flags: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "area_id": self.area_id,
            "body": self.body,
            "labels": list(self.labels),
            "observation_id": self.observation_id,
            "dimension_id": self.dimension_id,
            "selector": self.selector,
            "support": self.support,
            "dataset_ids": list(self.dataset_ids),
            "quality_flags": list(self.quality_flags),
        }


@dataclass(frozen=True, slots=True)
class RejectedObservation:
    area_id: str
    feature_id: str
    observation_id: str
    reasons: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "area_id": self.area_id,
            "feature_id": self.feature_id,
            "observation_id": self.observation_id,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class MaterializationOutcome:
    values: dict[tuple[str, str], MaterializedFeature] = field(default_factory=dict)
    categories: dict[tuple[str, str], MaterializedCategoryFeature] = field(default_factory=dict)
    rejected: list[RejectedObservation] = field(default_factory=list)


def _period_mean(
    entry: FeatureEligibility, row: Mapping[str, Any]
) -> tuple[float | None, list[str]]:
    """Derive the registered period mean from a stored timeseries.

    This is the only arithmetic performed on stored source values. It rejects rather than
    filling: a missing sample, a non-finite sample, a missing timestamp or a different temporal
    interval yields explicit reasons and no value.
    """

    reasons: list[str] = []
    expected = entry.selector.get("temporal_interval") or {}
    temporal = row.get("temporal_support") or {}
    if str(temporal.get("kind") or "") != "interval":
        reasons.append("temporal_support_mismatch")
    if str(temporal.get("start") or "") != str(expected.get("start") or "") or str(
        temporal.get("end") or ""
    ) != str(expected.get("end") or ""):
        reasons.append("temporal_support_mismatch")
    payload = row.get("value")
    if not isinstance(payload, Mapping):
        return None, [*reasons, "value_not_finite_numeric"]
    inner_unit = payload.get("unit")
    if inner_unit is not None and str(inner_unit) != entry.unit:
        reasons.append("timeseries_unit_mismatch")
    missing_count = payload.get("missing_count")
    if (
        isinstance(missing_count, (int, float))
        and not isinstance(missing_count, bool)
        and float(missing_count) > 0
    ):
        reasons.append("timeseries_missing_values")
    values = payload.get("values")
    if not isinstance(values, (list, tuple)) or not values:
        return None, [*reasons, "value_not_finite_numeric"]
    timestamps = payload.get("timestamps")
    if not isinstance(timestamps, (list, tuple)) or len(timestamps) != len(values):
        reasons.append("timeseries_timestamps_missing")
    numeric: list[float] = []
    for sample in values:
        number = _finite_number(sample)
        if number is None:
            return None, [*reasons, "value_not_finite_numeric"]
        numeric.append(number)
    if reasons:
        return None, reasons
    return sum(numeric) / len(numeric), []


def _query_numeric(
    entry: FeatureEligibility, row: Mapping[str, Any]
) -> tuple[float | None, list[str]]:
    if entry.support.get("filter_value_semantics") == "period_mean_of_stored_timeseries":
        return _period_mean(entry, row)
    numeric = _finite_number(row.get("value"))
    return numeric, [] if numeric is not None else ["value_not_finite_numeric"]


def _gate_observation(
    *,
    entry: FeatureEligibility,
    row: Mapping[str, Any],
    candidate_bbox: Mapping[str, Any] | None,
) -> tuple[list[str], float | None]:
    reasons: list[str] = []
    body = str(row.get("body") or "")
    if body not in entry.body_scope:
        reasons.append("body_out_of_scope")
    if row.get("state") != "detected":
        reasons.append(f"state:{row.get('state') or 'unknown'}")
    numeric, value_reasons = _query_numeric(entry, row)
    reasons.extend(value_reasons)
    if str(row.get("unit") or "") != entry.unit:
        reasons.append("unit_mismatch")
    if str(row.get("method_version") or "") not in entry.method_versions:
        reasons.append("method_version_mismatch")
    depth = entry.depth_interval
    if depth is not None:
        support = row.get("depth_support") or {}
        try:
            recorded = (float(support["min_m"]), float(support["max_m"]))
        except (KeyError, TypeError, ValueError):
            reasons.append("depth_support_missing")
        else:
            if abs(recorded[0] - depth[0]) > 1e-9 or abs(recorded[1] - depth[1]) > 1e-9:
                reasons.append("depth_interval_mismatch")
    spatial = row.get("spatial_support") or {}
    if entry.support.get("kind") == "area_envelope_mean" or entry.support.get(
        "requires_bbox_match"
    ):
        recorded_bbox = spatial.get("bbox")
        if not _bbox_close(recorded_bbox, candidate_bbox):
            reasons.append("spatial_bbox_mismatch")
        if (spatial.get("bbox_crs") or "").upper() != "EPSG:4326":
            reasons.append("spatial_crs_mismatch")
    return reasons, numeric


def _uncertainty_for(
    *,
    entry: FeatureEligibility,
    registry: Mapping[str, FeatureEligibility],
    rows_by_key: Mapping[tuple[str, str], list[Mapping[str, Any]]],
    area_id: str,
    candidate_bbox: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    quantiles: dict[str, float] = {}
    source_ids: list[str] = []
    for quantile_id in entry.uncertainty_feature_ids:
        quantile_entry = registry.get(quantile_id)
        if quantile_entry is None:
            continue
        candidates = rows_by_key.get((area_id, quantile_id), [])
        for row in candidates:
            reasons, numeric = _gate_observation(
                entry=quantile_entry, row=row, candidate_bbox=candidate_bbox
            )
            if reasons or numeric is None:
                continue
            suffix = "q05" if quantile_id.endswith("q05") else "q95"
            quantiles[suffix] = numeric
            source_ids.append(str(row["observation_id"]))
            break
    if not quantiles:
        return None
    return {
        "kind": "prediction_quantile",
        "q05": quantiles.get("q05"),
        "q95": quantiles.get("q95"),
        "interpretation": (
            "Separate SoilGrids prediction quantiles at the same area/property/depth. They "
            "quantify model uncertainty and are not a confidence interval for the AOI mean."
        ),
        "source_observation_ids": sorted(source_ids),
    }


def _materialize_category(
    *,
    outcome: MaterializationOutcome,
    entry: FeatureEligibility,
    area_id: str,
    feature_id: str,
    group: Sequence[Mapping[str, Any]],
    candidate_bbox: Mapping[str, Any] | None,
) -> None:
    label_sets: list[tuple[str, ...]] = []
    valid_rows: list[Mapping[str, Any]] = []
    rejected_reasons: list[str] = []
    for row in group:
        reasons: list[str] = []
        if str(row.get("body") or "") not in entry.body_scope:
            reasons.append("body_out_of_scope")
        if str(row.get("body") or "") != "Earth":
            reasons.append("category_body_out_of_scope")
        if row.get("state") != "detected":
            reasons.append(f"state:{row.get('state') or 'unknown'}")
        if str(row.get("method_version") or "") not in entry.method_versions:
            reasons.append("method_version_mismatch")
        labels = category_labels(row.get("value"))
        if not labels:
            reasons.append("category_value_missing")
        spatial = row.get("spatial_support") or {}
        if entry.support.get("requires_bbox_match") and not _bbox_close(
            spatial.get("bbox"), candidate_bbox
        ):
            reasons.append("spatial_bbox_mismatch")
        if reasons:
            rejected_reasons.extend(reason for reason in reasons if reason not in rejected_reasons)
            outcome.rejected.append(
                RejectedObservation(
                    area_id=area_id,
                    feature_id=feature_id,
                    observation_id=str(row["observation_id"]),
                    reasons=tuple(reasons),
                )
            )
            continue
        label_sets.append(labels)
        valid_rows.append(row)

    if not valid_rows:
        if not rejected_reasons:
            outcome.rejected.append(
                RejectedObservation(
                    area_id=area_id,
                    feature_id=feature_id,
                    observation_id=str(group[0]["observation_id"]),
                    reasons=("no_valid_observation",),
                )
            )
        return
    if len({frozenset(labels) for labels in label_sets}) > 1:
        outcome.rejected.append(
            RejectedObservation(
                area_id=area_id,
                feature_id=feature_id,
                observation_id=str(valid_rows[0]["observation_id"]),
                reasons=("ambiguous_duplicate_categories",),
            )
        )
        return
    row = min(valid_rows, key=lambda item: str(item["observation_id"]))
    outcome.categories[(area_id, feature_id)] = MaterializedCategoryFeature(
        feature_id=feature_id,
        area_id=area_id,
        body="Earth",
        labels=tuple(sorted(set(label_sets[0]))),
        observation_id=str(row["observation_id"]),
        dimension_id=entry.dimension_id,
        selector=dict(entry.selector),
        support=dict(entry.support),
        dataset_ids=tuple(str(item) for item in (row.get("dataset_ids") or [])),
        quality_flags=tuple(str(item) for item in (row.get("quality_flags") or [])),
    )


def materialize(
    *,
    registry: Mapping[str, FeatureEligibility],
    rows: Sequence[Mapping[str, Any]],
    candidate_bboxes: Mapping[str, Mapping[str, Any] | None],
) -> MaterializationOutcome:
    """Materialise query and filter values for exact candidate joins.

    Only features with a supported query role (similarity, range filter or category match) are
    materialised. Source rows are never modified, and a filter-only feature never gains a
    similarity role.
    """
    rows_by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_key[(str(row["area_id"]), str(row["feature_id"]))].append(row)

    outcome = MaterializationOutcome()
    for (area_id, feature_id), group in sorted(rows_by_key.items()):
        entry = registry.get(feature_id)
        if entry is None or not entry.materializable:
            continue
        if area_id not in candidate_bboxes:
            outcome.rejected.append(
                RejectedObservation(
                    area_id=area_id,
                    feature_id=feature_id,
                    observation_id=str(group[0]["observation_id"]),
                    reasons=("no_exact_candidate_location_id",),
                )
            )
            continue
        if entry.category_match_supported:
            _materialize_category(
                outcome=outcome,
                entry=entry,
                area_id=area_id,
                feature_id=feature_id,
                group=group,
                candidate_bbox=candidate_bboxes.get(area_id),
            )
            continue
        valid: list[tuple[Mapping[str, Any], float]] = []
        rejected_reasons: list[str] = []
        for row in group:
            reasons, numeric = _gate_observation(
                entry=entry,
                row=row,
                candidate_bbox=candidate_bboxes.get(area_id),
            )
            if reasons or numeric is None:
                rejected_reasons.extend(
                    reason for reason in reasons if reason not in rejected_reasons
                )
                outcome.rejected.append(
                    RejectedObservation(
                        area_id=area_id,
                        feature_id=feature_id,
                        observation_id=str(row["observation_id"]),
                        reasons=tuple(reasons),
                    )
                )
                continue
            valid.append((row, numeric))

        if not valid:
            if not rejected_reasons:
                outcome.rejected.append(
                    RejectedObservation(
                        area_id=area_id,
                        feature_id=feature_id,
                        observation_id=str(group[0]["observation_id"]),
                        reasons=("no_valid_observation",),
                    )
                )
            continue
        distinct = sorted({round(value, 12) for _, value in valid})
        if len(distinct) > 1:
            outcome.rejected.append(
                RejectedObservation(
                    area_id=area_id,
                    feature_id=feature_id,
                    observation_id=str(valid[0][0]["observation_id"]),
                    reasons=("ambiguous_duplicate_values",),
                )
            )
            continue
        row, value = min(valid, key=lambda item: str(item[0]["observation_id"]))
        outcome.values[(area_id, feature_id)] = MaterializedFeature(
            feature_id=feature_id,
            area_id=area_id,
            body=str(row.get("body") or ""),
            value=value,
            unit=str(row.get("unit") or ""),
            method_version=str(row.get("method_version") or ""),
            observation_id=str(row["observation_id"]),
            dimension_id=entry.dimension_id,
            selector=dict(entry.selector),
            support=dict(entry.support),
            dataset_ids=tuple(str(item) for item in (row.get("dataset_ids") or [])),
            quality_flags=tuple(str(item) for item in (row.get("quality_flags") or [])),
            uncertainty=_uncertainty_for(
                entry=entry,
                registry=registry,
                rows_by_key=rows_by_key,
                area_id=area_id,
                candidate_bbox=candidate_bboxes.get(area_id),
            ),
        )
    return outcome


def join_audit(
    *,
    typed_area_ids: Iterable[str],
    candidate_ids: Iterable[str],
    reference_ids: Iterable[str],
) -> dict[str, object]:
    typed = set(typed_area_ids)
    candidates = set(candidate_ids)
    references = set(reference_ids)
    typed_candidate_matches = sorted(typed & candidates)
    typed_reference_matches = sorted(typed & references)
    return {
        "join_key": "typed_areas.area_id == candidates.location_id (exact string equality)",
        "proximity_join_used": False,
        "typed_area_count": len(typed),
        "fine_candidate_count": len(candidates),
        "planetary_reference_count": len(references),
        "typed_areas_exactly_matching_fine_candidate": len(typed_candidate_matches),
        "typed_areas_exactly_matching_planetary_reference": len(typed_reference_matches),
        "matched_fine_candidate_ids": typed_candidate_matches,
        "matched_planetary_reference_ids": typed_reference_matches,
        "note": (
            "Typed records whose area_id is not an exact fine-candidate or planetary-reference id "
            "are retained as evidence but cannot silently contribute to a fine-candidate score. "
            "No nearest-location or proximity merge is performed."
        ),
    }


@dataclass(frozen=True, slots=True)
class FeatureCoverage:
    feature_id: str
    observed: ObservedFeature
    joined_candidate_ids: tuple[str, ...]
    gated_candidate_ids: tuple[str, ...]
    rejected_count: int
    filter_kind: str | None = None
    category_value_counts: dict[str, int] = field(default_factory=dict)
    filter_value_count: int = 0
    filter_value_min: float | None = None
    filter_value_max: float | None = None
    candidate_pool_size: int = 0
    coverage_fraction: float = 0.0

    def to_payload(self) -> dict[str, object]:
        return {
            **self.observed.to_payload(),
            "candidate_pool_size": self.candidate_pool_size,
            "coverage_fraction": self.coverage_fraction,
            "joined_candidate_count": len(self.joined_candidate_ids),
            "joined_candidate_ids": list(self.joined_candidate_ids),
            "gated_candidate_count": len(self.gated_candidate_ids),
            "gated_candidate_ids": list(self.gated_candidate_ids),
            "rejected_observation_count": self.rejected_count,
            "filter_kind": self.filter_kind,
            "category_value_counts": dict(sorted(self.category_value_counts.items())),
            "filter_value_count": self.filter_value_count,
            "filter_value_min": self.filter_value_min,
            "filter_value_max": self.filter_value_max,
        }


def coverage_for_registry(
    *,
    registry: Mapping[str, FeatureEligibility],
    observed: Mapping[str, ObservedFeature],
    candidate_ids: Iterable[str],
    outcome: MaterializationOutcome,
) -> dict[str, FeatureCoverage]:
    candidate_set = set(candidate_ids)
    rejected_counts = Counter(item.feature_id for item in outcome.rejected)
    gated: dict[str, set[str]] = defaultdict(set)
    for area_id, feature_id in outcome.values:
        gated[feature_id].add(area_id)
    for area_id, feature_id in outcome.categories:
        gated[feature_id].add(area_id)
    category_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for (_, feature_id), item in outcome.categories.items():
        for label in item.labels:
            category_counts[feature_id][label] += 1
    filter_values: dict[str, list[float]] = defaultdict(list)
    for (_, feature_id), materialized in outcome.values.items():
        filter_values[feature_id].append(materialized.value)
    coverage: dict[str, FeatureCoverage] = {}
    for feature_id, entry in registry.items():
        item = observed.get(feature_id) or ObservedFeature(feature_id=feature_id)
        derived = (
            filter_values.get(feature_id, [])
            if entry.support.get("filter_value_semantics") == "period_mean_of_stored_timeseries"
            else []
        )
        coverage[feature_id] = FeatureCoverage(
            feature_id=feature_id,
            observed=item,
            joined_candidate_ids=tuple(sorted(item.area_ids & candidate_set)),
            gated_candidate_ids=tuple(sorted(gated.get(feature_id, set()))),
            rejected_count=rejected_counts.get(feature_id, 0),
            filter_kind=entry.filter_kind,
            category_value_counts=dict(category_counts.get(feature_id, {})),
            filter_value_count=len(derived),
            filter_value_min=min(derived) if derived else None,
            filter_value_max=max(derived) if derived else None,
            candidate_pool_size=len(candidate_set),
            coverage_fraction=(
                round(len(gated.get(feature_id, set())) / len(candidate_set), 6)
                if candidate_set
                else 0.0
            ),
        )
    return coverage
