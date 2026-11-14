import { useEffect, useState } from "react";
import {
  errorCode,
  errorMessage,
  getLocation,
  isAbortError,
  type FeatureOut,
  type LocationOut,
  type NumericTarget,
  type SearchResultOut,
} from "../api/client";
import { formatNumber } from "../lib/format";
import { DataKindBadge } from "./DataKindBadge";
import { LocationDetails } from "./LocationDetails";

type DetailPanelProps = {
  selected: SearchResultOut;
  activeTarget: NumericTarget;
  features: FeatureOut[];
};

export function DetailPanel({ selected, activeTarget, features }: DetailPanelProps) {
  const [location, setLocation] = useState<LocationOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ message: string; code: string | null } | null>(
    null,
  );
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setLocation(null);

    getLocation(selected.location_id, controller.signal)
      .then((payload) => setLocation(payload))
      .catch((caught: unknown) => {
        if (isAbortError(caught)) {
          return;
        }
        setError({ message: errorMessage(caught), code: errorCode(caught) });
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [selected.location_id, retryKey]);

  return (
    <section className="detail-panel" aria-labelledby="detail-heading">
      <div className="section-heading">
        <p className="eyebrow">Step 3</p>
        <h2 id="detail-heading">Selected Earth candidate</h2>
        <p className="selected-name">
          <strong>{selected.display_name}</strong>{" "}
          <DataKindBadge kind={selected.data_kind} />
        </p>
        <p className="muted">
          Rank #{selected.rank} · distance {formatNumber(selected.distance, 4)} (lower is
          more similar; not a percentage)
        </p>
      </div>

      {loading && <p role="status">Loading location record…</p>}

      {error && (
        <div className="alert alert-error" role="alert">
          <p>
            <strong>Location record failed{error.code ? ` (${error.code})` : ""}.</strong>{" "}
            {error.message}
          </p>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setRetryKey((key) => key + 1)}
          >
            Retry location record
          </button>
        </div>
      )}

      {location && (
        <LocationDetails
          location={location}
          comparisonMetrics={selected.metrics}
          activeTarget={activeTarget}
          features={features}
          sourceLabel={`GET /locations/${location.location_id}`}
          idPrefix="search-detail"
        />
      )}
    </section>
  );
}
