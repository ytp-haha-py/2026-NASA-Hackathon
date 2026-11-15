import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { once } from "node:events";

// Occupy a temporary port to verify Vite's normal fallback without touching other servers.
const occupied = createServer();
occupied.listen(0, "127.0.0.1");
await once(occupied, "listening");
const port = occupied.address().port;
const child = spawn(process.execPath, [
  "node_modules/vite/bin/vite.js", "--configLoader", "native",
  "--host", "127.0.0.1", "--port", String(port),
], {
  env: { ...process.env, NO_COLOR: "1", VITE_API_PROXY_TARGET: "http://127.0.0.1:8000" },
  stdio: ["ignore", "pipe", "pipe"],
});
let output = "";
child.stdout.on("data", (data) => { output += data; });
child.stderr.on("data", (data) => { output += data; });
const exited = once(child, "exit");
try {
  const deadline = Date.now() + 30_000;
  let base;
  while (Date.now() < deadline) {
    base = output.match(/Local:\s+(http:\/\/127\.0\.0\.1:\d+\/)/)?.[1];
    if (base) break;
    assert.equal(child.exitCode, null, output);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.ok(base, `Vite did not start:\n${output}`);
  assert.notEqual(new URL(base).port, String(port));
  assert.match(output, /is in use, trying another one/);
  for (const [path, expected] of [
    ["", /src\/main\.tsx/],
    ["src/main.tsx", /import/],
    ["src/lib/earthBasemap.ts", /maplibre-gl\/dist\/maplibre-gl\.css/],
    ["node_modules/maplibre-gl/dist/maplibre-gl.css", /\.maplibregl-map/],
    ["node_modules/@maplibre/maplibre-gl-leaflet/leaflet-maplibre-gl.js", /maplibreGL/],
  ]) {
    const response = await fetch(new URL(path, base), { signal: AbortSignal.timeout(10_000) });
    assert.equal(response.status, 200, `${path}: HTTP ${response.status}`);
    assert.match(await response.text(), expected, path);
    console.log(`[check:dev] ${path || "/"}: HTTP 200`);
  }
  assert.doesNotMatch(output, /unsupported by|Failed to resolve|Internal server error/);
  console.log(output.trim());
  console.log("[check:dev] PASS: native config, port fallback, Earth modules and CSS");
} finally {
  child.kill("SIGTERM");
  await exited;
  occupied.close();
}
