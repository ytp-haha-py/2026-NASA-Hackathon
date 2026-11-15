import type { components } from "./schema";

export type BBoxOut = components["schemas"]["BBoxOut"];
export type AoiAnalyzeRequest = components["schemas"]["AoiAnalyzeRequest"];
export type AoiAnalyzeResponse = components["schemas"]["AoiAnalyzeResponse"];
export type CapabilitiesOut = components["schemas"]["CapabilitiesOut"];
export type ContextFeatureOut = components["schemas"]["ContextFeatureOut"];
export type ContextObservationOut = components["schemas"]["ContextObservationOut"];
export type ContextOut = components["schemas"]["ContextOut"];
export type DatasetOut = components["schemas"]["DatasetOut"];
export type DimensionOut = components["schemas"]["DimensionOut"];
export type DimensionsOut = components["schemas"]["DimensionsOut"];
export type ErrorOut = components["schemas"]["ErrorOut"];
export type FeatureObservationOut = components["schemas"]["FeatureObservationOut"];
export type FeatureOut = components["schemas"]["FeatureOut"];
export type GeoBBoxIn = components["schemas"]["GeoBBoxIn"];
export type GlobalCoverageOut = components["schemas"]["GlobalCoverageOut"];
export type GlobalSearchRequest = components["schemas"]["GlobalSearchRequest"];
export type GlobalSearchResponse = components["schemas"]["GlobalSearchResponse"];
export type GlobalSearchResultOut = components["schemas"]["GlobalSearchResultOut"];
export type JobOut = components["schemas"]["JobOut"];
export type LocationOut = components["schemas"]["LocationOut"];
export type MetricOut = components["schemas"]["MetricOut"];
export type ReferenceOut = components["schemas"]["ReferenceOut"];
export type RangeFilterIn = components["schemas"]["RangeFilterIn"];
export type RefinePlanOut = components["schemas"]["RefinePlanOut"];
export type RefinePlanRequest = components["schemas"]["RefinePlanRequest"];
export type RefineWindowOut = components["schemas"]["RefineWindowOut"];
export type SearchRequest = components["schemas"]["SearchRequest"];
export type SearchResponse = components["schemas"]["SearchResponse"];
export type SearchResultOut = components["schemas"]["SearchResultOut"];
export type TargetSpec = components["schemas"]["TargetSpec"];

/**
 * Typed-v2 responses are intentionally open (`additionalProperties: true`) in the committed
 * OpenAPI contract. These structural types mirror the committed v2 JSON schemas in
 * `contracts/v2/` and `pipelines/src/analog_pipelines/typed_contracts.py`; unknown extra
 * fields remain available through the index signatures.
 */
export type TypedObservationState =
  | "detected"
  | "not_detected"
  | "no_coverage"
  | "unknown"
  | "not_applicable";

export type TypedValueKind =
  | "numeric"
  | "category"
  | "distribution"
  | "detection"
  | "timeseries"
  | "spectrum"
  | "process_evidence";

export type TypedScoreRole =
  | "similarity"
  | "context_only"
  | "filter"
  | "evidence_only"
  | "not_comparable";

export type TypedSpatialSupport = {
  kind?: string;
  crs?: string;
  resolution_m?: number | null;
  window_size_m?: number | null;
  bbox?: Record<string, number> | null;
  bbox_crs?: string | null;
  area_km2?: number | null;
  source_resolution_m?: number | null;
  native_resolution_m?: number | null;
  valid_fraction?: number | null;
  notes?: string | null;
  [key: string]: unknown;
};

export type TypedTemporalSupport = {
  kind?: string;
  start?: string | null;
  end?: string | null;
  local_solar_time?: string | null;
  notes?: string | null;
  [key: string]: unknown;
};

export type TypedDepthSupport = {
  kind?: string;
  min_m?: number | null;
  max_m?: number | null;
  [key: string]: unknown;
};

export type TypedUncertainty = {
  kind?: string;
  value?: number | null;
  lower?: number | null;
  upper?: number | null;
  unit?: string | null;
  notes?: string | null;
  [key: string]: unknown;
};

export type TypedObservationOut = {
  observation_id: string;
  area_id: string;
  body: string;
  dimension_code: string | null;
  feature_id: string;
  value_kind: TypedValueKind | string;
  state: TypedObservationState | string;
  value: unknown;
  unit: string | null;
  method_version: string;
  dataset_ids: string[];
  source_asset_ids: string[];
  quality_flags: string[];
  score_role: TypedScoreRole | string;
  spatial_support: TypedSpatialSupport;
  temporal_support: TypedTemporalSupport;
  depth_support: TypedDepthSupport;
  uncertainty: TypedUncertainty | null;
  notes: string | null;
  [key: string]: unknown;
};

export type TypedAreaOut = {
  area_id: string;
  body: string;
  bbox: Record<string, number> | null;
  crs: string;
  coordinate_frame: string;
  area_km2: number | null;
  observations: TypedObservationOut[];
  [key: string]: unknown;
};

export type TypedDimensionCoverage = {
  total: number;
  by_body: Record<string, number>;
  by_state: Record<string, number>;
};

export type TypedCoverageOut = {
  total: number;
  by_dimension: Record<string, TypedDimensionCoverage>;
  scope_note: string;
  [key: string]: unknown;
};

export type GlobalCandidateOut = {
  candidate_id: string;
  grid_id: string;
  region_id: string;
  body: string;
  bbox: Record<string, number>;
  crs: string;
  area_km2: number;
  window_size_m: number;
  stride_m: number;
  source_resolution_m: number;
  slope_mean_deg: number | null;
  slope_p90_deg: number | null;
  flat_fraction: number | null;
  valid_fraction: number;
  method_version: string;
  grid_version: string;
  dataset_id: string;
  source_asset_id: string;
  qa_flags: string[];
  [key: string]: unknown;
};

export type NumericTarget = Record<string, number>;

export const DEFAULT_PUBLIC_API_BASE = "https://api.貓熊.台灣/api/v1";

const configuredApiBase = import.meta.env.VITE_API_BASE;
// Development always uses the same-origin Vite proxy so the local-first/remote-fallback decision
// stays in one place. VITE_API_BASE is a production-only setting; VITE_API_PROXY_TARGET is the
// development override.
const API_BASE = import.meta.env.PROD
  ? (configuredApiBase && configuredApiBase.trim().replace(/\/$/, "")) ||
    DEFAULT_PUBLIC_API_BASE
  : "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: { [key: string]: unknown }[];

  constructor(
    status: number,
    code: string,
    message: string,
    details: { [key: string]: unknown }[] = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function isErrorOut(value: unknown): value is ErrorOut {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error = (value as { error?: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof (error as { code?: unknown }).code === "string" &&
    "message" in error &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    signal,
    headers: {
      Accept: "application/json",
      ...(init.headers ?? {}),
    },
  });

  if (!response.ok) {
    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch {
      parsed = null;
    }

    if (isErrorOut(parsed)) {
      const details = Array.isArray(parsed.error.details) ? parsed.error.details : [];
      throw new ApiError(
        response.status,
        parsed.error.code,
        parsed.error.message,
        details,
      );
    }

    throw new ApiError(
      response.status,
      `http_${response.status}`,
      `The API returned HTTP ${response.status} without a documented error body.`,
    );
  }

  return (await response.json()) as T;
}

export function getCapabilities(signal?: AbortSignal): Promise<CapabilitiesOut> {
  return request<CapabilitiesOut>("/capabilities", {}, signal);
}

export function getReferences(signal?: AbortSignal): Promise<ReferenceOut[]> {
  return request<ReferenceOut[]>("/references", {}, signal);
}

export function getDatasets(signal?: AbortSignal): Promise<DatasetOut[]> {
  return request<DatasetOut[]>("/datasets", {}, signal);
}

export function searchCandidates(
  body: SearchRequest,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  return request<SearchResponse>(
    "/search",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    signal,
  );
}

export function getLocation(
  locationId: string,
  signal?: AbortSignal,
): Promise<LocationOut> {
  return request<LocationOut>(
    `/locations/${encodeURIComponent(locationId)}`,
    {},
    signal,
  );
}

export function getLocationContext(
  locationId: string,
  target: NumericTarget,
  signal?: AbortSignal,
): Promise<ContextOut> {
  const params = new URLSearchParams();
  if (Object.keys(target).length > 0) {
    params.set("target", JSON.stringify(target));
  }
  const query = params.toString();
  return request<ContextOut>(
    `/locations/${encodeURIComponent(locationId)}/context${query ? `?${query}` : ""}`,
    {},
    signal,
  );
}

export function analyzeArea(
  body: AoiAnalyzeRequest,
  signal?: AbortSignal,
): Promise<AoiAnalyzeResponse> {
  return request<AoiAnalyzeResponse>(
    "/areas/analyze",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    signal,
  );
}

export function getJob(jobId: string, signal?: AbortSignal): Promise<JobOut> {
  return request<JobOut>(`/jobs/${encodeURIComponent(jobId)}`, {}, signal);
}

export function getAreaFeatures(
  areaId: string,
  signal?: AbortSignal,
): Promise<LocationOut> {
  return request<LocationOut>(
    `/areas/${encodeURIComponent(areaId)}/features`,
    {},
    signal,
  );
}

export function compareArea(
  areaId: string,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  return request<SearchResponse>(
    `/areas/${encodeURIComponent(areaId)}/compare`,
    { method: "POST" },
    signal,
  );
}

export function getDimensions(signal?: AbortSignal): Promise<DimensionsOut> {
  return request<DimensionsOut>("/dimensions", {}, signal);
}

export function getTypedCoverage(signal?: AbortSignal): Promise<TypedCoverageOut> {
  return request<TypedCoverageOut>("/typed/coverage", {}, signal);
}

export type TypedObservationQuery = {
  dimension?: string;
  body?: string;
  featureId?: string;
};

export function getTypedObservations(
  query: TypedObservationQuery = {},
  signal?: AbortSignal,
): Promise<TypedObservationOut[]> {
  const params = new URLSearchParams();
  if (query.dimension) {
    params.set("dimension", query.dimension);
  }
  if (query.body) {
    params.set("body", query.body);
  }
  if (query.featureId) {
    params.set("feature_id", query.featureId);
  }
  const suffix = params.toString();
  return request<TypedObservationOut[]>(
    `/typed/observations${suffix ? `?${suffix}` : ""}`,
    {},
    signal,
  );
}

export function getTypedArea(
  areaId: string,
  signal?: AbortSignal,
): Promise<TypedAreaOut> {
  return request<TypedAreaOut>(`/typed/areas/${encodeURIComponent(areaId)}`, {}, signal);
}

export function getGlobalCoverage(signal?: AbortSignal): Promise<GlobalCoverageOut> {
  return request<GlobalCoverageOut>("/global/coverage", {}, signal);
}

export function searchGlobalCandidates(
  body: GlobalSearchRequest,
  signal?: AbortSignal,
): Promise<GlobalSearchResponse> {
  return request<GlobalSearchResponse>(
    "/global/search",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    signal,
  );
}

export function getGlobalCandidate(
  candidateId: string,
  signal?: AbortSignal,
): Promise<GlobalCandidateOut> {
  return request<GlobalCandidateOut>(
    `/global/candidates/${encodeURIComponent(candidateId)}`,
    {},
    signal,
  );
}

export function createRefinePlan(
  body: RefinePlanRequest,
  signal?: AbortSignal,
): Promise<RefinePlanOut> {
  return request<RefinePlanOut>(
    "/global/refine-plan",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    signal,
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "An unknown error occurred.";
}

export function errorCode(error: unknown): string | null {
  return error instanceof ApiError ? error.code : null;
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}
