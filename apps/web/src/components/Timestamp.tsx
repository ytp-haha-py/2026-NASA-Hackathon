import { useI18n } from "../i18n/I18nProvider";

/** Renders an ISO timestamp localized (UTC), keeping the raw value machine-readable. */
export function Timestamp({ iso }: { iso: string }) {
  const { fmt } = useI18n();
  return (
    <time dateTime={iso} title={iso}>
      {fmt.dateTime(iso) ?? iso}
    </time>
  );
}
