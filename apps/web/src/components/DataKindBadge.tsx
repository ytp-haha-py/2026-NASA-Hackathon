import { useI18n } from "../i18n/I18nProvider";

type DataKindBadgeProps = {
  kind: string;
};

export function DataKindBadge({ kind }: DataKindBadgeProps) {
  const { t } = useI18n();
  const normalized = kind.toLowerCase();
  if (normalized === "synthetic") {
    return (
      <span className="badge badge-synthetic" aria-label={t("dataKind.syntheticLabel")}>
        {t("dataKind.synthetic")}
      </span>
    );
  }
  if (normalized === "observed") {
    return (
      <span className="badge badge-observed" aria-label={t("dataKind.observedLabel")}>
        {t("dataKind.observed")}
      </span>
    );
  }
  return <span className="badge badge-unknown">{kind}</span>;
}
