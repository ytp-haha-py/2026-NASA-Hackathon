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
  searchMultidimensional,
  type CapabilitiesOut,
  type DatasetOut,
  type NumericTarget,
  type ReferenceOut,
  type SearchResponse,
  type SearchResultOut,
} from "./api/client";
import { adaptMultiDimResponse } from "./lib/multidim";
import { readAoiLimits } from "./lib/aoi";
import { I18nProvider, SourceText, useI18n } from "./i18n/I18nProvider";
import type { Locale } from "./i18n/translate";
import type { SearchSubmission } from "./lib/search-types";
import { DetailPanel } from "./components/DetailPanel";
import { GlobalPanel } from "./components/GlobalPanel";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
import { ResultsPanel } from "./components/ResultsPanel";
import { SearchForm } from "./components/SearchForm";
import { SourcesAndMethods } from "./components/SourcesAndMethods";

const NO_RESULTS: SearchResultOut[] = [];

type SearchErrorState = {
  message: string;
  code: string | null;
  status: number | null;
  details: { [key: string]: unknown }[];
};

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
  const [datasetsLoading, setDatasetsLoading] = useState(true);
  const [datasetsError, setDatasetsError] = useState<string | null>(null);
  const [bootstrapKey, setBootstrapKey] = useState(0);

  const [searching, setSearching] = useState(false);
  const [lastSubmission, setLastSubmission] = useState<SearchSubmission | null>(null);
  const [searchResponse, setSearchResponse] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState<SearchErrorState | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTarget, setActiveTarget] = useState<NumericTarget>({});
  const searchController = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => searchController.current?.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setCapabilitiesLoading(true);
    setCapabilitiesError(null);
    setReferencesError(null);
    setDatasetsLoading(true);
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
      })
      .finally(() => {
        if (!controller.signal.aborted) setDatasetsLoading(false);
      });

    return () => controller.abort();
  }, [bootstrapKey]);

  async function handleSearch(submission: SearchSubmission) {
    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;

    setSearching(true);
    setLastSubmission(submission);
    setSearchError(null);
    setSearchResponse(null);
    setSelectedId(null);
    setActiveTarget(submission.target);

    try {
      const response = submission.multidim
        ? adaptMultiDimResponse(
            await searchMultidimensional(submission.multidim, controller.signal),
          )
        : await searchCandidates(submission.request, controller.signal);
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

  const coverageOrigins = capabilities?.coverage.origins;
  const indexedOriginCount = coverageOrigins?.indexed ?? 0;
  const onDemandOriginCount = coverageOrigins?.on_demand ?? 0;
  const searchErrorMeaning = label.errorCode(searchError?.code);
  const moonReferenceCount = references.filter((reference) => reference.body === "Moon").length;
  const marsReferenceCount = references.filter((reference) => reference.body === "Mars").length;
  const aoiLimits = capabilities ? readAoiLimits(capabilities.aoi_analysis) : null;
  const canRefine =
    aoiLimits?.supportedBody === "Earth" &&
    aoiLimits.providerStatus["copernicus-dem-glo90"]?.status === "verified";

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        {t("app.skipLink")}
      </a>

      <header className="site-header">
        <div className="header-inner">
          <div className="header-left">
            <h1 className="brand">
              <img className="brand-mark" src="/favicon.svg" alt="" width="24" height="24" />
              <span className="brand-name">{t("app.title")}</span>
            </h1>
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
            <div className="panel-stack" id="section-search">
              <SearchForm
                features={capabilities.features}
                contextFeatures={capabilities.context_features}
                references={references}
                referencesError={referencesError}
                onSearch={handleSearch}
                loading={searching}
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

              <GlobalPanel
                indexedCount={searchResponse?.eligible_candidate_count ?? capabilities.indexed_candidate_count}
                indexedSnapshot={searchResponse?.data_snapshot_id ?? capabilities.data_snapshot_id}
                indexedLoading={searching}
                indexedSearched={searchResponse !== null}
                canRefine={canRefine}
                pollIntervalMs={aoiPollIntervalMs}
                onRerank={lastSubmission ? () => void handleSearch(lastSubmission) : undefined}
                map={{
                  results: searchResponse?.results ?? NO_RESULTS,
                  selectedId,
                  onSelect: setSelectedId,
                  response: searching ? undefined : (searchResponse ?? undefined),
                  features: capabilities.features,
                  target: activeTarget,
                  showIdle: !searchResponse && !searching,
                }}
              />

              {searchResponse && !searching && (
                <div className="results-grid">
                  <ResultsPanel
                    response={searchResponse}
                    features={capabilities.features}
                    contextFeatures={capabilities.context_features}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    selectedOnly
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

            <details className="about-panel">
              <summary>{t("app.aboutSummary")}</summary>
              <div className="about-content">
                <div className="about-overview">
                  <section className="coverage-strip" aria-labelledby="coverage-heading">
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
                    <p className="coverage-extent">
                      {capabilities.coverage.geographic_bbox
                        ? t("app.coverageExtent", {
                            bbox: fmt.bbox(capabilities.coverage.geographic_bbox),
                          })
                        : t("app.coverageExtentMissing")}
                    </p>
                  </section>
                  <section aria-labelledby="about-limits-heading">
                    <h2 id="about-limits-heading">{t("app.aboutLimits")}</h2>
                    <p className="about-coverage-note">{t("sources.closestOnly")}</p>
                    <p>{t("app.idleItemNotCompared")}</p>
                  </section>
                </div>
                <SourcesAndMethods
                  capabilities={capabilities}
                  datasets={datasets}
                  datasetsLoading={datasetsLoading}
                  datasetsError={datasetsError}
                />
              </div>
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
