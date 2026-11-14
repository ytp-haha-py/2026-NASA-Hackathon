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
import { formatNumber } from "../lib/format";

type AoiFormProps = {
  capabilities: CapabilitiesOut;
  onSubmit: (request: AoiAnalyzeRequest) => void;
  busy: boolean;
};

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

export function AoiForm({ capabilities, onSubmit, busy }: AoiFormProps) {
  const limits = useMemo(
    () => readAoiLimits(capabilities.aoi_analysis),
    [capabilities.aoi_analysis],
  );
  const [bbox, setBbox] = useState<BboxForm>(EXAMPLE_BBOX);
  const [errors, setErrors] = useState<string[]>([]);

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
    setBbox((current) => ({ ...current, [field]: value }));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const nextErrors: string[] = [];
    const values = {
      west: Number(bbox.west),
      south: Number(bbox.south),
      east: Number(bbox.east),
      north: Number(bbox.north),
    };

    if (Object.values(values).some((value) => !Number.isFinite(value))) {
      nextErrors.push("All four bbox coordinates must be finite decimal degrees.");
    } else {
      if (values.west < -180 || values.east > 180 || values.west >= values.east) {
        nextErrors.push(
          "Longitude must satisfy -180 ≤ west < east ≤ 180. Antimeridian-crossing AOIs are not supported.",
        );
      }
      if (values.south >= values.north) {
        nextErrors.push("Latitude must satisfy south < north.");
      }
      if (values.south < AOI_LATITUDE_MIN || values.north > AOI_LATITUDE_MAX) {
        nextErrors.push(
          `Latitude must stay within ${AOI_LATITUDE_MIN}° to ${AOI_LATITUDE_MAX}° for the Earth provider coverage.`,
        );
      }

      if (nextErrors.length === 0) {
        const { widthM, heightM } = bboxSideLengthsM(values);
        const areaKm2 = bboxAreaKm2(values);
        const shorterSide = Math.min(widthM, heightM);
        const longerSide = Math.max(widthM, heightM);

        if (shorterSide < limits.minSideM) {
          nextErrors.push(
            `AOI too small: each side must be at least ${formatNumber(limits.minSideM, 0)} m; got ${formatNumber(shorterSide, 0)} m.`,
          );
        }
        if (longerSide > limits.maxSideM) {
          nextErrors.push(
            `AOI too large: each side must be at most ${formatNumber(limits.maxSideM, 0)} m; got ${formatNumber(longerSide, 0)} m.`,
          );
        }
        if (areaKm2 > limits.maxAreaKm2) {
          nextErrors.push(
            `AOI too large: area must be at most ${formatNumber(limits.maxAreaKm2, 1)} km²; got ${formatNumber(areaKm2, 2)} km².`,
          );
        }
      }
    }

    if (limits.requestedFeatures.length === 0) {
      nextErrors.push("This API did not advertise any similarity features for AOI analysis.");
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

  return (
    <form className="search-form aoi-form" onSubmit={handleSubmit} noValidate>
      <div className="section-heading">
        <p className="eyebrow">On-demand analysis</p>
        <h2>Analyze a new Earth AOI</h2>
        <p className="muted">
          Request a bounded Copernicus DEM terrain window plus ESA WorldCover land-cover
          context. Land cover is context only and never enters the similarity distance.
        </p>
      </div>

      {errors.length > 0 && (
        <div className="alert alert-error" role="alert">
          <p>
            <strong>Check the AOI before submitting.</strong>
          </p>
          <ul>
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </div>
      )}

      <fieldset>
        <legend>Bounding box (WGS84 decimal degrees)</legend>
        <p className="example-note">
          <strong>Example bbox for interface testing, not a site recommendation.</strong>{" "}
          Replace west, south, east, and north with your own area. This MVP supports bbox
          requests; GeoJSON Polygon is accepted by the API but not drawn by this UI.
        </p>
        <div className="bbox-grid">
          <div className="number-field">
            <label htmlFor="aoi-west">West longitude (°)</label>
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
            <label htmlFor="aoi-south">South latitude (°)</label>
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
            <label htmlFor="aoi-east">East longitude (°)</label>
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
            <label htmlFor="aoi-north">North latitude (°)</label>
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
            ? `Approx. ${formatNumber(parsed.widthM, 0)} m × ${formatNumber(parsed.heightM, 0)} m · ${formatNumber(parsed.areaKm2, 2)} km² (server recomputes on WGS84).`
            : "Enter finite coordinates to preview the approximate AOI size."}
        </p>

        <ul className="aoi-limits">
          <li>
            Side length: {formatNumber(limits.minSideM, 0)}–{formatNumber(limits.maxSideM, 0)} m
          </li>
          <li>Area: at most {formatNumber(limits.maxAreaKm2, 1)} km²</li>
          <li>
            Latitude: {AOI_LATITUDE_MIN}° to {AOI_LATITUDE_MAX}° (provider coverage)
          </li>
          <li>Analysis resolution: {formatNumber(limits.analysisResolutionM, 0)} m</li>
          <li>Body: {limits.supportedBody}</li>
        </ul>
      </fieldset>

      <div className="provider-readiness">
        <h3>Provider readiness</h3>
        {providerStatuses.length === 0 ? (
          <p className="field-hint">No provider status was advertised by capabilities.</p>
        ) : (
          <ul>
            {providerStatuses.map(([id, status]) => (
              <li key={id}>
                <code>{id}</code>: <strong>{status.status ?? "unknown"}</strong>
                {status.role ? ` — ${status.role}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>

      <p className="field-hint">{limits.note}</p>

      <button className="primary-button" type="submit" disabled={busy}>
        {busy ? "Analyzing Earth AOI…" : "Analyze this Earth AOI"}
      </button>
      <p className="field-hint">
        Cached AOIs return immediately; otherwise the API runs a durable job. Results are
        user-requested on-demand windows, not part of a global survey.
      </p>
    </form>
  );
}
