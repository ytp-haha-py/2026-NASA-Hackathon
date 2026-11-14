import { useEffect, useState } from "react";
import {
  errorCode,
  errorMessage,
  getLocationContext,
  isAbortError,
  type ContextOut,
  type NumericTarget,
} from "../api/client";
import { SourceText, useI18n } from "../i18n/I18nProvider";
import type { MessageVars } from "../i18n/translate";
import type { MessageKey } from "../locales/en/messages";
import { Timestamp } from "./Timestamp";

type ContextExportProps = {
  locationId: string;
  target: NumericTarget;
};

class ClipboardRejectedError extends Error {}

type CopyStatus = { key: MessageKey; vars?: MessageVars; reasonKey?: MessageKey } | null;

function fallbackCopy(text: string): void {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!copied) {
    throw new ClipboardRejectedError("Clipboard copy was rejected by the browser.");
  }
}

export function ContextExport({ locationId, target }: ContextExportProps) {
  const { t, rich } = useI18n();
  const [context, setContext] = useState<ContextOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ message: string; code: string | null } | null>(
    null,
  );
  const [copyStatus, setCopyStatus] = useState<CopyStatus>(null);
  const [retryKey, setRetryKey] = useState(0);

  const hasTarget = Object.keys(target).length > 0;

  useEffect(() => {
    if (!hasTarget) {
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setContext(null);
    setCopyStatus(null);

    getLocationContext(locationId, target, controller.signal)
      .then((payload) => {
        setContext(payload);
      })
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
  }, [locationId, target, hasTarget, retryKey]);

  const serialized = context ? JSON.stringify(context, null, 2) : "";

  function handleDownload() {
    if (!context) {
      return;
    }
    if (typeof URL.createObjectURL !== "function") {
      setCopyStatus({ key: "export.downloadUnsupported" });
      return;
    }
    const blob = new Blob([serialized], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `analog-context-${locationId}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    setCopyStatus({ key: "export.downloadStarted" });
  }

  async function handleCopy() {
    if (!context) {
      return;
    }
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(serialized);
      } else {
        fallbackCopy(serialized);
      }
      setCopyStatus({ key: "export.copied" });
    } catch (caught) {
      if (caught instanceof ClipboardRejectedError) {
        setCopyStatus({ key: "export.copyFailed", reasonKey: "export.copyRejected" });
      } else if (caught instanceof Error) {
        setCopyStatus({ key: "export.copyFailed", vars: { reason: caught.message } });
      } else {
        setCopyStatus({ key: "export.copyFailed", reasonKey: "export.copyUnknown" });
      }
    }
  }

  return (
    <section className="context-export" aria-labelledby="context-heading">
      <div className="section-heading compact">
        <p className="eyebrow">{t("export.step")}</p>
        <h3 id="context-heading">{t("export.heading")}</h3>
        <p>
          {rich("export.intro", {
            notChatbot: <strong>{t("export.notChatbot")}</strong>,
            code: <code>context_only</code>,
          })}
        </p>
      </div>

      {!hasTarget && (
        <p className="field-hint">{t("export.needsTarget")}</p>
      )}

      {loading && <p role="status">{t("export.loading")}</p>}

      {error && (
        <div className="alert alert-error" role="alert">
          <p>
            <strong>
              {error.code
                ? t("export.failedWithCode", { code: error.code })
                : t("export.failed")}
            </strong>{" "}
            <SourceText>{error.message}</SourceText>
          </p>
          <button type="button" className="secondary-button" onClick={() => setRetryKey((key) => key + 1)}>
            {t("export.retry")}
          </button>
        </div>
      )}

      {context && (
        <>
          <dl className="export-meta">
            <div>
              <dt>{t("export.version")}</dt>
              <dd>{context.export_version}</dd>
            </div>
            <div>
              <dt>{t("export.generatedAt")}</dt>
              <dd>
                <Timestamp iso={context.generated_at} />
              </dd>
            </div>
          </dl>
          <p className="export-note">
            <SourceText>{context.note}</SourceText>
          </p>

          <div className="button-row">
            <button type="button" className="primary-button" onClick={handleDownload}>
              {t("export.download")}
            </button>
            <button type="button" className="secondary-button" onClick={handleCopy}>
              {t("export.copy")}
            </button>
          </div>

          {copyStatus && (
            <p className="copy-status" role="status" aria-live="polite">
              {t(copyStatus.key, {
                ...copyStatus.vars,
                ...(copyStatus.reasonKey ? { reason: t(copyStatus.reasonKey) } : {}),
              })}
            </p>
          )}

          <details className="json-preview">
            <summary>{t("export.preview")}</summary>
            <pre>{serialized}</pre>
          </details>
        </>
      )}
    </section>
  );
}
