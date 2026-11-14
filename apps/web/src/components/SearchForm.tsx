import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type {
  ContextFeatureOut,
  FeatureOut,
  NumericTarget,
  RangeFilterIn,
  ReferenceOut,
  SearchRequest,
  TargetSpec,
} from "../api/client";
import { SourceText, SourceTextNote, useI18n } from "../i18n/I18nProvider";
import type { MessageVars } from "../i18n/translate";
import type { MessageKey } from "../locales/en/messages";
import type { SearchSubmission, SearchTargetPreset } from "../lib/search-types";

type RowState = {
  included: boolean;
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
  onSearch: (submission: SearchSubmission) => void;
  loading: boolean;
  preset?: SearchTargetPreset | null;
};

type FilterState = {
  enabled: boolean;
  value: string;
};

/** Weight slider upper bound; the number input still accepts any finite value ≥ 0. */
const WEIGHT_SLIDER_MAX = 3;

function initialReferenceWeights(features: FeatureOut[]): Record<string, string> {
  return Object.fromEntries(features.map((feature) => [feature.id, "0"]));
}

const EXAMPLE_VALUES: Record<string, { value: string; weight: string; included: boolean }> = {
  slope_mean_deg: { value: "12", weight: "1", included: true },
  slope_p90_deg: { value: "25", weight: "0.5", included: false },
  terrain_relief_p95_p05_m: { value: "80", weight: "0.5", included: true },
  roughness_std_m: { value: "4", weight: "0.3", included: false },
};

function initialRows(features: FeatureOut[]): Record<string, RowState> {
  return Object.fromEntries(
    features.map((feature) => {
      const example = EXAMPLE_VALUES[feature.id];
      return [
        feature.id,
        example
          ? { ...example }
          : { included: false, value: "", weight: "1" },
      ];
    }),
  );
}

function referenceValues(reference: ReferenceOut | undefined): NumericTarget {
  if (!reference) {
    return {};
  }
  return Object.fromEntries(
    reference.features.map((feature) => [feature.feature_id, feature.value]),
  );
}

type FilterNameKey = "search.filterBareName" | "search.filterBuiltName";

function parseFilter(
  state: FilterState,
  labelKey: FilterNameKey,
): { value: number | null; error: UiMessage | null } {
  if (!state.enabled) {
    return { value: null, error: null };
  }
  if (state.value.trim() === "") {
    return {
      value: null,
      error: { key: "search.errFilterRequired", vars: { labelKey } },
    };
  }
  const value = Number(state.value);
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    return { value: null, error: { key: "search.errFilterRange", vars: { labelKey } } };
  }
  return { value, error: null };
}

function buildFilters(
  bareFilter: FilterState,
  builtFilter: FilterState,
): Record<string, RangeFilterIn> {
  const filters: Record<string, RangeFilterIn> = {};
  const bare = parseFilter(bareFilter, "search.filterBareName");
  if (bare.value !== null) {
    filters.landcover_bare_sparse_fraction = { min: bare.value };
  }
  const built = parseFilter(builtFilter, "search.filterBuiltName");
  if (built.value !== null) {
    filters.landcover_built_up_fraction = { max: built.value };
  }
  return filters;
}

export function SearchForm({
  features,
  contextFeatures,
  references,
  referencesError,
  onSearch,
  loading,
  preset = null,
}: SearchFormProps) {
  const { t, tp, rich, fmt, label } = useI18n();
  const [rows, setRows] = useState<Record<string, RowState>>(() => initialRows(features));
  /** Reference mode starts with every weight at 0: the user opts each metric in explicitly. */
  const [referenceWeights, setReferenceWeights] = useState<Record<string, string>>(() =>
    initialReferenceWeights(features),
  );
  const [body, setBody] = useState<SearchRequest["body"]>("custom");
  const [referenceId, setReferenceId] = useState("");
  const [limit, setLimit] = useState("10");
  const [bareFilter, setBareFilter] = useState<FilterState>({
    enabled: false,
    value: "0.5",
  });
  const [builtFilter, setBuiltFilter] = useState<FilterState>({
    enabled: false,
    value: "0.1",
  });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [errors, setErrors] = useState<UiMessage[]>([]);
  const lastAppliedPreset = useRef<number | null>(null);
  const latest = useRef({
    features,
    onSearch,
    limit,
    bareFilter,
    builtFilter,
  });

  useEffect(() => {
    latest.current = { features, onSearch, limit, bareFilter, builtFilter };
  }, [features, onSearch, limit, bareFilter, builtFilter]);

  const selectedReference = references.find(
    (reference) => reference.reference_id === referenceId,
  );
  const referenceMode = body !== "custom" && selectedReference !== undefined;
  const isLolaReference =
    selectedReference !== undefined &&
    (selectedReference.dataset_id.toLowerCase().includes("lola") ||
      selectedReference.notes?.toLowerCase().includes("lola") === true);
  const referenceTarget = useMemo(
    () => referenceValues(selectedReference),
    [selectedReference],
  );

  const availableBodies = useMemo(
    () => new Set(references.map((reference) => reference.body)),
    [references],
  );
  const bareContext = contextFeatures.find(
    (feature) => feature.feature_id === "landcover_bare_sparse_fraction",
  );
  const builtContext = contextFeatures.find(
    (feature) => feature.feature_id === "landcover_built_up_fraction",
  );

  function updateRow(featureId: string, patch: Partial<RowState>) {
    setRows((current) => ({
      ...current,
      [featureId]: { ...current[featureId], ...patch },
    }));
  }

  useEffect(() => {
    if (!preset || lastAppliedPreset.current === preset.nonce) {
      return;
    }
    lastAppliedPreset.current = preset.nonce;

    const {
      features: currentFeatures,
      onSearch: submit,
      limit: currentLimit,
      bareFilter: currentBareFilter,
      builtFilter: currentBuiltFilter,
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
        nextRows[feature.id] = {
          included: true,
          value: String(value),
          weight: "1",
        };
        target[feature.id] = { value, weight: 1 };
        targetValues[feature.id] = value;
      }
    }

    setRows((current) => {
      const merged: Record<string, RowState> = {};
      for (const feature of currentFeatures) {
        merged[feature.id] = nextRows[feature.id] ?? {
          ...(current[feature.id] ?? {
            included: false,
            value: "",
            weight: "1",
          }),
          included: false,
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
      Number.isInteger(parsedLimit) && parsedLimit >= 1 && parsedLimit <= 10
        ? parsedLimit
        : 10;

    submit({
      request: {
        body: "custom",
        reference_id: null,
        target,
        filters: buildFilters(currentBareFilter, currentBuiltFilter),
        limit: sanitizedLimit,
      },
      target: targetValues,
      body: "custom",
      referenceId: null,
      limit: sanitizedLimit,
    });
  }, [preset]);

  function handleBodyChange(nextBody: SearchRequest["body"]) {
    setBody(nextBody);
    if (nextBody === "custom") {
      setReferenceId("");
      return;
    }
    const firstReference = references.find(
      (reference) => reference.body === nextBody,
    );
    setReferenceId(firstReference?.reference_id ?? "");
  }

  function handleReferenceChange(nextReferenceId: string) {
    setReferenceId(nextReferenceId);
    if (!nextReferenceId) {
      setBody("custom");
      return;
    }
    const reference = references.find(
      (candidate) => candidate.reference_id === nextReferenceId,
    );
    if (reference) {
      setBody(reference.body);
    }
  }

  function validateAndSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const nextErrors: UiMessage[] = [];
    const nextFieldErrors: FieldErrors = {};
    const target: Record<string, TargetSpec> = {};
    const contextTarget: NumericTarget = {};
    const parsedLimit = Number(limit);
    const filters: Record<string, RangeFilterIn> = {};

    if (!Number.isInteger(parsedLimit) || parsedLimit < 1 || parsedLimit > 10) {
      nextErrors.push({ key: "search.errLimit" });
    }

    const bare = parseFilter(bareFilter, "search.filterBareName");
    const built = parseFilter(builtFilter, "search.filterBuiltName");
    if (bare.error) {
      nextErrors.push(bare.error);
    } else if (bare.value !== null) {
      filters.landcover_bare_sparse_fraction = { min: bare.value };
    }
    if (built.error) {
      nextErrors.push(built.error);
    } else if (built.value !== null) {
      filters.landcover_built_up_fraction = { max: built.value };
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
        if (!row?.included) {
          continue;
        }

        const value = Number(row.value);
        const weight = Number(row.weight);

        if (row.value.trim() === "" || !Number.isFinite(value)) {
          nextFieldErrors[feature.id] = {
            ...nextFieldErrors[feature.id],
            value: { key: "search.errFiniteNumber" },
          };
        } else if (value < feature.min_value || value > feature.max_value) {
          nextFieldErrors[feature.id] = {
            ...nextFieldErrors[feature.id],
            value: {
              key: "search.errRange",
              vars: { min: feature.min_value, max: feature.max_value, unit: feature.unit },
            },
          };
        }

        if (
          row.weight.trim() === "" ||
          !Number.isFinite(weight) ||
          weight < 0
        ) {
          nextFieldErrors[feature.id] = {
            ...nextFieldErrors[feature.id],
            weight: { key: "search.errWeight" },
          };
        }

        if (
          !nextFieldErrors[feature.id]?.value &&
          !nextFieldErrors[feature.id]?.weight &&
          weight > 0
        ) {
          target[feature.id] = { value, weight };
          contextTarget[feature.id] = value;
        }
      }

      if (Object.keys(target).length === 0) {
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
      filters,
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

  const moonAvailable = availableBodies.has("Moon");
  const marsAvailable = availableBodies.has("Mars");
  const unavailableBodies = [
    moonAvailable ? null : "Moon",
    marsAvailable ? null : "Mars",
  ].filter((value): value is string => value !== null);
  const unavailableBodyList = fmt.list(unavailableBodies.map((value) => label.body(value)));
  const renderedErrors = errors.map(renderMessage);

  const dimensionGroups = useMemo(() => {
    const groups: { dimensionId: string; features: FeatureOut[] }[] = [];
    for (const feature of features) {
      const existing = groups.find((group) => group.dimensionId === feature.dimension_id);
      if (existing) {
        existing.features.push(feature);
      } else {
        groups.push({ dimensionId: feature.dimension_id, features: [feature] });
      }
    }
    return groups;
  }, [features]);

  /** Weight that would actually be sent for this metric (0 when it is not compared). */
  function effectiveWeight(featureId: string): number {
    if (referenceMode) {
      const hasObservation = selectedReference?.features.some(
        (observation) => observation.feature_id === featureId,
      );
      const weight = Number(referenceWeights[featureId] ?? "0");
      return hasObservation && Number.isFinite(weight) ? weight : 0;
    }
    const row = rows[featureId];
    const weight = Number(row?.weight ?? "0");
    return row?.included && Number.isFinite(weight) ? weight : 0;
  }

  function isWeighted(featureId: string): boolean {
    return effectiveWeight(featureId) > 0;
  }

  function setWeight(featureId: string, value: string) {
    if (referenceMode) {
      setReferenceWeights((current) => ({ ...current, [featureId]: value }));
    } else {
      updateRow(featureId, { weight: value });
    }
  }

  function renderMetricRow(feature: FeatureOut) {
    const row = rows[feature.id];
    if (!row) {
      return null;
    }
    const referenceFeature = selectedReference?.features.find(
      (observation) => observation.feature_id === feature.id,
    );
    const valueDisabled = referenceMode;
    const weightDisabled = referenceMode && !referenceFeature;
    const displayValue = referenceMode
      ? referenceFeature
        ? String(referenceFeature.value)
        : ""
      : row.value;
    const displayWeight = referenceMode
      ? referenceFeature
        ? (referenceWeights[feature.id] ?? "0")
        : ""
      : row.weight;
    const sliderValue = Math.min(
      WEIGHT_SLIDER_MAX,
      Math.max(0, Number.isFinite(Number(displayWeight)) ? Number(displayWeight) : 0),
    );
    const weighted = isWeighted(feature.id);
    const valueError = fieldErrors[feature.id]?.value;
    const weightError = fieldErrors[feature.id]?.weight;
    const featureLabel = label.feature(feature.id, feature.label);
    const unitName = label.unitName(feature.unit);

    return (
      <div
        className={`metric-form-row${weighted ? " is-weighted" : ""}`}
        key={feature.id}
        role="group"
        aria-labelledby={`metric-${feature.id}-label`}
      >
        <div className="metric-form-header">
          <h4 id={`metric-${feature.id}-label`}>{featureLabel}</h4>
          <span className="unit-tag">{t("search.unitTag", { unit: unitName })}</span>
        </div>
        <div className="metric-inputs">
          {!referenceMode && (
            <div className="check-field">
              <input
                id={`metric-${feature.id}-included`}
                type="checkbox"
                checked={row.included}
                onChange={(event) => updateRow(feature.id, { included: event.target.checked })}
              />
              <label htmlFor={`metric-${feature.id}-included`}>
                {t("search.include", { label: featureLabel })}
              </label>
            </div>
          )}
          <div className="number-field">
            <label htmlFor={`metric-${feature.id}-value`}>
              {t("search.targetValue", { unit: unitName })}
            </label>
            <input
              id={`metric-${feature.id}-value`}
              type="number"
              inputMode="decimal"
              step="any"
              min={feature.min_value}
              max={feature.max_value}
              value={displayValue}
              disabled={valueDisabled}
              aria-invalid={valueError ? true : undefined}
              aria-describedby={valueError ? `metric-${feature.id}-value-error` : undefined}
              onChange={(event) => updateRow(feature.id, { value: event.target.value })}
            />
            {valueError && (
              <p className="field-error" id={`metric-${feature.id}-value-error`}>
                {renderMessage(valueError)}
              </p>
            )}
          </div>
          <div className="number-field weight-field">
            <label htmlFor={`metric-${feature.id}-weight`}>{t("search.weight")}</label>
            <div className="weight-control">
              <input
                type="range"
                className="weight-slider"
                min="0"
                max={WEIGHT_SLIDER_MAX}
                step="0.5"
                value={sliderValue}
                disabled={weightDisabled}
                aria-label={t("search.weightSlider", { label: featureLabel })}
                onChange={(event) => setWeight(feature.id, event.target.value)}
              />
              <input
                id={`metric-${feature.id}-weight`}
                className="weight-number"
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
          </div>
        </div>
        <details className="metric-more">
          <summary>{t("search.metricMore")}</summary>
          <code>{feature.id}</code>
          <p className="metric-description">
            <SourceText>{feature.description}</SourceText>
          </p>
          <p className="metric-domain">
            {rich("search.validDomain", {
              domain: <SourceText>{feature.valid_domain}</SourceText>,
            })}
          </p>
        </details>
      </div>
    );
  }

  return (
    <form className="search-form" onSubmit={validateAndSubmit} noValidate>
      <div className="section-heading">
        <p className="eyebrow">{t("search.step")}</p>
        <h2>{t("search.heading")}</h2>
        <p className="muted">{t("search.intro")}</p>
      </div>

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

      <fieldset className="preset-fieldset">
        <legend>{t("search.presetLegend")}</legend>
        <div className="field">
          <label htmlFor="body-select">{t("search.bodyPreset")}</label>
          <select
            id="body-select"
            value={body}
            onChange={(event) =>
              handleBodyChange(event.target.value as SearchRequest["body"])
            }
          >
            <option value="custom">{t("search.bodyCustom")}</option>
            <option value="Moon" disabled={!moonAvailable}>
              {t("search.bodyReference", { body: label.body("Moon") })}
              {moonAvailable ? "" : t("search.bodyUnavailableSuffix")}
            </option>
            <option value="Mars" disabled={!marsAvailable}>
              {t("search.bodyReference", { body: label.body("Mars") })}
              {marsAvailable ? "" : t("search.bodyUnavailableSuffix")}
            </option>
          </select>
        </div>

        {references.length > 0 && (
          <div className="field">
            <label htmlFor="reference-select">{t("search.referenceRecord")}</label>
            <select
              id="reference-select"
              value={referenceId}
              onChange={(event) => handleReferenceChange(event.target.value)}
            >
              <option value="">{t("search.noReference")}</option>
              {references.map((reference) => (
                <option key={reference.reference_id} value={reference.reference_id}>
                  {label.body(reference.body)} · {reference.display_name}
                </option>
              ))}
            </select>
            <p className="field-hint">{t("search.referenceHint")}</p>
          </div>
        )}

        {unavailableBodies.length > 0 && (
          <p className="field-hint" id="preset-unavailable-note">
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

        {referenceMode && selectedReference && (
          <p className="field-hint">
            {rich("search.referenceTargetHint", {
              name: <strong>{selectedReference.display_name}</strong>,
            })}
          </p>
        )}
      </fieldset>

      {selectedReference && (
        <section className="reference-details" aria-labelledby="reference-details-heading">
          <h3 id="reference-details-heading">{t("search.referenceDetails")}</h3>
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
            <details className="reference-notes">
              <summary>{t("search.referenceNotes")}</summary>
              <SourceTextNote />
              <p>
                <SourceText>{selectedReference.notes}</SourceText>
              </p>
            </details>
          )}
        </section>
      )}

      <div className="dimension-board">
      <fieldset className="target-fieldset">
        <legend>
          {referenceMode ? t("search.referenceMetricsLegend") : t("search.customMetricsLegend")}
        </legend>
        {referenceMode ? (
          <p className="field-hint">{t("search.referenceWeightHint")}</p>
        ) : (
          <p className="example-note" id="example-note">
            <strong>{t("search.exampleTitle")}</strong> {t("search.exampleBody")}
          </p>
        )}

        <div className="dimension-columns">
          {dimensionGroups.map((group) => {
            const activeCount = group.features.filter((feature) =>
              isWeighted(feature.id),
            ).length;
            return (
              <section
                className="dimension-column"
                key={group.dimensionId}
                aria-label={label.dimension(group.dimensionId)}
              >
                <div className="dimension-column-head">
                  <h3>{label.dimension(group.dimensionId)}</h3>
                  <span className={activeCount > 0 ? "dimension-count is-active" : "dimension-count"}>
                    {activeCount > 0
                      ? t("search.dimensionCount", {
                          active: activeCount,
                          total: group.features.length,
                        })
                      : t("search.dimensionNone")}
                  </span>
                </div>
                <div className="metric-form-list">
                  {group.features.map((feature) => renderMetricRow(feature))}
                </div>
              </section>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="filter-fieldset">
        <legend>{t("search.filtersLegend")}</legend>
        <p className="field-hint">{t("search.filtersHint")}</p>
        <div className="filter-row">
          <div className="check-field">
            <input
              id="bare-filter-enabled"
              type="checkbox"
              checked={bareFilter.enabled}
              onChange={(event) =>
                setBareFilter((current) => ({
                  ...current,
                  enabled: event.target.checked,
                }))
              }
            />
            <label htmlFor="bare-filter-enabled">
              {t("search.bareFilterLabel", {
                label: bareContext
                  ? label.feature(bareContext.feature_id, bareContext.label)
                  : t("search.bareFallback"),
              })}
            </label>
          </div>
          <div className="number-field">
            <label htmlFor="bare-filter-value">{t("search.minFraction")}</label>
            <input
              id="bare-filter-value"
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              max="1"
              value={bareFilter.value}
              disabled={!bareFilter.enabled}
              onChange={(event) =>
                setBareFilter((current) => ({
                  ...current,
                  value: event.target.value,
                }))
              }
            />
          </div>
        </div>
        <div className="filter-row">
          <div className="check-field">
            <input
              id="built-filter-enabled"
              type="checkbox"
              checked={builtFilter.enabled}
              onChange={(event) =>
                setBuiltFilter((current) => ({
                  ...current,
                  enabled: event.target.checked,
                }))
              }
            />
            <label htmlFor="built-filter-enabled">
              {t("search.builtFilterLabel", {
                label: builtContext
                  ? label.feature(builtContext.feature_id, builtContext.label)
                  : t("search.builtFallback"),
              })}
            </label>
          </div>
          <div className="number-field">
            <label htmlFor="built-filter-value">{t("search.maxFraction")}</label>
            <input
              id="built-filter-value"
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              max="1"
              value={builtFilter.value}
              disabled={!builtFilter.enabled}
              onChange={(event) =>
                setBuiltFilter((current) => ({
                  ...current,
                  value: event.target.value,
                }))
              }
            />
          </div>
        </div>
      </fieldset>
      </div>

      <div className="field limit-field">
        <label htmlFor="limit-input">{t("search.limitLabel")}</label>
        <input
          id="limit-input"
          type="number"
          inputMode="numeric"
          min="1"
          max="10"
          step="1"
          value={limit}
          onChange={(event) => setLimit(event.target.value)}
        />
        <p className="field-hint">{t("search.limitHint")}</p>
      </div>

      <button className="primary-button" type="submit" disabled={loading}>
        {loading ? t("search.submitting") : t("search.submit")}
      </button>
      <p className="field-hint">{t("search.submitHint")}</p>
    </form>
  );
}
