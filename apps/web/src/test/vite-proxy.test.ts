import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_API_PROXY_TARGET,
  LOCAL_API_ORIGIN,
  createApiProxy,
  isHealthyApiPayload,
  parseOrigin,
  probeLocalApi,
  resolveProxyTarget,
  type FetchLike,
} from "../../vite.proxy";

function response(status: number, payload: unknown) {
  return {
    status,
    json: async () => payload,
  };
}

const healthyPayload = {
  status: "ok",
  data_version: "dv-test",
  data_snapshot_id: "dv-test",
};

describe("parseOrigin", () => {
  it("accepts an origin and normalises the hostname", () => {
    expect(parseOrigin("https://api.貓熊.台灣")).toBe(
      "https://api.xn--2vxr09c.xn--kpry57d",
    );
    expect(parseOrigin("https://api.example.com/")).toBe("https://api.example.com");
  });

  it("rejects a path, credentials and non-http protocols", () => {
    expect(() => parseOrigin("https://api.example.com/api/v1")).toThrow(/origin only/);
    expect(() => parseOrigin("https://user:pass@example.com")).toThrow(/credentials/);
    expect(() => parseOrigin("file:///tmp/api")).toThrow(/http or https/);
  });
});

describe("isHealthyApiPayload", () => {
  it("requires status ok and both version fields", () => {
    expect(isHealthyApiPayload(healthyPayload)).toBe(true);
    expect(isHealthyApiPayload({ ...healthyPayload, status: "starting" })).toBe(false);
    expect(isHealthyApiPayload({ ...healthyPayload, data_version: "" })).toBe(false);
    expect(isHealthyApiPayload({ ...healthyPayload, data_snapshot_id: undefined })).toBe(false);
    expect(isHealthyApiPayload(null)).toBe(false);
    expect(isHealthyApiPayload("ok")).toBe(false);
  });
});

describe("probeLocalApi", () => {
  it("accepts only HTTP 200 with the expected health schema", async () => {
    const fetchImpl = vi.fn(async () => response(200, healthyPayload)) as FetchLike;
    await expect(probeLocalApi({ fetchImpl })).resolves.toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/v1/health",
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        headers: { Accept: "application/json" },
      }),
    );
  });

  it("rejects a non-200 response", async () => {
    const fetchImpl = vi.fn(async () => response(503, healthyPayload)) as FetchLike;
    await expect(probeLocalApi({ fetchImpl })).resolves.toBe(false);
  });

  it("rejects a port occupied by a different service", async () => {
    const fetchImpl = vi.fn(async () =>
      response(200, { hello: "not the API" }),
    ) as FetchLike;
    await expect(probeLocalApi({ fetchImpl })).resolves.toBe(false);
  });

  it("rejects connection errors and non-JSON bodies", async () => {
    const failed = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    }) as FetchLike;
    await expect(probeLocalApi({ fetchImpl: failed })).resolves.toBe(false);

    const badJson = vi.fn(async () => ({
      status: 200,
      json: async () => {
        throw new SyntaxError("not json");
      },
    })) as unknown as FetchLike;
    await expect(probeLocalApi({ fetchImpl: badJson })).resolves.toBe(false);
  });

  it("times out quickly when the local port does not answer", async () => {
    const hanging: FetchLike = (_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      });
    const started = Date.now();
    await expect(probeLocalApi({ fetchImpl: hanging, timeoutMs: 10 })).resolves.toBe(false);
    expect(Date.now() - started).toBeLessThan(500);
  });
});

describe("resolveProxyTarget", () => {
  it("uses an explicit override and never probes local", async () => {
    const fetchImpl = vi.fn() as unknown as FetchLike;
    const messages: string[] = [];
    const selection = await resolveProxyTarget(
      { VITE_API_PROXY_TARGET: "http://127.0.0.1:9000" },
      { fetchImpl, log: (message) => messages.push(message) },
    );
    expect(selection).toMatchObject({
      target: "http://127.0.0.1:9000",
      source: "explicit",
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(messages.join("\n")).toMatch(/VITE_API_PROXY_TARGET/);
  });

  it("selects local when the health check passes", async () => {
    const fetchImpl = vi.fn(async () => response(200, healthyPayload)) as FetchLike;
    const messages: string[] = [];
    const selection = await resolveProxyTarget(
      {},
      { fetchImpl, log: (message) => messages.push(message) },
    );
    expect(selection).toMatchObject({ target: LOCAL_API_ORIGIN, source: "local" });
    expect(messages.join("\n")).toMatch(/using http:\/\/127\.0\.0\.1:8000/);
  });

  it("selects the public API when local is absent, unhealthy or wrong", async () => {
    for (const fetchImpl of [
      vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      }) as FetchLike,
      vi.fn(async () => response(500, healthyPayload)) as FetchLike,
      vi.fn(async () => response(200, { hello: "wrong service" })) as FetchLike,
    ]) {
      const selection = await resolveProxyTarget({}, { fetchImpl });
      expect(selection).toMatchObject({
        target: DEFAULT_API_PROXY_TARGET,
        source: "remote",
      });
    }
  });

  it("does not probe for a production build", async () => {
    const fetchImpl = vi.fn() as unknown as FetchLike;
    const messages: string[] = [];
    const selection = await resolveProxyTarget(
      {},
      {
        probeLocal: false,
        fetchImpl,
        log: (message) => messages.push(message),
      },
    );
    expect(selection).toMatchObject({
      target: DEFAULT_API_PROXY_TARGET,
      source: "remote",
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(messages.join("\n")).toMatch(/production\/build mode/);
  });
});

describe("createApiProxy", () => {
  it("keeps a fixed origin with TLS verification and Host rewriting", () => {
    const proxy = createApiProxy("https://api.貓熊.台灣");
    expect(proxy["/api"]).toMatchObject({
      target: "https://api.xn--2vxr09c.xn--kpry57d",
      changeOrigin: true,
      secure: true,
      xfwd: true,
    });
    expect(() => createApiProxy("https://api.example.com/api/v1")).toThrow(/origin only/);
  });
});
