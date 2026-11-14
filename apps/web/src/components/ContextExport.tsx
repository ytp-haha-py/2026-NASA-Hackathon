import { useEffect, useState } from "react";
import {
  errorCode,
  errorMessage,
  getLocationContext,
  isAbortError,
  type ContextOut,
  type NumericTarget,
} from "../api/client";

type ContextExportProps = {
  locationId: string;
  target: NumericTarget;
};

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
    throw new Error("Clipboard copy was rejected by the browser.");
  }
}

export function ContextExport({ locationId, target }: ContextExportProps) {
  const [context, setContext] = useState<ContextOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ message: string; code: string | null } | null>(
    null,
  );
  const [copyStatus, setCopyStatus] = useState("");
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
    setCopyStatus("");

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
      setCopyStatus("Download is not supported by this browser. Use Copy JSON instead.");
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
    setCopyStatus("Download started.");
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
      setCopyStatus("Copied the context JSON to the clipboard.");
    } catch (caught) {
      setCopyStatus(
        `Copy failed: ${caught instanceof Error ? caught.message : "unknown clipboard error"}`,
      );
    }
  }

  return (
    <section className="context-export" aria-labelledby="context-heading">
      <div className="section-heading compact">
        <p className="eyebrow">Step 4</p>
        <h3 id="context-heading">Grounded context export</h3>
        <p>
          A structured JSON export for later external AI use. It contains source facts and
          computed comparisons only. <strong>This app is not a chatbot</strong> and does not
          generate a narrative or recommendation. When present, Earth land-cover context
          observations are included and marked <code>context_only</code>; they are not
          similarity inputs.
        </p>
      </div>

      {!hasTarget && (
        <p className="field-hint">
          A search target is required before a context export can be prepared.
        </p>
      )}

      {loading && <p role="status">Preparing context export…</p>}

      {error && (
        <div className="alert alert-error" role="alert">
          <p>
            <strong>Context export failed{error.code ? ` (${error.code})` : ""}.</strong>{" "}
            {error.message}
          </p>
          <button type="button" className="secondary-button" onClick={() => setRetryKey((key) => key + 1)}>
            Retry context export
          </button>
        </div>
      )}

      {context && (
        <>
          <dl className="export-meta">
            <div>
              <dt>Export version</dt>
              <dd>{context.export_version}</dd>
            </div>
            <div>
              <dt>Generated at</dt>
              <dd>
                <time dateTime={context.generated_at}>{context.generated_at}</time>
              </dd>
            </div>
          </dl>
          <p className="export-note">{context.note}</p>

          <div className="button-row">
            <button type="button" className="primary-button" onClick={handleDownload}>
              Download JSON
            </button>
            <button type="button" className="secondary-button" onClick={handleCopy}>
              Copy JSON
            </button>
          </div>

          {copyStatus && (
            <p className="copy-status" role="status" aria-live="polite">
              {copyStatus}
            </p>
          )}

          <details className="json-preview">
            <summary>Preview export JSON</summary>
            <pre>{serialized}</pre>
          </details>
        </>
      )}
    </section>
  );
}
