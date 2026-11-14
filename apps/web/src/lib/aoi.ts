export type ProviderStatus = {
  status?: string;
  role?: string;
  reason?: string;
  message?: string;
  source_url?: string;
  tile?: string;
  tiles?: number;
  valid_pixels?: number;
};

export type AoiLimits = {
  maxSideM: number;
  minSideM: number;
  maxAreaKm2: number;
  maxPolygonVertices: number;
  analysisResolutionM: number;
  requestedFeatures: string[];
  supportedBody: string;
  note: string;
  providerStatus: Record<string, ProviderStatus>;
};

const FALLBACK_LIMITS: AoiLimits = {
  maxSideM: 2000,
  minSideM: 600,
  maxAreaKm2: 4,
  maxPolygonVertices: 100,
  analysisResolutionM: 90,
  requestedFeatures: [
    "slope_mean_deg",
    "slope_p90_deg",
    "terrain_relief_p95_p05_m",
    "roughness_std_m",
  ],
  supportedBody: "Earth",
  note: "On-demand AOIs are bounded to a 2 km-class footprint so terrain support remains comparable to the indexed windows.",
  providerStatus: {},
};

export const AOI_LATITUDE_MIN = -80;
export const AOI_LATITUDE_MAX = 84;

function finiteNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringList(value: unknown, fallback: string[]): string[] {
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return value;
  }
  return fallback;
}

export function readAoiLimits(analysis: Record<string, unknown>): AoiLimits {
  const rawProviderStatus = analysis.provider_status;
  const providerStatus: Record<string, ProviderStatus> = {};
  if (
    typeof rawProviderStatus === "object" &&
    rawProviderStatus !== null &&
    !Array.isArray(rawProviderStatus)
  ) {
    for (const [key, value] of Object.entries(rawProviderStatus)) {
      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        providerStatus[key] = value as ProviderStatus;
      }
    }
  }

  return {
    maxSideM: finiteNumber(analysis.max_side_m, FALLBACK_LIMITS.maxSideM),
    minSideM: finiteNumber(analysis.min_side_m, FALLBACK_LIMITS.minSideM),
    maxAreaKm2: finiteNumber(analysis.max_area_km2, FALLBACK_LIMITS.maxAreaKm2),
    maxPolygonVertices: finiteNumber(
      analysis.max_polygon_vertices,
      FALLBACK_LIMITS.maxPolygonVertices,
    ),
    analysisResolutionM: finiteNumber(
      analysis.analysis_resolution_m,
      FALLBACK_LIMITS.analysisResolutionM,
    ),
    requestedFeatures: stringList(
      analysis.requested_features,
      FALLBACK_LIMITS.requestedFeatures,
    ),
    supportedBody:
      typeof analysis.supported_body === "string"
        ? analysis.supported_body
        : FALLBACK_LIMITS.supportedBody,
    note: typeof analysis.note === "string" ? analysis.note : FALLBACK_LIMITS.note,
    providerStatus,
  };
}

export type LonLatBBox = {
  west: number;
  south: number;
  east: number;
  north: number;
};

const EARTH_RADIUS_M = 6_371_008.8;

function haversineM(
  lon1: number,
  lat1: number,
  lon2: number,
  lat2: number,
): number {
  const toRadians = Math.PI / 180;
  const dLat = (lat2 - lat1) * toRadians;
  const dLon = (lon2 - lon1) * toRadians;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * toRadians) * Math.cos(lat2 * toRadians) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function bboxSideLengthsM(bbox: LonLatBBox): {
  widthM: number;
  heightM: number;
} {
  const midLat = (bbox.south + bbox.north) / 2;
  const midLon = (bbox.west + bbox.east) / 2;
  return {
    widthM: haversineM(bbox.west, midLat, bbox.east, midLat),
    heightM: haversineM(midLon, bbox.south, midLon, bbox.north),
  };
}

export function bboxAreaKm2(bbox: LonLatBBox): number {
  const { widthM, heightM } = bboxSideLengthsM(bbox);
  return (widthM * heightM) / 1_000_000;
}

export function providerStatusEntries(
  providerStatus: Record<string, unknown> | Record<string, ProviderStatus> | null | undefined,
): [string, ProviderStatus][] {
  if (!providerStatus || typeof providerStatus !== "object") {
    return [];
  }
  return Object.entries(providerStatus).map(([name, value]) => {
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      return [name, value as ProviderStatus];
    }
    return [name, { status: String(value) }];
  });
}

export function jobWarnings(result: Record<string, unknown> | null | undefined): string[] {
  if (!result || !Array.isArray(result.warnings)) {
    return [];
  }
  return result.warnings.filter((item): item is string => typeof item === "string");
}

export function jobResultNumber(
  result: Record<string, unknown> | null | undefined,
  key: string,
): number | null {
  if (!result) {
    return null;
  }
  const value = result[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
