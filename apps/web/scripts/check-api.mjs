import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const contractPath = resolve(webRoot, "../../contracts/openapi.json");
const committedPath = resolve(webRoot, "src/api/schema.d.ts");
const cliPath = resolve(webRoot, "node_modules/openapi-typescript/bin/cli.js");

const tempDir = mkdtempSync(join(tmpdir(), "analog-openapi-"));
const regeneratedPath = join(tempDir, "schema.d.ts");

try {
  const result = spawnSync(
    process.execPath,
    [cliPath, contractPath, "-o", regeneratedPath],
    { cwd: webRoot, encoding: "utf8" },
  );

  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.stdout || "openapi-typescript failed\n");
    process.exit(result.status ?? 1);
  }

  const committed = readFileSync(committedPath, "utf8");
  const regenerated = readFileSync(regeneratedPath, "utf8");

  if (committed !== regenerated) {
    const committedLines = committed.split("\n");
    const regeneratedLines = regenerated.split("\n");
    let differingLine = 0;
    while (
      differingLine < committedLines.length &&
      differingLine < regeneratedLines.length &&
      committedLines[differingLine] === regeneratedLines[differingLine]
    ) {
      differingLine += 1;
    }

    process.stderr.write(
      "Generated API types are out of date with ../../contracts/openapi.json.\n" +
        `First difference at line ${differingLine + 1}:\n` +
        `- committed:   ${committedLines[differingLine] ?? "<end of file>"}\n` +
        `+ regenerated: ${regeneratedLines[differingLine] ?? "<end of file>"}\n` +
        "Run `npm run generate:api` and commit src/api/schema.d.ts.\n",
    );
    process.exit(1);
  }

  process.stdout.write("API types match ../../contracts/openapi.json.\n");
} finally {
  rmSync(tempDir, { recursive: true, force: true });
}
