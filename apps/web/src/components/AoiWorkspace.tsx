import { useEffect, useMemo, useRef, useState } from "react";
import {
  compareArea,
  errorCode,
  errorMessage,
  isAbortError,
  type CapabilitiesOut,
  type FeatureOut,
  type NumericTarget,
  type SearchResponse,
  type SearchResultOut,
} from "../api/client";
import type { AoiAnalysis } from "../hooks/useAoiAnalysis";
import {
  jobResultNumber,
  jobWarnings,
  providerStatusEntries,
  readAoiLimits,
  type ProviderStatus,
} from "../lib/aoi";
import { SourceText, useI18n } from "../i18n/I18nProvider";
import { DataKindBadge } from "./DataKindBadge";
import { DetailPanel } from "./DetailPanel";
import { LocationDetails } from "./LocationDetails";
import { ResultsPanel } from "./ResultsPanel";
import { Timestamp } from "./Timestamp";

type AoiWorkspaceProps = {
  capabilities: CapabilitiesOut;
  analysis: AoiAnalysis;
  activeTarget: NumericTarget;
  onUseAsTarget: (values: NumericTarget) => void;
};

function statusClass(status: string | undefined): string {
  const normalized = (status ?? "unknown").toLowerCase();
  if (normalized === "verified" || normalized === "ok" || normalized === "completed") {
    return "status-ok";
  }
  if (normalized === "partial" || normalized === "running" || normalized === "queued") {
    return "status-pending";
  }
  if (normalized === "failed" || normalized === "not_loaded") {
    return "status-failed";
  }
  return "status-unknown";
}

function ProviderStatusList({
  providerStatus,
}: {
  providerStatus: Record<string, unknown> | Record<string, ProviderStatus> | null;
}) {
  const { t, rich, fmt, label } = useI18n();
  const entries = providerStatusEntries(providerStatus);
  if (entries.length === 0) {
    return <p className="field-hint">{t("aoi.providerPending")}</p>;
  }
  return (
    <ul className="provider-status-list">
      {entries.map(([providerId, status]) => (
        <li key={providerId}>
          <div className="provider-heading">
            <code>{providerId}</code>
            <span
              className={`status-chip ${statusClass(status.status)}`}
              data-status={status.status ?? "unknown"}
            >
              {label.status(status.status)}
            </span>
          </div>
          {status.role && (
            <p>
              <SourceText>{status.role}</SourceText>
            </p>
          )}
          {status.reason && (
            <p>
              {rich("aoi.reason", { reason: <code>{status.reason}</code> })}
              {label.errorCode(status.reason)
                ? t("common.codeMeaning", { meaning: label.errorCode(status.reason) ?? "" })
                : ""}
            </p>
          )}
          {status.message && (
            <p>
              <SourceText>{status.message}</SourceText>
            </p>
          )}
          {status.tile && <p>{rich("aoi.tile", { tile: <code>{status.tile}</code> })}</p>}
          {typeof status.tiles === "number" && (
            <p>
              {t("aoi.tilesRead", { tiles: fmt.number(status.tiles, 0) })}
              {typeof status.valid_pixels === "number"
                ? t("aoi.validPixels", { pixels: fmt.number(status.valid_pixels, 0) })
                : ""}
            </p>
          )}
          {status.source_url && (
            <p className="provider-source">
              <a href={status.source_url} target="_blank" rel="noreferrer">
                {t("aoi.providerSource")}
              </a>
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

function AoiComparison({
  areaId,
  capabilities,
  target,
}: {
  areaId: string;
  capabilities: CapabilitiesOut;
  target: NumericTarget;
}) {
  const { t, rich, label } = useI18n();
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ message: string; code: string | null } | null>(
    null,
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setResponse(null);
    setSelectedId(null);
    setError(null);
    return () => controllerRef.current?.abort();
  }, [areaId]);

  async function runComparison() {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setError(null);
    setResponse(null);
    setSelectedId(null);
    try {
      const payload = await compareArea(areaId, controller.signal);
      if (!controller.signal.aborted) {
        setResponse(payload);
      }
    } catch (caught: unknown) {
      if (isAbortError(caught)) {
        return;
      }
      setError({ message: errorMessage(caught), code: errorCode(caught) });
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }

  const selectedResult: SearchResultOut | null =
    response?.results.find((result) => result.location_id === selectedId) ?? null;

  return (
    <section className="aoi-comparison" aria-labelledby="aoi-comparison-heading">
      <div className="section-heading compact">
        <p className="eyebrow">{t("aoi.compareEyebrow")}</p>
        <h3 id="aoi-comparison-heading">{t("aoi.compareHeading")}</h3>
        <p className="muted">{t("aoi.compareIntro")}</p>
      </div>

      <button
        type="button"
        className="secondary-button"
        onClick={runComparison}
        disabled={loading}
      >
        {loading ? t("aoi.comparing") : t("aoi.compareButton")}
      </button>

      {loading && (
        <p className="status-panel" role="status">
          {t("aoi.comparingStatus")}
        </p>
      )}

      {error && (
        <div className="alert alert-error" role="alert">
          <strong>{t("aoi.compareFailed")}</strong> <SourceText>{error.message}</SourceText>
          {error.code && (
            <>
              {" "}
              {rich("aoi.compareCode", { code: <code>{error.code}</code> })}
              {label.errorCode(error.code)
                ? t("common.codeMeaning", { meaning: label.errorCode(error.code) ?? "" })
                : ""}
            </>
          )}
        </div>
      )}

      {response && (
        <>
          <ResultsPanel
            response={response}
            features={capabilities.features}
            contextFeatures={capabilities.context_features}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          {selectedResult && (
            <DetailPanel
              selected={selectedResult}
              activeTarget={target}
              features={capabilities.features}
            />
          )}
        </>
      )}
    </section>
  );
}

function ErrorCodeLine({
  code,
  status,
}: {
  code: string | null;
  status?: number | null;
}) {
  const { t, rich, label } = useI18n();
  if (!code) {
    return null;
  }
  const meaning = label.errorCode(code);
  return (
    <p>
      {rich("common.errorCode", { code: <code>{code}</code> })}
      {meaning ? t("common.codeMeaning", { meaning }) : ""}
      {status !== undefined && status !== null ? t("common.httpStatus", { status }) : ""}
    </p>
  );
}

export function AoiWorkspace({
  capabilities,
  analysis,
  activeTarget,
  onUseAsTarget,
}: AoiWorkspaceProps) {
  const { t, fmt, label } = useI18n();
  const limits = readAoiLimits(capabilities.aoi_analysis);
  const { job, analyzeResponse, location, status } = analysis;
  const providerStatus =
    job?.provider_status ?? analyzeResponse?.provider_status ?? {};
  const warnings = jobWarnings(job?.result);
  const areaKm2 = jobResultNumber(job?.result, "area_km2");
  const featureCount = jobResultNumber(job?.result, "feature_count");
  const contextFeatureCount = jobResultNumber(job?.result, "context_feature_count");
  const jobErrorCode = job?.error?.code ?? null;
  const jobErrorMessage = job?.error?.message ?? null;
  const isActive = status === "queued" || status === "running";
  const isTerminalSuccess = status === "completed" || status === "partial";
  const aoiFeatures: FeatureOut[] = capabilities.features;
  const aoiTarget = useMemo<NumericTarget>(
    () =>
      location
        ? Object.fromEntries(
            location.features.map((feature) => [feature.feature_id, feature.value]),
          )
        : {},
    [location],
  );
  const featureErrorMessage =
    analysis.featureError?.code === "missing_area_id"
      ? t("aoi.missingAreaId")
      : analysis.featureError?.message;

  function useAsTarget() {
    if (!location) {
      return;
    }
    const values: NumericTarget = Object.fromEntries(
      location.features.map((feature) => [feature.feature_id, feature.value]),
    );
    onUseAsTarget(values);
  }

  return (
    <section className="aoi-workspace" aria-labelledby="aoi-workspace-heading">
      <div className="section-heading">
        <p className="eyebrow">{t("aoi.eyebrow")}</p>
        <h2 id="aoi-workspace-heading">{t("aoi.heading")}</h2>
        <p className="muted">{t("aoi.intro")}</p>
      </div>

      {!analyzeResponse && !analysis.requestError && (
        <div className="idle-panel">
          <h3>{t("aoi.idleHeading")}</h3>
          <p>
            {t("aoi.idleBody", {
              tab: t("app.tabAoi"),
              min: fmt.number(limits.minSideM, 0),
              max: fmt.number(limits.maxSideM, 0),
              area: fmt.number(limits.maxAreaKm2, 1),
              resolution: fmt.number(limits.analysisResolutionM, 0),
            })}
          </p>
          <p className="field-hint">{t("aoi.idleHint")}</p>
        </div>
      )}

      {analysis.requestError && (
        <div className="alert alert-error" role="alert">
          <h3>{t("aoi.requestFailed")}</h3>
          <p>
            <SourceText>{analysis.requestError.message}</SourceText>
          </p>
          <ErrorCodeLine
            code={analysis.requestError.code}
            status={analysis.requestError.status}
          />
          {analysis.requestError.details.length > 0 && (
            <details>
              <summary>{t("common.validationDetails")}</summary>
              <pre>{JSON.stringify(analysis.requestError.details, null, 2)}</pre>
            </details>
          )}
        </div>
      )}

      {(analysis.submitting || isActive) && (
        <div
          className="status-panel aoi-status"
          role="status"
          aria-live="polite"
          data-testid="aoi-status"
          data-job-status={status ?? "pending"}
        >
          <span className="spinner" aria-hidden="true" />
          <div>
            <p>
              <strong>
                {analysis.submitting
                  ? t("aoi.submittingStatus")
                  : status === "queued"
                    ? t("aoi.queuedStatus")
                    : t("aoi.runningStatus")}
              </strong>
            </p>
            <p className="field-hint">
              {t("aoi.pollingHint", {
                job: job?.job_id ?? analyzeResponse?.job_id ?? t("common.pending"),
              })}
            </p>
          </div>
        </div>
      )}

      {analysis.pollError && (
        <div className="alert alert-error" role="alert">
          <h3>{t("aoi.pollFailed")}</h3>
          <p>
            <SourceText>{analysis.pollError.message}</SourceText>
          </p>
          <ErrorCodeLine code={analysis.pollError.code} />
          <button type="button" className="secondary-button" onClick={analysis.retry}>
            {t("aoi.retryPolling")}
          </button>
        </div>
      )}

      {analysis.featureError && (
        <div className="alert alert-error" role="alert">
          <h3>{t("aoi.featureFailed")}</h3>
          <p>
            {analysis.featureError.code === "missing_area_id" ? (
              featureErrorMessage
            ) : (
              <SourceText>{featureErrorMessage}</SourceText>
            )}
          </p>
          <ErrorCodeLine code={analysis.featureError.code} />
          <button type="button" className="secondary-button" onClick={analysis.retry}>
            {t("aoi.retryFeature")}
          </button>
        </div>
      )}

      {job && (
        <section
          className="aoi-job-panel"
          aria-labelledby="aoi-job-heading"
          data-testid="aoi-job-panel"
        >
          <div className="aoi-job-heading">
            <h3 id="aoi-job-heading">{t("aoi.jobStatus")}</h3>
            <span className={`status-chip ${statusClass(job.status)}`} data-status={job.status}>
              {label.status(job.status)}
            </span>
            {analyzeResponse?.cached && (
              <span className="status-chip status-cached">{t("aoi.cached")}</span>
            )}
          </div>
          <dl className="facts-grid compact-facts">
            <div>
              <dt>{t("aoi.jobId")}</dt>
              <dd>
                <code>{job.job_id}</code>
              </dd>
            </div>
            <div>
              <dt>{t("aoi.areaId")}</dt>
              <dd>
                <code>{job.area_id ?? t("common.pending")}</code>
              </dd>
            </div>
            <div>
              <dt>{t("aoi.created")}</dt>
              <dd>
                {job.created_at ? <Timestamp iso={job.created_at} /> : t("common.pending")}
              </dd>
            </div>
            <div>
              <dt>{t("aoi.finished")}</dt>
              <dd>
                {job.finished_at ? <Timestamp iso={job.finished_at} /> : t("common.pending")}
              </dd>
            </div>
          </dl>

          {status === "failed" && (
            <div className="alert alert-error" role="alert">
              <h4>{t("aoi.failedTitle")}</h4>
              <p>
                {jobErrorMessage ? (
                  <SourceText>{jobErrorMessage}</SourceText>
                ) : (
                  t("aoi.failedNoMessage")
                )}
              </p>
              <ErrorCodeLine code={jobErrorCode} />
            </div>
          )}

          {status === "partial" && (
            <div className="alert alert-warning" role="alert">
              <h4>{t("aoi.partialTitle")}</h4>
              <p>{t("aoi.partialBody")}</p>
            </div>
          )}

          {status === "completed" && <p className="field-hint">{t("aoi.completedHint")}</p>}

          <h4>{t("aoi.perProvider")}</h4>
          <ProviderStatusList providerStatus={providerStatus} />

          {warnings.length > 0 && (
            <div className="warnings">
              <h4>{t("aoi.warnings")}</h4>
              <ul>
                {warnings.map((warning) => (
                  <li key={warning}>
                    <code>{warning}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(areaKm2 !== null || featureCount !== null) && (
            <dl className="facts-grid compact-facts">
              {areaKm2 !== null && (
                <div>
                  <dt>{t("aoi.area")}</dt>
                  <dd>{t("format.km2", { value: fmt.number(areaKm2, 3) })}</dd>
                </div>
              )}
              {featureCount !== null && (
                <div>
                  <dt>{t("aoi.similarityFeatures")}</dt>
                  <dd>{fmt.number(featureCount, 0)}</dd>
                </div>
              )}
              {contextFeatureCount !== null && (
                <div>
                  <dt>{t("aoi.contextFeatures")}</dt>
                  <dd>{fmt.number(contextFeatureCount, 0)}</dd>
                </div>
              )}
            </dl>
          )}
        </section>
      )}

      {isTerminalSuccess && !location && !analysis.featureError && (
        <p className="status-panel" role="status">
          {t("aoi.loadingFeatures")}
        </p>
      )}

      {isTerminalSuccess && location && (
        <>
          <div className="aoi-actions">
            <button
              type="button"
              className="primary-button"
              onClick={useAsTarget}
              disabled={location.features.length === 0}
            >
              {t("aoi.useAsTarget")}
            </button>
            <p className="field-hint">{t("aoi.useAsTargetHint")}</p>
          </div>

          <section
            className="aoi-record"
            aria-labelledby="aoi-record-heading"
            data-testid="aoi-record"
          >
            <div className="section-heading compact">
              <p className="eyebrow">{t("aoi.recordEyebrow")}</p>
              <h3 id="aoi-record-heading">{location.display_name}</h3>
              <p className="selected-name">
                <DataKindBadge kind={location.data_kind} />{" "}
                <span className="badge badge-ondemand">{t("aoi.onDemandBadge")}</span>
              </p>
            </div>
            <LocationDetails
              location={location}
              comparisonMetrics={[]}
              activeTarget={activeTarget}
              features={aoiFeatures}
              sourceLabel={`GET /areas/${location.location_id}/features`}
              idPrefix="aoi-detail"
            />
          </section>

          <AoiComparison
            areaId={location.location_id}
            capabilities={capabilities}
            target={aoiTarget}
          />
        </>
      )}
    </section>
  );
}
