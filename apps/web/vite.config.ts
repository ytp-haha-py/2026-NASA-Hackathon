import { fileURLToPath } from "node:url";
import path from "node:path";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { createApiProxy, resolveProxyTarget } from "./vite.proxy.ts";

const webRoot = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webRoot, "../..");

export default defineConfig(async ({ command, mode }) => {
  // The root .env.example documents the optional override; Vite itself runs from apps/web, so
  // read both locations. Shell variables win.
  const env = {
    ...loadEnv(mode, repoRoot, ""),
    ...loadEnv(mode, webRoot, ""),
    ...process.env,
  };
  // Only an actual development server probes localhost. Tests, preview and production builds
  // never touch the network and keep the public API target.
  const probeLocal = command === "serve" && mode === "development";
  const selection = await resolveProxyTarget(env, { probeLocal });
  const apiProxy = createApiProxy(selection.target);

  return {
    plugins: [react()],
    server: {
      proxy: apiProxy,
    },
    preview: {
      proxy: apiProxy,
    },
    build: {
      outDir: "dist",
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      clearMocks: true,
      restoreMocks: true,
    },
  };
});
