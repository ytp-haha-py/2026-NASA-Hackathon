import { useMemo, useState, type FormEvent } from "react";
import type { AoiAnalyzeRequest, CapabilitiesOut } from "../api/client";
import {
  AOI_LATITUDE_MAX,
  AOI_LATITUDE_MIN,
  bboxAreaKm2,
  bboxSideLengthsM,
  providerStatusEntries,
  readAoiLimits,
} from "../lib/aoi";
import { SourceText, useI18n } from "../i18n/I18nProvider";
import type { MessageVars } from "../i18n/translate";
import type { MessageKey } from "../locales/en/messages";

/** Stored as key + numeric variables so a language switch re-renders the message. */
type UiMessage = { key: MessageKey; vars?: MessageVars };

type AoiFormProps = {
  capabilities: CapabilitiesOut;
  onSubmit: (request: AoiAnalyzeRequest) => void;
  busy: boolean;
  /** Seeds the fields, e.g. an area drawn on the map. Remount (key) to apply a new one. */
  initialBbox?: AoiBbox | null;
  /** Reports valid hand-typed edits so the map can outline them. */
  onBboxChange?: (bbox: AoiBbox | null) => void;
};

export type AoiBbox = { west: number; south: number; east: number; north: number };

type BboxForm = {
  west: string;
  south: string;
  east: string;
  north: string;
};

const EXAMPLE_BBOX: BboxForm = {
  west: "-13.645",
  south: "29.035",
  east: "-13.635",
  north: "29.045",
};

function bboxToForm(bbox: AoiBbox): BboxForm {
  const text = (value: number) => String(Number(value.toFixed(5)));
  return {
    west: text(bbox.west),
    south: text(bbox.south),
    east: text(bbox.east),
    north: text(bbox.north),
  };
}

export function AoiForm({
  capabilities,
  onSubmit,
  busy,
  initialBbox = null,
  onBboxChange,
}: AoiFormProps) {
  const { t, fmt, label } = useI18n();
  const limits = useMemo(
    () => readAoiLimits(capabilities.aoi_analysis),
    [capabilities.aoi_analysis],
  );
  const [bbox, setBbox] = useState<BboxForm>(() =>
    initialBbox ? bboxToForm(initialBbox) : EXAMPLE_BBOX,
  );
  const [errors, setErrors] = useState<UiMessage[]>([]);

  const parsed = useMemo(() => {
    const values = {
      west: Number(bbox.west),
      south: Number(bbox.south),
      east: Number(bbox.east),
      north: Number(bbox.north),
    };
    if (Object.values(values).some((value) => !Number.isFinite(value))) {
      return null;
    }
    return {
      ...values,
      ...bboxSideLengthsM(values),
      areaKm2: bboxAreaKm2(values),
    };
  }, [bbox]);

  function update(field: keyof BboxForm, value: string) {
    const next = { ...bbox, [field]: value };
    setBbox(next);
    const numbers = {
      west: Number(next.west),
      south: Number(next.south),
      east: Number(next.east),
      north: Number(next.north),
    };
    const valid =
      Object.values(numbers).every((n) => Number.isFinite(n)) &&
      numbers.west < numbers.east &&
      numbers.south < numbers.north;
    onBboxChange?.(valid ? numbers : null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const nextErrors: UiMessage[] = [];
    const values = {
      west: Number(bbox.west),
      south: Number(bbox.south),
      east: Number(bbox.east),
      north: Number(bbox.north),
    };

    if (Object.values(values).some((value) => !Number.isFinite(value))) {
      nextErrors.push({ key: "aoiForm.errFinite" });
    } else {
      if (values.west < -180 || values.east > 180 || values.west >= values.east) {
        nextErrors.push({ key: "aoiForm.errLongitude" });
      }
      if (values.south >= values.north) {
        nextErrors.push({ key: "aoiForm.errLatitudeOrder" });
      }
      if (values.south < AOI_LATITUDE_MIN || values.north > AOI_LATITUDE_MAX) {
        nextErrors.push({
          key: "aoiForm.errLatitudeRange",
          vars: { min: AOI_LATITUDE_MIN, max: AOI_LATITUDE_MAX },
        });
      }

      if (nextErrors.length === 0) {
        const { widthM, heightM } = bboxSideLengthsM(values);
        const areaKm2 = bboxAreaKm2(values);
        const shorterSide = Math.min(widthM, heightM);
        const longerSide = Math.max(widthM, heightM);

        if (shorterSide < limits.minSideM) {
          nextErrors.push({
            key: "aoiForm.errTooSmall",
            vars: { min: limits.minSideM, actual: shorterSide },
          });
        }
        if (longerSide > limits.maxSideM) {
          nextErrors.push({
            key: "aoiForm.errTooLong",
            vars: { max: limits.maxSideM, actual: longerSide },
          });
        }
        if (areaKm2 > limits.maxAreaKm2) {
          nextErrors.push({
            key: "aoiForm.errTooLargeArea",
            vars: { max: limits.maxAreaKm2, actual: areaKm2 },
          });
        }
      }
    }

    if (limits.requestedFeatures.length === 0) {
      nextErrors.push({ key: "aoiForm.errNoFeatures" });
    }

    setErrors(nextErrors);
    if (nextErrors.length > 0) {
      return;
    }

    onSubmit({
      body: "Earth",
      bbox: values,
      geometry: null,
      requested_features: limits.requestedFeatures,
      reference_id: null,
      idempotency_key: null,
    });
  }

  const providerStatuses = providerStatusEntries(limits.providerStatus);

  /** Formats stored numeric variables with the precision each message expects. */
  function renderError(message: UiMessage): string {
    const vars: MessageVars = { ...message.vars };
    const digits =
      message.key === "aoiForm.errTooLargeArea"
        ? { max: 1, actual: 2 }
        : message.key === "aoiForm.errLatitudeRange"
          ? null
          : { min: 0, max: 0, actual: 0 };
    if (digits) {
      for (const [name, fractionDigits] of Object.entries(digits)) {
        const value = vars[name];
        if (typeof value === "number") {
          vars[name] = fmt.number(value, fractionDigits);
        }
      }
    }
    return t(message.key, vars);
  }

  const renderedErrors = errors.map(renderError);

  return (
    <form className="search-form aoi-form" onSubmit={handleSubmit} noValidate>
      <div className="section-heading">
        <p className="eyebrow">{t("aoiForm.eyebrow")}</p>
        <h2>{t("aoiForm.heading")}</h2>
        <p className="muted">{t("aoiForm.intro")}</p>
      </div>

      {renderedErrors.length > 0 && (
        <div className="alert alert-error" role="alert">
          <p>
            <strong>{t("aoiForm.errorsTitle")}</strong>
          </p>
          <ul>
            {renderedErrors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </div>
      )}

      <fieldset>
        <legend>{t("aoiForm.bboxLegend")}</legend>
        <p className="example-note">
          <strong>{t("aoiForm.exampleTitle")}</strong> {t("aoiForm.exampleBody")}
        </p>
        <div className="bbox-grid">
          <div className="number-field">
            <label htmlFor="aoi-west">{t("aoiForm.west")}</label>
            <input
              id="aoi-west"
              type="number"
              inputMode="decimal"
              step="any"
              value={bbox.west}
              onChange={(event) => update("west", event.target.value)}
            />
          </div>
          <div className="number-field">
            <label htmlFor="aoi-south">{t("aoiForm.south")}</label>
            <input
              id="aoi-south"
              type="number"
              inputMode="decimal"
              step="any"
              value={bbox.south}
              onChange={(event) => update("south", event.target.value)}
            />
          </div>
          <div className="number-field">
            <label htmlFor="aoi-east">{t("aoiForm.east")}</label>
            <input
              id="aoi-east"
              type="number"
              inputMode="decimal"
              step="any"
              value={bbox.east}
              onChange={(event) => update("east", event.target.value)}
            />
          </div>
          <div className="number-field">
            <label htmlFor="aoi-north">{t("aoiForm.north")}</label>
            <input
              id="aoi-north"
              type="number"
              inputMode="decimal"
              step="any"
              value={bbox.north}
              onChange={(event) => update("north", event.target.value)}
            />
          </div>
        </div>

        <p className="aoi-measurement" aria-live="polite">
          {parsed
            ? t("aoiForm.measurement", {
                width: fmt.number(parsed.widthM, 0),
                height: fmt.number(parsed.heightM, 0),
                area: fmt.number(parsed.areaKm2, 2),
              })
            : t("aoiForm.measurementMissing")}
        </p>

        <ul className="aoi-limits">
          <li>
            {t("aoiForm.limitSide", {
              min: fmt.number(limits.minSideM, 0),
              max: fmt.number(limits.maxSideM, 0),
            })}
          </li>
          <li>{t("aoiForm.limitArea", { area: fmt.number(limits.maxAreaKm2, 1) })}</li>
          <li>
            {t("aoiForm.limitLatitude", { min: AOI_LATITUDE_MIN, max: AOI_LATITUDE_MAX })}
          </li>
          <li>
            {t("aoiForm.limitResolution", {
              resolution: fmt.number(limits.analysisResolutionM, 0),
            })}
          </li>
          <li>{t("aoiForm.limitBody", { body: label.body(limits.supportedBody) })}</li>
        </ul>
      </fieldset>

      <div className="provider-readiness">
        <h3>{t("aoiForm.providerReadiness")}</h3>
        {providerStatuses.length === 0 ? (
          <p className="field-hint">{t("aoiForm.noProviders")}</p>
        ) : (
          <ul>
            {providerStatuses.map(([id, status]) => (
              <li key={id}>
                <code>{id}</code>:{" "}
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
        )}
      </div>

      <p className="field-hint">
        <SourceText>{limits.note}</SourceText>
      </p>

      <button className="primary-button" type="submit" disabled={busy}>
        {busy ? t("aoiForm.submitting") : t("aoiForm.submit")}
      </button>
      <p className="field-hint">{t("aoiForm.submitHint")}</p>
    </form>
  );
}
