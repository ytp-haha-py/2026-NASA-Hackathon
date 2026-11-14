import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  errorCode,
  errorMessage,
  getCapabilities,
  getDatasets,
  getReferences,
  isAbortError,
  searchCandidates,
  type CapabilitiesOut,
  type DatasetOut,
  type NumericTarget,
  type ReferenceOut,
  type SearchResponse,
  type SearchResultOut,
} from "./api/client";
import { useAoiAnalysis } from "./hooks/useAoiAnalysis";
import { formatBBox } from "./lib/format";
import type { SearchSubmission, SearchTargetPreset } from "./lib/search-types";
import { AoiForm } from "./components/AoiForm";
import { AoiWorkspace } from "./components/AoiWorkspace";
import { DetailPanel } from "./components/DetailPanel";
import { MapPanel } from "./components/MapPanel";
import { ResultsPanel } from "./components/ResultsPanel";
import { SearchForm } from "./components/SearchForm";
import { SourcesAndMethods } from "./components/SourcesAndMethods";

type SearchErrorState = {
  message: string;
  code: string | null;
  status: number | null;
  details: { [key: string]: unknown }[];
};

type AnalysisPanel = "search" | "aoi";

type AppProps = {
  aoiPollIntervalMs?: number;
};

export function App({ aoiPollIntervalMs = 1000 }: AppProps) {
  const [capabilities, setCapabilities] = useState<CapabilitiesOut | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState<string | null>(null);
  const [capabilitiesLoading, setCapabilitiesLoading] = useState(true);
  const [references, setReferences] = useState<ReferenceOut[]>([]);
  const [referencesError, setReferencesError] = useState<string | null>(null);
  const [datasets, setDatasets] = useState<DatasetOut[]>([]);
  const [datasetsError, setDatasetsError] = useState<string | null>(null);
  const [bootstrapKey, setBootstrapKey] = useState(0);
  const [activePanel, setActivePanel] = useState<AnalysisPanel>("search");
  const [searchPreset, setSearchPreset] = useState<SearchTargetPreset | null>(null);
  const presetNonce = useRef(0);
  const aoi = useAoiAnalysis(aoiPollIntervalMs);

  const [searching, setSearching] = useState(false);
  const [searchResponse, setSearchResponse] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState<SearchErrorState | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTarget, setActiveTarget] = useState<NumericTarget>({});
  const searchController = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => searchController.current?.abort();
  }, []);

  const aoiLocationId = aoi.location?.location_id ?? null;
  useEffect(() => {
    if (aoiLocationId) {
      setBootstrapKey((key) => key + 1);
    }
  }, [aoiLocationId]);

  useEffect(() => {
    const controller = new AbortController();
    setCapabilitiesLoading(true);
    setCapabilitiesError(null);
    setReferencesError(null);
    setDatasetsError(null);

    getCapabilities(controller.signal)
      .then((payload) => {
        setCapabilities(payload);
      })
      .catch((caught: unknown) => {
        if (!isAbortError(caught)) {
          setCapabilitiesError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setCapabilitiesLoading(false);
        }
      });

    getReferences(controller.signal)
      .then((payload) => setReferences(payload))
      .catch((caught: unknown) => {
        if (!isAbortError(caught)) {
          setReferences([]);
          setReferencesError(errorMessage(caught));
        }
      });

    getDatasets(controller.signal)
      .then((payload) => setDatasets(payload))
      .catch((caught: unknown) => {
        if (!isAbortError(caught)) {
          setDatasets([]);
          setDatasetsError(errorMessage(caught));
        }
      });

    return () => controller.abort();
  }, [bootstrapKey]);

  async function handleSearch(submission: SearchSubmission) {
    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;

    setSearching(true);
    setSearchError(null);
    setSearchResponse(null);
    setSelectedId(null);
    setActiveTarget(submission.target);

    try {
      const response = await searchCandidates(submission.request, controller.signal);
      if (!controller.signal.aborted) {
        setSearchResponse(response);
        if (response.results.length > 0) {
          const returnedTarget = Object.fromEntries(
            response.results[0].metrics.map((metric) => [
              metric.feature_id,
              metric.target,
            ]),
          );
          if (Object.keys(returnedTarget).length > 0) {
            setActiveTarget(returnedTarget);
          }
        }
      }
    } catch (caught: unknown) {
      if (isAbortError(caught)) {
        return;
      }
      setSearchError({
        message: errorMessage(caught),
        code: errorCode(caught),
        status: caught instanceof ApiError ? caught.status : null,
        details: caught instanceof ApiError ? caught.details : [],
      });
    } finally {
      if (!controller.signal.aborted) {
        setSearching(false);
      }
    }
  }

  const selectedResult: SearchResultOut | null =
    searchResponse?.results.find((result) => result.location_id === selectedId) ?? null;

  function handleUseAoiAsTarget(values: NumericTarget) {
    presetNonce.current += 1;
    setSearchPreset({
      nonce: presetNonce.current,
      values,
      sourceLabel: `on-demand AOI ${aoi.location?.location_id ?? ""}`.trim(),
    });
    setActivePanel("search");
  }

  const coverageOrigins = capabilities?.coverage.origins;
  const indexedOriginCount = coverageOrigins?.indexed ?? 0;
  const onDemandOriginCount = coverageOrigins?.on_demand ?? 0;

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>

      <header className="site-header">
        <div className="header-inner">
          <p className="eyebrow">Terrain-shape similarity · indexed + on-demand Earth</p>
          <h1>Planetary Analog Explorer</h1>
          <p className="header-summary">
            Compare a hypothetical terrain-shape target with a committed Earth index, or
            analyze a bounded on-demand AOI. Land-cover context is shown separately and is
            never part of the similarity score. This is not a global survey, not a base-site
            recommendation, and not a physical-equivalence claim.
          </p>
        </div>
      </header>

      <main id="main-content" className="app-main">
        <div className="search-column">
          {capabilitiesLoading && !capabilities && (
            <p className="status-panel" role="status">
              Loading capabilities and indexed coverage…
            </p>
          )}

          {capabilitiesError && (
            <div className="alert alert-error" role="alert">
              <p>
                <strong>Capabilities could not be loaded.</strong> {capabilitiesError}
              </p>
              <p>
                The search form needs GET /api/v1/capabilities. Check that the API is
                running and reachable through the Vite proxy.
              </p>
              <button
                type="button"
                className="secondary-button"
                onClick={() => setBootstrapKey((key) => key + 1)}
              >
                Retry loading
              </button>
            </div>
          )}

          {capabilities && (
            <>
              <div className="panel-tabs" role="tablist" aria-label="Analysis mode">
                <button
                  type="button"
                  role="tab"
                  id="tab-search"
                  aria-selected={activePanel === "search"}
                  aria-controls="panel-search"
                  className={activePanel === "search" ? "is-active" : ""}
                  onClick={() => setActivePanel("search")}
                >
                  Search target
                </button>
                <button
                  type="button"
                  role="tab"
                  id="tab-aoi"
                  aria-selected={activePanel === "aoi"}
                  aria-controls="panel-aoi"
                  className={activePanel === "aoi" ? "is-active" : ""}
                  onClick={() => setActivePanel("aoi")}
                >
                  Analyze AOI
                </button>
              </div>

              <div
                role="tabpanel"
                id="panel-search"
                aria-labelledby="tab-search"
                hidden={activePanel !== "search"}
              >
                <SearchForm
                  features={capabilities.features}
                  contextFeatures={capabilities.context_features}
                  references={references}
                  referencesError={referencesError}
                  onSearch={handleSearch}
                  loading={searching}
                  preset={searchPreset}
                />
              </div>

              <div
                role="tabpanel"
                id="panel-aoi"
                aria-labelledby="tab-aoi"
                hidden={activePanel !== "aoi"}
              >
                <AoiForm
                  capabilities={capabilities}
                  onSubmit={aoi.submit}
                  busy={aoi.busy}
                />
              </div>
            </>
          )}
        </div>

        <div className="explore-column">
          {capabilities && (
            <section className="coverage-strip" aria-labelledby="coverage-heading">
              <p className="eyebrow">Searchable coverage</p>
              <h2 id="coverage-heading">
                {capabilities.indexed_candidate_count} searchable Earth region
                {capabilities.indexed_candidate_count === 1 ? "" : "s"}
              </h2>
              <p className="coverage-origins">
                Origins: <strong>{indexedOriginCount} indexed</strong> +{" "}
                <strong>{onDemandOriginCount} on-demand AOI</strong>. On-demand AOIs are
                user-created and are never global coverage.
              </p>
              <p>{capabilities.coverage.description}</p>
              <p className="coverage-extent">
                {capabilities.coverage.geographic_bbox
                  ? `Approximate coverage extent: ${formatBBox(capabilities.coverage.geographic_bbox)}`
                  : "No geographic coverage extent is available."}
              </p>
              {capabilities.indexed_candidate_count === 0 && (
                <p className="alert alert-warning" role="status">
                  No searchable Earth candidates are available. A search may return HTTP 409
                  <code> no_indexed_data</code>; this UI does not invent fallback data.
                </p>
              )}
            </section>
          )}

          {activePanel === "search" && (
            <>
              {searching && (
                <div className="status-panel" role="status">
                  Searching searchable Earth regions…
                </div>
              )}

              {searchError && (
                <div className="alert alert-error" role="alert">
                  <h2>
                    {searchError.code === "no_indexed_data"
                      ? "No indexed Earth data (HTTP 409)"
                      : "Search failed"}
                  </h2>
                  <p>{searchError.message}</p>
                  {searchError.code && (
                    <p>
                      Error code: <code>{searchError.code}</code>
                      {searchError.status !== null ? ` · HTTP ${searchError.status}` : ""}
                    </p>
                  )}
                  {searchError.details.length > 0 && (
                    <details>
                      <summary>Validation details</summary>
                      <pre>{JSON.stringify(searchError.details, null, 2)}</pre>
                    </details>
                  )}
                  <p>
                    Nothing is substituted when the API errors. Adjust the target or retry
                    when the API is available.
                  </p>
                </div>
              )}

              {searchResponse && !searching && (
                <ResultsPanel
                  response={searchResponse}
                  features={capabilities?.features ?? []}
                  contextFeatures={capabilities?.context_features ?? []}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                />
              )}

              {searchResponse && (
                <MapPanel
                  results={searchResponse.results}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                />
              )}

              {!selectedResult &&
                searchResponse &&
                !searching &&
                searchResponse.results.length > 0 && (
                  <div className="detail-placeholder" role="status">
                    <h2>Inspect a candidate</h2>
                    <p>
                      Select a result by its button in the ranked list (or a pin/rectangle
                      on the map) to load GET /locations/&#123;id&#125; and its grounded
                      context export.
                    </p>
                  </div>
                )}

              {!searchResponse && !searching && !searchError && capabilities && (
                <section className="idle-panel" aria-labelledby="idle-heading">
                  <h2 id="idle-heading">How this MVP answers</h2>
                  <p>
                    The prefilled target is a hypothetical example, not a NASA measurement.
                    A search returns the closest windows within the searchable coverage
                    using a weighted normalized Euclidean distance on terrain shape.
                  </p>
                  <ul>
                    <li>Lower distance means more similar. It is never a percentage.</li>
                    <li>
                      Observed and DEMO / SYNTHETIC records are labeled explicitly;
                      synthetic fixtures are not evidence.
                    </li>
                    <li>
                      On-demand AOIs are user-created windows, not global coverage. Their
                      origin appears in result caveats.
                    </li>
                    <li>
                      Gravity, atmosphere, radiation, regolith, mineralogy, temperature,
                      and geological history are not compared. ESA WorldCover context is
                      not mineralogy.
                    </li>
                  </ul>
                </section>
              )}
            </>
          )}

          {activePanel === "aoi" && capabilities && (
            <AoiWorkspace
              capabilities={capabilities}
              analysis={aoi}
              activeTarget={activeTarget}
              onUseAsTarget={handleUseAoiAsTarget}
            />
          )}

          {!capabilities && !capabilitiesLoading && !capabilitiesError && (
            <div className="status-panel" role="status">
              No capabilities were returned. Search and AOI analysis are unavailable.
            </div>
          )}
        </div>

        {activePanel === "search" && selectedResult && capabilities && (
          <DetailPanel
            selected={selectedResult}
            activeTarget={activeTarget}
            features={capabilities.features}
          />
        )}

        {capabilities && (
          <SourcesAndMethods
            capabilities={capabilities}
            datasets={datasets}
            datasetsError={datasetsError}
          />
        )}
      </main>

      <footer className="site-footer">
        <p>
          Planetary Analog Explorer MVP · contract {capabilities?.contract_version ?? "—"}{" "}
          · method {capabilities?.method_version ?? "—"} · data{" "}
          {capabilities?.data_version ?? "—"} · snapshot{" "}
          {capabilities?.data_snapshot_id ?? "—"}
        </p>
        <p>
          Distance is a weighted normalized Euclidean value on terrain shape only.
          Basemap © OpenStreetMap contributors.
        </p>
      </footer>
    </div>
  );
}
