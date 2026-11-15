import { useMemo, useState } from "react";
import type { FeatureOut, NumericTarget, SearchResponse } from "../api/client";
import { useI18n } from "../i18n/I18nProvider";
import { DataKindBadge } from "./DataKindBadge";

type RankingOverlayProps = {
  response: SearchResponse;
  features: FeatureOut[];
  target: NumericTarget;
  selectedId: string | null;
  onSelect: (locationId: string) => void;
};

type DimensionGroup = {
  dimensionId: string;
  features: FeatureOut[];
};

/** Groups similarity features by `dimension_id`, keeping the API's feature order. */
function groupByDimension(features: FeatureOut[]): DimensionGroup[] {
  const groups: DimensionGroup[] = [];
  for (const feature of features) {
    const existing = groups.find((group) => group.dimensionId === feature.dimension_id);
    if (existing) {
      existing.features.push(feature);
    } else {
      groups.push({ dimensionId: feature.dimension_id, features: [feature] });
    }
  }
  return groups;
}

/**
 * Floating ranking window over the map.
 *
 * Compact: the ranked list (rank, name, latitude, distance). Expanded: a table of target and
 * candidate values grouped by dimension, where each dimension's columns can be collapsed.
 * Only values returned by POST /search are shown; nothing is computed or filled in here.
 */
export function RankingOverlay({
  response,
  features,
  target,
  selectedId,
  onSelect,
}: RankingOverlayProps) {
  const { t, fmt, label } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const groups = useMemo(() => groupByDimension(features), [features]);
  const [openGroups, setOpenGroups] = useState<Set<string>>(
    () => new Set(groups.slice(0, 1).map((group) => group.dimensionId)),
  );

  const results = response.results;

  function toggleGroup(dimensionId: string) {
    setOpenGroups((current) => {
      const next = new Set(current);
      if (next.has(dimensionId)) {
        next.delete(dimensionId);
      } else {
        next.add(dimensionId);
      }
      return next;
    });
  }

  function latitude(value: number): string {
    return `${fmt.number(Math.abs(value), 1)}°${value >= 0 ? "N" : "S"}`;
  }

  return (
    <section
      className={`ranking-overlay${expanded ? " is-expanded" : ""}`}
      aria-label={t("overlay.regionLabel")}
    >
      <div className="ranking-overlay-head">
        <div className="ranking-overlay-title">
          <strong>{t("overlay.heading", { count: fmt.number(results.length, 0) })}</strong>
          <span>
            {t("overlay.total", { total: fmt.number(response.indexed_candidate_count, 0) })}
          </span>
        </div>
        {results.length > 0 && (
          <button
            type="button"
            className="overlay-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? t("overlay.collapse") : t("overlay.expand")}
          </button>
        )}
      </div>

      {results.length === 0 && <p className="ranking-overlay-empty">{t("overlay.empty")}</p>}

      {results.length > 0 && !expanded && (
        <ol className="ranking-list">
          {results.map((result) => {
            const selected = result.location_id === selectedId;
            return (
              <li key={result.location_id}>
                <button
                  type="button"
                  className={`ranking-row${selected ? " is-selected" : ""}`}
                  aria-pressed={selected}
                  aria-label={t("overlay.selectLabel", {
                    rank: result.rank,
                    name: result.display_name,
                  })}
                  onClick={() => onSelect(result.location_id)}
                >
                  <span className="ranking-rank">{String(result.rank).padStart(2, "0")}</span>
                  <span className="ranking-name">
                    <strong>{result.display_name}</strong>
                    <span>
                      {latitude(result.lat)}
                      {result.data_kind.toLowerCase() !== "observed" && (
                        <>
                          {" "}
                          <DataKindBadge kind={result.data_kind} />
                        </>
                      )}
                    </span>
                  </span>
                  <span className="ranking-distance">{fmt.distance(result.distance)}</span>
                </button>
              </li>
            );
          })}
        </ol>
      )}

      {results.length > 0 && expanded && (
        <div className="ranking-table-scroll">
          <table className="ranking-table">
            <caption className="sr-only">{t("overlay.tableCaption")}</caption>
            <thead>
              <tr>
                <td colSpan={4} />
                {groups.map((group) => {
                  const open = openGroups.has(group.dimensionId);
                  const name = label.dimension(group.dimensionId);
                  return (
                    <th
                      key={group.dimensionId}
                      scope="colgroup"
                      colSpan={open ? group.features.length : 1}
                      className="group-start"
                    >
                      <button
                        type="button"
                        className="group-toggle"
                        aria-expanded={open}
                        aria-label={t("overlay.groupToggle", { dimension: name })}
                        onClick={() => toggleGroup(group.dimensionId)}
                      >
                        <span aria-hidden="true">{open ? "▾" : "▸"}</span> {name}
                      </button>
                    </th>
                  );
                })}
              </tr>
              <tr>
                <th scope="col">{t("overlay.colRank")}</th>
                <th scope="col">{t("overlay.colName")}</th>
                <th scope="col" className="num">
                  {t("overlay.colLat")}
                </th>
                <th scope="col" className="num">
                  {t("overlay.colDistance")}
                </th>
                {groups.map((group) =>
                  openGroups.has(group.dimensionId) ? (
                    group.features.map((feature, index) => (
                      <th
                        key={feature.id}
                        scope="col"
                        className={`num${index === 0 ? " group-start" : ""}`}
                      >
                        {label.feature(feature.id, feature.label)} ({fmt.unit(feature.unit)})
                      </th>
                    ))
                  ) : (
                    <th key={group.dimensionId} scope="col" className="group-start">
                      <span className="sr-only">{label.dimension(group.dimensionId)}</span>
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              <tr className="ranking-target-row">
                <td>
                  <span className="target-mark" aria-hidden="true" />
                </td>
                <th scope="row">{t("overlay.target")}</th>
                <td className="num">—</td>
                <td className="num">0</td>
                {groups.map((group) =>
                  openGroups.has(group.dimensionId) ? (
                    group.features.map((feature, index) => (
                      <td
                        key={feature.id}
                        className={`num${index === 0 ? " group-start" : ""}`}
                      >
                        {typeof target[feature.id] === "number"
                          ? fmt.number(target[feature.id])
                          : "—"}
                      </td>
                    ))
                  ) : (
                    <td key={group.dimensionId} className="group-start collapsed-cell">
                      ·
                    </td>
                  ),
                )}
              </tr>
              {results.map((result) => {
                const selected = result.location_id === selectedId;
                const metrics = new Map(
                  result.metrics.map((metric) => [metric.feature_id, metric]),
                );
                return (
                  <tr key={result.location_id} className={selected ? "is-selected" : undefined}>
                    <td className="ranking-rank">{String(result.rank).padStart(2, "0")}</td>
                    <th scope="row">
                      <button
                        type="button"
                        className="ranking-name-button"
                        aria-pressed={selected}
                        onClick={() => onSelect(result.location_id)}
                      >
                        {result.display_name}
                      </button>{" "}
                      {result.data_kind.toLowerCase() !== "observed" && (
                        <DataKindBadge kind={result.data_kind} />
                      )}
                    </th>
                    <td className="num">{latitude(result.lat)}</td>
                    <td className="num strong">{fmt.distance(result.distance)}</td>
                    {groups.map((group) =>
                      openGroups.has(group.dimensionId) ? (
                        group.features.map((feature, index) => {
                          const metric = metrics.get(feature.id);
                          const compared = metric !== undefined && metric.weight > 0;
                          return (
                            <td
                              key={feature.id}
                              className={`num${index === 0 ? " group-start" : ""}${
                                compared ? " is-compared" : " is-muted"
                              }`}
                              title={compared ? undefined : t("overlay.notCompared")}
                            >
                              {metric ? fmt.number(metric.candidate) : "—"}
                            </td>
                          );
                        })
                      ) : (
                        <td key={group.dimensionId} className="group-start collapsed-cell">
                          ·
                        </td>
                      ),
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
