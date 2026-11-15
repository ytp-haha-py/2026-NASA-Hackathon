/** Local-first development proxy selection shared by Vite and tests. */

export const DEFAULT_API_PROXY_TARGET = "https://api.貓熊.台灣";
export const LOCAL_API_ORIGIN = "http://127.0.0.1:8000";
export const LOCAL_API_HEALTH_URL = `${LOCAL_API_ORIGIN}/api/v1/health`;
export const LOCAL_HEALTH_TIMEOUT_MS = 750;

export type ProxyEnv = Record<string, string | undefined>;
export type FetchLike = (
  input: string,
  init?: {
    signal?: AbortSignal;
    headers?: Record<string, string>;
  },
) => Promise<{ status: number; json: () => Promise<unknown> }>;

export type ProxySelection = {
  target: string;
  source: "explicit" | "local" | "remote";
  reason: string;
};

export function parseOrigin(raw: string, name = "API target"): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`${name} is not a valid URL: ${raw}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`${name} must use http or https: ${raw}`);
  }
  if (url.username || url.password) {
    throw new Error(`${name} must not include credentials: ${raw}`);
  }
  if (url.pathname !== "/" && url.pathname !== "") {
    throw new Error(
      `${name} must be an origin only (for example https://api.貓熊.台灣); got path ${url.pathname}`,
    );
  }
  if (url.search || url.hash) {
    throw new Error(`${name} must not include a query or fragment: ${raw}`);
  }
  return url.origin;
}

export function isHealthyApiPayload(value: unknown): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const payload = value as Record<string, unknown>;
  return (
    payload.status === "ok" &&
    typeof payload.data_version === "string" &&
    payload.data_version.length > 0 &&
    typeof payload.data_snapshot_id === "string" &&
    payload.data_snapshot_id.length > 0
  );
}

export async function probeLocalApi(options: {
  fetchImpl?: FetchLike;
  url?: string;
  timeoutMs?: number;
} = {}): Promise<boolean> {
  const fetchImpl = options.fetchImpl ?? (fetch as unknown as FetchLike);
  const url = options.url ?? LOCAL_API_HEALTH_URL;
  const timeoutMs = options.timeoutMs ?? LOCAL_HEALTH_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (response.status !== 200) {
      return false;
    }
    return isHealthyApiPayload(await response.json());
  } catch {
    // Connection refused, timeout, TLS/DNS failure, non-JSON body and schema mismatch all mean
    // "not a healthy local API". The process then uses the fixed remote target.
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function resolveProxyTarget(
  env: ProxyEnv,
  options: {
    probeLocal?: boolean;
    fetchImpl?: FetchLike;
    log?: (message: string) => void;
  } = {},
): Promise<ProxySelection> {
  const log = options.log ?? ((message: string) => console.log(message));
  const explicit = env.VITE_API_PROXY_TARGET?.trim();
  if (explicit) {
    const target = parseOrigin(explicit, "VITE_API_PROXY_TARGET");
    const reason = "explicit VITE_API_PROXY_TARGET override; automatic detection skipped";
    log(`[api-proxy] ${reason}: ${target}`);
    return { target, source: "explicit", reason };
  }

  if (options.probeLocal === false) {
    const reason = "production/build mode; public API target without local probe";
    log(`[api-proxy] ${reason}: ${DEFAULT_API_PROXY_TARGET}`);
    return { target: DEFAULT_API_PROXY_TARGET, source: "remote", reason };
  }

  const healthy = await probeLocalApi({ fetchImpl: options.fetchImpl });
  if (healthy) {
    const reason = `local FastAPI health check passed at ${LOCAL_API_HEALTH_URL}`;
    log(`[api-proxy] ${reason}; using ${LOCAL_API_ORIGIN}`);
    return { target: LOCAL_API_ORIGIN, source: "local", reason };
  }

  const reason = `local FastAPI health check failed at ${LOCAL_API_HEALTH_URL}; using public API`;
  log(`[api-proxy] ${reason}: ${DEFAULT_API_PROXY_TARGET}`);
  return { target: DEFAULT_API_PROXY_TARGET, source: "remote", reason };
}

export function createApiProxy(target: string) {
  const origin = parseOrigin(target, "resolved proxy target");
  return {
    "/api": {
      target: origin,
      changeOrigin: true,
      secure: true,
      xfwd: true,
    },
  };
}
