import { vi } from "vitest";
import type { AoiAnalyzeRequest, ReferenceOut, SearchRequest } from "../api/client";
import {
  analyzeQueuedFixture,
  aoiLocationFixture,
  capabilitiesFixture,
  contextFixture,
  datasetFixture,
  jobCompletedFixture,
  locationFixture,
  searchResponseFixture,
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
  search?: ResponseFactory<[SearchRequest]>;
  location?: ResponseFactory<[string]>;
  context?: ResponseFactory<[string, string | null]>;
  analyze?: ResponseFactory<[AoiAnalyzeRequest]>;
  job?: ResponseFactory<[string]>;
  areaFeatures?: ResponseFactory<[string]>;
  compare?: ResponseFactory<[string]>;
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
        : jsonResponse({
            contract_version: "1.0.0",
            method_version: "terrain-metrics-1.0.0",
            dimensions: [
              {
                dimension_id: "terrain_shape",
                label: "Terrain shape",
                description: "Local terrain shape.",
                score_role: "similarity",
                features: [],
              },
            ],
            context_features: capabilitiesFixture.context_features,
          });
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

    if (url.endsWith("/api/v1/search")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as SearchRequest;
      return handlers.search
        ? await handlers.search(body)
        : jsonResponse(searchResponseFixture);
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
