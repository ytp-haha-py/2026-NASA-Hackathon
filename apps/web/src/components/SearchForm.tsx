import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  getSearchFeatures,
  getTypedCoverage,
  getTypedObservations,
  type ContextFeatureOut,
  type FeatureOut,
  type FeatureRegistryOut,
  type MultiDimSearchRequest,
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
import {
  RESEARCH_FEATURE_IDS,
  RESEARCH_PRESETS,
  researchWeights,
  unavailableResearchFeatures,
} from "../lib/research-presets";
import { queryFeaturesBeyondD01, soilFeatureParts } from "../lib/multidim";
import { hasMapCoordinates, PlanetMap, type PlanetBody } from "./PlanetMap";
import { RangeSlider, SingleSlider } from "./RangeSlider";

type Body = SearchRequest["body"];
const PLANET_BODIES = ["Moon", "Mars"] as const;

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
  const [researchPresetId, setResearchPresetId] = useState("manual");
  const [referenceIds, setReferenceIds] = useState<Partial<Record<PlanetBody, string>>>({});
  const [planetMapOpen, setPlanetMapOpen] = useState(false);
  const activeBodyButton = useRef<HTMLButtonElement>(null);
  const [limit, setLimit] = useState("10");
  const [boardOpen, setBoardOpen] = useState(true);
  const [activeDimension, setActiveDimension] = useState("D01");
  const [soilDepth, setSoilDepth] = useState("0–5 cm");
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
    setResearchPresetId("manual");
  }, [features, contextFeatures]);

  const missingResearchFeatures = unavailableResearchFeatures(features);
  const selectedResearchPreset = missingResearchFeatures.length === 0
    ? RESEARCH_PRESETS.find((item) => item.id === researchPresetId)
    : undefined;

  function applyResearchPreset(id: string) {
    const researchPreset = RESEARCH_PRESETS.find((item) => item.id === id);
    if (!researchPreset) {
      setResearchPresetId("manual");
      return;
    }
    const weights = researchWeights(researchPreset, features);
    if (Object.keys(weights).length === 0) return;
    setRows((current) => ({
      ...current,
      ...Object.fromEntries(features.map((feature) => [
        feature.id,
        {
          ...(current[feature.id] ?? initialRows([feature])[feature.id]),
          weight: weights[feature.id],
        },
      ])),
    }));
    setResearchPresetId(id);
    setErrors([]);
    setFieldErrors({});
  }

  const mapReferences = useMemo(() => references.filter(hasMapCoordinates), [references]);
  const referenceMode = body !== "custom";
  const referenceId = referenceMode ? (referenceIds[body] ?? null) : null;
  const selectedReference = mapReferences.find(
    (reference) => reference.body === body && reference.reference_id === referenceId,
  );
  const isLolaReference =
    selectedReference !== undefined &&
    (selectedReference.dataset_id.toLowerCase().includes("lola") ||
      selectedReference.notes?.toLowerCase().includes("lola") === true);
  const referenceTarget = useMemo(() => referenceValues(selectedReference), [selectedReference]);

  useEffect(() => {
    onReferenceChange?.(selectedReference ?? null);
  }, [selectedReference, onReferenceChange]);

  const availableBodies = useMemo(
    () => new Set(mapReferences.map((reference) => reference.body)),
    [mapReferences],
  );
  const selectReference = useCallback((id: string) => {
    if (body !== "custom") {
      setReferenceIds((current) => ({ ...current, [body]: id }));
      setFieldErrors({});
      setErrors([]);
    }
  }, [body]);
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

  const [registry, setRegistry] = useState<FeatureRegistryOut | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getSearchFeatures(controller.signal)
      .then((payload) => setRegistry(payload))
      .catch(() => {
        if (!controller.signal.aborted) {
          setRegistry(null);
        }
      });
    return () => controller.abort();
  }, [typedRetry]);

  /** D02–D06 features the API accepts as similarity targets (Earth only today). */
  const queryFeatures = useMemo(() => {
    const capabilityIds = new Set(features.map((feature) => feature.id));
    return queryFeaturesBeyondD01(registry)
      .filter((feature) => !capabilityIds.has(feature.id))
      .map((feature) => {
        const soil = soilFeatureParts(feature.id);
        return soil
          ? {
              ...feature,
              label: t("soil.label", {
                property: t(`soil.${soil.property}` as MessageKey),
                depth: soil.depth,
              }),
            }
          : feature;
      });
  }, [registry, features, t]);
  /** Registry features per dimension that are observed but not score inputs (listed read-only). */
  const registryByDimension = useMemo(() => {
    const groups = new Map<string, FeatureRegistryOut["features"]>();
    for (const feature of registry?.features ?? []) {
      if (feature.roles.similarity?.supported) {
        continue;
      }
      if (!(feature.coverage?.observation_count)) {
        continue;
      }
      const list = groups.get(feature.dimension_id) ?? [];
      list.push(feature);
      groups.set(feature.dimension_id, list);
    }
    return groups;
  }, [registry]);
  const queryFeatureIds = useMemo(
    () => new Set(queryFeatures.map((feature) => feature.id)),
    [queryFeatures],
  );

  useEffect(() => {
    setRows((current) => ({ ...initialRows(queryFeatures), ...current }));
    setRanges((current) => ({ ...fullRanges(queryFeatures, []), ...current }));
  }, [queryFeatures]);

  const cards = useMemo(
    () =>
      dimensionCards(
        referenceMode ? features : [...features, ...queryFeatures],
        typedCoverage,
        typedObservations,
      ),
    [features, queryFeatures, referenceMode, typedCoverage, typedObservations],
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
    setResearchPresetId("manual");

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
      const merged: Record<string, RowState> = Object.fromEntries(
        Object.entries(current).map(([id, row]) => [id, { ...row, weight: "0" }]),
      );
      for (const feature of currentFeatures) {
        merged[feature.id] = nextRows[feature.id] ?? {
          ...(current[feature.id] ?? { value: "", weight: "0" }),
          weight: "0",
        };
      }
      return merged;
    });
    setBody("custom");
    setPlanetMapOpen(false);
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
    setPlanetMapOpen(nextBody !== "custom");
    setErrors([]);
    setFieldErrors({});
  }

  function resetAll() {
    setResearchPresetId("manual");
    setRows(initialRows([...features, ...queryFeatures]));
    setReferenceWeights(initialReferenceWeights(features));
    setRanges(fullRanges([...features, ...queryFeatures], contextFeatures));
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
      for (const feature of [...features, ...queryFeatures]) {
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
      reference_id: referenceId,
      target: referenceMode ? {} : target,
      ...(referenceMode ? { weights } : {}),
      filters: buildFilters(features, contextFeatures, ranges),
      limit: parsedLimit,
    };

    // Features beyond D01 (e.g. D06 soil) are only accepted by the multidimensional search.
    let multidim: MultiDimSearchRequest | undefined;
    if (!referenceMode) {
      const queryFilters = buildFilters(queryFeatures, [], ranges);
      const usesQueryFeatures =
        Object.keys(target).some((id) => queryFeatureIds.has(id)) ||
        Object.keys(queryFilters).length > 0;
      if (usesQueryFeatures) {
        const contextIds = new Set(contextFeatures.map((feature) => feature.feature_id));
        if (Object.keys(request.filters ?? {}).some((id) => contextIds.has(id))) {
          setErrors([{ key: "search.errLandcoverWithSoil" }]);
          return;
        }
        multidim = {
          candidate_body: "Earth",
          reference_id: null,
          targets: target,
          filters: { ...buildFilters(features, [], ranges), ...queryFilters },
          dimension_weights: {},
          limit: parsedLimit,
          offset: 0,
          include_evidence: false,
          evidence_limit: 12,
        };
      }
    }

    onSearch({
      request,
      multidim,
      target: effectiveTarget,
      body,
      referenceId,
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

  const unavailableBodies = PLANET_BODIES.filter((value) => !availableBodies.has(value));
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
      setResearchPresetId("manual");
      updateRow(featureId, { weight: value });
    }
  }

  function setRange(featureId: string, next: [number, number]) {
    setRanges((current) => ({ ...current, [featureId]: next }));
  }

  /**
   * SoilGrids features come as property × depth. Show one depth at a time (5 rows instead of 20);
   * a dot marks depths that already carry a weight.
   */
  function renderFeatureGroup(groupFeatures: FeatureOut[]) {
    const soil = groupFeatures.map((feature) => soilFeatureParts(feature.id));
    if (soil.some((part) => part === null)) {
      return <div className="metric-list">{groupFeatures.map(renderMetric)}</div>;
    }
    const depths = [...new Set(soil.map((part) => part!.depth))].sort(
      (a, b) => parseFloat(a) - parseFloat(b),
    );
    const depth = depths.includes(soilDepth) ? soilDepth : depths[0];
    return (
      <>
        <div className="segmented depth-tabs" role="group" aria-label={t("soil.depthLabel")}>
          {depths.map((value) => {
            const weighted = groupFeatures.some(
              (feature, index) => soil[index]!.depth === value && weightOf(feature.id) > 0,
            );
            return (
              <button
                key={value}
                type="button"
                aria-pressed={value === depth}
                className={`segment${value === depth ? " is-active" : ""}`}
                onClick={() => setSoilDepth(value)}
              >
                {value}
                {weighted && <span className="depth-dot" aria-label={t("soil.depthWeighted")} />}
              </button>
            );
          })}
        </div>
        <div className="metric-list">
          {groupFeatures.filter((_, index) => soil[index]!.depth === depth).map(renderMetric)}
        </div>
      </>
    );
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
    ...PLANET_BODIES.map((value) => ({ value, available: availableBodies.has(value) })),
    { value: "custom", available: true },
  ];

  return (
    <form className="search-form" onSubmit={validateAndSubmit} noValidate>
      <h2 className="sr-only">{t("search.heading")}</h2>
      <section className="target-bar" aria-label={t("search.targetBarLabel")}>
        <div className="target-row">
          <span className="row-label" aria-hidden="true">{t("search.targetRowLabel")}</span>
        <div className="segmented" role="radiogroup" aria-label={t("search.bodyPreset")}>
          {bodyOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              ref={body === option.value ? activeBodyButton : undefined}
              aria-checked={body === option.value}
              aria-controls={option.value === "custom" ? undefined : "planet-preview"}
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
          <p className="target-summary">
            <span className="target-mark" aria-hidden="true" />
            <strong role="status">
              {selectedReference?.display_name ?? t("search.pickReferenceOnMap")}
            </strong>
          </p>
        )}

        </div>

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

        {body === "custom" && (
          <ResearchScenarioPicker
            selectedId={selectedResearchPreset?.id ?? "manual"}
            missing={missingResearchFeatures}
            features={features}
            onChange={applyResearchPreset}
          />
        )}

        <div id="planet-preview" className="planet-preview" hidden={!planetMapOpen}>
          {body !== "custom" && planetMapOpen && (
            <>
              <button
                type="button"
                className="secondary-button planet-preview-collapse"
                onClick={() => {
                  setPlanetMapOpen(false);
                  activeBodyButton.current?.focus();
                }}
              >
                {t("map.collapsePlanetPreview")}
              </button>
              <PlanetMap
                key={body}
                body={body}
                references={mapReferences}
                selectedReferenceId={selectedReference?.reference_id ?? null}
                onSelectReference={selectReference}
                regionLabel={t("map.planetRegionLabel", { body: label.body(body) })}
              />
            </>
          )}
        </div>
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
          <div className="board-title-controls">
            <h2 id="dimension-board-title">{t("search.boardTitle")}</h2>
          </div>
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
              disabled={loading || (referenceMode && !selectedReference)}
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
              return (
                <section
                  className={`dimension-column dimension-card${card.features.length > 0 ? " is-similarity" : ""}${card.code === activeDimension ? " is-active" : ""}`}
                  key={card.code}
                  aria-label={`${card.code} ${name}`}
                  data-testid={`dimension-card-${card.code}`}
                >
                  <div className="dimension-head">
                    <h3>
                      <span className="dimension-code mono">{card.code}</span> {name}
                    </h3>
                    {card.features.length > 0 && (
                      <span className={`dimension-tag${activeCount > 0 ? " is-active" : ""}`}>
                        {activeCount > 0
                          ? t("search.dimensionCount", {
                              active: activeCount,
                              total: card.features.length,
                            })
                          : t("search.dimensionNone")}
                      </span>
                    )}
                  </div>

                  {card.features.length > 0 ? (
                    <>
                      {card.code === "D01" && <p className="field-hint">{role}</p>}
                      {renderFeatureGroup(card.features)}
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
                      {(registryByDimension.get(card.code) ?? []).length > 0 && (
                        <ul className="observed-feature-list">
                          {(registryByDimension.get(card.code) ?? []).map((feature) => {
                            const coverage = feature.coverage;
                            const bodies = fmt.list(
                              Object.keys(coverage?.by_body ?? {}).map((name) => label.body(name)),
                            );
                            // Long technical units (e.g. dimensionless_spectral_parameter) add noise.
                            const unit =
                              feature.unit.length <= 12 && !feature.unit.includes("_")
                                ? feature.unit
                                : "";
                            const min = coverage?.observed_numeric_min;
                            const max = coverage?.observed_numeric_max;
                            return (
                              <li key={feature.feature_id}>
                                <span className="observed-feature-name">
                                  {readableFeatureName(feature.feature_id, feature.label, label.feature)}
                                </span>
                                <span className="observed-feature-meta">
                                  {bodies && <span>{bodies}</span>}
                                  <span className="mono">
                                    {t("search.observationCount", {
                                      count: coverage?.observation_count ?? 0,
                                    })}
                                  </span>
                                  {typeof min === "number" && typeof max === "number" && (
                                    <span className="mono">
                                      {min === max
                                        ? `${fmt.number(min, 3)} ${unit}`.trim()
                                        : `${fmt.number(min, 3)} – ${fmt.number(max, 3)} ${unit}`.trim()}
                                    </span>
                                  )}
                                </span>
                              </li>
                            );
                          })}
                        </ul>
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

/**
 * "How to compare": one button shows the current research scenario; it opens a grouped list.
 */
function ResearchScenarioPicker({
  selectedId,
  missing,
  features,
  onChange,
}: {
  selectedId: string;
  missing: readonly string[];
  features: FeatureOut[];
  onChange: (id: string) => void;
}) {
  const { t, fmt, label } = useI18n();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const unavailable = missing.length > 0;
  const selected = RESEARCH_PRESETS.find((item) => item.id === selectedId);
  const featureLabels = useMemo(
    () => new Map(features.map((f) => [f.id, f.label])),
    [features],
  );
  const emphasis = useMemo(
    () => selected
      ? RESEARCH_FEATURE_IDS.map((id, index) => ({ id, index, r: selected.importance[id] }))
          .sort((a, b) => b.r - a.r || a.index - b.index)
          .slice(0, 3)
          .map((entry) => label.feature(entry.id, featureLabels.get(entry.id)))
      : [],
    [selected, featureLabels, label],
  );
  const groups = [...new Set(RESEARCH_PRESETS.map((item) => item.groupKey))];

  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function choose(id: string) {
    onChange(id);
    setOpen(false);
  }

  function option(id: string, title: string) {
    return (
      <label key={id} className={`scenario-option${id === selectedId ? " is-selected" : ""}`}>
        <input
          type="radio"
          name="research-scenario"
          value={id}
          checked={id === selectedId}
          disabled={id !== "manual" && unavailable}
          onChange={() => choose(id)}
        />
        <span className="scenario-option-title">{title}</span>
      </label>
    );
  }

  return (
    <div className="target-row research-row" ref={rootRef}>
      <span className="row-label" id="research-row-label">
        {t("research.scenarioLabel")}
      </span>
      <div className="scenario-picker">
        <button
          type="button"
          className="scenario-trigger"
          aria-haspopup="true"
          aria-expanded={open}
          aria-labelledby="research-row-label research-trigger-value"
          data-scenario={selectedId}
          title={
            unavailable
              ? t("research.unavailable", { features: fmt.list(missing.map((id) => label.feature(id))) })
              : t("research.disclaimer")
          }
          onClick={() => setOpen((value) => !value)}
        >
          <span id="research-trigger-value">{selected ? t(selected.titleKey) : t("research.manual")}</span>
          <span className="scenario-chevron" aria-hidden="true" />
        </button>
        {open && (
          <fieldset className="scenario-menu">
            <legend className="sr-only">{t("research.scenarioLabel")}</legend>
            {groups.map((groupKey) => (
              <div key={groupKey} className="scenario-group">
                <p className="scenario-group-title">{t(groupKey)}</p>
                {RESEARCH_PRESETS.filter((item) => item.groupKey === groupKey).map((item) =>
                  option(item.id, t(item.titleKey)),
                )}
              </div>
            ))}
            <div className="scenario-group is-manual">
              {option("manual", t("research.manualLong"))}
            </div>
          </fieldset>
        )}
      </div>
      {selected && emphasis.length > 0 && (
        <p className="research-emphasis">
          {t("research.emphasis", { features: fmt.list(emphasis) })}{" "}
        </p>
      )}
    </div>
  );
}

/** Uses a localized label when one exists, otherwise tidies the registry's raw label. */
function readableFeatureName(
  featureId: string,
  apiLabel: string,
  localize: (id: string, fallback?: string | null) => string,
): string {
  const localized = localize(featureId, null);
  if (localized && localized !== featureId && localized !== featureId.replaceAll("_", " ")) {
    return localized;
  }
  const text = apiLabel.trim() || featureId.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
