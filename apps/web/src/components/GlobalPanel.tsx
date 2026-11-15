import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  ApiError,
  createRefinePlan,
  errorCode,
  errorMessage,
  getGlobalCandidate,
  getGlobalCoverage,
  isAbortError,
  searchGlobalCandidates,
  type GlobalCandidateOut,
  type GlobalCoverageOut,
  type GlobalSearchRequest,
  type GlobalSearchResponse,
  type GlobalSearchResultOut,
  type RefinePlanOut,
} from "../api/client";
import { SourceText, useI18n } from "../i18n/I18nProvider";
import { looksLikeFixture } from "../lib/six-dimensions";
import { GlobalMap } from "./GlobalMap";

type CoarseFeature = {
  featureId: string;
  label: string;
  unit: string;
  methodVersion: string;
  minValue: number | null;
  maxValue: number | null;
  scale: number | null;
  description: string;
  sourceResolutionM: number | null;
  gridWindowM: number | null;
};

type CoverageError = { message: string; code: string | null; status: number | null };
type CandidateError = { message: string; code: string | null };
type RowState = { value: string; weight: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function coarseFeatures(coverage: GlobalCoverageOut | null): CoarseFeature[] {
  if (!coverage) {
    return [];
  }
  const features: CoarseFeature[] = [];
  for (const raw of coverage.features) {
    if (!isRecord(raw)) {
      continue;
    }
    const featureId = stringOrNull(raw.feature_id);
    const label = stringOrNull(raw.label);
    const unit = stringOrNull(raw.unit);
    if (!featureId || !label || !unit) {
      continue;
    }
    features.push({
      featureId,
      label,
      unit,
      methodVersion: stringOrNull(raw.method_version) ?? "",
      minValue: numberOrNull(raw.min_value),
      maxValue: numberOrNull(raw.max_value),
      scale: numberOrNull(raw.scale),
      description: stringOrNull(raw.description) ?? "",
      sourceResolutionM: numberOrNull(raw.source_resolution_m),
      gridWindowM: numberOrNull(raw.grid_window_m),
    });
  }
  return features;
}

function bboxText(
  bbox: Record<string, number> | null,
  format: (bbox: { west: number; south: number; east: number; north: number }) => string,
): string | null {
  if (
    !bbox ||
    numberOrNull(bbox.west) === null ||
    numberOrNull(bbox.south) === null ||
    numberOrNull(bbox.east) === null ||
    numberOrNull(bbox.north) === null
  ) {
    return null;
  }
  return format({
    west: bbox.west,
    south: bbox.south,
    east: bbox.east,
    north: bbox.north,
  });
}

function candidateFixture(candidate: GlobalCandidateOut | null): boolean {
  return candidate
    ? looksLikeFixture(
        candidate.candidate_id,
        candidate.grid_id,
        candidate.region_id,
        candidate.dataset_id,
        ...candidate.qa_flags,
      )
    : false;
}

export function GlobalPanel() {
  const { t, tp, fmt, label } = useI18n();
  const [coverage, setCoverage] = useState<GlobalCoverageOut | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(true);
  const [coverageError, setCoverageError] = useState<CoverageError | null>(null);
  const [coverageRetry, setCoverageRetry] = useState(0);

  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [limit, setLimit] = useState("10");
  const [regionId, setRegionId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const [searching, setSearching] = useState(false);
  const [response, setResponse] = useState<GlobalSearchResponse | null>(null);
  const [searchError, setSearchError] = useState<CoverageError | null>(null);
  const [lastRequest, setLastRequest] = useState<GlobalSearchRequest | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [candidate, setCandidate] = useState<GlobalCandidateOut | null>(null);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [candidateError, setCandidateError] = useState<CandidateError | null>(null);
  const [candidateRetry, setCandidateRetry] = useState(0);

  const [perSide, setPerSide] = useState("2");
  const [plan, setPlan] = useState<RefinePlanOut | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setCoverageLoading(true);
    setCoverageError(null);

    getGlobalCoverage(controller.signal)
      .then((payload) => setCoverage(payload))
      .catch((caught: unknown) => {
        if (isAbortError(caught)) {
          return;
        }
        setCoverage(null);
        setCoverageError({
          message: errorMessage(caught),
          code: errorCode(caught),
          status: caught instanceof ApiError ? caught.status : null,
        });
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setCoverageLoading(false);
        }
      });

    return () => controller.abort();
  }, [coverageRetry]);

  const features = useMemo(() => coarseFeatures(coverage), [coverage]);
  const featureKey = features.map((feature) => feature.featureId).join("|");

  useEffect(() => {
    setRows((current) =>
      Object.fromEntries(
        features.map((feature) => [
          feature.featureId,
          current[feature.featureId] ?? { value: "", weight: "1" },
        ]),
      ),
    );
    // Feature IDs are the stable identity of the form rows.
  }, [featureKey, features]);

  useEffect(() => {
    if (!selectedId) {
      setCandidate(null);
      setCandidateError(null);
      setCandidateLoading(false);
      return;
    }
    const controller = new AbortController();
    setCandidate(null);
    setCandidateError(null);
    setCandidateLoading(true);

    getGlobalCandidate(selectedId, controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) {
          setCandidate(payload);
        }
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught)) {
          return;
        }
        setCandidateError({ message: errorMessage(caught), code: errorCode(caught) });
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setCandidateLoading(false);
        }
      });

    return () => controller.abort();
  }, [selectedId, candidateRetry]);

  const fixtureIndex =
    coverage !== null &&
    (looksLikeFixture(
      coverage.snapshot_id,
      coverage.label,
      coverage.note,
      ...coverage.regions,
    ) ||
      (response?.results.some((result) =>
        looksLikeFixture(result.candidate_id, result.grid_id, result.region_id),
      ) ??
        false));

  function updateRow(featureId: string, patch: Partial<RowState>) {
    setRows((current) => ({
      ...current,
      [featureId]: { ...(current[featureId] ?? { value: "", weight: "1" }), ...patch },
    }));
    setRowErrors((current) => {
      if (!(featureId in current)) {
        return current;
      }
      const next = { ...current };
      delete next[featureId];
      return next;
    });
    setFormError(null);
  }

  async function runSearch(request: GlobalSearchRequest) {
    setSearching(true);
    setSearchError(null);
    setSelectedId(null);
    setCandidate(null);
    setPlan(null);
    setPlanError(null);

    try {
      const payload = await searchGlobalCandidates(request);
      setResponse(payload);
    } catch (caught: unknown) {
      if (isAbortError(caught)) {
        return;
      }
      setResponse(null);
      setSearchError({
        message: errorMessage(caught),
        code: errorCode(caught),
        status: caught instanceof ApiError ? caught.status : null,
      });
    } finally {
      setSearching(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextRowErrors: Record<string, string> = {};
    const target: GlobalSearchRequest["target"] = {};

    for (const feature of features) {
      const row = rows[feature.featureId] ?? { value: "", weight: "1" };
      const rawValue = row.value.trim();
      const rawWeight = row.weight.trim();
      if (rawValue === "" && rawWeight === "") {
        continue;
      }
      const value = Number(rawValue);
      const weight = Number(rawWeight);
      if (rawValue !== "" && !Number.isFinite(value)) {
        nextRowErrors[feature.featureId] = t("global.errValue", { label: feature.label });
        continue;
      }
      if (rawWeight !== "" && (!Number.isFinite(weight) || weight < 0)) {
        nextRowErrors[feature.featureId] = t("global.errWeight");
        continue;
      }
      if (rawValue !== "" && weight > 0) {
        target[feature.featureId] = { value, weight };
      }
    }

    const parsedLimit = Number(limit);
    if (!Number.isInteger(parsedLimit) || parsedLimit < 1 || parsedLimit > 10) {
      setFormError(t("global.errLimit"));
      setRowErrors(nextRowErrors);
      return;
    }

    if (Object.keys(target).length === 0) {
      setFormError(
        Object.keys(nextRowErrors).length > 0 ? null : t("global.errTarget"),
      );
      setRowErrors(nextRowErrors);
      return;
    }

    const request: GlobalSearchRequest = {
      target,
      limit: parsedLimit,
      ...(regionId ? { region_id: regionId } : {}),
    };
    setFormError(null);
    setRowErrors(nextRowErrors);
    setLastRequest(request);
    void runSearch(request);
  }

  async function createPlan() {
    if (!selectedId) {
      return;
    }
    setPlanning(true);
    setPlanError(null);
    setPlan(null);
    try {
      const payload = await createRefinePlan({
        candidate_id: selectedId,
        per_side: Number(perSide),
      });
      setPlan(payload);
    } catch (caught: unknown) {
      if (!isAbortError(caught)) {
        setPlanError(errorMessage(caught));
      }
    } finally {
      setPlanning(false);
    }
  }

  return (
    <section className="global-panel" aria-labelledby="global-heading">
      <div className="section-heading">
        <p className="eyebrow">{t("global.eyebrow")}</p>
        <h2 id="global-heading">{t("global.heading")}</h2>
        <p>{t("global.intro")}</p>
      </div>

      {coverageLoading && (
        <p className="status-panel" role="status">
          {t("global.loading")}
        </p>
      )}

      {coverageError && (
        <div className="alert alert-error" role="alert">
          <p>
            <strong>
              {coverageError.code
                ? t("global.failedWithCode", { code: coverageError.code })
                : t("global.failed")}
            </strong>{" "}
            <SourceText>{coverageError.message}</SourceText>
          </p>
          {coverageError.status !== null && (
            <p>{t("common.httpStatus", { status: coverageError.status })}</p>
          )}
          <button
            type="button"
            className="secondary-button"
            onClick={() => setCoverageRetry((key) => key + 1)}
          >
            {t("global.retry")}
          </button>
        </div>
      )}

      {coverage && coverage.status === "no_index" && (
        <div className="alert alert-warning" role="status">
          <h3>{t("global.noIndexTitle")}</h3>
          <p>{t("global.noIndexBody")}</p>
          <p>{t("global.noIndexHelp")}</p>
          <p>
            <SourceText>{coverage.note}</SourceText>
          </p>
        </div>
      )}

      {coverage && coverage.status !== "no_index" && (
        <>
          {fixtureIndex && (
            <div className="alert alert-synthetic" role="alert">
              <strong>{t("global.fixture")}</strong>
            </div>
          )}

          <section className="coverage-panel" aria-labelledby="global-coverage-heading">
            <h3 id="global-coverage-heading">{t("global.coverageHeading")}</h3>
            <dl className="facts-grid">
              <div>
                <dt>{t("global.status")}</dt>
                <dd>
                  <code>{coverage.status}</code>
                </dd>
              </div>
              <div>
                <dt>{t("global.snapshot")}</dt>
                <dd>
                  <code>{coverage.snapshot_id ?? t("common.notReported")}</code>
                </dd>
              </div>
              <div>
                <dt>{t("global.rows")}</dt>
                <dd>{fmt.number(coverage.row_count, 0)}</dd>
              </div>
              <div>
                <dt>{t("global.regions")}</dt>
                <dd>
                  {coverage.regions.length > 0
                    ? coverage.regions.join(", ")
                    : t("common.noneReported")}
                </dd>
              </div>
              <div>
                <dt>{t("global.bbox")}</dt>
                <dd>{bboxText(coverage.bbox, fmt.bbox) ?? t("common.notReported")}</dd>
              </div>
              <div>
                <dt>{t("global.methodVersion")}</dt>
                <dd>
                  <code>{coverage.method_version ?? t("common.notReported")}</code>
                </dd>
              </div>
              <div>
                <dt>{t("global.label")}</dt>
                <dd>
                  <code>{coverage.label}</code>
                </dd>
              </div>
            </dl>
            <p>
              <SourceText>{coverage.note}</SourceText>
            </p>
          </section>

          <section className="coverage-panel" aria-labelledby="global-scale-heading">
            <h3 id="global-scale-heading">{t("global.scaleHeading")}</h3>
            <ul>
              <li>{t("global.scaleCoarse")}</li>
              <li>{t("global.scaleFine")}</li>
            </ul>
            <p className="coverage-warning">{t("global.scaleWarning")}</p>
          </section>

          {features.length === 0 ? (
            <div className="alert alert-warning" role="status">
              <p>{t("global.searchHint")}</p>
            </div>
          ) : (
            <section className="global-search" aria-labelledby="global-search-heading">
              <h3 id="global-search-heading">{t("global.searchHeading")}</h3>
              <p className="field-hint">{t("global.searchHint")}</p>
              <form onSubmit={submit} noValidate>
                <div className="table-scroll">
                  <table className="metric-table global-target-table">
                    <caption>{t("global.searchHeading")}</caption>
                    <thead>
                      <tr>
                        <th scope="col">{t("global.colFeature")}</th>
                        <th scope="col">{t("global.colValue")}</th>
                        <th scope="col">{t("global.colWeight")}</th>
                        <th scope="col">{t("global.colSource")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {features.map((feature) => {
                        const row = rows[feature.featureId] ?? {
                          value: "",
                          weight: "1",
                        };
                        return (
                          <tr key={feature.featureId}>
                            <th scope="row">
                              <span>{feature.label}</span>
                              <span className="metric-meta">
                                <code>{feature.featureId}</code>
                              </span>
                              {feature.description && (
                                <span className="metric-meta">
                                  <SourceText>{feature.description}</SourceText>
                                </span>
                              )}
                              {rowErrors[feature.featureId] && (
                                <span className="field-error" role="alert">
                                  {rowErrors[feature.featureId]}
                                </span>
                              )}
                            </th>
                            <td>
                              <input
                                type="number"
                                inputMode="decimal"
                                step="any"
                                aria-label={`${feature.label} ${t("global.colValue")}`}
                                value={row.value}
                                onChange={(event) =>
                                  updateRow(feature.featureId, { value: event.target.value })
                                }
                              />
                              <span className="unit-tag">{feature.unit}</span>
                            </td>
                            <td>
                              <input
                                type="number"
                                inputMode="decimal"
                                min="0"
                                step="any"
                                aria-label={`${feature.label} ${t("global.colWeight")}`}
                                value={row.weight}
                                onChange={(event) =>
                                  updateRow(feature.featureId, { weight: event.target.value })
                                }
                              />
                            </td>
                            <td>
                              {feature.sourceResolutionM === null
                                ? t("common.notReported")
                                : t("format.meters", {
                                    value: fmt.number(feature.sourceResolutionM),
                                  })}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <div className="global-search-controls">
                  <label>
                    <span>{t("global.limit")}</span>
                    <input
                      type="number"
                      inputMode="numeric"
                      min="1"
                      max="10"
                      step="1"
                      value={limit}
                      onChange={(event) => setLimit(event.target.value)}
                    />
                  </label>
                  <label>
                    <span>{t("global.region")}</span>
                    <select
                      value={regionId}
                      onChange={(event) => setRegionId(event.target.value)}
                    >
                      <option value="">{t("global.allRegions")}</option>
                      {coverage.regions.map((region) => (
                        <option key={region} value={region}>
                          {region}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                {formError && (
                  <p className="alert alert-error" role="alert">
                    {formError}
                  </p>
                )}

                <button
                  type="submit"
                  className="primary-button"
                  disabled={searching}
                  aria-label={t("global.submit")}
                >
                  {searching ? t("global.submitting") : t("global.submit")}
                </button>
              </form>
            </section>
          )}

          {searchError && (
            <div className="alert alert-error" role="alert">
              <h3>{t("global.searchFailed")}</h3>
              <p>
                <SourceText>{searchError.message}</SourceText>
              </p>
              {searchError.code && (
                <p>
                  {t("common.errorCode", { code: searchError.code })}
                  {searchError.status !== null
                    ? t("common.httpStatus", { status: searchError.status })
                    : ""}
                </p>
              )}
              <p>{t("global.searchErrorHelp")}</p>
              {lastRequest && (
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void runSearch(lastRequest)}
                >
                  {t("global.submit")}
                </button>
              )}
            </div>
          )}

          {response && !searchError && (
            <section className="global-results" aria-labelledby="global-results-heading">
              <div className="section-heading">
                <h3 id="global-results-heading">{t("global.resultsHeading")}</h3>
                <p className="coverage-headline" data-testid="global-result-summary">
                  {t("global.resultSummary", {
                    shown: fmt.number(response.results.length, 0),
                    eligible: fmt.number(response.eligible_candidate_count, 0),
                    indexed: fmt.number(response.indexed_candidate_count, 0),
                    excluded: fmt.number(response.excluded_candidate_count, 0),
                  })}
                </p>
                <p className="coverage-warning">
                  <strong>{t("global.notTop")}</strong> {t("global.notTopBody")}
                </p>
                <p>
                  {t("global.coverageLabel", {
                    label: response.coverage_label,
                  })}
                </p>
                <p>
                  {t("global.versions", {
                    snapshot: response.snapshot_id,
                    index: response.index_version,
                    method: response.method_version,
                  })}
                </p>
                {response.excluded_candidate_count > 0 && (
                  <p>{t("global.exclusions", { count: fmt.number(response.excluded_candidate_count, 0) })}</p>
                )}
              </div>

              {response.results.length === 0 ? (
                <div className="empty-state" role="status">
                  <h3>{t("global.emptyTitle")}</h3>
                  <p>{t("global.emptyBody")}</p>
                  {response.excluded_candidate_count > 0 && (
                    <p>{tp("global.rowsExcluded", response.excluded_candidate_count)}</p>
                  )}
                </div>
              ) : (
                <>
                  <GlobalMap
                    coverage={coverage}
                    results={response.results}
                    selectedId={selectedId}
                    onSelect={(candidateId) => {
                      setSelectedId(candidateId);
                      setPlan(null);
                      setPlanError(null);
                    }}
                  />
                  <ol className="global-candidate-list">
                    {response.results.map((result) => (
                      <GlobalCandidateCard
                        key={result.candidate_id}
                        result={result}
                        selected={result.candidate_id === selectedId}
                        onSelect={() => {
                          setSelectedId(result.candidate_id);
                          setPlan(null);
                          setPlanError(null);
                        }}
                      />
                    ))}
                  </ol>
                </>
              )}
            </section>
          )}

          {selectedId && (
            <section className="global-detail" aria-labelledby="global-detail-heading">
              <h3 id="global-detail-heading">{t("global.detailHeading")}</h3>
              {candidateLoading && (
                <p className="status-panel" role="status">
                  {t("global.detailLoading")}
                </p>
              )}
              {candidateError && (
                <div className="alert alert-error" role="alert">
                  <p>
                    <strong>
                      {candidateError.code
                        ? t("global.detailFailedWithCode", { code: candidateError.code })
                        : t("global.detailFailed")}
                    </strong>{" "}
                    <SourceText>{candidateError.message}</SourceText>
                  </p>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => setCandidateRetry((key) => key + 1)}
                  >
                    {t("global.detailRetry")}
                  </button>
                </div>
              )}

              {candidate && !candidateLoading && (
                <article
                  className="global-candidate-detail"
                  data-testid="global-candidate-detail"
                >
                  {candidateFixture(candidate) && (
                    <p className="alert alert-synthetic" role="status">
                      {t("global.fixture")}
                    </p>
                  )}
                  <dl className="facts-grid">
                    <div>
                      <dt>{t("global.candidateId")}</dt>
                      <dd>
                        <code>{candidate.candidate_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.gridId")}</dt>
                      <dd>
                        <code>{candidate.grid_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.colRegion")}</dt>
                      <dd>
                        <code>{candidate.region_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.body")}</dt>
                      <dd>{label.body(candidate.body)}</dd>
                    </div>
                    <div>
                      <dt>{t("global.bbox")}</dt>
                      <dd>{bboxText(candidate.bbox, fmt.bbox) ?? t("common.notReported")}</dd>
                    </div>
                    <div>
                      <dt>{t("global.crs")}</dt>
                      <dd>
                        <code>{candidate.crs}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.area")}</dt>
                      <dd>{t("format.km2", { value: fmt.number(candidate.area_km2, 3) })}</dd>
                    </div>
                    <div>
                      <dt>{t("global.window")}</dt>
                      <dd>
                        {t("global.windowAt", {
                          window: fmt.number(candidate.window_size_m, 0),
                          stride: fmt.number(candidate.stride_m, 0),
                          source: fmt.number(candidate.source_resolution_m, 0),
                        })}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.sourceResolution")}</dt>
                      <dd>
                        {t("format.meters", {
                          value: fmt.number(candidate.source_resolution_m, 0),
                        })}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.dataset")}</dt>
                      <dd>
                        <code>{candidate.dataset_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.sourceAsset")}</dt>
                      <dd>
                        <code>{candidate.source_asset_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.methodVersion")}</dt>
                      <dd>
                        <code>{candidate.method_version}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.gridVersion")}</dt>
                      <dd>
                        <code>{candidate.grid_version}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("global.colValid")}</dt>
                      <dd>{fmt.percent(candidate.valid_fraction)}</dd>
                    </div>
                  </dl>

                  <div className="table-scroll">
                    <table className="metric-table">
                      <caption>{t("global.detailValues")}</caption>
                      <tbody>
                        <tr>
                          <th scope="row">{t("global.valueMeanSlope")}</th>
                          <td>
                            {candidate.slope_mean_deg === null
                              ? t("six.noSource")
                              : `${fmt.number(candidate.slope_mean_deg, 3)} degree`}
                          </td>
                        </tr>
                        <tr>
                          <th scope="row">{t("global.valueP90Slope")}</th>
                          <td>
                            {candidate.slope_p90_deg === null
                              ? t("six.noSource")
                              : `${fmt.number(candidate.slope_p90_deg, 3)} degree`}
                          </td>
                        </tr>
                        <tr>
                          <th scope="row">{t("global.valueFlatFraction")}</th>
                          <td>
                            {candidate.flat_fraction === null
                              ? t("six.noSource")
                              : fmt.percent(candidate.flat_fraction)}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>

                  <p>
                    <strong>{t("global.colQa")}:</strong>{" "}
                    {candidate.qa_flags.length > 0 ? (
                      <span className="chips mono">
                        {candidate.qa_flags.map((flag) => (
                          <code key={flag}>{flag}</code>
                        ))}
                      </span>
                    ) : (
                      t("global.noQa")
                    )}
                  </p>

                  <section className="global-refine" aria-labelledby="global-refine-heading">
                    <h4 id="global-refine-heading">{t("global.refineHeading")}</h4>
                    <p>{t("global.refineIntro")}</p>
                    <div className="global-search-controls">
                      <label>
                        <span>{t("global.perSide")}</span>
                        <select
                          value={perSide}
                          onChange={(event) => setPerSide(event.target.value)}
                        >
                          {[1, 2, 3, 4].map((value) => (
                            <option key={value} value={String(value)}>
                              {value}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={planning}
                        onClick={() => void createPlan()}
                      >
                        {planning ? t("global.refining") : t("global.refineButton")}
                      </button>
                    </div>

                    {planError && (
                      <p className="alert alert-error" role="alert">
                        {t("global.refineFailed")} <SourceText>{planError}</SourceText>
                      </p>
                    )}

                    {plan ? (
                      <div className="refine-plan" data-testid="refine-plan">
                        <p className="alert alert-warning" role="status">
                          <strong>{t("global.planOnly")}</strong>
                        </p>
                        <dl className="facts-grid">
                          <div>
                            <dt>{t("global.candidateId")}</dt>
                            <dd>
                              <code>{plan.candidate_id}</code>
                            </dd>
                          </div>
                          <div>
                            <dt>{t("global.planParent")}</dt>
                            <dd>
                              <code>{plan.parent_method_version}</code>
                            </dd>
                          </div>
                          <div>
                            <dt>{t("global.planFine")}</dt>
                            <dd>
                              <code>{plan.fine_method_version}</code>
                            </dd>
                          </div>
                          <div>
                            <dt>{t("global.planWindows", { count: plan.windows.length })}</dt>
                            <dd>{t("global.noExecution")}</dd>
                          </div>
                        </dl>
                        <p>
                          <strong>{t("global.planNote")}:</strong>{" "}
                          <SourceText>{plan.note}</SourceText>
                        </p>
                        <div className="table-scroll">
                          <table className="metric-table refine-window-table">
                            <caption>{t("global.refineHeading")}</caption>
                            <thead>
                              <tr>
                                <th scope="col">{t("global.colFineWindow")}</th>
                                <th scope="col">{t("global.bbox")}</th>
                                <th scope="col">{t("global.colWindowSize")}</th>
                                <th scope="col">{t("global.methodVersion")}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {plan.windows.map((window) => (
                                <tr key={window.fine_window_id}>
                                  <th scope="row">
                                    <code>{window.fine_window_id}</code>
                                  </th>
                                  <td>
                                    {bboxText(window.bbox_wgs84, fmt.bbox) ??
                                      t("common.notReported")}
                                  </td>
                                  <td>
                                    {t("format.meters", {
                                      value: fmt.number(window.window_size_m, 0),
                                    })}
                                  </td>
                                  <td>
                                    <code>{window.method_version}</code>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    ) : (
                      <p className="field-hint">{t("global.noPlan")}</p>
                    )}
                  </section>
                </article>
              )}
            </section>
          )}
        </>
      )}

      {coverage && coverage.status === "no_index" && (
        <p className="field-hint">{t("global.noExecution")}</p>
      )}
    </section>
  );
}

function GlobalCandidateCard({
  result,
  selected,
  onSelect,
}: {
  result: GlobalSearchResultOut;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t, fmt } = useI18n();
  return (
    <li>
      <article
        className={`result-card global-candidate${selected ? " is-selected" : ""}`}
        data-selected={selected ? "true" : "false"}
        data-testid={`global-candidate-${result.candidate_id}`}
      >
        <button
          type="button"
          className="result-select"
          aria-pressed={selected}
          aria-label={t("global.selectLabel", { name: result.candidate_id })}
          onClick={onSelect}
        >
          <span className="result-rank">#{result.rank}</span>
          <span className="result-title">
            <strong>{result.candidate_id}</strong>
            <span className="result-location">
              {result.region_id} · {bboxText(result.bbox, fmt.bbox)}
            </span>
          </span>
          <span className="result-distance">
            <span className="distance-value">{fmt.distance(result.distance)}</span>
            <span className="distance-label">{t("global.colDistance")}</span>
          </span>
        </button>
        <div className="result-body">
          <dl className="typed-facts">
            <div>
              <dt>{t("global.colValid")}</dt>
              <dd>{fmt.percent(result.valid_fraction)}</dd>
            </div>
            <div>
              <dt>{t("global.valueMeanSlope")}</dt>
              <dd>
                {result.slope_mean_deg === null
                  ? t("six.noSource")
                  : `${fmt.number(result.slope_mean_deg, 3)} degree`}
              </dd>
            </div>
            <div>
              <dt>{t("global.valueP90Slope")}</dt>
              <dd>
                {result.slope_p90_deg === null
                  ? t("six.noSource")
                  : `${fmt.number(result.slope_p90_deg, 3)} degree`}
              </dd>
            </div>
            <div>
              <dt>{t("global.valueFlatFraction")}</dt>
              <dd>
                {result.flat_fraction === null
                  ? t("six.noSource")
                  : fmt.percent(result.flat_fraction)}
              </dd>
            </div>
            <div>
              <dt>{t("global.colMethod")}</dt>
              <dd>
                <code>{result.method_version}</code>
              </dd>
            </div>
            <div>
              <dt>{t("global.colQa")}</dt>
              <dd>
                {result.qa_flags.length > 0
                  ? result.qa_flags.join(", ")
                  : t("global.noQa")}
              </dd>
            </div>
          </dl>
        </div>
      </article>
    </li>
  );
}
