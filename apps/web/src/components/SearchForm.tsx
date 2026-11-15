import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  getTypedCoverage,
  getTypedObservations,
  type ContextFeatureOut,
  type FeatureOut,
  type NumericTarget,
  type RangeFilterIn,
  type ReferenceOut,
  type SearchRequest,
  type TargetSpec,
  type TypedCoverageOut,
  type TypedObservationOut,
} from "../api/client";
import { SourceText, SourceTextNote, useI18n } from "../i18n/I18nProvider";
import type { MessageVars } from "../i18n/translate";
import type { MessageKey } from "../locales/en/messages";
import {
  FRACTION_RANGE,
  dimensionCards,
  orderContextFeatures,
  rangeToFilter,
  sliderRange,
  type SliderRange,
} from "../lib/dimensions";
import type { SearchSubmission, SearchTargetPreset } from "../lib/search-types";
import { DataKindBadge } from "./DataKindBadge";
import { RangeSlider, SingleSlider } from "./RangeSlider";

type Body = SearchRequest["body"];

type RowState = {
  value: string;
  weight: string;
};

/**
 * Validation messages are stored as keys + variables, not rendered strings, so that a
 * language switch re-renders existing errors in the new language.
 */
type UiMessage = { key: MessageKey; vars?: MessageVars };

type FieldErrors = Record<string, { value?: UiMessage; weight?: UiMessage }>;

type SearchFormProps = {
  features: FeatureOut[];
  contextFeatures: ContextFeatureOut[];
  references: ReferenceOut[];
  referencesError: string | null;
  /** Notifies the app shell which reference is active for the six-dimension panel. */
  onReferenceChange?: (reference: ReferenceOut | null) => void;
  onSearch: (submission: SearchSubmission) => void;
  loading: boolean;
  preset?: SearchTargetPreset | null;
  /** Opens the six-dimension workspace, preserving the current candidate/reference. */
  onOpenDimensions?: () => void;
};

/** Weight slider upper bound; the number input still accepts any finite value ≥ 0. */
const WEIGHT_SLIDER_MAX = 3;

/** Custom mode starts neutral: every target at 0 (clamped into the metric's range) and every
 * weight at 0, so nothing is compared until the user raises a weight. */
function initialRows(features: FeatureOut[]): Record<string, RowState> {
  return Object.fromEntries(
    features.map((feature) => [
      feature.id,
      {
        value: String(Math.min(Math.max(0, feature.min_value), feature.max_value)),
        weight: "0",
      },
    ]),
  );
}

function initialReferenceWeights(features: FeatureOut[]): Record<string, string> {
  return Object.fromEntries(features.map((feature) => [feature.id, "0"]));
}

function fullRanges(
  features: FeatureOut[],
  contextFeatures: ContextFeatureOut[],
): Record<string, [number, number]> {
  const ranges: Record<string, [number, number]> = {};
  for (const feature of features) {
    const slider = sliderRange(feature);
    ranges[feature.id] = [slider.lo, slider.hi];
  }
  for (const feature of contextFeatures) {
    ranges[feature.feature_id] = [FRACTION_RANGE.lo, FRACTION_RANGE.hi];
  }
  return ranges;
}

function referenceValues(reference: ReferenceOut | undefined): NumericTarget {
  if (!reference) {
    return {};
  }
  return Object.fromEntries(
    reference.features.map((feature) => [feature.feature_id, feature.value]),
  );
}

/** "Land cover: Bare / sparse vegetation fraction" → "Bare / sparse vegetation fraction". */
function shortContextLabel(full: string): string {
  return full.replace(/^(Land cover:\s*|土地覆蓋：)/, "");
}

function numberOr(value: string, fallback: number): number {
  const parsed = Number(value);
  return value.trim() !== "" && Number.isFinite(parsed) ? parsed : fallback;
}

function rangeText(range: [number, number], slider: SliderRange, unit: string): string {
  const low = range[0].toFixed(slider.dp);
  const atOpenEnd = slider.open && range[1] >= slider.hi;
  const high = `${range[1].toFixed(slider.dp)}${atOpenEnd ? "+" : ""}`;
  return `${low}–${high}${unit ? ` ${unit}` : ""}`;
}

export function SearchForm({
  features,
  contextFeatures,
  references,
  referencesError,
  onReferenceChange,
  onSearch,
  loading,
  preset = null,
  onOpenDimensions,
}: SearchFormProps) {
  const { t, tp, rich, fmt, label } = useI18n();
  const [rows, setRows] = useState<Record<string, RowState>>(() => initialRows(features));
  /** Reference mode starts with every weight at 0: the user opts each metric in explicitly. */
  const [referenceWeights, setReferenceWeights] = useState<Record<string, string>>(() =>
    initialReferenceWeights(features),
  );
  const [ranges, setRanges] = useState<Record<string, [number, number]>>(() =>
    fullRanges(features, contextFeatures),
  );
  const [body, setBody] = useState<Body>("custom");
  const [referenceId, setReferenceId] = useState("");
  const [limit, setLimit] = useState("10");
  const [boardOpen, setBoardOpen] = useState(true);
  const [activeDimension, setActiveDimension] = useState("D01");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [errors, setErrors] = useState<UiMessage[]>([]);
  const lastAppliedPreset = useRef<number | null>(null);
  const latest = useRef({ features, contextFeatures, onSearch, limit, ranges });

  useEffect(() => {
    latest.current = { features, contextFeatures, onSearch, limit, ranges };
  }, [features, contextFeatures, onSearch, limit, ranges]);

  // Keep rows/ranges in sync when the API adds features (e.g. a new dimension) after mount.
  useEffect(() => {
    setRows((current) => ({ ...initialRows(features), ...current }));
    setReferenceWeights((current) => ({ ...initialReferenceWeights(features), ...current }));
    setRanges((current) => ({ ...fullRanges(features, contextFeatures), ...current }));
  }, [features, contextFeatures]);

  const bodyReferences = useMemo(
    () => references.filter((reference) => reference.body === body),
    [references, body],
  );
  const selectedReference = references.find(
    (reference) => reference.reference_id === referenceId,
  );
  const referenceMode = body !== "custom" && selectedReference !== undefined;
  const isLolaReference =
    selectedReference !== undefined &&
    (selectedReference.dataset_id.toLowerCase().includes("lola") ||
      selectedReference.notes?.toLowerCase().includes("lola") === true);
  const referenceTarget = useMemo(() => referenceValues(selectedReference), [selectedReference]);

  useEffect(() => {
    onReferenceChange?.(body !== "custom" ? (selectedReference ?? null) : null);
  }, [body, selectedReference, onReferenceChange]);

  const availableBodies = useMemo(
    () => new Set(references.map((reference) => reference.body)),
    [references],
  );
  const [typedCoverage, setTypedCoverage] = useState<TypedCoverageOut | null>(null);
  const [typedObservations, setTypedObservations] = useState<TypedObservationOut[]>([]);
  const [typedLoading, setTypedLoading] = useState(true);
  const [typedError, setTypedError] = useState<string | null>(null);
  const [typedRetry, setTypedRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setTypedLoading(true);
    setTypedError(null);
    getTypedCoverage(controller.signal)
      .then((payload) => setTypedCoverage(payload))
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setTypedCoverage(null);
          setTypedError(caught instanceof Error ? caught.message : String(caught));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setTypedLoading(false);
        }
      });
    getTypedObservations({}, controller.signal)
      .then((payload) => setTypedObservations(payload))
      .catch(() => {
        if (!controller.signal.aborted) {
          setTypedObservations([]);
        }
      });
    return () => controller.abort();
  }, [typedRetry]);

  const cards = useMemo(
    () => dimensionCards(features, typedCoverage, typedObservations),
    [features, typedCoverage, typedObservations],
  );
  const { primary: primaryContext, more: moreContext } = useMemo(
    () => orderContextFeatures(contextFeatures),
    [contextFeatures],
  );

  function updateRow(featureId: string, patch: Partial<RowState>) {
    setRows((current) => ({
      ...current,
      [featureId]: { ...(current[featureId] ?? { value: "", weight: "0" }), ...patch },
    }));
  }

  function buildFilters(
    currentFeatures: FeatureOut[],
    currentContext: ContextFeatureOut[],
    currentRanges: Record<string, [number, number]>,
  ): Record<string, RangeFilterIn> {
    const filters: Record<string, RangeFilterIn> = {};
    for (const feature of currentFeatures) {
      const range = currentRanges[feature.id];
      const filter = range ? rangeToFilter(range, sliderRange(feature)) : null;
      if (filter) {
        filters[feature.id] = filter;
      }
    }
    for (const feature of currentContext) {
      const range = currentRanges[feature.feature_id];
      const filter = range ? rangeToFilter(range, FRACTION_RANGE) : null;
      if (filter) {
        filters[feature.feature_id] = filter;
      }
    }
    return filters;
  }

  useEffect(() => {
    if (!preset || lastAppliedPreset.current === preset.nonce) {
      return;
    }
    lastAppliedPreset.current = preset.nonce;

    const {
      features: currentFeatures,
      contextFeatures: currentContext,
      onSearch: submit,
      limit: currentLimit,
      ranges: currentRanges,
    } = latest.current;
    const target: Record<string, TargetSpec> = {};
    const targetValues: NumericTarget = {};
    const nextRows: Record<string, RowState> = {};

    for (const feature of currentFeatures) {
      const value = preset.values[feature.id];
      if (
        typeof value === "number" &&
        Number.isFinite(value) &&
        value >= feature.min_value &&
        value <= feature.max_value
      ) {
        nextRows[feature.id] = { value: String(value), weight: "1" };
        target[feature.id] = { value, weight: 1 };
        targetValues[feature.id] = value;
      }
    }

    setRows((current) => {
      const merged: Record<string, RowState> = {};
      for (const feature of currentFeatures) {
        merged[feature.id] = nextRows[feature.id] ?? {
          ...(current[feature.id] ?? { value: "", weight: "0" }),
          weight: "0",
        };
      }
      return merged;
    });
    setBody("custom");
    setReferenceId("");
    setFieldErrors({});

    if (Object.keys(target).length === 0) {
      setErrors([{ key: "search.errAoiNoValues" }]);
      return;
    }

    setErrors([]);
    const parsedLimit = Number(currentLimit);
    const sanitizedLimit =
      Number.isInteger(parsedLimit) && parsedLimit >= 1 && parsedLimit <= 10 ? parsedLimit : 10;

    submit({
      request: {
        body: "custom",
        reference_id: null,
        target,
        filters: buildFilters(currentFeatures, currentContext, currentRanges),
        limit: sanitizedLimit,
      },
      target: targetValues,
      body: "custom",
      referenceId: null,
      limit: sanitizedLimit,
    });
  }, [preset]);

  function handleBodyChange(nextBody: Body) {
    setBody(nextBody);
    setErrors([]);
    setFieldErrors({});
    if (nextBody === "custom") {
      setReferenceId("");
      return;
    }
    const firstReference = references.find((reference) => reference.body === nextBody);
    setReferenceId(firstReference?.reference_id ?? "");
  }

  function resetAll() {
    setRows(initialRows(features));
    setReferenceWeights(initialReferenceWeights(features));
    setRanges(fullRanges(features, contextFeatures));
    setLimit("10");
    setErrors([]);
    setFieldErrors({});
  }

  function validateAndSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const nextErrors: UiMessage[] = [];
    const nextFieldErrors: FieldErrors = {};
    const target: Record<string, TargetSpec> = {};
    const contextTarget: NumericTarget = {};
    const parsedLimit = Number(limit);

    if (!Number.isInteger(parsedLimit) || parsedLimit < 1 || parsedLimit > 10) {
      nextErrors.push({ key: "search.errLimit" });
    }

    const weights: Record<string, number> = {};

    if (body !== "custom") {
      if (!selectedReference) {
        nextErrors.push({ key: "search.errSelectReference", vars: { body } });
      } else if (selectedReference.features.length === 0) {
        nextErrors.push({ key: "search.errReferenceEmpty", vars: { body } });
      } else {
        for (const observation of selectedReference.features) {
          if (!features.some((feature) => feature.id === observation.feature_id)) {
            continue;
          }
          const raw = referenceWeights[observation.feature_id] ?? "0";
          const weight = Number(raw);
          if (raw.trim() === "" || !Number.isFinite(weight) || weight < 0) {
            nextFieldErrors[observation.feature_id] = {
              ...nextFieldErrors[observation.feature_id],
              weight: { key: "search.errWeight" },
            };
            continue;
          }
          weights[observation.feature_id] = weight;
        }
        if (
          !Object.keys(nextFieldErrors).length &&
          !Object.values(weights).some((weight) => weight > 0)
        ) {
          nextErrors.push({ key: "search.errNoWeight" });
        }
      }
    } else {
      for (const feature of features) {
        const row = rows[feature.id];
        if (!row) {
          continue;
        }
        const weight = Number(row.weight);
        if (row.weight.trim() === "" || !Number.isFinite(weight) || weight < 0) {
          nextFieldErrors[feature.id] = { weight: { key: "search.errWeight" } };
          continue;
        }
        if (weight === 0) {
          continue;
        }

        const value = Number(row.value);
        if (row.value.trim() === "" || !Number.isFinite(value)) {
          nextFieldErrors[feature.id] = { value: { key: "search.errFiniteNumber" } };
        } else if (value < feature.min_value || value > feature.max_value) {
          nextFieldErrors[feature.id] = {
            value: {
              key: "search.errRange",
              vars: { min: feature.min_value, max: feature.max_value, unit: feature.unit },
            },
          };
        } else {
          target[feature.id] = { value, weight };
          contextTarget[feature.id] = value;
        }
      }

      if (Object.keys(target).length === 0 && Object.keys(nextFieldErrors).length === 0) {
        nextErrors.push({ key: "search.errNoMetric" });
      }
    }

    const fieldMessages: UiMessage[] = Object.entries(nextFieldErrors).flatMap(
      ([featureId, messages]) =>
        (["value", "weight"] as const).flatMap((field): UiMessage[] => {
          const message = messages[field];
          if (!message) {
            return [];
          }
          return [
            {
              key: "search.fieldError",
              vars: { ...message.vars, featureId, field, messageKey: message.key },
            },
          ];
        }),
    );

    const allErrors = [...nextErrors, ...fieldMessages];
    setFieldErrors(nextFieldErrors);
    setErrors(allErrors);

    if (allErrors.length > 0) {
      return;
    }

    const effectiveTarget = referenceMode ? referenceTarget : contextTarget;
    if (Object.keys(effectiveTarget).length === 0) {
      setErrors([{ key: "search.errNoReferenceValues" }]);
      return;
    }

    const request: SearchRequest = {
      body,
      reference_id: body === "custom" ? null : referenceId,
      target: referenceMode ? {} : target,
      ...(referenceMode ? { weights } : {}),
      filters: buildFilters(features, contextFeatures, ranges),
      limit: parsedLimit,
    };

    onSearch({
      request,
      target: effectiveTarget,
      body,
      referenceId: body === "custom" ? null : referenceId,
      limit: parsedLimit,
    });
  }

  /** Renders a stored validation message in the active language. */
  function renderMessage(message: UiMessage): string {
    const vars: MessageVars = { ...message.vars };
    if (typeof vars.labelKey === "string") {
      vars.label = t(vars.labelKey as MessageKey);
    }
    if (typeof vars.min === "number") {
      vars.min = fmt.number(vars.min);
    }
    if (typeof vars.max === "number") {
      vars.max = fmt.number(vars.max);
    }
    if (typeof vars.unit === "string") {
      vars.unit = label.unitName(vars.unit);
    }
    if (typeof vars.body === "string") {
      vars.body = label.body(vars.body);
    }
    if (message.key === "search.fieldError") {
      const featureId = String(vars.featureId);
      const feature = features.find((candidate) => candidate.id === featureId);
      vars.label = label.feature(featureId, feature?.label);
      vars.field = t(vars.field === "weight" ? "search.fieldWeight" : "search.fieldValue");
      vars.message = t(vars.messageKey as MessageKey, vars);
    }
    return t(message.key, vars);
  }

  const unavailableBodies = (["Moon", "Mars"] as const).filter((value) => !availableBodies.has(value));
  const unavailableBodyList = fmt.list(unavailableBodies.map((value) => label.body(value)));
  const renderedErrors = errors.map(renderMessage);

  function weightOf(featureId: string): number {
    if (referenceMode) {
      const hasObservation = selectedReference?.features.some(
        (observation) => observation.feature_id === featureId,
      );
      return hasObservation ? numberOr(referenceWeights[featureId] ?? "0", 0) : 0;
    }
    return numberOr(rows[featureId]?.weight ?? "0", 0);
  }

  function setWeight(featureId: string, value: string) {
    if (referenceMode) {
      setReferenceWeights((current) => ({ ...current, [featureId]: value }));
    } else {
      updateRow(featureId, { weight: value });
    }
  }

  function setRange(featureId: string, next: [number, number]) {
    setRanges((current) => ({ ...current, [featureId]: next }));
  }

  function renderMetric(feature: FeatureOut) {
    const row = rows[feature.id] ?? { value: "", weight: "0" };
    const slider = sliderRange(feature);
    const referenceFeature = selectedReference?.features.find(
      (observation) => observation.feature_id === feature.id,
    );
    const weightDisabled = referenceMode && !referenceFeature;
    const displayWeight = referenceMode
      ? referenceFeature
        ? (referenceWeights[feature.id] ?? "0")
        : ""
      : row.weight;
    const weight = weightOf(feature.id);
    const weighted = weight > 0;
    const valueError = fieldErrors[feature.id]?.value;
    const weightError = fieldErrors[feature.id]?.weight;
    const featureLabel = label.feature(feature.id, feature.label);
    const unitName = label.unitName(feature.unit);
    const unitSymbol = feature.unit === "fraction" ? "" : fmt.unit(feature.unit);
    const range = ranges[feature.id] ?? [slider.lo, slider.hi];
    const filtered = rangeToFilter(range, slider) !== null;

    return (
      <div
        className={`metric${weighted ? " is-weighted" : ""}`}
        key={feature.id}
        role="group"
        aria-labelledby={`metric-${feature.id}-label`}
      >
        <div className="metric-head">
          <span className="metric-name" id={`metric-${feature.id}-label`}>
            {featureLabel}
          </span>
          <span className="metric-target mono">
            {referenceMode ? (
              referenceFeature ? (
                <span title={t("search.referenceValueTitle")}>
                  ◆ {fmt.number(referenceFeature.value)}
                  {unitSymbol ? ` ${unitSymbol}` : ""}
                </span>
              ) : (
                <span className="is-muted">{t("search.noReferenceValue")}</span>
              )
            ) : null}
          </span>
        </div>

        {!referenceMode && (
          <div className="metric-row">
            <span className="metric-label" aria-hidden="true">
              {t("search.targetShort")}
            </span>
            <SingleSlider
              value={numberOr(row.value, slider.lo)}
              min={slider.lo}
              max={slider.hi}
              step={slider.step}
              tone="target"
              label={t("search.targetSlider", { label: featureLabel })}
              onChange={(value) => updateRow(feature.id, { value: String(value) })}
            />
            <input
              id={`metric-${feature.id}-value`}
              aria-label={t("search.targetValue", { unit: unitName })}
              className="metric-number"
              type="number"
              inputMode="decimal"
              step="any"
              min={feature.min_value}
              max={feature.max_value}
              value={row.value}
              aria-invalid={valueError ? true : undefined}
              aria-describedby={valueError ? `metric-${feature.id}-value-error` : undefined}
              onChange={(event) => updateRow(feature.id, { value: event.target.value })}
            />
          </div>
        )}
        {valueError && (
          <p className="field-error" id={`metric-${feature.id}-value-error`}>
            {renderMessage(valueError)}
          </p>
        )}

        <div className="metric-row">
          <span className="metric-label" aria-hidden="true">
            {t("search.rangeLabel")}
          </span>
          <RangeSlider
            id={`range-${feature.id}`}
            range={range}
            slider={slider}
            minLabel={t("search.rangeMin", { label: featureLabel })}
            maxLabel={t("search.rangeMax", { label: featureLabel })}
            onChange={(next) => setRange(feature.id, next)}
          />
          <span className={`metric-range mono${filtered ? " is-active" : ""}`}>
            {rangeText(range, slider, unitSymbol)}
          </span>
        </div>

        <div className="metric-row metric-row-weight">
          <span className="metric-label" aria-hidden="true">
            {t("search.weightShort")}
          </span>
          <SingleSlider
            value={weight}
            min={0}
            max={WEIGHT_SLIDER_MAX}
            step={0.5}
            disabled={weightDisabled}
            label={t("search.weightSlider", { label: featureLabel })}
            onChange={(value) => setWeight(feature.id, String(value))}
          />
          <input
            id={`metric-${feature.id}-weight`}
            aria-label={t("search.weight")}
            className={`metric-number weight-pill${weighted ? " is-active" : ""}`}
            type="number"
            inputMode="decimal"
            step="any"
            min="0"
            value={displayWeight}
            disabled={weightDisabled}
            aria-invalid={weightError ? true : undefined}
            aria-describedby={weightError ? `metric-${feature.id}-weight-error` : undefined}
            onChange={(event) => setWeight(feature.id, event.target.value)}
          />
        </div>
        {weightError && (
          <p className="field-error" id={`metric-${feature.id}-weight-error`}>
            {renderMessage(weightError)}
          </p>
        )}

        <details className="metric-more">
          <summary>{t("search.metricMore")}</summary>
          <code>{feature.id}</code>
          <p>
            <SourceText>{feature.description}</SourceText>
          </p>
          <p>
            {rich("search.validDomain", {
              domain: <SourceText>{feature.valid_domain}</SourceText>,
            })}
          </p>
        </details>
      </div>
    );
  }

  function renderContextFilter(feature: ContextFeatureOut) {
    const range = ranges[feature.feature_id] ?? [0, 1];
    const featureLabel = shortContextLabel(label.feature(feature.feature_id, feature.label));
    const filtered = rangeToFilter(range, FRACTION_RANGE) !== null;
    return (
      <div className="metric context-metric" key={feature.feature_id} role="group" aria-label={featureLabel}>
        <div className="metric-head">
          <span className="metric-name">{featureLabel}</span>
          <span className={`metric-range mono${filtered ? " is-active" : ""}`}>
            {rangeText(range, FRACTION_RANGE, "")}
          </span>
        </div>
        <RangeSlider
          id={`range-${feature.feature_id}`}
          range={range}
          slider={FRACTION_RANGE}
          minLabel={t("search.rangeMin", { label: featureLabel })}
          maxLabel={t("search.rangeMax", { label: featureLabel })}
          onChange={(next) => setRange(feature.feature_id, next)}
        />
      </div>
    );
  }

  const bodyOptions: { value: Body; available: boolean }[] = [
    { value: "Moon", available: availableBodies.has("Moon") },
    { value: "custom", available: true },
    { value: "Mars", available: availableBodies.has("Mars") },
  ];

  return (
    <form className="search-form" onSubmit={validateAndSubmit} noValidate>
      <h2 className="sr-only">{t("search.heading")}</h2>
      <section className="target-bar" aria-label={t("search.targetBarLabel")}>
        <div className="segmented" role="radiogroup" aria-label={t("search.bodyPreset")}>
          {bodyOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={body === option.value}
              disabled={!option.available}
              className={`segment${body === option.value ? " is-active" : ""} body-${option.value.toLowerCase()}`}
              onClick={() => handleBodyChange(option.value)}
            >
              {option.value === "custom" ? t("search.bodyCustomShort") : label.body(option.value)}
              {!option.available && <span className="sr-only">{t("search.bodyUnavailableSuffix")}</span>}
            </button>
          ))}
        </div>

        {body === "custom" ? (
          <p className="target-summary">
            <span className="target-mark is-custom" aria-hidden="true" />
            <strong>{t("search.customTargetTitle")}</strong>
            <span className="muted">{t("search.exampleTitle")}</span>
          </p>
        ) : (
          <div className="target-summary">
            <span className="target-mark" aria-hidden="true" />
            <label className="sr-only" htmlFor="reference-select">
              {t("search.referenceRecord")}
            </label>
            <select
              id="reference-select"
              className="reference-select"
              value={referenceId}
              onChange={(event) => setReferenceId(event.target.value)}
            >
              {bodyReferences.map((reference) => (
                <option key={reference.reference_id} value={reference.reference_id}>
                  {reference.display_name}
                </option>
              ))}
            </select>
            {selectedReference && (
              <span className="chips mono">
                <span className="chip">{selectedReference.dataset_id}</span>
                <DataKindBadge kind="observed" />
                <span className="chip">
                  {fmt.number(selectedReference.window_size_m / 1000, 2)} ×{" "}
                  {fmt.number(selectedReference.window_size_m / 1000, 2)} km
                </span>
                <span className="chip">
                  {t("search.gridChip", {
                    resolution: fmt.number(selectedReference.analysis_resolution_m, 0),
                  })}
                </span>
              </span>
            )}
          </div>
        )}

        {unavailableBodies.length > 0 && (
          <p className="field-hint target-note" id="preset-unavailable-note">
            {referencesError
              ? tp("search.presetsLoadFailed", unavailableBodies.length, {
                  error: referencesError,
                  bodies: unavailableBodyList,
                })
              : tp("search.presetsUnavailable", unavailableBodies.length, {
                  bodies: unavailableBodyList,
                })}
          </p>
        )}

        {body === "custom" && (
          <details className="target-details">
            <summary>{t("search.aboutCustom")}</summary>
            <p className="example-note" id="example-note">
              {t("search.exampleBody")}
            </p>
          </details>
        )}

        {selectedReference && body !== "custom" && (
          <details className="target-details reference-details">
            <summary>{t("search.referenceDetails")}</summary>
            <p className="field-hint">
              {rich("search.referenceTargetHint", {
                name: <strong>{selectedReference.display_name}</strong>,
              })}
            </p>
            <dl className="reference-facts">
              <div>
                <dt>{t("search.factBody")}</dt>
                <dd>{label.body(selectedReference.body)}</dd>
              </div>
              <div>
                <dt>{t("search.factDataset")}</dt>
                <dd>
                  <code>{selectedReference.dataset_id}</code>
                </dd>
              </div>
              <div>
                <dt>{t("search.factWindow")}</dt>
                <dd>
                  {t("format.windowAt", {
                    window: fmt.number(selectedReference.window_size_m),
                    resolution: fmt.number(selectedReference.analysis_resolution_m),
                  })}
                </dd>
              </div>
              <div>
                <dt>{t("search.factValidFraction")}</dt>
                <dd>{fmt.percent(selectedReference.valid_fraction)}</dd>
              </div>
            </dl>
            <div className="table-scroll">
              <table className="reference-table">
                <caption>{t("search.referenceTableCaption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("search.colFeature")}</th>
                    <th scope="col">{t("search.colValue")}</th>
                    <th scope="col">{t("search.colQuality")}</th>
                    <th scope="col">{t("search.colValidFraction")}</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedReference.features.map((feature) => (
                    <tr key={feature.feature_id}>
                      <th scope="row">{label.feature(feature.feature_id, feature.label)}</th>
                      <td>{fmt.withUnit(feature.value, feature.unit)}</td>
                      <td>{label.quality(feature.quality)}</td>
                      <td>{fmt.percent(feature.valid_fraction)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {selectedReference.quality_flags.length > 0 && (
              <ul className="reference-flags" aria-label={t("search.qualityFlags")}>
                {selectedReference.quality_flags.map((flag) => (
                  <li key={flag}>
                    <code>{flag}</code>
                  </li>
                ))}
              </ul>
            )}
            {isLolaReference && (
              <p className="reference-caveat">
                <strong>{t("search.lolaCaveatTitle")}</strong> {t("search.lolaCaveat")}
              </p>
            )}
            {selectedReference.notes && (
              <div className="reference-notes">
                <SourceTextNote />
                <p>
                  <SourceText>{selectedReference.notes}</SourceText>
                </p>
              </div>
            )}
          </details>
        )}
      </section>

      {renderedErrors.length > 0 && (
        <div className="alert alert-error" role="alert" aria-labelledby="form-errors-title">
          <p id="form-errors-title">
            <strong>{t("search.errorsTitle")}</strong>
          </p>
          <ul>
            {renderedErrors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </div>
      )}

      <section className="dimension-board" aria-labelledby="dimension-board-title">
        <div className="board-head">
          <h2 id="dimension-board-title">{t("search.boardTitle")}</h2>
          <div className="board-actions">
            <label className="limit-control">
              <span>{t("search.limitShort")}</span>
              <input
                id="limit-input"
                type="number"
                inputMode="numeric"
                min="1"
                max="10"
                step="1"
                value={limit}
                aria-label={t("search.limitLabel")}
                onChange={(event) => setLimit(event.target.value)}
              />
            </label>
            <button type="button" className="link-button" onClick={resetAll}>
              {t("search.reset")}
            </button>
            {onOpenDimensions && (
              <button
                type="button"
                className="link-button"
                onClick={onOpenDimensions}
              >
                {t("search.dimensionOpen")}
              </button>
            )}
            <button
              type="button"
              className="secondary-button"
              aria-expanded={boardOpen}
              aria-controls="dimension-columns"
              onClick={() => setBoardOpen((open) => !open)}
            >
              {boardOpen ? t("search.collapse") : t("search.expand")}
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={loading}
              aria-label={t("search.submit")}
            >
              {loading ? t("search.submitting") : t("search.submitShort")}
            </button>
          </div>
        </div>

        <div className="dimension-scroll" id="dimension-columns" hidden={!boardOpen}>
          {typedError && (
            <div className="alert alert-error dimension-data-alert" role="alert">
              <p>
                <strong>{t("search.dimensionDataFailed")}</strong>{" "}
                <SourceText>{typedError}</SourceText>
              </p>
              <button
                type="button"
                className="secondary-button"
                onClick={() => setTypedRetry((value) => value + 1)}
              >
                {t("search.dimensionRetry")}
              </button>
            </div>
          )}

          <div className="dimension-layout">
          <div className="dimension-nav" role="group" aria-label={t("search.boardTitle")}>
            {cards.map((card) => {
              const count = card.features.filter((feature) => weightOf(feature.id) > 0).length;
              const active = card.code === activeDimension;
              return (
                <button
                  type="button"
                  key={card.code}
                  className={`dimension-nav-item${active ? " is-active" : ""}`}
                  aria-pressed={active}
                  onClick={() => setActiveDimension(card.code)}
                >
                  <span>{label.dimension(card.code)}</span>
                  {count > 0 && <span className="dimension-nav-count mono">{count}</span>}
                </button>
              );
            })}
          </div>
          <div className="dimension-grid dimension-board-grid">
            {cards.map((card) => {
              const activeCount = card.features.filter((feature) => weightOf(feature.id) > 0).length;
              const name = label.dimension(card.code);
              const role =
                card.role === "similarity"
                  ? t("search.dimensionRoleSimilarity")
                  : card.role === "context_only"
                    ? t("search.dimensionRoleContext")
                    : t("search.dimensionRoleEvidence");
              const bodies = Object.entries(card.byBody)
                .map(([body, count]) => `${label.body(body)} ${count}`)
                .join(" · ");
              const states = Object.entries(card.byState)
                .map(([state, count]) => `${label.status(state)} ${count}`)
                .join(" · ");
              return (
                <section
                  className={`dimension-column dimension-card${card.code === "D01" ? " is-similarity" : ""}${card.code === activeDimension ? " is-active" : ""}`}
                  key={card.code}
                  aria-label={`${card.code} ${name}`}
                  data-testid={`dimension-card-${card.code}`}
                >
                  <div className="dimension-head">
                    <h3>
                      <span className="dimension-code mono">{card.code}</span> {name}
                    </h3>
                    {card.code === "D01" ? (
                      <span className={`dimension-tag${activeCount > 0 ? " is-active" : ""}`}>
                        {activeCount > 0
                          ? t("search.dimensionCount", {
                              active: activeCount,
                              total: card.features.length,
                            })
                          : t("search.dimensionNone")}
                      </span>
                    ) : (
                      <span className={`dimension-tag${card.role === "similarity" ? " is-active" : " is-context"}`}>
                        {role}
                      </span>
                    )}
                  </div>

                  {card.code === "D01" ? (
                    <>
                      <p className="field-hint">{role}</p>
                      <div className="metric-list">{card.features.map(renderMetric)}</div>
                    </>
                  ) : typedLoading ? (
                    <p className="field-hint" role="status">
                      {t("search.dimensionLoading")}
                    </p>
                  ) : (
                    <>
                      <p data-testid={`dimension-total-${card.code}`}>
                        {card.total > 0
                          ? t("search.dimensionObservations", { count: card.total })
                          : t("search.dimensionNoObservations")}
                      </p>
                      {bodies && (
                        <p className="field-hint">
                          {t("search.dimensionBodies", { bodies })}
                        </p>
                      )}
                      {states && (
                        <p className="field-hint">
                          {t("search.dimensionStates", { states })}
                        </p>
                      )}
                      {card.valueKinds.length > 0 && (
                        <p className="field-hint">
                          {t("search.dimensionKinds", {
                            kinds: card.valueKinds.join(", "),
                          })}
                        </p>
                      )}
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={!onOpenDimensions}
                        onClick={() => onOpenDimensions?.()}
                      >
                        {t("search.dimensionView")}
                      </button>
                    </>
                  )}
                </section>
              );
            })}
          </div>
          </div>

          <section className="context-filters" aria-labelledby="context-heading">
            <div className="dimension-head">
              <h3 id="context-heading">{t("search.filtersLegend")}</h3>
              <span className="dimension-tag is-context">{t("search.contextTag")}</span>
            </div>
            <p className="field-hint">{t("search.filtersHint")}</p>
            <div className="context-grid">{primaryContext.map(renderContextFilter)}</div>
            {moreContext.length > 0 && (
              <details className="metric-more context-more">
                <summary>{t("search.moreContext", { count: moreContext.length })}</summary>
                <div className="context-grid">{moreContext.map(renderContextFilter)}</div>
              </details>
            )}
          </section>
        </div>
        <p className="board-foot field-hint">
          {referenceMode ? t("search.referenceWeightHint") : t("search.customWeightHint")}{" "}
          {t("search.submitHint")}
        </p>
      </section>
    </form>
  );
}
