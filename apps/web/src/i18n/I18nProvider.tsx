import {
  createContext,
  Fragment,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { MessageKey } from "../locales/en/messages";
import {
  formatCoordinate,
  formatDateTime,
  formatDistance,
  formatList,
  formatNumber,
  formatPercent,
  formatSignedNumber,
  humanizeFeatureId,
} from "../lib/format";
import {
  CATALOGS,
  detectInitialLocale,
  interpolate,
  lookupMessage,
  pluralKey,
  splitTemplate,
  storeLocale,
  type Catalog,
  type Locale,
  type MessageVars,
  type PluralBase,
} from "./translate";

type BBox = { west: number; south: number; east: number; north: number };

export type I18n = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  /** Plain-text message. */
  t: (key: MessageKey, vars?: MessageVars) => string;
  /** Plural message; `{count}` is filled with the locale-formatted count. */
  tp: (base: PluralBase, count: number, vars?: MessageVars) => string;
  /** Message whose placeholders may be React nodes (for <strong>, <code>, links). */
  rich: (key: MessageKey, vars: Record<string, ReactNode>) => ReactNode;
  fmt: {
    number: (value: number, maxFractionDigits?: number) => string;
    signed: (value: number, maxFractionDigits?: number) => string;
    distance: (value: number) => string;
    percent: (fraction: number) => string;
    coordinate: (value: number) => string;
    dateTime: (iso: string | null | undefined) => string | null;
    list: (items: string[]) => string;
    bbox: (bbox: BBox) => string;
    /** Short unit symbol for a value ("deg", "m"); unknown units are shown as sent. */
    unit: (unit: string) => string;
    /** `value unit` with a localized number and unit symbol. */
    withUnit: (value: number, unit: string, maxFractionDigits?: number) => string;
    exclusionReason: (reason: string, labels: Map<string, string>) => string;
  };
  label: {
    /** Localized label for a stable feature id, else the API label, else the id. */
    feature: (featureId: string, apiLabel?: string | null) => string;
    body: (body: string) => string;
    status: (status: string | null | undefined) => string;
    quality: (quality: string) => string;
    dimension: (dimensionId: string) => string;
    /** Localized meaning of a stable error code, or null when none is known. */
    errorCode: (code: string | null | undefined) => string | null;
    unitName: (unit: string) => string;
  };
};

function fromMap(map: Record<string, string>, key: string): string | undefined {
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : undefined;
}

export function createI18n(
  locale: Locale,
  setLocale: (locale: Locale) => void = () => undefined,
  catalog: Catalog = CATALOGS[locale],
): I18n {
  const labels = catalog.labels;

  const t: I18n["t"] = (key, vars) => interpolate(lookupMessage(key, catalog), vars);

  const number: I18n["fmt"]["number"] = (value, maxFractionDigits = 2) =>
    formatNumber(value, maxFractionDigits, locale);

  const tp: I18n["tp"] = (base, count, vars) =>
    t(pluralKey(base, count, locale, catalog), { ...vars, count: number(count, 0) });

  const rich: I18n["rich"] = (key, vars) => {
    const template = lookupMessage(key, catalog);
    const parts = splitTemplate(template, (name) =>
      Object.prototype.hasOwnProperty.call(vars, name),
    );
    return parts.map((part, index) =>
      "text" in part ? (
        <Fragment key={index}>{part.text}</Fragment>
      ) : (
        <Fragment key={index}>{vars[part.name]}</Fragment>
      ),
    );
  };

  const unit: I18n["fmt"]["unit"] = (rawUnit) => fromMap(labels.unitSymbol, rawUnit) ?? rawUnit;

  const featureLabel: I18n["label"]["feature"] = (featureId, apiLabel) =>
    fromMap(labels.feature, featureId) ?? apiLabel ?? featureId;

  const exclusionNames = (ids: string[], names: Map<string, string>) =>
    formatList(
      ids.map((id) => featureLabel(id, names.get(id) ?? humanizeFeatureId(id))),
      locale,
    );

  const exclusionReason: I18n["fmt"]["exclusionReason"] = (reason, names) => {
    if (reason.startsWith("missing_metric:")) {
      const ids = reason.slice("missing_metric:".length).split(",");
      return t("exclusion.missingMetric", { names: exclusionNames(ids, names) });
    }
    if (reason.startsWith("missing_context:")) {
      const ids = reason.slice("missing_context:".length).split(",");
      return t("exclusion.missingContext", { names: exclusionNames(ids, names) });
    }
    if (reason.startsWith("filter:")) {
      const ids = reason.split(",").map((part) => part.replace(/^filter:/, ""));
      return t("exclusion.filter", { names: exclusionNames(ids, names) });
    }
    if (reason === "incompatible_method_version") {
      return t("exclusion.incompatibleMethod");
    }
    return reason.replaceAll("_", " ");
  };

  return {
    locale,
    setLocale,
    t,
    tp,
    rich,
    fmt: {
      number,
      signed: (value, maxFractionDigits = 2) =>
        formatSignedNumber(value, maxFractionDigits, locale),
      distance: formatDistance,
      percent: (fraction) => formatPercent(fraction, locale),
      coordinate: (value) => formatCoordinate(value, locale),
      dateTime: (iso) => formatDateTime(iso, locale),
      list: (items) => formatList(items, locale),
      bbox: (bbox) =>
        t("format.bbox", {
          south: number(bbox.south, 4),
          north: number(bbox.north, 4),
          west: number(bbox.west, 4),
          east: number(bbox.east, 4),
        }),
      unit,
      withUnit: (value, rawUnit, maxFractionDigits = 2) =>
        `${number(value, maxFractionDigits)} ${unit(rawUnit)}`,
      exclusionReason,
    },
    label: {
      feature: featureLabel,
      body: (body) => fromMap(labels.body, body) ?? body,
      status: (status) => {
        const raw = status ?? "unknown";
        return fromMap(labels.status, raw) ?? raw;
      },
      quality: (quality) => fromMap(labels.quality, quality) ?? quality,
      dimension: (dimensionId) => fromMap(labels.dimension, dimensionId) ?? dimensionId,
      errorCode: (code) => (code ? (fromMap(labels.errorCode, code) ?? null) : null),
      unitName: (rawUnit) => fromMap(labels.unitName, rawUnit) ?? rawUnit,
    },
  };
}

const I18nContext = createContext<I18n | null>(null);

type I18nProviderProps = {
  children: ReactNode;
  /** Overrides detection (tests, storybook-style previews). */
  initialLocale?: Locale;
};

export function I18nProvider({ children, initialLocale }: I18nProviderProps) {
  const [locale, setLocaleState] = useState<Locale>(
    () => initialLocale ?? detectInitialLocale(),
  );

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    storeLocale(next);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo(() => createI18n(locale, setLocale), [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

const englishFallback = createI18n("en");

/**
 * Returns the active translator. Components rendered outside a provider (isolated unit
 * tests) get English, which is also the runtime fallback.
 */
export function useI18n(): I18n {
  return useContext(I18nContext) ?? englishFallback;
}

/**
 * Wraps text that the API supplied in English (explanations, caveats, notes) so it is
 * announced in English by screen readers and is never silently re-interpreted.
 */
export function SourceText({ children }: { children: ReactNode }) {
  return (
    <span lang="en" className="source-text">
      {children}
    </span>
  );
}

/** One-line note shown before blocks of untranslated API text (empty in English). */
export function SourceTextNote() {
  const { t } = useI18n();
  const note = t("common.sourceTextNote");
  if (!note) {
    return null;
  }
  return <p className="source-text-note">{note}</p>;
}
