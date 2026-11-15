import { useMemo } from "react";
import type {
  ContextFeatureOut,
  FeatureOut,
  SearchResponse,
  SearchResultOut,
} from "../api/client";
import { SourceText, SourceTextNote, useI18n } from "../i18n/I18nProvider";
import { DataKindBadge } from "./DataKindBadge";

type ResultsPanelProps = {
  response: SearchResponse;
  features: FeatureOut[];
  contextFeatures?: ContextFeatureOut[];
  selectedId: string | null;
  onSelect: (locationId: string) => void;
  selectedOnly?: boolean;
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
  const { t, fmt, label } = useI18n();

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
          aria-label={t("results.selectLabel", {
            rank: result.rank,
            name: result.display_name,
          })}
          onClick={() => onSelect(result.location_id)}
        >
          <span className="result-rank">#{result.rank}</span>
          <span className="result-title">
            <strong>{result.display_name}</strong>
            <span className="result-location">
              {fmt.number(result.lat, 4)}°, {fmt.number(result.lon, 4)}°
            </span>
          </span>
          <span className="result-distance">
            <span className="distance-value">{fmt.distance(result.distance)}</span>
            <span className="distance-label">{t("results.distance")}</span>
          </span>
          <DataKindBadge kind={result.data_kind} />
          {result.caveats.some((caveat) => /on-demand/i.test(caveat)) && (
            <span className="badge badge-ondemand">{t("results.onDemandBadge")}</span>
          )}
        </button>

        <div className="result-body">
          <div className="table-scroll">
            <table className="metric-table">
              <caption>{t("results.tableCaption", { name: result.display_name })}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("results.colMetric")}</th>
                  <th scope="col">{t("results.colCandidate")}</th>
                  <th scope="col">{t("results.colTarget")}</th>
                  <th scope="col">{t("results.colDelta")}</th>
                </tr>
              </thead>
              <tbody>
                {result.metrics.map((metric) => (
                  <tr key={metric.feature_id}>
                    <th scope="row">
                      <span>
                        {label.feature(metric.feature_id, featureLabels.get(metric.feature_id))}
                      </span>
                      <span className="metric-meta">
                        {t("results.weightScale", {
                          weight: fmt.number(metric.weight),
                          scale: fmt.number(metric.scale),
                        })}
                      </span>
                    </th>
                    <td>{fmt.withUnit(metric.candidate, metric.unit)}</td>
                    <td>{fmt.withUnit(metric.target, metric.unit)}</td>
                    <td>
                      {fmt.signed(metric.delta)} {fmt.unit(metric.unit)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {result.caveats.length > 0 && (
            <div className="result-caveats">
              <h4>{t("results.caveats")}</h4>
              <SourceTextNote />
              <ul>
                {result.caveats.map((caveat) => (
                  <li key={caveat}>
                    <SourceText>{caveat}</SourceText>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {Object.keys(result.dimension_distances).length > 0 && (
            <p className="dimension-distances">
              {t("results.dimensionDistances", {
                values: Object.entries(result.dimension_distances)
                  .map(
                    ([dimensionId, value]) =>
                      `${label.dimension(dimensionId)} ${fmt.number(value, 4)}`,
                  )
                  .join(" · "),
              })}
            </p>
          )}

          <p className="result-datasets">
            {t("results.datasetIds", {
              ids:
                result.dataset_ids.length > 0
                  ? result.dataset_ids.join(", ")
                  : t("common.noneReported"),
            })}
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
  selectedOnly = false,
}: ResultsPanelProps) {
  const { t, tp, rich, fmt, label } = useI18n();
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
  const shownResults = selectedOnly
    ? response.results.filter((result) => result.location_id === selectedId)
    : response.results;
  const origins = response.coverage.origins;
  const indexedOriginCount = origins.indexed ?? 0;
  const onDemandOriginCount = origins.on_demand ?? 0;
  const hasOnDemandOrigin = onDemandOriginCount > 0;
  const requestedFeatureLabels = response.requested_features.map((featureId) =>
    label.feature(featureId, featureLabels.get(featureId)),
  );
  const appliedFilters = Object.entries(response.filters_applied).map(
    ([featureId, value]) => {
      const filterLabel = label.feature(featureId, contextLabels.get(featureId));
      if (typeof value !== "object" || value === null) {
        return `${filterLabel}: ${String(value)}`;
      }
      const range = value as { min?: unknown; max?: unknown };
      const parts: string[] = [];
      if (typeof range.min === "number") {
        parts.push(t("results.filterMin", { value: fmt.number(range.min, 3) }));
      }
      if (typeof range.max === "number") {
        parts.push(t("results.filterMax", { value: fmt.number(range.max, 3) }));
      }
      return `${filterLabel}: ${parts.length > 0 ? parts.join(", ") : t("results.filterApplied")}`;
    },
  );

  return (
    <section className="results-panel" aria-labelledby="results-heading">
      <div className="section-heading">
        <p className="eyebrow">{t("results.step")}</p>
        <h2 id="results-heading">{t("results.heading")}</h2>
      </div>

      <div className="coverage-panel">
        <p className="coverage-headline" data-testid="coverage-headline">
          {t("results.headline", {
            shown: fmt.number(response.results.length, 0),
            total: fmt.number(response.indexed_candidate_count, 0),
            scope: hasOnDemandOrigin ? t("results.scopeMixed") : t("results.scopeIndexed"),
          })}
        </p>
        <p className="coverage-origins">
          {rich("results.originSplit", {
            indexed: (
              <strong>
                {t("coverage.indexedCount", { count: fmt.number(indexedOriginCount, 0) })}
              </strong>
            ),
            onDemand: (
              <strong>
                {t("coverage.onDemandCount", { count: fmt.number(onDemandOriginCount, 0) })}
              </strong>
            ),
          })}
        </p>
        <p>
          {rich("results.eligibility", {
            eligible: <strong>{fmt.number(response.eligible_candidate_count, 0)}</strong>,
            excluded: <strong>{fmt.number(response.excluded_candidate_count, 0)}</strong>,
            label: <code>{response.coverage_label}</code>,
          })}
        </p>
        <p className="coverage-description">
          <SourceText>{response.coverage.description}</SourceText>
        </p>
        <p className="coverage-features">
          {t("results.requestedFeatures", {
            features:
              requestedFeatureLabels.length > 0
                ? requestedFeatureLabels.join(", ")
                : t("common.noneReported"),
            filters: appliedFilters.length > 0 ? appliedFilters.join("; ") : t("common.none"),
          })}
        </p>
        <p className="coverage-warning">
          <strong>{t("results.notGlobalTitle")}</strong> {t("results.notGlobalBody")}
        </p>
        <p className="coverage-versions">
          {t("results.versions", {
            query: response.query_version,
            method: response.method_version,
            data: response.data_version,
            snapshot: response.data_snapshot_id,
          })}
        </p>
        <p className="mobile-scroll-hint">{t("results.scrollHint")}</p>
      </div>

      {syntheticCount > 0 && (
        <div className="alert alert-synthetic" role="alert">
          <strong>
            {tp("results.syntheticCount", syntheticCount, {
              total: fmt.number(response.results.length, 0),
            })}
          </strong>{" "}
          {t("results.syntheticBody")}
        </div>
      )}

      {response.results.length === 0 ? (
        <div className="empty-state" role="status">
          <h3>{t("results.emptyTitle")}</h3>
          <p>{t("results.emptyBody")}</p>
          {response.excluded_candidate_count > 0 && (
            <p>{tp("results.emptyExcluded", response.excluded_candidate_count)}</p>
          )}
        </div>
      ) : shownResults.length > 0 ? (
        <ol className="results-list">
          {shownResults.map((result) => (
            <ResultCard
              key={result.location_id}
              result={result}
              featureLabels={featureLabels}
              selected={result.location_id === selectedId}
              onSelect={onSelect}
            />
          ))}
        </ol>
      ) : null}

      <div className="excluded-panel">
        {response.excluded_candidate_count === 0 ? (
          <p>{t("results.noneExcluded")}</p>
        ) : (
          <details>
            <summary>{tp("results.excludedSummary", response.excluded_candidate_count)}</summary>
            <ul>
              {response.excluded.map((exclusion) => (
                <li key={`${exclusion.location_id}-${exclusion.reason}`}>
                  <code>{exclusion.location_id}</code>:{" "}
                  {fmt.exclusionReason(exclusion.reason, exclusionLabels)}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {response.limitations.length > 0 && (
        <div className="limitations-note">
          <h3>{t("results.limitations")}</h3>
          <SourceTextNote />
          <ul>
            {response.limitations.map((limitation) => (
              <li key={limitation}>
                <SourceText>{limitation}</SourceText>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
