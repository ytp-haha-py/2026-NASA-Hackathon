const numberFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});

function trimTrailingZeros(value: string): string {
  if (!value.includes(".")) {
    return value;
  }
  return value.replace(/\.?0+$/, "");
}

export function formatNumber(value: number, maxFractionDigits = 2): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  if (maxFractionDigits === 2) {
    return numberFormatter.format(value);
  }
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: maxFractionDigits,
  }).format(value);
}

export function formatDistance(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return trimTrailingZeros(value.toFixed(4));
}

export function formatSignedNumber(value: number, maxFractionDigits = 2): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  const rendered = formatNumber(Math.abs(value), maxFractionDigits);
  if (value > 0) {
    return `+${rendered}`;
  }
  if (value < 0) {
    return `−${rendered}`;
  }
  return "0";
}

export function formatUnit(unit: string): string {
  if (unit === "degree") {
    return "deg";
  }
  if (unit === "meter") {
    return "m";
  }
  return unit;
}

export function formatPercent(fraction: number): string {
  if (!Number.isFinite(fraction)) {
    return "—";
  }
  return `${(fraction * 100).toFixed(1)}%`;
}

export function formatCoordinate(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return `${formatNumber(value, 4)}°`;
}

export function formatBBox(bbox: {
  west: number;
  south: number;
  east: number;
  north: number;
}): string {
  return `lat ${formatNumber(bbox.south, 4)}° to ${formatNumber(bbox.north, 4)}°, lon ${formatNumber(bbox.west, 4)}° to ${formatNumber(bbox.east, 4)}°`;
}

export function humanizeFeatureId(featureId: string): string {
  return featureId.replaceAll("_", " ");
}

export function formatExclusionReason(
  reason: string,
  labels: Map<string, string>,
): string {
  if (reason.startsWith("missing_metric:")) {
    const ids = reason.slice("missing_metric:".length).split(",");
    const names = ids.map((id) => labels.get(id) ?? humanizeFeatureId(id));
    return `Missing required metric: ${names.join(", ")}`;
  }
  if (reason.startsWith("missing_context:")) {
    const ids = reason.slice("missing_context:".length).split(",");
    const names = ids.map((id) => labels.get(id) ?? humanizeFeatureId(id));
    return `Missing land-cover context: ${names.join(", ")}`;
  }
  if (reason.startsWith("filter:")) {
    const ids = reason
      .split(",")
      .map((part) => part.replace(/^filter:/, ""));
    const names = ids.map((id) => labels.get(id) ?? humanizeFeatureId(id));
    return `Excluded by land-cover filter: ${names.join(", ")}`;
  }
  if (reason === "incompatible_method_version") {
    return "Incompatible method version";
  }
  return reason.replaceAll("_", " ");
}
