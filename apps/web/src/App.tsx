import { useEffect, useRef, useState } from "react";
import { ThemeToggle } from "./components/ThemeToggle";
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
import { I18nProvider, SourceText, useI18n } from "./i18n/I18nProvider";
import type { Locale } from "./i18n/translate";
import type { SearchSubmission, SearchTargetPreset } from "./lib/search-types";
import { AoiForm } from "./components/AoiForm";
import { AoiWorkspace } from "./components/AoiWorkspace";
import { DetailPanel } from "./components/DetailPanel";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
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
  /** Overrides saved/browser locale detection (used by tests). */
  initialLocale?: Locale;
};

export function App({ aoiPollIntervalMs = 1000, initialLocale }: AppProps) {
  return (
    <I18nProvider initialLocale={initialLocale}>
      <AppContent aoiPollIntervalMs={aoiPollIntervalMs} />
    </I18nProvider>
  );
}

function AppContent({ aoiPollIntervalMs }: { aoiPollIntervalMs: number }) {
  const { t, tp, rich, fmt, label } = useI18n();
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
  const searchErrorMeaning = label.errorCode(searchError?.code);
  const moonReferenceCount = references.filter((reference) => reference.body === "Moon").length;
  const marsReferenceCount = references.filter((reference) => reference.body === "Mars").length;

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        {t("app.skipLink")}
      </a>

      <header className="site-header">
        <div className="header-inner">
          <div className="header-left">
            <h1 className="brand">
              <span className="brand-mark" aria-hidden="true">
                PAE
              </span>{" "}
              <span className="brand-name">{t("app.title")}</span>
            </h1>
            {capabilities && (
              <div className="header-tabs" role="tablist" aria-label={t("app.tabsLabel")}>
                <button
                  type="button"
                  role="tab"
                  id="tab-search"
                  aria-selected={activePanel === "search"}
                  aria-controls="panel-search"
                  className={activePanel === "search" ? "is-active" : ""}
                  onClick={() => setActivePanel("search")}
                >
                  {t("app.tabSearch")}
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
                  {t("app.tabAoi")}
                </button>
              </div>
            )}
          </div>
          <div className="header-tools">
            {capabilities && (
              <p className="coverage-badge mono" aria-label={t("app.badgeLabel")}>
                <span>
                  {t("app.badgeEarth", { count: fmt.number(indexedOriginCount, 0) })}
                </span>
                <span>
                  {t("app.badgeAoi", { count: fmt.number(onDemandOriginCount, 0) })}
                </span>
                <span className="is-moon">
                  {t("app.badgeMoon", { count: fmt.number(moonReferenceCount, 0) })}
                </span>
                {marsReferenceCount === 0 ? (
                  <span className="is-unavailable">{t("app.badgeMarsNone")}</span>
                ) : (
                  <span className="is-mars">
                    {t("app.badgeMars", { count: fmt.number(marsReferenceCount, 0) })}
                  </span>
                )}
              </p>
            )}
            <LanguageSwitcher />
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main id="main-content" className="app-main">
        {capabilitiesLoading && !capabilities && (
          <p className="status-panel" role="status">
            {t("app.capabilitiesLoading")}
          </p>
        )}

        {capabilitiesError && (
          <div className="alert alert-error" role="alert">
            <p>
              <strong>{t("app.capabilitiesErrorTitle")}</strong>{" "}
              <SourceText>{capabilitiesError}</SourceText>
            </p>
            <p>{t("app.capabilitiesErrorHelp")}</p>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setBootstrapKey((key) => key + 1)}
            >
              {t("app.retryLoading")}
            </button>
          </div>
        )}

        {!capabilities && !capabilitiesLoading && !capabilitiesError && (
          <div className="status-panel" role="status">
            {t("app.noCapabilities")}
          </div>
        )}

        {capabilities && (
          <>
            <div
              role="tabpanel"
              id="panel-search"
              aria-labelledby="tab-search"
              className="panel-stack"
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

              {searching && (
                <div className="status-panel" role="status">
                  {t("app.searching")}
                </div>
              )}

              {searchError && (
                <div className="alert alert-error" role="alert">
                  <h2>
                    {searchError.code === "no_indexed_data"
                      ? t("app.noIndexedTitle")
                      : t("app.searchFailed")}
                  </h2>
                  <p>
                    <SourceText>{searchError.message}</SourceText>
                  </p>
                  {searchError.code && (
                    <p>
                      {rich("common.errorCode", { code: <code>{searchError.code}</code> })}
                      {searchErrorMeaning
                        ? t("common.codeMeaning", { meaning: searchErrorMeaning })
                        : ""}
                      {searchError.status !== null
                        ? t("common.httpStatus", { status: searchError.status })
                        : ""}
                    </p>
                  )}
                  {searchError.details.length > 0 && (
                    <details>
                      <summary>{t("common.validationDetails")}</summary>
                      <pre>{JSON.stringify(searchError.details, null, 2)}</pre>
                    </details>
                  )}
                  <p>{t("app.searchErrorHelp")}</p>
                </div>
              )}

              {activePanel === "search" && (
                <MapPanel
                  results={searchResponse?.results ?? []}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  response={searching ? undefined : (searchResponse ?? undefined)}
                  features={capabilities.features}
                  target={activeTarget}
                  showIdle={!searchResponse && !searching}
                />
              )}

              {searchResponse && !searching && (
                <div className="results-grid">
                  <ResultsPanel
                    response={searchResponse}
                    features={capabilities.features}
                    contextFeatures={capabilities.context_features}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                  />
                  {selectedResult ? (
                    <DetailPanel
                      selected={selectedResult}
                      activeTarget={activeTarget}
                      features={capabilities.features}
                    />
                  ) : (
                    searchResponse.results.length > 0 && (
                      <div className="detail-placeholder" role="status">
                        <h2>{t("app.inspectTitle")}</h2>
                        <p>{t("app.inspectBody")}</p>
                      </div>
                    )
                  )}
                </div>
              )}
            </div>

            <div
              role="tabpanel"
              id="panel-aoi"
              aria-labelledby="tab-aoi"
              className="panel-stack"
              hidden={activePanel !== "aoi"}
            >
              <AoiForm capabilities={capabilities} onSubmit={aoi.submit} busy={aoi.busy} />
              {activePanel === "aoi" && (
                <AoiWorkspace
                  capabilities={capabilities}
                  analysis={aoi}
                  activeTarget={activeTarget}
                  onUseAsTarget={handleUseAoiAsTarget}
                />
              )}
            </div>

            <details className="about-panel">
              <summary>{t("app.aboutSummary")}</summary>
              <section className="coverage-strip" aria-labelledby="coverage-heading">
                <p className="eyebrow">{t("app.coverageEyebrow")}</p>
                <h2 id="coverage-heading">
                  {tp("app.coverageHeading", capabilities.indexed_candidate_count)}
                </h2>
                <p className="coverage-origins">
                  {rich("app.coverageOrigins", {
                    indexed: (
                      <strong>
                        {t("coverage.indexedCount", { count: fmt.number(indexedOriginCount, 0) })}
                      </strong>
                    ),
                    onDemand: (
                      <strong>
                        {t("coverage.onDemandCount", {
                          count: fmt.number(onDemandOriginCount, 0),
                        })}
                      </strong>
                    ),
                  })}
                </p>
                <p>
                  <SourceText>{capabilities.coverage.description}</SourceText>
                </p>
                <p className="coverage-extent">
                  {capabilities.coverage.geographic_bbox
                    ? t("app.coverageExtent", {
                        bbox: fmt.bbox(capabilities.coverage.geographic_bbox),
                      })
                    : t("app.coverageExtentMissing")}
                </p>
              </section>
              <section className="idle-panel" aria-labelledby="idle-heading">
                <h2 id="idle-heading">{t("app.idleHeading")}</h2>
                <p>{t("app.idleBody")}</p>
                <ul>
                  <li>{t("app.idleItemDistance")}</li>
                  <li>{t("app.idleItemSynthetic")}</li>
                  <li>{t("app.idleItemOnDemand")}</li>
                  <li>{t("app.idleItemNotCompared")}</li>
                </ul>
              </section>
              <SourcesAndMethods
                capabilities={capabilities}
                datasets={datasets}
                datasetsError={datasetsError}
              />
            </details>

            {capabilities.indexed_candidate_count === 0 && (
              <p className="alert alert-warning" role="status">
                {rich("app.noCandidates", { code: <code>no_indexed_data</code> })}
              </p>
            )}
          </>
        )}
      </main>

      <footer className="site-footer mono">
        <p>
          {t("app.footerVersions", {
            contract: capabilities?.contract_version ?? "—",
            method: capabilities?.method_version ?? "—",
            data: capabilities?.data_version ?? "—",
            snapshot: capabilities?.data_snapshot_id ?? "—",
          })}
        </p>
        <p>{t("app.footerNotCompared")}</p>
        <p>{t("app.footerNote")}</p>
      </footer>
    </div>
  );
}
