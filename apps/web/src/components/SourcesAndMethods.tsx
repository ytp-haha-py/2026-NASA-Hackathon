import type { CapabilitiesOut, DatasetOut } from "../api/client";
import { SourceText, useI18n } from "../i18n/I18nProvider";
import { providerStatusEntries, readAoiLimits } from "../lib/aoi";
import { DataKindBadge } from "./DataKindBadge";
import { Timestamp } from "./Timestamp";

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
  const { t, tp, rich, fmt, label } = useI18n();
  const windowSize = analysisNumber(capabilities.analysis, "window_size_m");
  const resolution = analysisNumber(capabilities.analysis, "analysis_resolution_m");
  const coverage = capabilities.coverage;
  const origins = coverage.origins;
  const aoiLimits = readAoiLimits(capabilities.aoi_analysis);
  const aoiProviders = providerStatusEntries(aoiLimits.providerStatus);

  return (
    <section className="sources-methods" id="sources-and-methods" aria-labelledby="sources-heading">
      <div className="section-heading">
        <p className="eyebrow">{t("sources.eyebrow")}</p>
        <h2 id="sources-heading">{t("sources.heading")}</h2>
      </div>

      <div className="coverage-disclosure">
        <h3>{t("sources.disclaimerHeading")}</h3>
        <p>
          <SourceText>{coverage.description}</SourceText>
        </p>
        <ul>
          <li>
            {rich("sources.candidateRegions", {
              count: <strong>{fmt.number(coverage.indexed_candidate_count, 0)}</strong>,
              label: <code>{coverage.label}</code>,
            })}
          </li>
          <li>
            {rich("sources.originSplit", {
              indexed: (
                <strong>
                  {t("coverage.indexedCount", { count: fmt.number(origins.indexed ?? 0, 0) })}
                </strong>
              ),
              onDemand: (
                <strong>
                  {t("coverage.onDemandCount", {
                    count: fmt.number(origins.on_demand ?? 0, 0),
                  })}
                </strong>
              ),
            })}
          </li>
          <li>
            {t("sources.extent", {
              extent: coverage.geographic_bbox
                ? fmt.bbox(coverage.geographic_bbox)
                : t("sources.extentMissing"),
            })}
          </li>
          <li>
            {t("sources.dataKinds", {
              kinds:
                coverage.data_kinds.length > 0
                  ? coverage.data_kinds.join(", ")
                  : t("common.noneReported"),
            })}
          </li>
          <li>{t("sources.closestOnly")}</li>
        </ul>
      </div>

      <div className="methods-block">
        <h3>{t("sources.methodHeading")}</h3>
        <dl className="facts-grid">
          <div>
            <dt>{t("sources.contractVersion")}</dt>
            <dd>{capabilities.contract_version}</dd>
          </div>
          <div>
            <dt>{t("sources.methodVersion")}</dt>
            <dd>{capabilities.method_version}</dd>
          </div>
          <div>
            <dt>{t("sources.analysisWindow")}</dt>
            <dd>
              {windowSize === null
                ? t("common.notReported")
                : t("format.meters", { value: fmt.number(windowSize) })}
            </dd>
          </div>
          <div>
            <dt>{t("sources.analysisResolution")}</dt>
            <dd>
              {resolution === null
                ? t("common.notReported")
                : t("format.meters", { value: fmt.number(resolution) })}
            </dd>
          </div>
          <div>
            <dt>{t("sources.dataVersion")}</dt>
            <dd>{capabilities.data_version}</dd>
          </div>
          <div>
            <dt>{t("sources.dataSnapshot")}</dt>
            <dd>
              <code>{capabilities.data_snapshot_id}</code>
            </dd>
          </div>
          <div>
            <dt>{t("sources.aoiLimits")}</dt>
            <dd>
              {t("sources.aoiLimitsValue", {
                min: fmt.number(aoiLimits.minSideM, 0),
                max: fmt.number(aoiLimits.maxSideM, 0),
                area: fmt.number(aoiLimits.maxAreaKm2, 1),
              })}
            </dd>
          </div>
          <div>
            <dt>{t("sources.distance")}</dt>
            <dd>{t("sources.distanceValue")}</dd>
          </div>
        </dl>

        {aoiProviders.length > 0 && (
          <div className="aoi-provider-readiness">
            <h4>{t("sources.aoiProviders")}</h4>
            <ul>
              {aoiProviders.map(([providerId, status]) => (
                <li key={providerId}>
                  <code>{providerId}</code>:{" "}
                  <strong data-status={status.status ?? "unknown"}>
                    {label.status(status.status)}
                  </strong>
                  {status.role ? (
                    <>
                      {" — "}
                      <SourceText>{status.role}</SourceText>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="method-features">
          {capabilities.features.map((feature) => (
            <article key={feature.id} className="method-feature">
              <h4>
                {label.feature(feature.id, feature.label)}{" "}
                <span className="unit-tag">{label.unitName(feature.unit)}</span>
              </h4>
              <p>
                <SourceText>{feature.description}</SourceText>
              </p>
              <dl>
                <div>
                  <dt>{t("sources.validDomain")}</dt>
                  <dd>
                    <SourceText>{feature.valid_domain}</SourceText>
                  </dd>
                </div>
                <div>
                  <dt>{t("sources.fixedScale")}</dt>
                  <dd>{fmt.number(feature.scale)}</dd>
                </div>
                <div>
                  <dt>{t("sources.scaleRationale")}</dt>
                  <dd>
                    <SourceText>{feature.scale_rationale}</SourceText>
                  </dd>
                </div>
                <div>
                  <dt>{t("sources.extractionScale")}</dt>
                  <dd>{t("format.meters", { value: fmt.number(feature.extraction_scale_m) })}</dd>
                </div>
                <div>
                  <dt>{t("sources.method")}</dt>
                  <dd>
                    <SourceText>{feature.method}</SourceText>
                  </dd>
                </div>
                <div>
                  <dt>{t("sources.missingPolicy")}</dt>
                  <dd>
                    <SourceText>{feature.missing_policy}</SourceText>
                  </dd>
                </div>
              </dl>
            </article>
          ))}
        </div>

        <div className="context-methods">
          <h3>{t("sources.contextHeading")}</h3>
          <p>{t("sources.contextBody")}</p>
          <details>
            <summary>{tp("sources.contextShow", capabilities.context_features.length)}</summary>
            <ul className="context-feature-list">
              {capabilities.context_features.map((feature) => (
                <li key={feature.feature_id}>
                  <h4>
                    {label.feature(feature.feature_id, feature.label)}{" "}
                    <span className="unit-tag">
                      {t("sources.classTag", { code: feature.class_code })}
                    </span>
                  </h4>
                  <p>
                    <SourceText>{feature.description}</SourceText>
                  </p>
                  <p className="field-hint">
                    {rich("sources.contextRole", {
                      role: <code>{feature.score_role}</code>,
                      unit: label.unitName(feature.unit),
                      method: feature.method_version,
                    })}
                  </p>
                  <p className="field-hint">
                    <SourceText>{feature.quality_rules}</SourceText>
                  </p>
                  <p>
                    <a href={feature.reference} target="_blank" rel="noreferrer">
                      {t("sources.worldCoverReference")}
                    </a>
                  </p>
                </li>
              ))}
            </ul>
          </details>
          {capabilities.context_features.length > 0 && (
            <p className="field-hint">{t("sources.worldCoverAttribution")}</p>
          )}
        </div>
      </div>

      <div className="datasets-block">
        <h3>{t("sources.datasetsHeading")}</h3>
        <p className="muted">{t("sources.datasetsIntro")}</p>

        {datasetsError && (
          <div className="alert alert-error" role="alert">
            <strong>{t("sources.datasetsFailed")}</strong>{" "}
            <SourceText>{datasetsError}</SourceText>
          </div>
        )}

        {!datasetsError && datasets.length === 0 && (
          <p role="status">{t("sources.noDatasets")}</p>
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
                    <dt>{t("dataset.provider")}</dt>
                    <dd>{dataset.provider}</dd>
                  </div>
                  <div>
                    <dt>{t("dataset.product")}</dt>
                    <dd>
                      <code>{dataset.product_id}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>{t("dataset.accessTimestamp")}</dt>
                    <dd>
                      <Timestamp iso={dataset.access_timestamp} />
                    </dd>
                  </div>
                  <div>
                    <dt>{t("dataset.license")}</dt>
                    <dd>
                      <a href={dataset.license_url} target="_blank" rel="noreferrer">
                        {dataset.license}
                      </a>
                    </dd>
                  </div>
                  <div>
                    <dt>{t("dataset.processingVersion")}</dt>
                    <dd>{dataset.processing_version}</dd>
                  </div>
                  <div>
                    <dt>{t("dataset.sourceKind")}</dt>
                    <dd>
                      <code>{dataset.source_kind}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>{t("dataset.nativeResolution")}</dt>
                    <dd>
                      {dataset.native_resolution_m === null
                        ? t("common.notReported")
                        : t("format.meters", {
                            value: fmt.number(dataset.native_resolution_m, 1),
                          })}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("dataset.sourceCrs")}</dt>
                    <dd>{dataset.source_crs ?? t("common.notReported")}</dd>
                  </div>
                </dl>
                <p>
                  <a href={dataset.url} target="_blank" rel="noreferrer">
                    {t("dataset.recordLink")}
                  </a>
                </p>
                {dataset.notes && (
                  <p className="dataset-notes">
                    <SourceText>{dataset.notes}</SourceText>
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
