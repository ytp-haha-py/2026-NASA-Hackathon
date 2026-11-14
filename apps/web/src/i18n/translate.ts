import { en, enLabels, type DisplayLabels, type MessageKey } from "../locales/en/messages";
import { zhTW, zhTWLabels } from "../locales/zh-TW/messages";

export const SUPPORTED_LOCALES = ["en", "zh-TW"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_STORAGE_KEY = "planetary-analog-explorer.locale";

export type Catalog = {
  messages: { [K in MessageKey]?: string };
  labels: DisplayLabels;
};

export const CATALOGS: Record<Locale, Catalog> = {
  en: { messages: en, labels: enLabels },
  "zh-TW": { messages: zhTW, labels: zhTWLabels },
};

/** Keys that have `_one` / `_other` plural variants, named without the suffix. */
export type PluralBase = {
  [K in MessageKey]: K extends `${infer Base}_other` ? Base : never;
}[MessageKey];

export type MessageVars = Record<string, string | number>;

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

/**
 * Maps one BCP 47 language tag to a supported locale, or null when unsupported.
 * Traditional-Chinese tags (zh, zh-TW, zh-HK, zh-MO, zh-Hant-*) map to zh-TW.
 * Simplified-Chinese tags (zh-CN, zh-SG, zh-Hans-*) are not supported (non-goal).
 */
export function matchLocale(tag: string): Locale | null {
  const normalized = tag.trim().toLowerCase().replaceAll("_", "-");
  if (normalized === "") {
    return null;
  }
  const [language, ...rest] = normalized.split("-");
  if (language === "en") {
    return "en";
  }
  if (language === "zh") {
    if (rest.includes("hans") || rest.includes("cn") || rest.includes("sg")) {
      return null;
    }
    return "zh-TW";
  }
  return null;
}

/** First supported locale in the browser's preference order, else English. */
export function resolveBrowserLocale(languages: readonly string[]): Locale {
  for (const tag of languages) {
    const match = matchLocale(tag);
    if (match) {
      return match;
    }
  }
  return DEFAULT_LOCALE;
}

export function readStoredLocale(): Locale | null {
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(stored) ? stored : null;
  } catch {
    return null;
  }
}

export function storeLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Storage can be unavailable (private mode, blocked site data). The switch still
    // applies for this session; it just is not remembered.
  }
}

function browserLanguages(): readonly string[] {
  if (typeof navigator === "undefined") {
    return [];
  }
  if (Array.isArray(navigator.languages) && navigator.languages.length > 0) {
    return navigator.languages;
  }
  return navigator.language ? [navigator.language] : [];
}

/** Saved explicit preference first, then browser language, then English. */
export function detectInitialLocale(): Locale {
  return readStoredLocale() ?? resolveBrowserLocale(browserLanguages());
}

const PLACEHOLDER = /\{(\w+)\}/g;

/** Replaces `{name}` placeholders that have a variable; leaves others untouched. */
export function interpolate(template: string, vars?: MessageVars): string {
  if (!vars) {
    return template;
  }
  return template.replace(PLACEHOLDER, (whole, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : whole,
  );
}

/**
 * Splits a template into literal strings and placeholder names, for rich rendering.
 * Placeholders without a matching variable stay literal.
 */
export function splitTemplate(
  template: string,
  hasVar: (name: string) => boolean,
): ({ text: string } | { name: string })[] {
  const parts: ({ text: string } | { name: string })[] = [];
  let last = 0;
  for (const match of template.matchAll(PLACEHOLDER)) {
    const name = match[1];
    if (!hasVar(name)) {
      continue;
    }
    const index = match.index ?? 0;
    if (index > last) {
      parts.push({ text: template.slice(last, index) });
    }
    parts.push({ name });
    last = index + match[0].length;
  }
  if (last < template.length) {
    parts.push({ text: template.slice(last) });
  }
  return parts;
}

/**
 * Looks a message up in the active catalog, then in the English fallback. A key missing
 * from both (only possible with a stale build) renders as the key itself so it is visible.
 */
export function lookupMessage(
  key: string,
  catalog: Catalog,
  fallback: Catalog = CATALOGS.en,
): string {
  const messages = catalog.messages as Record<string, string | undefined>;
  const fallbackMessages = fallback.messages as Record<string, string | undefined>;
  return messages[key] ?? fallbackMessages[key] ?? key;
}

export function pluralKey(
  base: PluralBase,
  count: number,
  locale: Locale,
  catalog: Catalog,
): MessageKey {
  const rule = new Intl.PluralRules(locale).select(count);
  const candidate = `${base}_${rule}`;
  const messages = catalog.messages as Record<string, string | undefined>;
  const fallbackMessages = CATALOGS.en.messages as Record<string, string | undefined>;
  if (messages[candidate] !== undefined) {
    return candidate as MessageKey;
  }
  if (messages[`${base}_other`] !== undefined) {
    return `${base}_other` as MessageKey;
  }
  const englishRule = new Intl.PluralRules("en").select(count);
  if (fallbackMessages[`${base}_${englishRule}`] !== undefined) {
    return `${base}_${englishRule}` as MessageKey;
  }
  return `${base}_other` as MessageKey;
}
