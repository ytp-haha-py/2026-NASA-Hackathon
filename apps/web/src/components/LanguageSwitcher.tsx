import { useI18n } from "../i18n/I18nProvider";
import type { Locale } from "../i18n/translate";

/**
 * Each option is named in its own language so it is recognizable from either UI. The visible
 * text is short ("中文" / "EN"); the accessible name stays the full language name.
 */
const OPTIONS: { locale: Locale; label: string; short: string }[] = [
  { locale: "zh-TW", label: "繁體中文", short: "中文" },
  { locale: "en", label: "English", short: "EN" },
];

export function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();

  return (
    <div className="language-switcher" role="group" aria-label={t("language.label")}>
      {OPTIONS.map((option) => (
        <button
          key={option.locale}
          type="button"
          lang={option.locale}
          className={option.locale === locale ? "is-active" : ""}
          aria-pressed={option.locale === locale}
          aria-label={option.label}
          onClick={() => setLocale(option.locale)}
        >
          {option.short}
        </button>
      ))}
    </div>
  );
}
