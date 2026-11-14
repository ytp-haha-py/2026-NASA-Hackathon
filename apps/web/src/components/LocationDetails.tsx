import type {
  FeatureOut,
  LocationOut,
  MetricOut,
  NumericTarget,
} from "../api/client";
import { SourceText, SourceTextNote, useI18n, type I18n } from "../i18n/I18nProvider";
import type { MessageKey } from "../locales/en/messages";
import { ContextExport } from "./ContextExport";
import { DataKindBadge } from "./DataKindBadge";
import { Timestamp } from "./Timestamp";

type LocationDetailsProps = {
  location: LocationOut;
  comparisonMetrics: MetricOut[];
  activeTarget: NumericTarget;
  features: FeatureOut[];
  sourceLabel: string;
  idPrefix: string;
};

const NOT_COMPARED: MessageKey[] = [
  "location.notGravity",
  "location.notAtmosphere",
  "location.notRadiation",
  "location.notRegolith",
  "location.notMineralogy",
  "location.notTemperature",
  "location.notGeologicalHistory",
];

const GENERAL_LIMITATIONS: MessageKey[] = [
  "location.limitEquivalence",
  "location.limitDistance",
  "location.limitWindow",
  "location.limitDem",
  "location.limitCoverage",
  "location.limitWorldCover",
  "location.limitSynthetic",
];

function DatasetFacts({
  dataset,
}: {
  dataset: LocationOut["datasets"][number];
}) {
  const { t, fmt } = useI18n();
  return (
    <article className="dataset-facts">
      <h4>{dataset.title ?? dataset.product_id}</h4>
      <dl>
        <div>
          <dt>{t("dataset.provider")}</dt>
          <dd>{dataset.provider}</dd>
        </div>
        <div>
          <dt>{t("dataset.productId")}</dt>
          <dd>
            <code>{dataset.product_id}</code>
          </dd>
        </div>
        <div>
          <dt>{t("dataset.datasetId")}</dt>
          <dd>
            <code>{dataset.dataset_id}</code>
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
          <dt>{t("dataset.sourceCrs")}</dt>
          <dd>{dataset.source_crs ?? t("common.notReported")}</dd>
        </div>
        <div>
          <dt>{t("dataset.nativeResolution")}</dt>
          <dd>
            {dataset.native_resolution_m === null
              ? t("common.notReported")
              : t("format.meters", { value: fmt.number(dataset.native_resolution_m, 1) })}
          </dd>
        </div>
        <div>
          <dt>{t("dataset.checksum")}</dt>
          <dd>{dataset.checksum ?? t("common.notReported")}</dd>
        </div>
      </dl>
      <p>
        <a href={dataset.url} target="_blank" rel="noreferrer">
          {t("dataset.sourceLink")}
        </a>
      </p>
      {dataset.notes && (
        <p className="dataset-notes">
          <SourceText>{dataset.notes}</SourceText>
        </p>
      )}
    </article>
  );
}

function geometrySummary(geometry: LocationOut["geometry"], i18n: I18n): string {
  if (!geometry) {
    return i18n.t("location.geometryBboxOnly");
  }
  const type = typeof geometry.type === "string" ? geometry.type : "Geometry";
  const coordinates = geometry.coordinates;
  if (Array.isArray(coordinates) && Array.isArray(coordinates[0])) {
    return i18n.t("location.geometryRing", {
      type,
      count: i18n.fmt.number(coordinates[0].length, 0),
    });
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
  const i18n = useI18n();
  const { t, fmt, label } = i18n;
  const featureLabels = new Map(features.map((feature) => [feature.id, feature.label]));
  const contextObservations = [...location.context_observations].sort(
    (a, b) => a.class_code - b.class_code,
  );

  return (
    <>
      <dl className="facts-grid">
        <div>
          <dt>{t("location.coordinates")}</dt>
          <dd>
            {fmt.coordinate(location.lat)}, {fmt.coordinate(location.lon)}
          </dd>
        </div>
        <div>
          <dt>{t("location.dataKind")}</dt>
          <dd>
            <DataKindBadge kind={location.data_kind} />
          </dd>
        </div>
        <div>
          <dt>{t("location.origin")}</dt>
          <dd>
            {location.origin === "on_demand"
              ? t("location.originOnDemand")
              : t("location.originIndexed")}
          </dd>
        </div>
        {location.area_km2 !== null && (
          <div>
            <dt>{t("location.aoiArea")}</dt>
            <dd>{t("format.km2", { value: fmt.number(location.area_km2, 3) })}</dd>
          </div>
        )}
        <div>
          <dt>{t("location.analysisWindow")}</dt>
          <dd>
            {t("location.windowValue", {
              window: fmt.number(location.window_size_m),
              resolution: fmt.number(location.analysis_resolution_m),
            })}
          </dd>
        </div>
        <div>
          <dt>{t("location.windowBbox")}</dt>
          <dd>{fmt.bbox(location.bbox)}</dd>
        </div>
        <div>
          <dt>{t("location.crs")}</dt>
          <dd>{location.crs}</dd>
        </div>
        <div>
          <dt>{t("location.validFraction")}</dt>
          <dd>{fmt.percent(location.valid_fraction)}</dd>
        </div>
        <div>
          <dt>{t("location.qualityFlags")}</dt>
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
              t("common.noneReported")
            )}
          </dd>
        </div>
        <div>
          <dt>{t("location.versions")}</dt>
          <dd>
            {t("location.versionsValue", {
              contract: location.contract_version,
              method: location.method_version,
              data: location.data_version,
            })}
            <span className="metric-meta">
              {t("location.snapshot", { snapshot: location.data_snapshot_id })}
            </span>
          </dd>
        </div>
        {location.origin === "on_demand" && (
          <div>
            <dt>{t("location.geometry")}</dt>
            <dd>{geometrySummary(location.geometry, i18n)}</dd>
          </div>
        )}
      </dl>

      {location.notes && (
        <p className="record-note">
          <strong>{t("location.recordNote")}</strong>{" "}
          <SourceText>{location.notes}</SourceText>
        </p>
      )}

      {location.origin === "on_demand" && (
        <p className="record-note on-demand-note">
          <strong>{t("location.onDemandNoteTitle")}</strong> {t("location.onDemandNote")}
        </p>
      )}

      <section className="detail-section" aria-labelledby={`${idPrefix}-features-heading`}>
        <h3 id={`${idPrefix}-features-heading`}>{t("location.featuresHeading")}</h3>
        <p className="muted">{t("location.featuresIntro", { source: sourceLabel })}</p>
        <p className="mobile-scroll-hint">{t("location.scrollHint")}</p>
        {location.features.length === 0 ? (
          <p>{t("location.noFeatures")}</p>
        ) : (
          <div className="table-scroll">
            <table className="feature-table">
              <caption className="sr-only">
                {t("location.featuresCaption", { name: location.display_name })}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("location.colFeature")}</th>
                  <th scope="col">{t("location.colValue")}</th>
                  <th scope="col">{t("location.colQuality")}</th>
                  <th scope="col">{t("location.colValidFraction")}</th>
                  <th scope="col">{t("location.colExtractionScale")}</th>
                </tr>
              </thead>
              <tbody>
                {location.features.map((feature) => (
                  <tr key={feature.feature_id}>
                    <th scope="row">
                      <span>{label.feature(feature.feature_id, feature.label)}</span>
                      <span className="metric-meta">{feature.feature_id}</span>
                      <span className="feature-description">
                        <SourceText>{feature.description}</SourceText>
                      </span>
                    </th>
                    <td>{fmt.withUnit(feature.value, feature.unit)}</td>
                    <td>{label.quality(feature.quality)}</td>
                    <td>{fmt.percent(feature.valid_fraction)}</td>
                    <td>
                      {t("format.meters", { value: fmt.number(feature.extraction_scale_m, 0) })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="detail-section" aria-labelledby={`${idPrefix}-comparison-heading`}>
        <h3 id={`${idPrefix}-comparison-heading`}>{t("location.comparisonHeading")}</h3>
        <p className="muted">{t("location.comparisonIntro")}</p>
        {comparisonMetrics.length === 0 ? (
          <p>{t("location.noComparison")}</p>
        ) : (
          <div className="table-scroll">
            <table className="comparison-table">
              <caption className="sr-only">
                {t("location.comparisonCaption", { name: location.display_name })}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("location.colMetric")}</th>
                  <th scope="col">{t("location.colCandidate")}</th>
                  <th scope="col">{t("location.colTarget")}</th>
                  <th scope="col">{t("location.colDelta")}</th>
                  <th scope="col">{t("location.colDisplay")}</th>
                </tr>
              </thead>
              <tbody>
                {comparisonMetrics.map((metric) => {
                  const close = Math.abs(metric.normalized_delta) <= 0.5;
                  return (
                    <tr key={metric.feature_id}>
                      <th scope="row">
                        {label.feature(metric.feature_id, featureLabels.get(metric.feature_id))}
                        <span className="metric-meta">
                          {t("location.normalizedDelta", {
                            delta: fmt.signed(metric.normalized_delta, 3),
                            scale: fmt.number(metric.scale),
                          })}
                        </span>
                      </th>
                      <td>{fmt.withUnit(metric.candidate, metric.unit)}</td>
                      <td>{fmt.withUnit(metric.target, metric.unit)}</td>
                      <td>
                        {fmt.signed(metric.delta)} {fmt.unit(metric.unit)}
                      </td>
                      <td>
                        <span
                          className={`comparison-chip ${close ? "is-close" : "is-different"}`}
                        >
                          {close ? t("location.close") : t("location.differs")}
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
        <h3 id={`${idPrefix}-context-heading`}>{t("location.contextHeading")}</h3>
        <p className="context-warning">{t("location.contextWarning")}</p>
        {contextObservations.length === 0 ? (
          <p>{t("location.noContext")}</p>
        ) : (
          <div className="table-scroll">
            <table className="context-table">
              <caption className="sr-only">
                {t("location.contextCaption", { name: location.display_name })}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("location.colClass")}</th>
                  <th scope="col">{t("location.colClassCode")}</th>
                  <th scope="col">{t("location.colFraction")}</th>
                  <th scope="col">{t("location.colProvider")}</th>
                  <th scope="col">{t("location.colValidFraction")}</th>
                  <th scope="col">{t("location.colSourceResolution")}</th>
                </tr>
              </thead>
              <tbody>
                {contextObservations.map((observation) => (
                  <tr key={observation.feature_id}>
                    <th scope="row">
                      {label.feature(observation.feature_id, observation.label)}
                    </th>
                    <td>{observation.class_code}</td>
                    <td>{fmt.percent(observation.value)}</td>
                    <td>{observation.provider}</td>
                    <td>{fmt.percent(observation.valid_fraction)}</td>
                    <td>
                      {t("format.meters", {
                        value: fmt.number(observation.source_resolution_m, 0),
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="context-warning">{t("location.contextSeparate")}</p>
      </section>

      <section className="detail-section" aria-labelledby={`${idPrefix}-provenance-heading`}>
        <h3 id={`${idPrefix}-provenance-heading`}>{t("location.provenanceHeading")}</h3>
        {location.datasets.length === 0 ? (
          <p>{t("location.noDatasets")}</p>
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
        <h3 id={`${idPrefix}-record-limits-heading`}>{t("location.recordLimitsHeading")}</h3>
        {location.known_limitations.length === 0 ? (
          <p>{t("location.noRecordLimits")}</p>
        ) : (
          <>
            <SourceTextNote />
            <ul>
              {location.known_limitations.map((limitation) => (
                <li key={limitation}>
                  <SourceText>{limitation}</SourceText>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section
        className="detail-section"
        aria-labelledby={`${idPrefix}-general-limits-heading`}
      >
        <h3 id={`${idPrefix}-general-limits-heading`}>{t("location.generalLimitsHeading")}</h3>
        <ul>
          {GENERAL_LIMITATIONS.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ul>
      </section>

      <aside className="not-simulated" aria-labelledby={`${idPrefix}-not-simulated-heading`}>
        <h3 id={`${idPrefix}-not-simulated-heading`}>{t("location.notSimulatedHeading")}</h3>
        <p>{t("location.notSimulatedIntro")}</p>
        <ul>
          {NOT_COMPARED.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ul>
      </aside>

      <ContextExport locationId={location.location_id} target={activeTarget} />
    </>
  );
}
