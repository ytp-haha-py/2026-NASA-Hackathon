type DataKindBadgeProps = {
  kind: string;
};

export function DataKindBadge({ kind }: DataKindBadgeProps) {
  const normalized = kind.toLowerCase();
  if (normalized === "synthetic") {
    return (
      <span
        className="badge badge-synthetic"
        aria-label="DEMO / SYNTHETIC data: not a real terrain observation"
      >
        DEMO / SYNTHETIC
      </span>
    );
  }
  if (normalized === "observed") {
    return (
      <span className="badge badge-observed" aria-label="Observed data">
        Observed
      </span>
    );
  }
  return <span className="badge badge-unknown">{kind}</span>;
}
