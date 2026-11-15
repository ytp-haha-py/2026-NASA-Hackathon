import { useEffect, useMemo, useRef, useState } from "react";
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
import { AoiForm, type AoiBbox } from "./components/AoiForm";
import { AoiWorkspace } from "./components/AoiWorkspace";
import { DetailPanel } from "./components/DetailPanel";
import { GlobalPanel } from "./components/GlobalPanel";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
import { MapPanel } from "./components/MapPanel";
import { ResultsPanel } from "./components/ResultsPanel";
import { SearchForm } from "./components/SearchForm";
import {
  SixDimensionPanel,
  fineValuesFromReference,
  fineValuesFromSearchResult,
  type SixDimensionTarget,
} from "./components/SixDimensionPanel";
import { SourcesAndMethods } from "./components/SourcesAndMethods";
import { WorkflowSection } from "./components/WorkflowSection";

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
  const [datasetsError, setDatasetsError] = useState<string | null>(null);
  const [bootstrapKey, setBootstrapKey] = useState(0);
  const [aoiOpen, setAoiOpen] = useState(false);
  const [dimensionsOpen, setDimensionsOpen] = useState(false);
  const [globalOpen, setGlobalOpen] = useState(false);
  const [aoiBbox, setAoiBbox] = useState<AoiBbox | null>(null);
  const [aoiFormKey, setAoiFormKey] = useState(0);
  const [searchPreset, setSearchPreset] = useState<SearchTargetPreset | null>(null);
  const presetNonce = useRef(0);
  const aoi = useAoiAnalysis(aoiPollIntervalMs);

  const [searching, setSearching] = useState(false);
  const [searchResponse, setSearchResponse] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState<SearchErrorState | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTarget, setActiveTarget] = useState<NumericTarget>({});
  const [selectedReference, setSelectedReference] = useState<ReferenceOut | null>(null);
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

  const sixDimensionTargets = useMemo<SixDimensionTarget[]>(() => {
    const targets: SixDimensionTarget[] = [];
    if (selectedResult) {
      targets.push({
        id: selectedResult.location_id,
        label: selectedResult.display_name,
        kind: "candidate",
        body: "Earth",
        fineValues: fineValuesFromSearchResult(
          selectedResult.metrics,
          capabilities?.features ?? [],
        ),
      });
    }
    if (selectedReference) {
      targets.push({
        id: selectedReference.reference_id,
        label: selectedReference.display_name,
        kind: "reference",
        body: selectedReference.body,
        fineValues: fineValuesFromReference(selectedReference.features),
      });
    }
    return targets;
  }, [selectedResult, selectedReference, capabilities?.features]);

  function scrollToSection(id: string) {
    window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    }, 0);
  }

  function openDimensions() {
    setDimensionsOpen(true);
    scrollToSection("section-dimensions");
  }

  /** An area drawn on the map seeds the analysis form and opens it. */
  function handleDrawBbox(bbox: AoiBbox) {
    setAoiBbox(bbox);
    setAoiFormKey((key) => key + 1);
    setAoiOpen(true);
    scrollToSection("section-aoi");
  }

  function handleUseAoiAsTarget(values: NumericTarget) {
    presetNonce.current += 1;
    setSearchPreset({
      nonce: presetNonce.current,
      values,
      sourceLabel: `on-demand AOI ${aoi.location?.location_id ?? ""}`.trim(),
    });
    scrollToSection("section-search");
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
                onReferenceChange={setSelectedReference}
                onSearch={handleSearch}
                loading={searching}
                preset={searchPreset}
                onOpenDimensions={openDimensions}
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

              <MapPanel
                results={searchResponse?.results ?? []}
                selectedId={selectedId}
                onSelect={setSelectedId}
                response={searching ? undefined : (searchResponse ?? undefined)}
                features={capabilities.features}
                target={activeTarget}
                showIdle={!searchResponse && !searching}
                aoiBbox={aoiBbox}
                onDrawBbox={handleDrawBbox}
              />

              <WorkflowSection
                id="section-aoi"
                title={t("flow.aoiTitle")}
                open={aoiOpen}
                onOpenChange={setAoiOpen}
                keepMounted
              >
                <AoiForm
                  key={aoiFormKey}
                  capabilities={capabilities}
                  onSubmit={aoi.submit}
                  busy={aoi.busy}
                  initialBbox={aoiBbox}
                  onBboxChange={setAoiBbox}
                />
                <AoiWorkspace
                  capabilities={capabilities}
                  analysis={aoi}
                  activeTarget={activeTarget}
                  onUseAsTarget={handleUseAoiAsTarget}
                />
              </WorkflowSection>

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
                      onViewDimensions={openDimensions}
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

              <WorkflowSection
                id="section-dimensions"
                title={t("flow.dimensionsTitle")}
                open={dimensionsOpen}
                onOpenChange={setDimensionsOpen}
              >
                <SixDimensionPanel
                  features={capabilities.features}
                  datasets={datasets}
                  targets={sixDimensionTargets}
                />
              </WorkflowSection>

              <WorkflowSection
                id="section-global"
                title={t("flow.globalTitle")}
                open={globalOpen}
                onOpenChange={setGlobalOpen}
              >
                <GlobalPanel />
              </WorkflowSection>
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
