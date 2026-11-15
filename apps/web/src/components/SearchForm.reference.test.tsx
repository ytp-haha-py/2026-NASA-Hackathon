import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type { FeatureOut, ReferenceOut } from "../api/client";
import { I18nProvider } from "../i18n/I18nProvider";
import { contextFeaturesFixture, featuresFixture, referenceFixture } from "../test/fixtures";
import { installMockFetch, jsonResponse } from "../test/mock-api";
import { SearchForm } from "./SearchForm";

// Read the real stylesheet because Vitest stubs CSS imports; the hide rule is the regression.
const tasteCss = readFileSync(resolve(process.cwd(), "src/taste.css"), "utf8");

/**
 * Regression for #170: selecting a Moon/Mars reference on the map must update the read-only D01
 * values. The values are rendered in the DOM, but taste.css hides `.metric-target` for metrics
 * with weight 0 — and reference weights start at 0 — so the numbers were invisible on the
 * deployed site while only the reference name changed.
 */

const flatFeature: FeatureOut = {
  ...featuresFixture[0],
  id: "flat_terrain_fraction",
  label: "Flat terrain fraction",
  unit: "fraction",
  min_value: 0,
  max_value: 1,
  scale: 0.25,
  method_version: "flat-terrain-fraction-1.0.0",
};

// Same five D01 feature ids the public GET /references returns for every Moon/Mars reference.
const features: FeatureOut[] = [...featuresFixture, flatFeature];

function reference(
  base: Omit<ReferenceOut, "features">,
  values: number[],
): ReferenceOut {
  return {
    ...base,
    features: features.map((feature, index) => ({
      feature_id: feature.id,
      label: feature.label,
      value: values[index] ?? 0,
      unit: feature.unit,
      method_version: feature.method_version,
      extraction_scale_m: feature.extraction_scale_m,
      valid_fraction: 1,
      quality: "verified",
      scale: feature.scale,
      description: feature.description,
    })),
  };
}

const moonA = reference(
  {
    ...referenceFixture,
    reference_id: "moon-lola-site01-roi1",
    body: "Moon",
    display_name: "Connecting Ridge (LOLA Site01 roi1)",
    lat: -89.43892646,
    lon: -137.09597807,
  },
  [8.161142, 13.99, 162.24, 48.45, 0.25],
);

const moonB = reference(
  {
    ...referenceFixture,
    reference_id: "moon-lola-site07-roi1",
    body: "Moon",
    display_name: "Peak near Shackleton (LOLA Site07 roi1)",
    lat: -88.80636301,
    lon: 123.54628783,
  },
  [7.4, 14.73, 177.52, 40.97, 0.34],
);

const mars = reference(
  {
    ...referenceFixture,
    reference_id: "mars-jezero-ctx-window-07",
    body: "Mars",
    display_name: "Jezero CTX expansion window 07",
    dataset_id: "mars2020-jezero-ctx-dem-20m",
    lat: 19.14813651396077,
    lon: 78.3492054182794,
  },
  [4.830566, 8.467398, 105.272675, 18.446172, 0.615],
);

const registry = {
  registry_version: "test",
  data_version: "test",
  filters: {},
  join_audit: {},
  audit_summary: {},
  notes: [],
  features: [],
};

let styleSheet: HTMLStyleElement;

beforeAll(() => {
  // Vitest does not load CSS by default; inject the real stylesheet so the regression is a
  // user-visible visibility check, not a class-name check.
  styleSheet = document.createElement("style");
  styleSheet.textContent = tasteCss;
  document.head.append(styleSheet);
});

afterAll(() => {
  styleSheet.remove();
});

function setup() {
  installMockFetch({ searchFeatures: () => jsonResponse(registry) });
  render(
    <I18nProvider initialLocale="en">
      <SearchForm
        features={features}
        contextFeatures={contextFeaturesFixture}
        references={[moonA, moonB, mars]}
        referencesError={null}
        loading={false}
        onSearch={vi.fn()}
      />
    </I18nProvider>,
  );
}

const d01 = () => screen.getByTestId("dimension-card-D01");
const selectedName = () => document.querySelector(".target-summary strong");

function referenceTable(): HTMLElement {
  return document.querySelector(".reference-table") as HTMLElement;
}

/** Reference values are only visible if the weight-0 hide rule excludes reference rows. */
function expectVisibleReferenceValues(values: string[]) {
  const card = d01();
  const marks = within(card).getAllByTitle("Reference value (read-only)");
  expect(marks).toHaveLength(values.length);
  for (const mark of marks) expect(mark).toBeVisible();
  for (const value of values) {
    const mark = within(card).getByText(`◆ ${value}`);
    expect(mark).toBeVisible();
  }
}

/**
 * The details block is intentionally "small print" in taste.css, so assert its contents rather
 * than its visibility; the user-facing values are the always-visible D01 metric targets above.
 */
function expectReferenceTable(values: string[]) {
  const table = referenceTable();
  expect(table).not.toBeNull();
  for (const value of values) {
    expect(
      within(table).getByText(new RegExp(`^${value.replace(".", "\\.")}( |$)`)),
    ).toBeInTheDocument();
  }
}

/** Dense planetary pins zoom before they select; keep clicking until this one is the selection. */
function clickPin(displayName: string) {
  const pin = screen.getByRole("button", { name: displayName });
  fireEvent.click(pin, { detail: 1 });
  if (selectedName()?.textContent !== displayName) {
    fireEvent.click(pin, { detail: 1 });
  }
  expect(selectedName()).toHaveTextContent(displayName);
}

describe("Moon/Mars reference selection updates the D01 values", () => {
  it("shows the selected reference's values, not a stale or blank metric", async () => {
    const user = userEvent.setup();
    setup();

    // Reference mode starts on the Moon map; select a point there.
    await user.click(screen.getByRole("radio", { name: "Moon" }));
    clickPin(moonA.display_name);
    expect(selectedName()).toHaveTextContent(moonA.display_name);
    expectVisibleReferenceValues(["8.16 deg", "13.99 deg", "162.24 m", "48.45 m", "0.25"]);
    expectReferenceTable(["8.16", "13.99", "162.24", "48.45", "0.25"]);

    // A second Moon point replaces every number with that point's values.
    clickPin(moonB.display_name);
    expect(selectedName()).toHaveTextContent(moonB.display_name);
    expectVisibleReferenceValues(["7.4 deg", "14.73 deg", "177.52 m", "40.97 m", "0.34"]);
    expectReferenceTable(["7.4", "14.73", "177.52", "40.97", "0.34"]);
    expect(within(d01()).queryByText("◆ 8.16 deg")).not.toBeInTheDocument();

    // Crossing bodies switches to the Mars point's values as well.
    await user.click(screen.getByRole("radio", { name: "Mars" }));
    clickPin(mars.display_name);
    expect(selectedName()).toHaveTextContent(mars.display_name);
    expectVisibleReferenceValues(["4.83 deg", "8.47 deg", "105.27 m", "18.45 m", "0.62"]);
    expectReferenceTable(["4.83", "8.47", "105.27", "18.45", "0.62"]);
    expect(within(d01()).queryByText("◆ 7.4 deg")).not.toBeInTheDocument();
  });
});
