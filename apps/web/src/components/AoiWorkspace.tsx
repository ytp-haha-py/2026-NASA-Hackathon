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
import { formatNumber } from "../lib/format";
import { DataKindBadge } from "./DataKindBadge";
import { DetailPanel } from "./DetailPanel";
import { LocationDetails } from "./LocationDetails";
import { ResultsPanel } from "./ResultsPanel";

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
  const entries = providerStatusEntries(providerStatus);
  if (entries.length === 0) {
    return <p className="field-hint">Provider status is pending.</p>;
  }
  return (
    <ul className="provider-status-list">
      {entries.map(([providerId, status]) => (
        <li key={providerId}>
          <div className="provider-heading">
            <code>{providerId}</code>
            <span className={`status-chip ${statusClass(status.status)}`}>
              {status.status ?? "unknown"}
            </span>
          </div>
          {status.role && <p>{status.role}</p>}
          {status.reason && (
            <p>
              Reason: <code>{status.reason}</code>
            </p>
          )}
          {status.message && <p>{status.message}</p>}
          {status.tile && (
            <p>
              Tile: <code>{status.tile}</code>
            </p>
          )}
          {typeof status.tiles === "number" && (
            <p>
              Tiles read: {formatNumber(status.tiles, 0)}
              {typeof status.valid_pixels === "number"
                ? ` · valid pixels: ${formatNumber(status.valid_pixels, 0)}`
                : ""}
            </p>
          )}
          {status.source_url && (
            <p className="provider-source">
              <a href={status.source_url} target="_blank" rel="noreferrer">
                Provider source
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
        <p className="eyebrow">Compare</p>
        <h3 id="aoi-comparison-heading">Compare this AOI with Earth candidates</h3>
        <p className="muted">
          Uses the normal weighted normalized Euclidean comparator with this AOI&apos;s own
          observed terrain values as the target. The call sends no target body and does not
          introduce a second scoring method. This AOI is itself searchable, so it may appear
          in its own ranking with distance 0.
        </p>
      </div>

      <button
        type="button"
        className="secondary-button"
        onClick={runComparison}
        disabled={loading}
      >
        {loading ? "Comparing AOI…" : "Compare this AOI against indexed Earth candidates"}
      </button>

      {loading && (
        <p className="status-panel" role="status">
          Comparing the AOI against searchable Earth regions…
        </p>
      )}

      {error && (
        <div className="alert alert-error" role="alert">
          <strong>AOI comparison failed.</strong> {error.message}
          {error.code && (
            <>
              {" "}
              (code <code>{error.code}</code>)
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

export function AoiWorkspace({
  capabilities,
  analysis,
  activeTarget,
  onUseAsTarget,
}: AoiWorkspaceProps) {
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
        <p className="eyebrow">On-demand workspace</p>
        <h2 id="aoi-workspace-heading">New Earth AOI analysis</h2>
        <p className="muted">
          Bounded Copernicus DEM terrain metrics plus ESA WorldCover land-cover context.
          On-demand AOIs are user-requested and are never presented as global coverage.
        </p>
      </div>

      {!analyzeResponse && !analysis.requestError && (
        <div className="idle-panel">
          <h3>No AOI submitted yet</h3>
          <p>
            Use the <strong>Analyze AOI</strong> tab to submit a bounding box. Limits from
            GET /capabilities: sides {formatNumber(limits.minSideM, 0)}–
            {formatNumber(limits.maxSideM, 0)} m, area ≤{" "}
            {formatNumber(limits.maxAreaKm2, 1)} km², latitude −80° to 84°, analysis
            resolution {formatNumber(limits.analysisResolutionM, 0)} m.
          </p>
          <p className="field-hint">
            A completed AOI can be used as a search target; it also becomes searchable for
            later searches, with its on-demand origin shown in caveats.
          </p>
        </div>
      )}

      {analysis.requestError && (
        <div className="alert alert-error" role="alert">
          <h3>AOI request failed</h3>
          <p>{analysis.requestError.message}</p>
          {analysis.requestError.code && (
            <p>
              Error code: <code>{analysis.requestError.code}</code>
              {analysis.requestError.status !== null
                ? ` · HTTP ${analysis.requestError.status}`
                : ""}
            </p>
          )}
          {analysis.requestError.details.length > 0 && (
            <details>
              <summary>Validation details</summary>
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
                  ? "Submitting AOI analysis…"
                  : status === "queued"
                    ? "AOI analysis queued…"
                    : "AOI analysis running…"}
              </strong>
            </p>
            <p className="field-hint">
              Job {job?.job_id ?? analyzeResponse?.job_id ?? "pending"} · polling every ~1
              s. Provider reads may take several seconds.
            </p>
          </div>
        </div>
      )}

      {analysis.pollError && (
        <div className="alert alert-error" role="alert">
          <h3>AOI job polling failed</h3>
          <p>{analysis.pollError.message}</p>
          {analysis.pollError.code && (
            <p>
              Error code: <code>{analysis.pollError.code}</code>
            </p>
          )}
          <button type="button" className="secondary-button" onClick={analysis.retry}>
            Retry job polling
          </button>
        </div>
      )}

      {analysis.featureError && (
        <div className="alert alert-error" role="alert">
          <h3>AOI feature record failed to load</h3>
          <p>{analysis.featureError.message}</p>
          {analysis.featureError.code && (
            <p>
              Error code: <code>{analysis.featureError.code}</code>
            </p>
          )}
          <button type="button" className="secondary-button" onClick={analysis.retry}>
            Retry feature record
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
            <h3 id="aoi-job-heading">Job status</h3>
            <span className={`status-chip ${statusClass(job.status)}`}>
              {job.status}
            </span>
            {analyzeResponse?.cached && (
              <span className="status-chip status-cached">cached result</span>
            )}
          </div>
          <dl className="facts-grid compact-facts">
            <div>
              <dt>Job ID</dt>
              <dd>
                <code>{job.job_id}</code>
              </dd>
            </div>
            <div>
              <dt>Area ID</dt>
              <dd>
                <code>{job.area_id ?? "pending"}</code>
              </dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{job.created_at ?? "pending"}</dd>
            </div>
            <div>
              <dt>Finished</dt>
              <dd>{job.finished_at ?? "pending"}</dd>
            </div>
          </dl>

          {status === "failed" && (
            <div className="alert alert-error" role="alert">
              <h4>AOI analysis failed</h4>
              <p>
                {jobErrorMessage ?? "The job failed without a detailed error message."}
              </p>
              {jobErrorCode && (
                <p>
                  Error code: <code>{jobErrorCode}</code>
                </p>
              )}
            </div>
          )}

          {status === "partial" && (
            <div className="alert alert-warning" role="alert">
              <h4>Partial result</h4>
              <p>
                The terrain job finished, but at least one provider did not verify. Inspect
                the per-provider status before using this record.
              </p>
            </div>
          )}

          {status === "completed" && (
            <p className="field-hint">
              All reported providers verified. The result remains a user-requested
              on-demand AOI, not global coverage.
            </p>
          )}

          <h4>Per-provider status</h4>
          <ProviderStatusList providerStatus={providerStatus} />

          {warnings.length > 0 && (
            <div className="warnings">
              <h4>Warnings</h4>
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
                  <dt>Area</dt>
                  <dd>{formatNumber(areaKm2, 3)} km²</dd>
                </div>
              )}
              {featureCount !== null && (
                <div>
                  <dt>Similarity features</dt>
                  <dd>{formatNumber(featureCount, 0)}</dd>
                </div>
              )}
              {contextFeatureCount !== null && (
                <div>
                  <dt>Context features</dt>
                  <dd>{formatNumber(contextFeatureCount, 0)}</dd>
                </div>
              )}
            </dl>
          )}
        </section>
      )}

      {isTerminalSuccess && !location && !analysis.featureError && (
        <p className="status-panel" role="status">
          Loading AOI feature record…
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
              Use AOI features as search target
            </button>
            <p className="field-hint">
              Maps the AOI&apos;s observed terrain values into the manual target form and
              runs the same search API. The AOI itself remains searchable with its
              on-demand caveat.
            </p>
          </div>

          <section
            className="aoi-record"
            aria-labelledby="aoi-record-heading"
            data-testid="aoi-record"
          >
            <div className="section-heading compact">
              <p className="eyebrow">Completed on-demand record</p>
              <h3 id="aoi-record-heading">{location.display_name}</h3>
              <p className="selected-name">
                <DataKindBadge kind={location.data_kind} />{" "}
                <span className="badge badge-ondemand">On-demand AOI</span>
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
