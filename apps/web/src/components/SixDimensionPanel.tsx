import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  errorCode,
  errorMessage,
  getDimensions,
  getTypedArea,
  getTypedCoverage,
  getTypedObservations,
  isAbortError,
  type DatasetOut,
  type DimensionsOut,
  type FeatureOut,
  type TypedAreaOut,
  type TypedCoverageOut,
  type TypedObservationOut,
  type TypedSpatialSupport,
  type TypedTemporalSupport,
  type TypedDepthSupport,
  type TypedUncertainty,
} from "../api/client";
import { SourceText, useI18n, type I18n } from "../i18n/I18nProvider";
import type { MessageKey } from "../locales/en/messages";
import {
  DIMENSION_CODES,
  DIMENSION_NAMES,
  groupTypedObservations,
  looksLikeFixture,
  observationLooksLikeFixture,
  type DimensionCode,
} from "../lib/six-dimensions";

export type SixDimensionFineValue = {
  featureId: string;
  label: string;
  value: number;
  unit: string;
  methodVersion?: string;
  quality?: string;
  validFraction?: number;
  extractionScaleM?: number;
  scoreRole?: string;
};

export type SixDimensionTarget = {
  id: string;
  label: string;
  kind: "candidate" | "reference";
  body?: string;
  fineValues: SixDimensionFineValue[];
};

type SixDimensionPanelProps = {
  features: FeatureOut[];
  datasets?: DatasetOut[];
  targets: SixDimensionTarget[];
};

const STATE_KEYS: Record<string, MessageKey> = {
  detected: "six.state.detected",
  not_detected: "six.state.not_detected",
  no_coverage: "six.state.no_coverage",
  unknown: "six.state.unknown",
  not_applicable: "six.state.not_applicable",
};

const ROLE_KEYS: Record<string, MessageKey> = {
  similarity: "six.role.similarity",
  context_only: "six.role.context_only",
  filter: "six.role.filter",
  evidence_only: "six.role.evidence_only",
  not_comparable: "six.role.not_comparable",
};

const KIND_KEYS: Record<string, MessageKey> = {
  numeric: "six.kind.numeric",
  category: "six.kind.category",
  distribution: "six.kind.distribution",
  detection: "six.kind.detection",
  timeseries: "six.kind.timeseries",
  spectrum: "six.kind.spectrum",
  process_evidence: "six.kind.process_evidence",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function stateLabel(state: string, t: I18n["t"]): string {
  const key = STATE_KEYS[state];
  return key ? t(key) : state;
}

function roleLabel(role: string, t: I18n["t"]): string {
  const key = ROLE_KEYS[role];
  return key ? t(key) : role;
}

function valueKindLabel(kind: string, t: I18n["t"]): string {
  const key = KIND_KEYS[kind];
  return key ? t(key) : kind;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function dimensionName(code: DimensionCode, label: I18n["label"]): string {
  const localized = label.dimension(code);
  return localized === code ? DIMENSION_NAMES[code] : localized;
}

function dimensionDefinition(
  dimensions: DimensionsOut | null,
  code: DimensionCode,
): { label: string; description: string; scoreRole: string } | null {
  if (!dimensions) {
    return null;
  }
  const legacy = code === "D01" ? "terrain_shape" : null;
  const found = dimensions.dimensions.find(
    (dimension) => dimension.dimension_id === code || dimension.dimension_id === legacy,
  );
  if (!found) {
    return null;
  }
  return {
    label: found.label,
    description: found.description,
    scoreRole: found.score_role,
  };
}

function FactList({ entries }: { entries: { label: string; value: string }[] }) {
  const { t } = useI18n();
  if (entries.length === 0) {
    return <p className="field-hint">{t("six.noSource")}</p>;
  }
  return (
    <dl className="typed-facts">
      {entries.map((entry) => (
        <div key={`${entry.label}-${entry.value}`}>
          <dt>{entry.label}</dt>
          <dd>{entry.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function SupportFacts({
  spatial,
  temporal,
  depth,
}: {
  spatial: TypedSpatialSupport;
  temporal: TypedTemporalSupport;
  depth: TypedDepthSupport;
}) {
  const { t, fmt } = useI18n();
  const spatialEntries: { label: string; value: string }[] = [];
  if (spatial.kind) {
    spatialEntries.push({ label: t("six.supportKind"), value: spatial.kind });
  }
  if (spatial.crs) {
    spatialEntries.push({ label: t("six.supportCrs"), value: spatial.crs });
  }
  const resolution = numberOrNull(spatial.resolution_m);
  if (resolution !== null) {
    spatialEntries.push({
      label: t("six.supportResolution"),
      value: t("format.meters", { value: fmt.number(resolution) }),
    });
  }
  const windowSize = numberOrNull(spatial.window_size_m);
  if (windowSize !== null) {
    spatialEntries.push({
      label: t("six.supportWindow"),
      value: t("format.meters", { value: fmt.number(windowSize) }),
    });
  }
  const bbox = spatial.bbox;
  if (
    isRecord(bbox) &&
    numberOrNull(bbox.west) !== null &&
    numberOrNull(bbox.south) !== null &&
    numberOrNull(bbox.east) !== null &&
    numberOrNull(bbox.north) !== null
  ) {
    spatialEntries.push({
      label: t("six.supportBbox"),
      value: fmt.bbox({
        west: bbox.west as number,
        south: bbox.south as number,
        east: bbox.east as number,
        north: bbox.north as number,
      }),
    });
  }
  const area = numberOrNull(spatial.area_km2);
  if (area !== null) {
    spatialEntries.push({
      label: t("six.supportArea"),
      value: t("format.km2", { value: fmt.number(area, 3) }),
    });
  }
  const valid = numberOrNull(spatial.valid_fraction);
  if (valid !== null) {
    spatialEntries.push({ label: t("six.supportValid"), value: fmt.percent(valid) });
  }
  if (spatial.notes) {
    spatialEntries.push({ label: t("six.supportNotes"), value: String(spatial.notes) });
  }

  const temporalEntries: { label: string; value: string }[] = [];
  if (temporal.kind) {
    temporalEntries.push({ label: t("six.supportKind"), value: temporal.kind });
  }
  if (stringOrNull(temporal.start)) {
    temporalEntries.push({ label: t("six.supportStart"), value: String(temporal.start) });
  }
  if (stringOrNull(temporal.end)) {
    temporalEntries.push({ label: t("six.supportEnd"), value: String(temporal.end) });
  }
  if (stringOrNull(temporal.local_solar_time)) {
    temporalEntries.push({
      label: t("six.supportSolar"),
      value: String(temporal.local_solar_time),
    });
  }
  if (temporal.notes) {
    temporalEntries.push({ label: t("six.supportNotes"), value: String(temporal.notes) });
  }

  const depthEntries: { label: string; value: string }[] = [];
  if (depth.kind) {
    depthEntries.push({ label: t("six.supportKind"), value: depth.kind });
  }
  const minDepth = numberOrNull(depth.min_m);
  const maxDepth = numberOrNull(depth.max_m);
  if (minDepth !== null && maxDepth !== null) {
    depthEntries.push({
      label: t("six.supportDepth"),
      value: t("six.depthRange", {
        min: fmt.number(minDepth, 3),
        max: fmt.number(maxDepth, 3),
      }),
    });
  } else if (depth.kind === "surface") {
    depthEntries.push({ label: t("six.supportDepth"), value: t("six.depthSurface") });
  }

  return (
    <div className="typed-support">
      <div>
        <h5>{t("six.spatialSupport")}</h5>
        <FactList entries={spatialEntries} />
      </div>
      <div>
        <h5>{t("six.temporalSupport")}</h5>
        <FactList entries={temporalEntries} />
      </div>
      <div>
        <h5>{t("six.depthSupport")}</h5>
        <FactList entries={depthEntries} />
      </div>
    </div>
  );
}

function Uncertainty({ uncertainty }: { uncertainty: TypedUncertainty | null }) {
  const { t, fmt } = useI18n();
  if (!uncertainty) {
    return <span>{t("six.noUncertainty")}</span>;
  }
  const unit = uncertainty.unit ?? "";
  const value = numberOrNull(uncertainty.value);
  const lower = numberOrNull(uncertainty.lower);
  const upper = numberOrNull(uncertainty.upper);
  if (lower !== null && upper !== null) {
    return (
      <span>
        {t("six.uncertaintyRange", {
          lower: fmt.number(lower),
          upper: fmt.number(upper),
          unit,
        })}
        {uncertainty.kind ? ` · ${uncertainty.kind}` : ""}
      </span>
    );
  }
  if (value !== null) {
    return (
      <span>
        {t("six.uncertaintyValue", { value: fmt.number(value), unit })}
        {uncertainty.kind ? ` · ${uncertainty.kind}` : ""}
      </span>
    );
  }
  return <span>{uncertainty.kind ?? t("six.noUncertainty")}</span>;
}

function Value({
  observation,
  unit,
}: {
  observation: TypedObservationOut;
  unit: string;
}) {
  const { t, fmt } = useI18n();
  const state = observation.state;
  const stateText = stateLabel(state, t);
  if (state !== "detected") {
    return (
      <p className="typed-value is-missing" data-state={state}>
        <strong>{stateText}</strong>{" "}
        {t("six.valueNotShown", { state: stateText })}
      </p>
    );
  }

  const value = observation.value;
  if (observation.value_kind === "numeric") {
    const numeric = numberOrNull(value);
    return (
      <p className="typed-value" data-value-kind="numeric">
        {numeric === null
          ? t("six.structuredValue")
          : fmt.withUnit(numeric, unit)}
      </p>
    );
  }

  if (observation.value_kind === "category") {
    if (typeof value === "string") {
      return <p className="typed-value">{value}</p>;
    }
    if (isRecord(value) && typeof value.code === "string") {
      const scheme = typeof value.scheme === "string" ? value.scheme : null;
      return (
        <p className="typed-value">
          {t("six.categoryCode")}: {value.code}
          {scheme ? ` · ${t("six.categoryScheme")}: ${scheme}` : ""}
        </p>
      );
    }
  }

  if (observation.value_kind === "distribution") {
    if (isRecord(value) && Array.isArray(value.bins) && Array.isArray(value.fractions)) {
      const bins = value.bins;
      const fractions = value.fractions;
      if (bins.length === fractions.length && bins.length > 0) {
        return (
          <div className="typed-table-scroll">
            <table className="metric-table">
              <caption>{t("six.distributionBins")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("six.distributionBins")}</th>
                  <th scope="col">{t("six.distributionFractions")}</th>
                </tr>
              </thead>
              <tbody>
                {bins.map((bin, index) => (
                  <tr key={`${String(bin)}-${index}`}>
                    <td>{typeof bin === "number" ? fmt.number(bin, 3) : String(bin)}</td>
                    <td>
                      {typeof fractions[index] === "number"
                        ? fmt.percent(fractions[index] as number)
                        : String(fractions[index])}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
    }
  }

  if (observation.value_kind === "detection") {
    if (isRecord(value)) {
      const indicator = numberOrNull(value.indicator);
      const confidence = numberOrNull(value.confidence);
      return (
        <p className="typed-value">
          <strong>{t("six.detectionIndicator")}:</strong> {indicator ?? "—"} ·{" "}
          <strong>{t("six.detectionConfidence")}:</strong>{" "}
          {confidence === null ? t("common.notReported") : fmt.percent(confidence)}
        </p>
      );
    }
  }

  if (observation.value_kind === "timeseries") {
    if (
      isRecord(value) &&
      Array.isArray(value.timestamps) &&
      Array.isArray(value.values) &&
      value.timestamps.length === value.values.length &&
      value.timestamps.length > 0
    ) {
      const timestamps = value.timestamps;
      const seriesValues = value.values;
      return (
        <div className="typed-table-scroll">
          <table className="metric-table">
            <caption>{t("six.timeseries")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("six.timeseriesTime")}</th>
                <th scope="col">{t("six.timeseriesValue")}</th>
              </tr>
            </thead>
            <tbody>
              {timestamps.map((timestamp, index) => (
                <tr key={`${String(timestamp)}-${index}`}>
                  <td>{String(timestamp)}</td>
                  <td>
                    {typeof seriesValues[index] === "number"
                      ? fmt.withUnit(seriesValues[index] as number, unit)
                      : String(seriesValues[index])}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
  }

  if (observation.value_kind === "spectrum") {
    if (
      isRecord(value) &&
      Array.isArray(value.wavelengths) &&
      Array.isArray(value.values) &&
      value.wavelengths.length === value.values.length &&
      value.wavelengths.length > 0
    ) {
      const wavelengthUnit =
        typeof value.wavelength_unit === "string" ? value.wavelength_unit : "";
      const wavelengths = value.wavelengths;
      const spectrumValues = value.values;
      return (
        <div className="typed-table-scroll">
          <table className="metric-table">
            <caption>{t("six.spectrum")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("six.spectrumWavelength")}</th>
                <th scope="col">{t("six.timeseriesValue")}</th>
              </tr>
            </thead>
            <tbody>
              {wavelengths.map((wavelength, index) => (
                <tr key={`${String(wavelength)}-${index}`}>
                  <td>
                    {typeof wavelength === "number"
                      ? `${fmt.number(wavelength, 3)} ${wavelengthUnit}`.trim()
                      : String(wavelength)}
                  </td>
                  <td>
                    {typeof spectrumValues[index] === "number"
                      ? fmt.withUnit(spectrumValues[index] as number, unit)
                      : String(spectrumValues[index])}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
  }

  if (observation.value_kind === "process_evidence" && isRecord(value)) {
    const processCode = stringOrNull(value.process_code);
    const evidenceType = stringOrNull(value.evidence_type);
    const description = stringOrNull(value.description);
    return (
      <div className="typed-value">
        {processCode && (
          <p>
            <strong>{t("six.processCode")}:</strong> <code>{processCode}</code>
          </p>
        )}
        {evidenceType && (
          <p>
            <strong>{t("six.evidenceType")}:</strong> <code>{evidenceType}</code>
          </p>
        )}
        {description && <SourceText>{description}</SourceText>}
      </div>
    );
  }

  return (
    <pre className="typed-json">
      {JSON.stringify(value, null, 2) ?? t("six.structuredValue")}
    </pre>
  );
}

function ObservationCard({
  observation,
  datasets,
}: {
  observation: TypedObservationOut;
  datasets: DatasetOut[];
}) {
  const { t, fmt, label } = useI18n();
  const featureLabel = label.feature(
    observation.feature_id,
    observation.feature_id.replaceAll("_", " "),
  );
  const unit = observation.unit ?? "";
  const spatial = observation.spatial_support ?? {};
  const nativeResolution =
    numberOrNull(spatial.native_resolution_m) ??
    numberOrNull(spatial.source_resolution_m) ??
    numberOrNull(spatial.resolution_m);
  const fixture = observationLooksLikeFixture(observation);
  const datasetLinks = observation.dataset_ids
    .map((dataset) => datasets.find((item) => item.dataset_id === dataset))
    .filter((dataset): dataset is DatasetOut => dataset !== undefined);

  return (
    <article
      className={`typed-observation${fixture ? " is-fixture" : ""}`}
      data-testid={`typed-observation-${observation.observation_id}`}
    >
      <div className="typed-observation-head">
        <h4>{featureLabel}</h4>
        <span className="typed-badge" data-state={observation.state}>
          {stateLabel(observation.state, t)}
        </span>
        <span className="typed-badge is-role" data-role={observation.score_role}>
          {roleLabel(observation.score_role, t)}
        </span>
        {fixture && <span className="badge badge-synthetic">{t("six.fixture")}</span>}
      </div>

      <Value observation={observation} unit={unit} />

      <dl className="typed-facts">
        <div>
          <dt>{t("six.valueKindLabel")}</dt>
          <dd>{valueKindLabel(observation.value_kind, t)}</dd>
        </div>
        <div>
          <dt>{t("six.unitLabel")}</dt>
          <dd>{unit || t("six.noSource")}</dd>
        </div>
        <div>
          <dt>{t("six.methodVersion")}</dt>
          <dd>
            <code>{observation.method_version}</code>
          </dd>
        </div>
        <div>
          <dt>{t("six.nativeResolution")}</dt>
          <dd>
            {nativeResolution === null
              ? t("six.noSource")
              : t("format.meters", { value: fmt.number(nativeResolution) })}
          </dd>
        </div>
        <div>
          <dt>{t("six.uncertainty")}</dt>
          <dd>
            <Uncertainty uncertainty={observation.uncertainty} />
          </dd>
        </div>
      </dl>

      <details className="typed-provenance">
        <summary>
          {t("six.openDetails", {
            name: featureLabel,
          })}
        </summary>
        <SupportFacts
          spatial={spatial}
          temporal={observation.temporal_support ?? {}}
          depth={observation.depth_support ?? {}}
        />
        <dl className="typed-facts">
          <div>
            <dt>{t("six.qaFlags")}</dt>
            <dd>
              {observation.quality_flags.length > 0 ? (
                <span className="chips mono">
                  {observation.quality_flags.map((flag) => (
                    <code key={flag}>{flag}</code>
                  ))}
                </span>
              ) : (
                t("six.noQa")
              )}
            </dd>
          </div>
          <div>
            <dt>{t("six.datasetIds")}</dt>
            <dd>
              {observation.dataset_ids.length > 0 ? (
                <>
                  <span className="chips mono">
                    {observation.dataset_ids.map((dataset) => (
                      <code key={dataset}>{dataset}</code>
                    ))}
                  </span>
                  {datasetLinks.length > 0 && (
                    <ul className="source-links">
                      {datasetLinks.map((dataset) => (
                        <li key={dataset.dataset_id}>
                          <a href={dataset.url} target="_blank" rel="noreferrer">
                            {dataset.title ?? dataset.product_id}
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : (
                t("six.noSource")
              )}
            </dd>
          </div>
          <div>
            <dt>{t("six.sourceAssetIds")}</dt>
            <dd>
              {observation.source_asset_ids.length > 0 ? (
                <span className="chips mono">
                  {observation.source_asset_ids.map((asset) => (
                    <code key={asset}>{asset}</code>
                  ))}
                </span>
              ) : (
                t("six.noSource")
              )}
            </dd>
          </div>
          {observation.notes && (
            <div>
              <dt>{t("six.notes")}</dt>
              <dd>
                <SourceText>{observation.notes}</SourceText>
              </dd>
            </div>
          )}
        </dl>
      </details>
    </article>
  );
}

function SimilarityTable({
  values,
  features,
}: {
  values: SixDimensionFineValue[];
  features: FeatureOut[];
}) {
  const { t, fmt, label } = useI18n();
  if (values.length === 0) {
    return <p className="field-hint">{t("six.noSimilarity")}</p>;
  }
  return (
    <div className="table-scroll">
      <table className="metric-table">
        <caption>{t("six.similarityHeading")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("six.tableFeature")}</th>
            <th scope="col">{t("six.tableValue")}</th>
            <th scope="col">{t("six.tableRole")}</th>
            <th scope="col">{t("six.tableMethod")}</th>
          </tr>
        </thead>
        <tbody>
          {values.map((value) => {
            const feature = features.find((item) => item.id === value.featureId);
            return (
              <tr key={value.featureId}>
                <th scope="row">{label.feature(value.featureId, value.label)}</th>
                <td>{fmt.withUnit(value.value, value.unit, 4)}</td>
                <td>
                  <span className="typed-badge is-role" data-role="similarity">
                    {roleLabel("similarity", t)}
                  </span>
                </td>
                <td>
                  <code>{feature?.method_version ?? t("six.noSource")}</code>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function SixDimensionPanel({ features, datasets = [], targets }: SixDimensionPanelProps) {
  const { t, fmt, label } = useI18n();
  const [dimensions, setDimensions] = useState<DimensionsOut | null>(null);
  const [dimensionsError, setDimensionsError] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<TypedCoverageOut | null>(null);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const [allObservations, setAllObservations] = useState<TypedObservationOut[]>([]);
  const [area, setArea] = useState<TypedAreaOut | null>(null);
  const [areaMissing, setAreaMissing] = useState(false);
  const [areaLoading, setAreaLoading] = useState(false);
  const [areaError, setAreaError] = useState<{ message: string; code: string | null } | null>(
    null,
  );
  const [selectedTargetId, setSelectedTargetId] = useState<string | null>(
    targets[0]?.id ?? null,
  );
  const [manualAreaId, setManualAreaId] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const [bootstrapKey, setBootstrapKey] = useState(0);

  const targetKey = targets.map((target) => target.id).join("|");
  useEffect(() => {
    setSelectedTargetId(targets[0]?.id ?? null);
    // The target list is derived from the current selection in App.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey]);

  const activeTarget = targets.find((target) => target.id === selectedTargetId) ?? null;
  const activeAreaId = activeTarget?.id ?? (manualAreaId || null);

  useEffect(() => {
    const controller = new AbortController();
    setDimensionsError(null);
    setCoverageError(null);

    getDimensions(controller.signal)
      .then((payload) => setDimensions(payload))
      .catch((caught: unknown) => {
        if (!isAbortError(caught)) {
          setDimensions(null);
          setDimensionsError(errorMessage(caught));
        }
      });

    getTypedCoverage(controller.signal)
      .then((payload) => setCoverage(payload))
      .catch((caught: unknown) => {
        if (!isAbortError(caught)) {
          setCoverage(null);
          setCoverageError(errorMessage(caught));
        }
      });

    getTypedObservations({}, controller.signal)
      .then((payload) => setAllObservations(payload))
      .catch((caught: unknown) => {
        if (!isAbortError(caught)) {
          setAllObservations([]);
        }
      });

    return () => controller.abort();
  }, [bootstrapKey]);

  useEffect(() => {
    if (!activeAreaId) {
      setArea(null);
      setAreaMissing(false);
      setAreaError(null);
      setAreaLoading(false);
      return;
    }
    const controller = new AbortController();
    setArea(null);
    setAreaMissing(false);
    setAreaError(null);
    setAreaLoading(true);

    getTypedArea(activeAreaId, controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) {
          setArea(payload);
        }
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught)) {
          return;
        }
        if (caught instanceof ApiError && caught.status === 404) {
          setAreaMissing(true);
        } else {
          setAreaError({ message: errorMessage(caught), code: errorCode(caught) });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setAreaLoading(false);
        }
      });

    return () => controller.abort();
  }, [activeAreaId, retryKey]);

  const typedAreas = useMemo(() => {
    const map = new Map<string, { body: string; count: number; dimensions: Set<string> }>();
    for (const observation of allObservations) {
      const entry = map.get(observation.area_id) ?? {
        body: observation.body,
        count: 0,
        dimensions: new Set<string>(),
      };
      entry.count += 1;
      if (observation.dimension_code) {
        entry.dimensions.add(observation.dimension_code);
      }
      map.set(observation.area_id, entry);
    }
    return [...map.entries()]
      .map(([id, entry]) => ({ id, body: entry.body, count: entry.count }))
      .sort((left, right) => left.id.localeCompare(right.id));
  }, [allObservations]);

  const grouped = useMemo(
    () => groupTypedObservations(area?.observations ?? []),
    [area],
  );

  const d01Definition = dimensionDefinition(dimensions, "D01");
  const d01Role = d01Definition?.scoreRole ?? "similarity";
  const dimensionRoles = DIMENSION_CODES.reduce<Record<DimensionCode, string[]>>(
    (accumulator, code) => {
      if (code === "D01") {
        accumulator[code] = [d01Role];
      } else {
        accumulator[code] = [
          ...new Set(grouped.byDimension[code].map((observation) => observation.score_role)),
        ];
      }
      return accumulator;
    },
    {} as Record<DimensionCode, string[]>,
  );

  return (
    <section className="six-dimension-panel" aria-labelledby="six-dimension-heading">
      <div className="section-heading">
        <p className="eyebrow">{t("six.eyebrow")}</p>
        <h2 id="six-dimension-heading">{t("six.heading")}</h2>
        <p>{t("six.intro")}</p>
      </div>

      {dimensionsError && (
        <div className="alert alert-warning" role="status">
          <p>
            <strong>{t("six.coverageFailed")}</strong>{" "}
            <SourceText>{dimensionsError}</SourceText>
          </p>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setBootstrapKey((key) => key + 1)}
          >
            {t("six.retry")}
          </button>
        </div>
      )}

      <div className="six-controls">
        {targets.length > 0 && (
          <div className="segmented" role="group" aria-label={t("six.targetLabel")}>
            {targets.map((target) => (
              <button
                key={`${target.kind}-${target.id}`}
                type="button"
                aria-pressed={target.id === selectedTargetId}
                className={`segment${target.id === selectedTargetId ? " is-active" : ""}`}
                onClick={() => {
                  setSelectedTargetId(target.id);
                  setManualAreaId("");
                }}
              >
                {target.kind === "reference" ? t("six.targetReference") : t("six.targetCandidate")}{" "}
                · {target.label}
              </button>
            ))}
          </div>
        )}

        <label className="six-area-select">
          <span>{t("six.areaSelect")}</span>
          <select
            value={activeTarget ? "" : manualAreaId}
            onChange={(event) => {
              setManualAreaId(event.target.value);
              setSelectedTargetId(null);
            }}
          >
            <option value="">{t("six.defaultAreaOption")}</option>
            {typedAreas.map((typedArea) => (
              <option key={typedArea.id} value={typedArea.id}>
                {typedArea.id} · {label.body(typedArea.body)} · {typedArea.count}
              </option>
            ))}
          </select>
        </label>
        <p className="field-hint">{t("six.areaSelectHint")}</p>
      </div>

      {!activeAreaId && (
        <div className="empty-state" role="status">
          <h3>{t("six.noTarget")}</h3>
          <p>{t("six.noTargetHint")}</p>
        </div>
      )}

      {coverage && (
        <section className="coverage-panel" aria-labelledby="typed-coverage-heading">
          <h3 id="typed-coverage-heading">{t("six.coverageHeading")}</h3>
          <p>{t("six.coverageTotal", { total: fmt.number(coverage.total, 0) })}</p>
          <ul className="typed-coverage-list">
            {Object.entries(coverage.by_dimension).map(([dimension, entry]) => (
              <li key={dimension}>
                {t("six.coverageDimension", {
                  code: dimension,
                  count: fmt.number(entry.total, 0),
                })}
                <span className="muted">
                  {" "}
                  ·{" "}
                  {Object.entries(entry.by_state)
                    .map(([state, count]) => `${stateLabel(state, t)}: ${count}`)
                    .join(" · ")}
                </span>
              </li>
            ))}
          </ul>
          <p>
            <SourceText>{coverage.scope_note}</SourceText>
          </p>
        </section>
      )}

      {coverageError && (
        <p className="field-hint">
          {t("six.coverageFailed")} <SourceText>{coverageError}</SourceText>
        </p>
      )}

      {areaLoading && (
        <p className="status-panel" role="status">
          {t("six.loading")}
        </p>
      )}

      {areaError && (
        <div className="alert alert-error" role="alert">
          <p>
            <strong>
              {areaError.code
                ? t("six.failedWithCode", { code: areaError.code })
                : t("six.failed")}
            </strong>{" "}
            <SourceText>{areaError.message}</SourceText>
          </p>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setRetryKey((key) => key + 1)}
          >
            {t("six.retry")}
          </button>
        </div>
      )}

      {activeAreaId && areaMissing && (
        <div className="alert alert-warning" role="status">
          <p>{t("six.noTypedArea", { id: activeAreaId })}</p>
          <p>{t("six.noTypedRecords")}</p>
        </div>
      )}

      {!areaLoading && !areaError && (
        <>
          {area && (
            <section className="coverage-panel" aria-labelledby="typed-area-facts">
            <h3 id="typed-area-facts">{t("six.areaFacts")}</h3>
            <dl className="facts-grid">
              <div>
                <dt>{t("six.bodyLabel")}</dt>
                <dd>{label.body(area.body)}</dd>
              </div>
              <div>
                <dt>{t("six.crsLabel")}</dt>
                <dd>
                  <code>{area.crs}</code>
                </dd>
              </div>
              <div>
                <dt>{t("six.coordinateFrame")}</dt>
                <dd>
                  <code>{area.coordinate_frame}</code>
                </dd>
              </div>
              <div>
                <dt>{t("six.areaKm2")}</dt>
                <dd>
                  {area.area_km2 === null
                    ? t("six.noSource")
                    : t("format.km2", { value: fmt.number(area.area_km2, 3) })}
                </dd>
              </div>
              <div>
                <dt>{t("six.bboxLabel")}</dt>
                <dd>
                  {area.bbox &&
                  numberOrNull(area.bbox.west) !== null &&
                  numberOrNull(area.bbox.south) !== null &&
                  numberOrNull(area.bbox.east) !== null &&
                  numberOrNull(area.bbox.north) !== null
                    ? fmt.bbox({
                        west: area.bbox.west,
                        south: area.bbox.south,
                        east: area.bbox.east,
                        north: area.bbox.north,
                      })
                    : t("six.noSource")}
                </dd>
              </div>
              <div>
                <dt>{t("six.obsCount", { count: area.observations.length })}</dt>
                <dd>{area.area_id}</dd>
              </div>
            </dl>
            {looksLikeFixture(area.area_id) && (
              <p className="alert alert-synthetic" role="status">
                {t("six.fixture")}
              </p>
            )}
            </section>
          )}

          <section className="dimension-board six-dimension-grid" aria-label={t("six.heading")}>
            {DIMENSION_CODES.map((code) => {
              const observations = grouped.byDimension[code];
              const definition = dimensionDefinition(dimensions, code);
              const roles = dimensionRoles[code];
              const isD01 = code === "D01";
              return (
                <section
                  key={code}
                  className="dimension-column six-dimension-card"
                  aria-label={dimensionName(code, label)}
                  data-dimension={code}
                >
                  <div className="dimension-head">
                    <h3>
                      <span className="dimension-code mono">{code}</span>{" "}
                      {dimensionName(code, label)}
                    </h3>
                    <span
                      className={`dimension-tag${roles.includes("similarity") ? " is-active" : " is-context"}`}
                    >
                      {roles.length > 0
                        ? roles.map((role) => roleLabel(role, t)).join(" · ")
                        : t("six.roleNotEstablished")}
                    </span>
                  </div>

                  <p className="field-hint">
                    {definition ? definition.description : t("six.definitionMissing")}
                  </p>
                  {definition && (
                    <p className="field-hint">
                      {t("six.dimensionRole", {
                        role: roleLabel(definition.scoreRole, t),
                      })}
                    </p>
                  )}

                  {isD01 && (
                    <div className="six-similarity">
                      <p className="field-hint">{t("six.similarityIntro")}</p>
                      <SimilarityTable
                        values={activeTarget?.fineValues ?? []}
                        features={features}
                      />
                      <p className="field-hint">{t("six.similarityDistanceNote")}</p>
                    </div>
                  )}

                  {observations.length === 0 ? (
                    <p className="placeholder-note">{t("six.noDimensionData")}</p>
                  ) : (
                    <ul className="typed-observation-list">
                      {observations.map((observation) => (
                        <li key={observation.observation_id}>
                          <ObservationCard observation={observation} datasets={datasets} />
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              );
            })}
          </section>

          {(grouped.contextOnly.length > 0 || grouped.unassigned.length > 0) && (
            <section className="coverage-panel" aria-labelledby="six-context-heading">
              <h3 id="six-context-heading">{t("six.contextHeading")}</h3>
              <p>{t("six.contextIntro")}</p>
              <ul className="typed-observation-list">
                {[...grouped.contextOnly, ...grouped.unassigned].map((observation) => (
                  <li key={observation.observation_id}>
                    <ObservationCard observation={observation} datasets={datasets} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      <section className="six-legend" aria-labelledby="six-legend-heading">
        <h3 id="six-legend-heading">{t("six.legendHeading")}</h3>
        <p>{t("six.legendStates")}</p>
        <p>{t("six.legendRoles")}</p>
        <p className="coverage-warning">
          <strong>{t("six.noTotalScore")}</strong>
        </p>
      </section>
    </section>
  );
}

export function fineValuesFromSearchResult(
  metrics: { feature_id: string; unit: string; candidate: number }[],
  features: FeatureOut[],
): SixDimensionFineValue[] {
  return metrics.map((metric) => {
    const feature = features.find((item) => item.id === metric.feature_id);
    return {
      featureId: metric.feature_id,
      label: feature?.label ?? metric.feature_id.replaceAll("_", " "),
      value: metric.candidate,
      unit: metric.unit,
      methodVersion: feature?.method_version,
      scoreRole: "similarity",
    };
  });
}

export function fineValuesFromReference(
  observations: { feature_id: string; label: string; value: number; unit: string; method_version: string; quality: string; valid_fraction: number; extraction_scale_m: number }[],
): SixDimensionFineValue[] {
  return observations.map((observation) => ({
    featureId: observation.feature_id,
    label: observation.label,
    value: observation.value,
    unit: observation.unit,
    methodVersion: observation.method_version,
    quality: observation.quality,
    validFraction: observation.valid_fraction,
    extractionScaleM: observation.extraction_scale_m,
    scoreRole: "similarity",
  }));
}


