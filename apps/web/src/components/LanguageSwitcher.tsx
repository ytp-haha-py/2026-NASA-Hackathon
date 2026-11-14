import { useI18n } from "../i18n/I18nProvider";
import type { Locale } from "../i18n/translate";

/** Each option is named in its own language so it is recognizable from either UI. */
const OPTIONS: { locale: Locale; label: string }[] = [
  { locale: "zh-TW", label: "繁體中文" },
  { locale: "en", label: "English" },
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
          onClick={() => setLocale(option.locale)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
