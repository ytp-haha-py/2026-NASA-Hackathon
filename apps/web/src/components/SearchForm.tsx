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
import { formatNumber, formatPercent, formatUnit } from "../lib/format";
import type { SearchSubmission, SearchTargetPreset } from "../lib/search-types";

type RowState = {
  included: boolean;
  value: string;
  weight: string;
};

type FieldErrors = Record<string, { value?: string; weight?: string }>;

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

function parseFilter(
  state: FilterState,
  label: string,
): { value: number | null; error: string | null } {
  if (!state.enabled) {
    return { value: null, error: null };
  }
  if (state.value.trim() === "") {
    return { value: null, error: `${label} requires a value in the closed interval [0, 1].` };
  }
  const value = Number(state.value);
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    return { value: null, error: `${label} must be a finite fraction from 0 to 1.` };
  }
  return { value, error: null };
}

function buildFilters(
  bareFilter: FilterState,
  builtFilter: FilterState,
): Record<string, RangeFilterIn> {
  const filters: Record<string, RangeFilterIn> = {};
  const bare = parseFilter(bareFilter, "Bare/sparse minimum");
  if (bare.value !== null) {
    filters.landcover_bare_sparse_fraction = { min: bare.value };
  }
  const built = parseFilter(builtFilter, "Built-up maximum");
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
  const [rows, setRows] = useState<Record<string, RowState>>(() => initialRows(features));
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
  const [errors, setErrors] = useState<string[]>([]);
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
      setErrors([
        "The completed AOI did not return any similarity feature values that can be used as a target.",
      ]);
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

    const nextErrors: string[] = [];
    const nextFieldErrors: FieldErrors = {};
    const target: Record<string, TargetSpec> = {};
    const contextTarget: NumericTarget = {};
    const parsedLimit = Number(limit);
    const filters: Record<string, RangeFilterIn> = {};

    if (!Number.isInteger(parsedLimit) || parsedLimit < 1 || parsedLimit > 10) {
      nextErrors.push("Maximum results must be a whole number between 1 and 10.");
    }

    const bare = parseFilter(bareFilter, "Bare/sparse minimum");
    const built = parseFilter(builtFilter, "Built-up maximum");
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

    if (body !== "custom") {
      if (!selectedReference) {
        nextErrors.push(`Select a ${body} reference record before searching.`);
      } else if (selectedReference.features.length === 0) {
        nextErrors.push(
          `The selected ${body} reference contains no compatible feature observations.`,
        );
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
            value: "Enter a finite number.",
          };
        } else if (value < feature.min_value || value > feature.max_value) {
          nextFieldErrors[feature.id] = {
            ...nextFieldErrors[feature.id],
            value: `Enter a value between ${formatNumber(feature.min_value)} and ${formatNumber(feature.max_value)} ${feature.unit}.`,
          };
        }

        if (
          row.weight.trim() === "" ||
          !Number.isFinite(weight) ||
          weight < 0
        ) {
          nextFieldErrors[feature.id] = {
            ...nextFieldErrors[feature.id],
            weight: "Weight must be a finite number greater than or equal to 0.",
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
        nextErrors.push(
          "Choose at least one metric with a positive weight. Metrics with weight 0 are omitted.",
        );
      }
    }

    const fieldMessages = Object.entries(nextFieldErrors).flatMap(
      ([featureId, messages]) => {
        const feature = features.find((candidate) => candidate.id === featureId);
        const label = feature?.label ?? featureId;
        return Object.entries(messages).map(
          ([field, message]) => `${label} ${field}: ${message}`,
        );
      },
    );

    const allErrors = [...nextErrors, ...fieldMessages];
    setFieldErrors(nextFieldErrors);
    setErrors(allErrors);

    if (allErrors.length > 0) {
      return;
    }

    const effectiveTarget = referenceMode ? referenceTarget : contextTarget;
    if (Object.keys(effectiveTarget).length === 0) {
      setErrors([
        "This reference preset has no usable target values. Choose another reference or a custom target.",
      ]);
      return;
    }

    const request: SearchRequest = {
      body,
      reference_id: body === "custom" ? null : referenceId,
      target: referenceMode ? {} : target,
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

  const moonAvailable = availableBodies.has("Moon");
  const marsAvailable = availableBodies.has("Mars");
  const unavailableBodies = [
    moonAvailable ? null : "Moon",
    marsAvailable ? null : "Mars",
  ].filter((value): value is string => value !== null);

  return (
    <form className="search-form" onSubmit={validateAndSubmit} noValidate>
      <div className="section-heading">
        <p className="eyebrow">Step 1</p>
        <h2>Describe a terrain-shape target</h2>
        <p className="muted">
          Every metric is computed from 90 m elevation cells inside a 1,980 m analysis
          window. Only these local terrain-shape dimensions are compared.
        </p>
      </div>

      {errors.length > 0 && (
        <div className="alert alert-error" role="alert" aria-labelledby="form-errors-title">
          <p id="form-errors-title">
            <strong>Check the target before searching.</strong>
          </p>
          <ul>
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </div>
      )}

      <fieldset className="preset-fieldset">
        <legend>Preset body</legend>
        <div className="field">
          <label htmlFor="body-select">Body preset</label>
          <select
            id="body-select"
            value={body}
            onChange={(event) =>
              handleBodyChange(event.target.value as SearchRequest["body"])
            }
          >
            <option value="custom">Custom target — search indexed Earth</option>
            <option value="Moon" disabled={!moonAvailable}>
              Moon reference{moonAvailable ? "" : " — unavailable"}
            </option>
            <option value="Mars" disabled={!marsAvailable}>
              Mars reference{marsAvailable ? "" : " — unavailable"}
            </option>
          </select>
        </div>

        {references.length > 0 && (
          <div className="field">
            <label htmlFor="reference-select">Reference record</label>
            <select
              id="reference-select"
              value={referenceId}
              onChange={(event) => handleReferenceChange(event.target.value)}
            >
              <option value="">No reference — use the custom target</option>
              {references.map((reference) => (
                <option key={reference.reference_id} value={reference.reference_id}>
                  {reference.body} · {reference.display_name}
                </option>
              ))}
            </select>
            <p className="field-hint">
              Selecting a reference switches to that body and uses the reference&apos;s
              returned observation values as the search target. Manual target inputs are
              not sent for reference searches.
            </p>
          </div>
        )}

        {unavailableBodies.length > 0 && (
          <p className="field-hint" id="preset-unavailable-note">
            {referencesError
              ? `Reference records could not be loaded (${referencesError}), so the ${unavailableBodies.join(" and ")} ${unavailableBodies.length === 1 ? "preset remains" : "presets remain"} unavailable. They are not simulated.`
              : `The ${unavailableBodies.join(" and ")} ${unavailableBodies.length === 1 ? "preset is" : "presets are"} unavailable because GET /references returned no reference records for ${unavailableBodies.length === 1 ? "that body" : "those bodies"}. They are not simulated.`}
          </p>
        )}

        {referenceMode && selectedReference && (
          <p className="field-hint">
            Reference target supplied by GET /references for{" "}
            <strong>{selectedReference.display_name}</strong>. Weights default to 1.0
            for every compatible reference feature.
          </p>
        )}
      </fieldset>

      {selectedReference && (
        <section className="reference-details" aria-labelledby="reference-details-heading">
          <h3 id="reference-details-heading">Selected reference details</h3>
          <dl className="reference-facts">
            <div>
              <dt>Body</dt>
              <dd>{selectedReference.body}</dd>
            </div>
            <div>
              <dt>Dataset</dt>
              <dd>
                <code>{selectedReference.dataset_id}</code>
              </dd>
            </div>
            <div>
              <dt>Window / resolution</dt>
              <dd>
                {formatNumber(selectedReference.window_size_m)} m at{" "}
                {formatNumber(selectedReference.analysis_resolution_m)} m
              </dd>
            </div>
            <div>
              <dt>Valid fraction</dt>
              <dd>{formatPercent(selectedReference.valid_fraction)}</dd>
            </div>
          </dl>

          <div className="table-scroll">
            <table className="reference-table">
              <caption>Reference target observations supplied by GET /references</caption>
              <thead>
                <tr>
                  <th scope="col">Feature</th>
                  <th scope="col">Value</th>
                  <th scope="col">Quality</th>
                  <th scope="col">Valid fraction</th>
                </tr>
              </thead>
              <tbody>
                {selectedReference.features.map((feature) => (
                  <tr key={feature.feature_id}>
                    <th scope="row">{feature.label}</th>
                    <td>
                      {formatNumber(feature.value)} {formatUnit(feature.unit)}
                    </td>
                    <td>{feature.quality}</td>
                    <td>{formatPercent(feature.valid_fraction)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selectedReference.quality_flags.length > 0 && (
            <ul className="reference-flags" aria-label="Reference quality flags">
              {selectedReference.quality_flags.map((flag) => (
                <li key={flag}>
                  <code>{flag}</code>
                </li>
              ))}
            </ul>
          )}

          {isLolaReference && (
            <p className="reference-caveat">
              <strong>LOLA reference caveat:</strong> the source is the NASA LRO-LOLA 5
              m/pixel Site04 DEM; about 90% of its 5 m pixels are interpolated, and these
              observations were resampled to the 90 m analysis grid. Do not treat 5 m
              texture as independent measurements. Moon coordinates use the source
              planetary frame, not Earth WGS84.
            </p>
          )}

          {selectedReference.notes && (
            <details className="reference-notes">
              <summary>Reference notes from the API</summary>
              <p>{selectedReference.notes}</p>
            </details>
          )}
        </section>
      )}

      <fieldset className="target-fieldset">
        <legend>
          {referenceMode ? "Reference target metrics (read-only)" : "Custom target metrics"}
        </legend>
        {referenceMode ? (
          <p className="field-hint">
            Values are read from the selected GET /references record. They are not
            manually entered target values and are not a claim of physical equivalence.
          </p>
        ) : (
          <p className="example-note" id="example-note">
            <strong>Hypothetical example, not a NASA measurement.</strong> The prefilled
            values are illustrative starting points; edit or replace them. Enabling a
            metric adds it to the target. Metrics with weight 0 are omitted from the
            request so they do not filter candidates.
          </p>
        )}

        <div className="metric-form-list">
          {features.map((feature) => {
            const row = rows[feature.id];
            if (!row) {
              return null;
            }
            const referenceFeature = selectedReference?.features.find(
              (observation) => observation.feature_id === feature.id,
            );
            const inputDisabled = referenceMode;
            const displayValue = referenceMode
              ? referenceFeature
                ? String(referenceFeature.value)
                : ""
              : row.value;
            const displayWeight = referenceMode
              ? referenceFeature
                ? "1"
                : ""
              : row.weight;
            const valueError = fieldErrors[feature.id]?.value;
            const weightError = fieldErrors[feature.id]?.weight;

            return (
              <div
                className="metric-form-row"
                key={feature.id}
                role="group"
                aria-labelledby={`metric-${feature.id}-label`}
              >
                <div className="metric-form-header">
                  <div>
                    <h3 id={`metric-${feature.id}-label`}>{feature.label}</h3>
                    <code>{feature.id}</code>
                  </div>
                  <span className="unit-tag">unit: {feature.unit}</span>
                </div>
                <p className="metric-description">{feature.description}</p>
                <p className="metric-domain">
                  Valid domain: {feature.valid_domain}
                </p>
                <div className="metric-inputs">
                  <div className="check-field">
                    <input
                      id={`metric-${feature.id}-included`}
                      type="checkbox"
                      checked={referenceMode ? Boolean(referenceFeature) : row.included}
                      disabled={inputDisabled}
                      onChange={(event) =>
                        updateRow(feature.id, { included: event.target.checked })
                      }
                    />
                    <label htmlFor={`metric-${feature.id}-included`}>
                      Include {feature.label}
                    </label>
                  </div>
                  <div className="number-field">
                    <label htmlFor={`metric-${feature.id}-value`}>
                      Target value ({feature.unit})
                    </label>
                    <input
                      id={`metric-${feature.id}-value`}
                      type="number"
                      inputMode="decimal"
                      step="any"
                      min={feature.min_value}
                      max={feature.max_value}
                      value={displayValue}
                      disabled={inputDisabled}
                      aria-invalid={valueError ? true : undefined}
                      aria-describedby={
                        valueError ? `metric-${feature.id}-value-error` : undefined
                      }
                      onChange={(event) =>
                        updateRow(feature.id, { value: event.target.value })
                      }
                    />
                    {valueError && (
                      <p
                        className="field-error"
                        id={`metric-${feature.id}-value-error`}
                      >
                        {valueError}
                      </p>
                    )}
                  </div>
                  <div className="number-field">
                    <label htmlFor={`metric-${feature.id}-weight`}>
                      Weight (≥ 0)
                    </label>
                    <input
                      id={`metric-${feature.id}-weight`}
                      type="number"
                      inputMode="decimal"
                      step="any"
                      min="0"
                      value={displayWeight}
                      disabled={inputDisabled}
                      aria-invalid={weightError ? true : undefined}
                      aria-describedby={
                        weightError ? `metric-${feature.id}-weight-error` : undefined
                      }
                      onChange={(event) =>
                        updateRow(feature.id, { weight: event.target.value })
                      }
                    />
                    {weightError && (
                      <p
                        className="field-error"
                        id={`metric-${feature.id}-weight-error`}
                      >
                        {weightError}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="filter-fieldset">
        <legend>Optional Earth land-cover filters (context only)</legend>
        <p className="field-hint">
          These are hard subset filters on ESA WorldCover context fractions. They do not
          enter the distance and are not mineralogy or geology. Filtered-out candidates are
          counted as exclusions by the API.
        </p>
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
              Only include candidates with {bareContext?.label ?? "bare/sparse fraction"} ≥
            </label>
          </div>
          <div className="number-field">
            <label htmlFor="bare-filter-value">Minimum fraction (0–1)</label>
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
              Only include candidates with {builtContext?.label ?? "built-up fraction"} ≤
            </label>
          </div>
          <div className="number-field">
            <label htmlFor="built-filter-value">Maximum fraction (0–1)</label>
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

      <div className="field limit-field">
        <label htmlFor="limit-input">Maximum results (1–10)</label>
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
        <p className="field-hint">
          The API returns min(limit, eligible indexed windows). Lower distance means
          more similar; it is not a percentage.
        </p>
      </div>

      <button className="primary-button" type="submit" disabled={loading}>
        {loading ? "Searching indexed Earth candidates…" : "Search indexed Earth candidates"}
      </button>
      <p className="field-hint">
        This searches only the committed indexed subset. It is not a global survey and
        not a base-site recommendation.
      </p>
    </form>
  );
}
