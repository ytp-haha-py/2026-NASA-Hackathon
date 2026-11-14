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
export type JobOut = components["schemas"]["JobOut"];
export type LocationOut = components["schemas"]["LocationOut"];
export type MetricOut = components["schemas"]["MetricOut"];
export type ReferenceOut = components["schemas"]["ReferenceOut"];
export type RangeFilterIn = components["schemas"]["RangeFilterIn"];
export type SearchRequest = components["schemas"]["SearchRequest"];
export type SearchResponse = components["schemas"]["SearchResponse"];
export type SearchResultOut = components["schemas"]["SearchResultOut"];
export type TargetSpec = components["schemas"]["TargetSpec"];

export type NumericTarget = Record<string, number>;

const API_BASE = "/api/v1";

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
