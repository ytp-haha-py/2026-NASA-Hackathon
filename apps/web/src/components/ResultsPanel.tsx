import { useMemo } from "react";
import type {
  ContextFeatureOut,
  FeatureOut,
  SearchResponse,
  SearchResultOut,
} from "../api/client";
import {
  formatDistance,
  formatExclusionReason,
  formatNumber,
  formatSignedNumber,
  formatUnit,
} from "../lib/format";
import { DataKindBadge } from "./DataKindBadge";

type ResultsPanelProps = {
  response: SearchResponse;
  features: FeatureOut[];
  contextFeatures?: ContextFeatureOut[];
  selectedId: string | null;
  onSelect: (locationId: string) => void;
};

function ResultCard({
  result,
  featureLabels,
  selected,
  onSelect,
}: {
  result: SearchResultOut;
  featureLabels: Map<string, string>;
  selected: boolean;
  onSelect: (locationId: string) => void;
}) {
  return (
    <li>
      <article
        className={`result-card${selected ? " is-selected" : ""}`}
        data-selected={selected ? "true" : "false"}
        data-testid={`result-${result.location_id}`}
      >
        <button
          type="button"
          className="result-select"
          aria-pressed={selected}
          aria-label={`Select rank ${result.rank}: ${result.display_name}`}
          onClick={() => onSelect(result.location_id)}
        >
          <span className="result-rank">#{result.rank}</span>
          <span className="result-title">
            <strong>{result.display_name}</strong>
            <span className="result-location">
              {formatNumber(result.lat, 4)}°, {formatNumber(result.lon, 4)}°
            </span>
          </span>
          <span className="result-distance">
            <span className="distance-value">{formatDistance(result.distance)}</span>
            <span className="distance-label">distance</span>
          </span>
          <DataKindBadge kind={result.data_kind} />
          {result.caveats.some((caveat) => /on-demand/i.test(caveat)) && (
            <span className="badge badge-ondemand">On-demand AOI</span>
          )}
        </button>

        <div className="result-body">
          <div className="table-scroll">
            <table className="metric-table">
              <caption>
                Target comparison for {result.display_name}. Lower distance is more
                similar. Distance is not a percentage.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Metric</th>
                  <th scope="col">Candidate</th>
                  <th scope="col">Target</th>
                  <th scope="col">Delta</th>
                </tr>
              </thead>
              <tbody>
                {result.metrics.map((metric) => (
                  <tr key={metric.feature_id}>
                    <th scope="row">
                      <span>{featureLabels.get(metric.feature_id) ?? metric.feature_id}</span>
                      <span className="metric-meta">
                        weight {formatNumber(metric.weight)} · scale{" "}
                        {formatNumber(metric.scale)}
                      </span>
                    </th>
                    <td>
                      {formatNumber(metric.candidate)} {formatUnit(metric.unit)}
                    </td>
                    <td>
                      {formatNumber(metric.target)} {formatUnit(metric.unit)}
                    </td>
                    <td>
                      {formatSignedNumber(metric.delta)} {formatUnit(metric.unit)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {result.caveats.length > 0 && (
            <div className="result-caveats">
              <h4>Caveats for this record</h4>
              <ul>
                {result.caveats.map((caveat) => (
                  <li key={caveat}>{caveat}</li>
                ))}
              </ul>
            </div>
          )}

          {Object.keys(result.dimension_distances).length > 0 && (
            <p className="dimension-distances">
              Dimension distances:{" "}
              {Object.entries(result.dimension_distances)
                .map(([dimensionId, value]) => `${dimensionId} ${formatNumber(value, 4)}`)
                .join(" · ")}
            </p>
          )}

          <p className="result-datasets">
            Dataset IDs: {result.dataset_ids.length > 0 ? result.dataset_ids.join(", ") : "none reported"}
          </p>
        </div>
      </article>
    </li>
  );
}

export function ResultsPanel({
  response,
  features,
  contextFeatures = [],
  selectedId,
  onSelect,
}: ResultsPanelProps) {
  const featureLabels = useMemo(
    () => new Map(features.map((feature) => [feature.id, feature.label])),
    [features],
  );
  const contextLabels = useMemo(
    () => new Map(contextFeatures.map((feature) => [feature.feature_id, feature.label])),
    [contextFeatures],
  );
  const exclusionLabels = useMemo(
    () => new Map([...featureLabels, ...contextLabels]),
    [featureLabels, contextLabels],
  );
  const syntheticCount = response.results.filter(
    (result) => result.data_kind.toLowerCase() === "synthetic",
  ).length;
  const origins = response.coverage.origins;
  const indexedOriginCount = origins.indexed ?? 0;
  const onDemandOriginCount = origins.on_demand ?? 0;
  const hasOnDemandOrigin = onDemandOriginCount > 0;
  const requestedFeatureLabels = response.requested_features.map(
    (featureId) => featureLabels.get(featureId) ?? featureId,
  );
  const appliedFilters = Object.entries(response.filters_applied).map(
    ([featureId, value]) => {
      const label = contextLabels.get(featureId) ?? featureId;
      if (typeof value !== "object" || value === null) {
        return `${label}: ${String(value)}`;
      }
      const range = value as { min?: unknown; max?: unknown };
      const parts: string[] = [];
      if (typeof range.min === "number") {
        parts.push(`min ${formatNumber(range.min, 3)}`);
      }
      if (typeof range.max === "number") {
        parts.push(`max ${formatNumber(range.max, 3)}`);
      }
      return `${label}: ${parts.length > 0 ? parts.join(", ") : "applied"}`;
    },
  );

  return (
    <section className="results-panel" aria-labelledby="results-heading">
      <div className="section-heading">
        <p className="eyebrow">Step 2</p>
        <h2 id="results-heading">Ranked matches</h2>
      </div>

      <div className="coverage-panel">
        <p className="coverage-headline" data-testid="coverage-headline">
          Top {response.results.length} among {response.indexed_candidate_count}{" "}
          {hasOnDemandOrigin ? "indexed and on-demand" : "indexed"} regions
        </p>
        <p className="coverage-origins">
          Origin split at this response: <strong>{indexedOriginCount} indexed</strong> +{" "}
          <strong>{onDemandOriginCount} on-demand AOI</strong>. On-demand AOIs are
          user-created and are not global coverage.
        </p>
        <p>
          Eligible after metric availability checks:{" "}
          <strong>{response.eligible_candidate_count}</strong>. Excluded:{" "}
          <strong>{response.excluded_candidate_count}</strong>. Coverage label:{" "}
          <code>{response.coverage_label}</code>.
        </p>
        <p className="coverage-description">{response.coverage.description}</p>
        <p className="coverage-features">
          Requested similarity features:{" "}
          {requestedFeatureLabels.length > 0
            ? requestedFeatureLabels.join(", ")
            : "none reported"}
          . Context filters:{" "}
          {appliedFilters.length > 0 ? appliedFilters.join("; ") : "none"}.
        </p>
        <p className="coverage-warning">
          <strong>This is not a global top 10.</strong> It is the closest set within the
          searchable coverage only, not a complete ranking, not a survey, and not a
          base-site recommendation.
        </p>
        <p className="coverage-versions">
          Query {response.query_version} · method {response.method_version} · data{" "}
          {response.data_version} · snapshot {response.data_snapshot_id}
        </p>
        <p className="mobile-scroll-hint">
          On narrow screens, metric tables scroll horizontally to show every column.
        </p>
      </div>

      {syntheticCount > 0 && (
        <div className="alert alert-synthetic" role="alert">
          <strong>
            {syntheticCount} of {response.results.length} returned{" "}
            {syntheticCount === 1 ? "record is" : "records are"} DEMO / SYNTHETIC.
          </strong>{" "}
          Synthetic fixture values are not real terrain observations and must not be
          cited as evidence.
        </div>
      )}

      {response.results.length === 0 ? (
        <div className="empty-state" role="status">
          <h3>No eligible indexed candidates</h3>
          <p>
            The search reached the index, but no candidate met the target&apos;s metric
            availability or context-filter requirements. This does not mean no real place
            on Earth is similar; the searchable coverage is not a global survey.
          </p>
          {response.excluded_candidate_count > 0 && (
            <p>
              {response.excluded_candidate_count} candidate
              {response.excluded_candidate_count === 1 ? " was" : "s were"} excluded.
              See the reasons below.
            </p>
          )}
        </div>
      ) : (
        <ol className="results-list">
          {response.results.map((result) => (
            <ResultCard
              key={result.location_id}
              result={result}
              featureLabels={featureLabels}
              selected={result.location_id === selectedId}
              onSelect={onSelect}
            />
          ))}
        </ol>
      )}

      <div className="excluded-panel">
        {response.excluded_candidate_count === 0 ? (
          <p>No indexed candidates were excluded for missing metrics.</p>
        ) : (
          <details>
            <summary>
              {response.excluded_candidate_count} excluded candidate
              {response.excluded_candidate_count === 1 ? "" : "s"} — show reasons
            </summary>
            <ul>
              {response.excluded.map((exclusion) => (
                <li key={`${exclusion.location_id}-${exclusion.reason}`}>
                  <code>{exclusion.location_id}</code>:{" "}
                  {formatExclusionReason(exclusion.reason, exclusionLabels)}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {response.limitations.length > 0 && (
        <div className="limitations-note">
          <h3>Search limitations returned by the API</h3>
          <ul>
            {response.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
