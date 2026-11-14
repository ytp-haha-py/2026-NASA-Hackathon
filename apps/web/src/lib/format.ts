/**
 * Locale-aware display formatting.
 *
 * Every function formats a value for display only. Request payloads, JSON exports and
 * machine-readable identifiers are never passed through these helpers.
 */

export const DEFAULT_FORMAT_LOCALE = "en-US";

const numberFormatters = new Map<string, Intl.NumberFormat>();

function numberFormatter(locale: string, maxFractionDigits: number): Intl.NumberFormat {
  const cacheKey = `${locale}|${maxFractionDigits}`;
  let formatter = numberFormatters.get(cacheKey);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, { maximumFractionDigits: maxFractionDigits });
    numberFormatters.set(cacheKey, formatter);
  }
  return formatter;
}

function trimTrailingZeros(value: string): string {
  if (!value.includes(".")) {
    return value;
  }
  return value.replace(/\.?0+$/, "");
}

export function formatNumber(
  value: number,
  maxFractionDigits = 2,
  locale: string = DEFAULT_FORMAT_LOCALE,
): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return numberFormatter(locale, maxFractionDigits).format(value);
}

/**
 * Distance is shown with up to four decimals and no grouping so that it can never be
 * mistaken for a percentage or a localized score.
 */
export function formatDistance(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return trimTrailingZeros(value.toFixed(4));
}

export function formatSignedNumber(
  value: number,
  maxFractionDigits = 2,
  locale: string = DEFAULT_FORMAT_LOCALE,
): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  const rendered = formatNumber(Math.abs(value), maxFractionDigits, locale);
  if (value > 0) {
    return `+${rendered}`;
  }
  if (value < 0) {
    return `−${rendered}`;
  }
  return "0";
}

export function formatPercent(
  fraction: number,
  locale: string = DEFAULT_FORMAT_LOCALE,
): string {
  if (!Number.isFinite(fraction)) {
    return "—";
  }
  return new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(fraction);
}

export function formatCoordinate(
  value: number,
  locale: string = DEFAULT_FORMAT_LOCALE,
): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return `${formatNumber(value, 4, locale)}°`;
}

/**
 * Formats an ISO timestamp in UTC for display. The raw ISO value must still be kept in
 * the `<time dateTime>` attribute; unparseable values are returned unchanged.
 */
export function formatDateTime(
  iso: string | null | undefined,
  locale: string = DEFAULT_FORMAT_LOCALE,
): string | null {
  if (!iso) {
    return null;
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  const rendered = new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "UTC",
  }).format(parsed);
  return `${rendered} UTC`;
}

export function formatList(items: string[], locale: string = DEFAULT_FORMAT_LOCALE): string {
  return new Intl.ListFormat(locale, { style: "long", type: "conjunction" }).format(items);
}

export function humanizeFeatureId(featureId: string): string {
  return featureId.replaceAll("_", " ");
}
