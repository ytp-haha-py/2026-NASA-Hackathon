import type {
  FeatureOut,
  LocationOut,
  MetricOut,
  NumericTarget,
} from "../api/client";
import {
  formatBBox,
  formatCoordinate,
  formatNumber,
  formatPercent,
  formatSignedNumber,
  formatUnit,
} from "../lib/format";
import { ContextExport } from "./ContextExport";
import { DataKindBadge } from "./DataKindBadge";

type LocationDetailsProps = {
  location: LocationOut;
  comparisonMetrics: MetricOut[];
  activeTarget: NumericTarget;
  features: FeatureOut[];
  sourceLabel: string;
  idPrefix: string;
};

const NOT_COMPARED = [
  "Gravity",
  "Atmosphere",
  "Radiation",
  "Regolith",
  "Mineralogy",
  "Temperature",
  "Geological history",
];

function DatasetFacts({
  dataset,
}: {
  dataset: LocationOut["datasets"][number];
}) {
  return (
    <article className="dataset-facts">
      <h4>{dataset.title ?? dataset.product_id}</h4>
      <dl>
        <div>
          <dt>Provider</dt>
          <dd>{dataset.provider}</dd>
        </div>
        <div>
          <dt>Product ID</dt>
          <dd>
            <code>{dataset.product_id}</code>
          </dd>
        </div>
        <div>
          <dt>Dataset ID</dt>
          <dd>
            <code>{dataset.dataset_id}</code>
          </dd>
        </div>
        <div>
          <dt>Access timestamp</dt>
          <dd>
            <time dateTime={dataset.access_timestamp}>{dataset.access_timestamp}</time>
          </dd>
        </div>
        <div>
          <dt>License</dt>
          <dd>
            <a href={dataset.license_url} target="_blank" rel="noreferrer">
              {dataset.license}
            </a>
          </dd>
        </div>
        <div>
          <dt>Processing version</dt>
          <dd>{dataset.processing_version}</dd>
        </div>
        <div>
          <dt>Source kind</dt>
          <dd>{dataset.source_kind}</dd>
        </div>
        <div>
          <dt>Source CRS</dt>
          <dd>{dataset.source_crs ?? "not reported"}</dd>
        </div>
        <div>
          <dt>Native resolution</dt>
          <dd>
            {dataset.native_resolution_m === null
              ? "not reported"
              : `${formatNumber(dataset.native_resolution_m, 1)} m`}
          </dd>
        </div>
        <div>
          <dt>Checksum</dt>
          <dd>{dataset.checksum ?? "not reported"}</dd>
        </div>
      </dl>
      <p>
        <a href={dataset.url} target="_blank" rel="noreferrer">
          Source product link
        </a>
      </p>
      {dataset.notes && <p className="dataset-notes">{dataset.notes}</p>}
    </article>
  );
}

function geometrySummary(geometry: LocationOut["geometry"]): string {
  if (!geometry) {
    return "BBox-only request (no polygon geometry)";
  }
  const type = typeof geometry.type === "string" ? geometry.type : "Geometry";
  const coordinates = geometry.coordinates;
  if (Array.isArray(coordinates) && Array.isArray(coordinates[0])) {
    return `${type} (${coordinates[0].length} outer-ring positions)`;
  }
  return type;
}

export function LocationDetails({
  location,
  comparisonMetrics,
  activeTarget,
  features,
  sourceLabel,
  idPrefix,
}: LocationDetailsProps) {
  const featureLabels = new Map(features.map((feature) => [feature.id, feature.label]));
  const contextObservations = [...location.context_observations].sort(
    (a, b) => a.class_code - b.class_code,
  );

  return (
    <>
      <dl className="facts-grid">
        <div>
          <dt>Coordinates</dt>
          <dd>
            {formatCoordinate(location.lat)}, {formatCoordinate(location.lon)}
          </dd>
        </div>
        <div>
          <dt>Data kind</dt>
          <dd>
            <DataKindBadge kind={location.data_kind} />
          </dd>
        </div>
        <div>
          <dt>Origin</dt>
          <dd>
            {location.origin === "on_demand"
              ? "On-demand AOI (user-requested; not fixed coverage)"
              : "Indexed Earth window"}
          </dd>
        </div>
        {location.area_km2 !== null && (
          <div>
            <dt>AOI area</dt>
            <dd>{formatNumber(location.area_km2, 3)} km²</dd>
          </div>
        )}
        <div>
          <dt>Analysis window</dt>
          <dd>
            {formatNumber(location.window_size_m)} m at{" "}
            {formatNumber(location.analysis_resolution_m)} m resolution
          </dd>
        </div>
        <div>
          <dt>Window bbox</dt>
          <dd>{formatBBox(location.bbox)}</dd>
        </div>
        <div>
          <dt>CRS</dt>
          <dd>{location.crs}</dd>
        </div>
        <div>
          <dt>Valid fraction</dt>
          <dd>{formatPercent(location.valid_fraction)}</dd>
        </div>
        <div>
          <dt>Quality flags</dt>
          <dd>
            {location.quality_flags.length > 0 ? (
              <ul className="flag-list">
                {location.quality_flags.map((flag) => (
                  <li key={flag}>
                    <code>{flag}</code>
                  </li>
                ))}
              </ul>
            ) : (
              "none reported"
            )}
          </dd>
        </div>
        <div>
          <dt>Versions</dt>
          <dd>
            contract {location.contract_version} · method {location.method_version} · data{" "}
            {location.data_version}
            <span className="metric-meta">
              snapshot {location.data_snapshot_id}
            </span>
          </dd>
        </div>
        {location.origin === "on_demand" && (
          <div>
            <dt>Geometry</dt>
            <dd>{geometrySummary(location.geometry)}</dd>
          </div>
        )}
      </dl>

      {location.notes && (
        <p className="record-note">
          <strong>API record note:</strong> {location.notes}
        </p>
      )}

      {location.origin === "on_demand" && (
        <p className="record-note on-demand-note">
          <strong>On-demand AOI:</strong> this window was requested by a user and is
          searchable, but it is not part of a fixed global index or a complete survey.
        </p>
      )}

      <section className="detail-section" aria-labelledby={`${idPrefix}-features-heading`}>
        <h3 id={`${idPrefix}-features-heading`}>Observed terrain-shape features</h3>
        <p className="muted">
          Values as returned by {sourceLabel}. Units and valid fractions are record facts,
          not UI conversions.
        </p>
        <p className="mobile-scroll-hint">
          On narrow screens, record tables scroll horizontally.
        </p>
        {location.features.length === 0 ? (
          <p>No feature observations were returned for this location.</p>
        ) : (
          <div className="table-scroll">
            <table className="feature-table">
              <caption className="sr-only">
                Terrain-shape feature observations for {location.display_name}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Feature</th>
                  <th scope="col">Value</th>
                  <th scope="col">Quality</th>
                  <th scope="col">Valid fraction</th>
                  <th scope="col">Extraction scale</th>
                </tr>
              </thead>
              <tbody>
                {location.features.map((feature) => (
                  <tr key={feature.feature_id}>
                    <th scope="row">
                      <span>{feature.label}</span>
                      <span className="metric-meta">{feature.feature_id}</span>
                      <span className="feature-description">{feature.description}</span>
                    </th>
                    <td>
                      {formatNumber(feature.value)} {formatUnit(feature.unit)}
                    </td>
                    <td>{feature.quality}</td>
                    <td>{formatPercent(feature.valid_fraction)}</td>
                    <td>{formatNumber(feature.extraction_scale_m, 0)} m</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="detail-section" aria-labelledby={`${idPrefix}-comparison-heading`}>
        <h3 id={`${idPrefix}-comparison-heading`}>Requested target comparison</h3>
        <p className="muted">
          These comparisons come from the search response, not from a new calculation.
          &ldquo;Close&rdquo; is a display aid for |delta| ≤ 0.5 × the fixed metric scale;
          it is not a scientific significance threshold.
        </p>
        {comparisonMetrics.length === 0 ? (
          <p>
            This record was not returned by a search request, so no target comparison is
            available. Use the AOI as a search target to compare it with the index.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="comparison-table">
              <caption className="sr-only">
                Requested target versus candidate for {location.display_name}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Metric</th>
                  <th scope="col">Candidate</th>
                  <th scope="col">Target</th>
                  <th scope="col">Delta</th>
                  <th scope="col">Display</th>
                </tr>
              </thead>
              <tbody>
                {comparisonMetrics.map((metric) => {
                  const close = Math.abs(metric.normalized_delta) <= 0.5;
                  return (
                    <tr key={metric.feature_id}>
                      <th scope="row">
                        {featureLabels.get(metric.feature_id) ?? metric.feature_id}
                        <span className="metric-meta">
                          normalized delta{" "}
                          {formatSignedNumber(metric.normalized_delta, 3)} · scale{" "}
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
                      <td>
                        <span
                          className={`comparison-chip ${close ? "is-close" : "is-different"}`}
                        >
                          {close ? "Close" : "Differs"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section
        className="detail-section context-only-section"
        aria-labelledby={`${idPrefix}-context-heading`}
      >
        <h3 id={`${idPrefix}-context-heading`}>
          Earth land-cover context (ESA WorldCover 2021) — context only, NOT
          mineralogy/geology, excluded from the similarity score
        </h3>
        <p className="context-warning">
          These class fractions describe Earth land cover for this footprint. They are
          context only: they are not mineralogy, not geology, and are never entered into
          the weighted normalized Euclidean distance.
        </p>
        {contextObservations.length === 0 ? (
          <p>No land-cover context observations were returned for this location.</p>
        ) : (
          <div className="table-scroll">
            <table className="context-table">
              <caption className="sr-only">
                ESA WorldCover land-cover context observations for {location.display_name}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Land-cover class</th>
                  <th scope="col">Class code</th>
                  <th scope="col">Fraction</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Valid fraction</th>
                  <th scope="col">Source resolution</th>
                </tr>
              </thead>
              <tbody>
                {contextObservations.map((observation) => (
                  <tr key={observation.feature_id}>
                    <th scope="row">{observation.label}</th>
                    <td>{observation.class_code}</td>
                    <td>{formatPercent(observation.value)}</td>
                    <td>{observation.provider}</td>
                    <td>{formatPercent(observation.valid_fraction)}</td>
                    <td>{formatNumber(observation.source_resolution_m, 0)} m</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="context-warning">
          Context observations are shown separately and are not counted as similarity
          metrics or displayed in the comparison table above.
        </p>
      </section>

      <section className="detail-section" aria-labelledby={`${idPrefix}-provenance-heading`}>
        <h3 id={`${idPrefix}-provenance-heading`}>Source, license and access</h3>
        {location.datasets.length === 0 ? (
          <p>No dataset provenance records were returned for this location.</p>
        ) : (
          <div className="dataset-list">
            {location.datasets.map((dataset) => (
              <DatasetFacts key={dataset.dataset_id} dataset={dataset} />
            ))}
          </div>
        )}
      </section>

      <section
        className="detail-section"
        aria-labelledby={`${idPrefix}-record-limits-heading`}
      >
        <h3 id={`${idPrefix}-record-limits-heading`}>
          Record limitations returned by the API
        </h3>
        {location.known_limitations.length === 0 ? (
          <p>No record-specific limitations were returned.</p>
        ) : (
          <ul>
            {location.known_limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        )}
      </section>

      <section
        className="detail-section"
        aria-labelledby={`${idPrefix}-general-limits-heading`}
      >
        <h3 id={`${idPrefix}-general-limits-heading`}>
          General limitations (not record-specific)
        </h3>
        <ul>
          <li>Terrain-shape similarity is not physical or environmental equivalence.</li>
          <li>
            Distance is a weighted normalized Euclidean value where lower is more similar.
            It is not a similarity percentage and has no physical unit.
          </li>
          <li>
            Metrics describe only a 1,980 m window at 90 m analysis resolution; smaller
            landforms and processes are not resolved or compared.
          </li>
          <li>
            Source DEMs are surface models. Vegetation or buildings can bias slope and
            roughness in non-bare terrain.
          </li>
          <li>
            Only the indexed subset plus user-requested on-demand AOIs are searched.
            Absence from results is not evidence that no similar terrain exists. On-demand
            AOIs are never global coverage.
          </li>
          <li>
            ESA WorldCover fractions are Earth land-cover context from a 2021 epoch. They
            are not mineralogy or geology and are not comparable to lunar surface
            materials.
          </li>
          <li>
            DEMO / SYNTHETIC records are fixtures, not observations, and must not be cited
            as evidence.
          </li>
        </ul>
      </section>

      <aside className="not-simulated" aria-labelledby={`${idPrefix}-not-simulated-heading`}>
        <h3 id={`${idPrefix}-not-simulated-heading`}>
          What is NOT simulated or compared
        </h3>
        <p>
          This tool does not simulate or compare the following. It makes no habitability,
          engineering, operational, or mission-suitability claim. Land-cover fractions do
          not substitute for any of these dimensions.
        </p>
        <ul>
          {NOT_COMPARED.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </aside>

      <ContextExport locationId={location.location_id} target={activeTarget} />
    </>
  );
}
