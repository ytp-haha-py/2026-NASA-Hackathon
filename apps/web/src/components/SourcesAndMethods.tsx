import type { CapabilitiesOut, DatasetOut } from "../api/client";
import { providerStatusEntries, readAoiLimits } from "../lib/aoi";
import { formatBBox, formatNumber } from "../lib/format";
import { DataKindBadge } from "./DataKindBadge";

type SourcesAndMethodsProps = {
  capabilities: CapabilitiesOut;
  datasets: DatasetOut[];
  datasetsError: string | null;
};

function analysisNumber(
  analysis: CapabilitiesOut["analysis"],
  key: string,
): number | null {
  const value = analysis[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function SourcesAndMethods({
  capabilities,
  datasets,
  datasetsError,
}: SourcesAndMethodsProps) {
  const windowSize = analysisNumber(capabilities.analysis, "window_size_m");
  const resolution = analysisNumber(capabilities.analysis, "analysis_resolution_m");
  const coverage = capabilities.coverage;
  const origins = coverage.origins;
  const aoiLimits = readAoiLimits(capabilities.aoi_analysis);
  const aoiProviders = providerStatusEntries(aoiLimits.providerStatus);

  return (
    <section className="sources-methods" id="sources-and-methods" aria-labelledby="sources-heading">
      <div className="section-heading">
        <p className="eyebrow">Provenance</p>
        <h2 id="sources-heading">Sources, coverage and methods</h2>
      </div>

      <div className="coverage-disclosure">
        <h3>Indexing and coverage disclaimer</h3>
        <p>{coverage.description}</p>
        <ul>
          <li>
            Searchable candidate regions:{" "}
            <strong>{coverage.indexed_candidate_count}</strong> (coverage label{" "}
            <code>{coverage.label}</code>).
          </li>
          <li>
            Origin split: <strong>{origins.indexed ?? 0} indexed</strong> +{" "}
            <strong>{origins.on_demand ?? 0} on-demand AOI</strong>. On-demand AOIs are
            user-created windows and are never global coverage.
          </li>
          <li>
            Geographic extent:{" "}
            {coverage.geographic_bbox
              ? formatBBox(coverage.geographic_bbox)
              : "not available — there may be no searchable windows."}
          </li>
          <li>
            Data kinds present:{" "}
            {coverage.data_kinds.length > 0
              ? coverage.data_kinds.join(", ")
              : "none reported"}
            .
          </li>
          <li>
            Results are the closest matches among the searchable coverage only. This is
            not a global survey, not an exhaustive top-10, and not a base-site
            recommendation. Absence from results does not mean a location is a poor analog.
          </li>
        </ul>
      </div>

      <div className="methods-block">
        <h3>Analysis method</h3>
        <dl className="facts-grid">
          <div>
            <dt>Contract version</dt>
            <dd>{capabilities.contract_version}</dd>
          </div>
          <div>
            <dt>Method version</dt>
            <dd>{capabilities.method_version}</dd>
          </div>
          <div>
            <dt>Analysis window</dt>
            <dd>{windowSize === null ? "not reported" : `${formatNumber(windowSize)} m`}</dd>
          </div>
          <div>
            <dt>Analysis resolution</dt>
            <dd>
              {resolution === null ? "not reported" : `${formatNumber(resolution)} m`}
            </dd>
          </div>
          <div>
            <dt>Data version</dt>
            <dd>{capabilities.data_version}</dd>
          </div>
          <div>
            <dt>Data snapshot</dt>
            <dd>
              <code>{capabilities.data_snapshot_id}</code>
            </dd>
          </div>
          <div>
            <dt>On-demand AOI limits</dt>
            <dd>
              {formatNumber(aoiLimits.minSideM, 0)}–{formatNumber(aoiLimits.maxSideM, 0)} m
              per side · ≤ {formatNumber(aoiLimits.maxAreaKm2, 1)} km² · latitude −80° to
              84°
            </dd>
          </div>
          <div>
            <dt>Distance</dt>
            <dd>
              Weighted normalized Euclidean; lower is more similar. Not a percentage.
            </dd>
          </div>
        </dl>

        {aoiProviders.length > 0 && (
          <div className="aoi-provider-readiness">
            <h4>AOI provider readiness</h4>
            <ul>
              {aoiProviders.map(([providerId, status]) => (
                <li key={providerId}>
                  <code>{providerId}</code>: <strong>{status.status ?? "unknown"}</strong>
                  {status.role ? ` — ${status.role}` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="method-features">
          {capabilities.features.map((feature) => (
            <article key={feature.id} className="method-feature">
              <h4>
                {feature.label} <span className="unit-tag">{feature.unit}</span>
              </h4>
              <p>{feature.description}</p>
              <dl>
                <div>
                  <dt>Valid domain</dt>
                  <dd>{feature.valid_domain}</dd>
                </div>
                <div>
                  <dt>Fixed scale</dt>
                  <dd>{formatNumber(feature.scale)}</dd>
                </div>
                <div>
                  <dt>Scale rationale</dt>
                  <dd>{feature.scale_rationale}</dd>
                </div>
                <div>
                  <dt>Extraction scale</dt>
                  <dd>{formatNumber(feature.extraction_scale_m)} m</dd>
                </div>
                <div>
                  <dt>Method</dt>
                  <dd>{feature.method}</dd>
                </div>
                <div>
                  <dt>Missing policy</dt>
                  <dd>{feature.missing_policy}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>

        <div className="context-methods">
          <h3>Context-only land cover (excluded from similarity)</h3>
          <p>
            ESA WorldCover 2021 v200 class fractions are Earth land-cover context. They are
            not mineralogy or geology, are not comparable to lunar surface materials, and
            are never included in the weighted normalized Euclidean distance.
          </p>
          <details>
            <summary>
              Show {capabilities.context_features.length} context-only feature definitions
            </summary>
            <ul className="context-feature-list">
              {capabilities.context_features.map((feature) => (
                <li key={feature.feature_id}>
                  <h4>
                    {feature.label} <span className="unit-tag">class {feature.class_code}</span>
                  </h4>
                  <p>{feature.description}</p>
                  <p className="field-hint">
                    Role: <code>{feature.score_role}</code> · unit: {feature.unit} · method:{" "}
                    {feature.method_version}
                  </p>
                  <p className="field-hint">{feature.quality_rules}</p>
                  <p>
                    <a href={feature.reference} target="_blank" rel="noreferrer">
                      ESA WorldCover reference
                    </a>
                  </p>
                </li>
              ))}
            </ul>
          </details>
          {capabilities.context_features.length > 0 && (
            <p className="field-hint">
              Data attribution: © ESA WorldCover project 2021 / Contains modified
              Copernicus Sentinel data (2021) processed by ESA WorldCover consortium.
            </p>
          )}
        </div>
      </div>

      <div className="datasets-block">
        <h3>Dataset provenance</h3>
        <p className="muted">
          Records below come directly from GET /datasets. They identify the source,
          license, access time, and processing version; they are not decorative imagery.
        </p>

        {datasetsError && (
          <div className="alert alert-error" role="alert">
            <strong>Dataset provenance could not be loaded.</strong> {datasetsError}
          </div>
        )}

        {!datasetsError && datasets.length === 0 && (
          <p role="status">No dataset provenance records were returned by the API.</p>
        )}

        {datasets.length > 0 && (
          <ul className="dataset-provenance-list">
            {datasets.map((dataset) => (
              <li key={dataset.dataset_id} className="dataset-provenance-card">
                <div className="dataset-card-heading">
                  <h4>{dataset.title ?? dataset.product_id}</h4>
                  {dataset.source_kind.toLowerCase().includes("synthetic") && (
                    <DataKindBadge kind="synthetic" />
                  )}
                </div>
                <dl>
                  <div>
                    <dt>Provider</dt>
                    <dd>{dataset.provider}</dd>
                  </div>
                  <div>
                    <dt>Product</dt>
                    <dd>
                      <code>{dataset.product_id}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>Access timestamp</dt>
                    <dd>
                      <time dateTime={dataset.access_timestamp}>
                        {dataset.access_timestamp}
                      </time>
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
                    <dt>Native resolution</dt>
                    <dd>
                      {dataset.native_resolution_m === null
                        ? "not reported"
                        : `${formatNumber(dataset.native_resolution_m, 1)} m`}
                    </dd>
                  </div>
                  <div>
                    <dt>Source CRS</dt>
                    <dd>{dataset.source_crs ?? "not reported"}</dd>
                  </div>
                </dl>
                <p>
                  <a href={dataset.url} target="_blank" rel="noreferrer">
                    Dataset record
                  </a>
                </p>
                {dataset.notes && <p className="dataset-notes">{dataset.notes}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
