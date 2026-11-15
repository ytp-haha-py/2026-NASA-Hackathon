import type {
  FeatureOut,
  FeatureRegistryOut,
  MultiDimSearchResponse,
  SearchResponse,
} from "../api/client";

/**
 * Registry features outside D01 that the API accepts as similarity targets (today: Earth
 * SoilGrids means in D06). Shaped as FeatureOut so the dimension board can render them like
 * D01 metrics; `dimension_id` keeps the registry code (D02–D06).
 */
export function queryFeaturesBeyondD01(registry: FeatureRegistryOut | null): FeatureOut[] {
  if (!registry) {
    return [];
  }
  return registry.features
    .filter(
      (feature) =>
        feature.dimension_id !== "D01" &&
        feature.value_kind === "numeric" &&
        feature.roles.similarity?.supported === true &&
        feature.min_value !== null &&
        feature.max_value !== null &&
        feature.scale !== null,
    )
    .sort((a, b) => soilOrder(a.feature_id) - soilOrder(b.feature_id))
    .map((feature) => ({
      id: feature.feature_id,
      label: feature.label,
      dimension_id: feature.dimension_id,
      unit: feature.unit,
      min_value: feature.min_value as number,
      max_value: feature.max_value as number,
      scale: feature.scale as number,
      scale_rationale: feature.scale_rationale ?? "",
      score_role: "similarity",
      value_kind: "numeric",
      description: "",
      comparability_gate: feature.comparability_gates.join("; "),
      extraction_scale_m: 0,
      method: "",
      method_version: feature.method_versions[0] ?? "",
      missing_policy: feature.missing_policy,
      provider_coverage_by_body: Object.fromEntries(
        feature.body_scope.map((body) => [body, "supported"]),
      ),
      spatial_support: "",
      temporal_support: "",
      valid_domain: `${feature.min_value}–${feature.max_value} ${feature.unit}`,
    }));
}

const SOIL_ID = /^soil_(bdod|clay|sand|silt|soc)_(\d+)_(\d+)cm_mean$/;
const SOIL_PROPERTY_ORDER = ["clay", "sand", "silt", "bdod", "soc"];

/** Texture first, then density and carbon; shallow depths before deep ones. Others keep order. */
function soilOrder(featureId: string): number {
  const match = SOIL_ID.exec(featureId);
  if (!match) {
    return Number.MAX_SAFE_INTEGER;
  }
  return SOIL_PROPERTY_ORDER.indexOf(match[1]) * 1000 + Number(match[2]);
}

/** Splits a SoilGrids feature id into its property and depth, e.g. clay, "0–5 cm". */
export function soilFeatureParts(
  featureId: string,
): { property: "bdod" | "clay" | "sand" | "silt" | "soc"; depth: string } | null {
  const match = SOIL_ID.exec(featureId);
  if (!match) {
    return null;
  }
  return {
    property: match[1] as "bdod" | "clay" | "sand" | "silt" | "soc",
    depth: `${match[2]}–${match[3]} cm`,
  };
}

/**
 * Presents a multidimensional search response in the shape the result views already use.
 * Values are copied, not derived: per-feature contributions become metrics and per-dimension
 * distances become dimension_distances.
 */
export function adaptMultiDimResponse(response: MultiDimSearchResponse): SearchResponse {
  return {
    contract_version: response.registry_version,
    method_version: response.scoring?.version ?? response.registry_version,
    query_version: response.registry_version,
    data_snapshot_id: response.data_snapshot_id,
    data_version: response.data_version,
    coverage: {
      data_kinds: [],
      description: response.coverage.scope_note,
      geographic_bbox: null,
      indexed_candidate_count: response.candidate_pool_size,
      label: response.coverage.scope_note,
      origins: {},
    },
    coverage_label: response.coverage.scope_note,
    indexed_candidate_count: response.candidate_pool_size,
    eligible_candidate_count: response.eligible_candidate_count,
    excluded_candidate_count: response.excluded_candidate_count,
    excluded: response.excluded.map((item) => ({
      location_id: item.location_id,
      reason: item.reason,
    })),
    filters_applied: response.filters_applied,
    limitations: response.limitations,
    requested_features: response.requested_features,
    results: response.results.map((result) => ({
      rank: result.rank,
      location_id: result.location_id,
      display_name: result.display_name,
      lat: result.lat,
      lon: result.lon,
      bbox: result.bbox,
      distance: result.distance,
      data_kind: result.data_kind,
      quality_flags: result.quality_flags,
      caveats: [],
      dataset_ids: [],
      dimension_distances: Object.fromEntries(
        Object.entries(result.dimension_contributions).map(([dimension, value]) => [
          dimension,
          value.distance,
        ]),
      ),
      metrics: result.contributions.map((contribution) => ({
        feature_id: contribution.feature_id,
        unit: contribution.unit,
        target: contribution.target,
        candidate: contribution.candidate,
        delta: contribution.delta,
        scale: contribution.scale,
        weight: contribution.weight,
        normalized_delta: contribution.normalized_delta,
        contribution: contribution.contribution,
      })),
    })),
  };
}
