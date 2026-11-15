import { vi } from "vitest";
import type {
  AoiAnalyzeRequest,
  GlobalSearchRequest,
  ReferenceOut,
  RefinePlanRequest,
  SearchRequest,
} from "../api/client";
import {
  analyzeQueuedFixture,
  aoiLocationFixture,
  capabilitiesFixture,
  contextFixture,
  datasetFixture,
  dimensionsFixture,
  globalCandidateFixture,
  globalCoverageFixture,
  globalSearchResponseFixture,
  jobCompletedFixture,
  locationFixture,
  refinePlanFixture,
  searchResponseFixture,
  typedAreaFixture,
  typedCoverageFixture,
} from "./fixtures";

export function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

export function errorResponse(
  status: number,
  code: string,
  message: string,
  details: { [key: string]: unknown }[] = [],
): Response {
  return jsonResponse({ error: { code, message, details } }, status);
}

type ResponseFactory<Args extends unknown[] = []> = (
  ...args: Args
) => Response | Promise<Response>;

export type FetchHandlers = {
  capabilities?: ResponseFactory;
  references?: ResponseFactory;
  datasets?: ResponseFactory;
  dimensions?: ResponseFactory;
  typedCoverage?: ResponseFactory;
  typedObservations?: ResponseFactory<[string]>;
  typedArea?: ResponseFactory<[string]>;
  search?: ResponseFactory<[SearchRequest]>;
  searchFeatures?: ResponseFactory;
  multidim?: ResponseFactory<[unknown]>;
  location?: ResponseFactory<[string]>;
  context?: ResponseFactory<[string, string | null]>;
  analyze?: ResponseFactory<[AoiAnalyzeRequest]>;
  job?: ResponseFactory<[string]>;
  areaFeatures?: ResponseFactory<[string]>;
  compare?: ResponseFactory<[string]>;
  globalCoverage?: ResponseFactory;
  globalSearch?: ResponseFactory<[GlobalSearchRequest]>;
  globalCandidate?: ResponseFactory<[string]>;
  refinePlan?: ResponseFactory<[RefinePlanRequest]>;
  refine?: ResponseFactory<[string]>;
};

function urlOf(input: string | URL | Request): string {
  if (typeof input === "string") {
    return input;
  }
  if (input instanceof URL) {
    return input.toString();
  }
  return input.url;
}

export function installMockFetch(handlers: FetchHandlers = {}) {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = urlOf(input);

    if (url.endsWith("/api/v1/capabilities")) {
      return handlers.capabilities
        ? await handlers.capabilities()
        : jsonResponse(capabilitiesFixture);
    }

    if (url.endsWith("/api/v1/references")) {
      return handlers.references ? await handlers.references() : jsonResponse([]);
    }

    if (url.endsWith("/api/v1/datasets")) {
      return handlers.datasets
        ? await handlers.datasets()
        : jsonResponse([datasetFixture]);
    }

    if (url.endsWith("/api/v1/dimensions")) {
      return handlers.dimensions
        ? await handlers.dimensions()
        : jsonResponse(dimensionsFixture);
    }

    if (url.endsWith("/api/v1/typed/coverage")) {
      return handlers.typedCoverage
        ? await handlers.typedCoverage()
        : jsonResponse(typedCoverageFixture);
    }

    if (url.includes("/api/v1/typed/areas/")) {
      const match = url.match(/\/api\/v1\/typed\/areas\/([^/?]+)$/);
      const areaId = decodeURIComponent(match?.[1] ?? "");
      return handlers.typedArea
        ? await handlers.typedArea(areaId)
        : jsonResponse({ ...typedAreaFixture, area_id: areaId });
    }

    if (url.includes("/api/v1/typed/observations")) {
      const parsed = new URL(url, "http://localhost");
      const dimension = parsed.searchParams.get("dimension") ?? "";
      const observations = dimension
        ? typedAreaFixture.observations.filter(
            (observation) => observation.dimension_code === dimension,
          )
        : typedAreaFixture.observations;
      return handlers.typedObservations
        ? await handlers.typedObservations(dimension)
        : jsonResponse(observations);
    }

    if (url.endsWith("/api/v1/areas/analyze")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as AoiAnalyzeRequest;
      return handlers.analyze
        ? await handlers.analyze(body)
        : jsonResponse(analyzeQueuedFixture, 202);
    }

    if (url.includes("/api/v1/areas/") && url.endsWith("/compare")) {
      const match = url.match(/\/api\/v1\/areas\/([^/]+)\/compare$/);
      return handlers.compare
        ? await handlers.compare(decodeURIComponent(match?.[1] ?? ""))
        : jsonResponse(searchResponseFixture);
    }

    if (url.includes("/api/v1/areas/") && url.endsWith("/features")) {
      const match = url.match(/\/api\/v1\/areas\/([^/]+)\/features$/);
      const areaId = decodeURIComponent(match?.[1] ?? "");
      return handlers.areaFeatures
        ? await handlers.areaFeatures(areaId)
        : jsonResponse({ ...aoiLocationFixture, location_id: areaId });
    }

    if (url.includes("/api/v1/jobs/")) {
      const match = url.match(/\/api\/v1\/jobs\/([^/?]+)$/);
      return handlers.job
        ? await handlers.job(decodeURIComponent(match?.[1] ?? ""))
        : jsonResponse(jobCompletedFixture);
    }

    if (url.endsWith("/api/v1/search/features")) {
      return handlers.searchFeatures
        ? await handlers.searchFeatures()
        : errorResponse(404, "not_found", "No feature registry in this test.");
    }

    if (url.endsWith("/api/v1/search/multidimensional")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as unknown;
      return handlers.multidim
        ? await handlers.multidim(body)
        : errorResponse(404, "not_found", "No multidimensional search in this test.");
    }

    if (url.endsWith("/api/v1/search")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as SearchRequest;
      return handlers.search
        ? await handlers.search(body)
        : jsonResponse(searchResponseFixture);
    }

    if (url.endsWith("/api/v1/global/coverage")) {
      return handlers.globalCoverage
        ? await handlers.globalCoverage()
        : jsonResponse(globalCoverageFixture);
    }

    if (url.endsWith("/api/v1/global/search")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as GlobalSearchRequest;
      return handlers.globalSearch
        ? await handlers.globalSearch(body)
        : jsonResponse(globalSearchResponseFixture);
    }

    if (url.includes("/api/v1/global/candidates/")) {
      const match = url.match(/\/api\/v1\/global\/candidates\/([^/?]+)$/);
      const candidateId = decodeURIComponent(match?.[1] ?? "");
      return handlers.globalCandidate
        ? await handlers.globalCandidate(candidateId)
        : jsonResponse(globalCandidateFixture(candidateId));
    }

    if (url.endsWith("/api/v1/global/refine-plan")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as RefinePlanRequest;
      return handlers.refinePlan
        ? await handlers.refinePlan(body)
        : jsonResponse({ ...refinePlanFixture, candidate_id: body.candidate_id });
    }

    if (url.endsWith("/api/v1/global/refine")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as { candidate_id: string };
      return handlers.refine
        ? await handlers.refine(body.candidate_id)
        : errorResponse(503, "service_unavailable", "No refinement fixture configured.");
    }

    if (url.includes("/context")) {
      const parsed = new URL(url, "http://localhost");
      const match = parsed.pathname.match(/\/api\/v1\/locations\/([^/]+)\/context$/);
      const target = parsed.searchParams.get("target");
      return handlers.context
        ? await handlers.context(decodeURIComponent(match?.[1] ?? ""), target)
        : jsonResponse(contextFixture);
    }

    const locationMatch = url.match(/\/api\/v1\/locations\/([^/?]+)$/);
    if (locationMatch) {
      const locationId = decodeURIComponent(locationMatch[1]);
      return handlers.location
        ? await handlers.location(locationId)
        : jsonResponse(locationFixture(locationId));
    }

    return errorResponse(404, "not_found", `Unexpected request in test: ${url}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export type ReferenceList = ReferenceOut[];

export function searchRequestFromCalls(
  fetchMock: ReturnType<typeof installMockFetch>,
): SearchRequest | null {
  const call = fetchMock.mock.calls.find(([input]) =>
    urlOf(input).endsWith("/api/v1/search"),
  );
  if (!call) {
    return null;
  }
  return JSON.parse(String((call[1] as RequestInit | undefined)?.body ?? "{}")) as SearchRequest;
}

export function contextUrlFromCalls(
  fetchMock: ReturnType<typeof installMockFetch>,
): string | null {
  const call = fetchMock.mock.calls.find(([input]) =>
    urlOf(input).includes("/context"),
  );
  return call ? urlOf(call[0]) : null;
}

export function locationCallCount(
  fetchMock: ReturnType<typeof installMockFetch>,
  locationId: string,
): number {
  return fetchMock.mock.calls.filter(([input]) => {
    const url = urlOf(input);
    return (
      url.endsWith(`/api/v1/locations/${encodeURIComponent(locationId)}`) &&
      !url.includes("/context")
    );
  }).length;
}
