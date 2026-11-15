import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n/I18nProvider";
import { RESEARCH_PRESETS, researchWeights, unavailableResearchFeatures } from "../lib/research-presets";
import { contextFeaturesFixture, featuresFixture, referenceFixture } from "../test/fixtures";
import { installMockFetch } from "../test/mock-api";
import { SearchForm } from "./SearchForm";

// Synthetic software fixture matching the additive registry contract; not observed data.
const features = [...featuresFixture, {
  ...featuresFixture[0],
  id: "flat_terrain_fraction", label: "Flat terrain fraction", unit: "fraction",
  min_value: 0, max_value: 1, scale: 0.25,
  method_version: "flat-terrain-fraction-1.0.0",
}];
const props = {
  features, contextFeatures: contextFeaturesFixture, references: [referenceFixture],
  referencesError: null, loading: false,
};

function setup(extra: Partial<React.ComponentProps<typeof SearchForm>> = {}, locale: "en" | "zh-TW" = "en") {
  installMockFetch();
  const onSearch = vi.fn();
  const view = render(
    <I18nProvider initialLocale={locale}><SearchForm {...props} onSearch={onSearch} {...extra} /></I18nProvider>,
  );
  return { ...view, onSearch, user: userEvent.setup() };
}

function metric(label: string) { return within(screen.getByRole("group", { name: label })); }
function selector() { return screen.getByRole("combobox", { name: "Research preset" }); }
const labels = features.map((feature) => feature.label);

describe("Research presets", () => {
  it("starts Manual and switches all five weights without automatically submitting", async () => {
    const { user, onSearch } = setup();
    expect(selector()).toHaveValue("manual");
    for (const label of labels) expect(metric(label).getByRole("spinbutton", { name: "Weight (≥ 0)" })).toHaveValue(0);
    const cases = [
      ["rover-mobility", [2, 2.5, 1, 2, 1]],
      ["precision-landing", [1, 2.5, 0.5, 2, 2.5]],
      ["geological-fieldwork", [1.5, 1, 2.5, 2, 0.5]],
    ] as const;
    for (const [id, weights] of cases) {
      await user.selectOptions(selector(), id);
      labels.forEach((label, index) => {
        expect(metric(label).getByRole("spinbutton", { name: "Weight (≥ 0)" })).toHaveValue(weights[index]);
        expect(metric(label).getByRole("slider", { name: `${label} weight slider` })).toHaveValue(String(weights[index]));
      });
      expect(onSearch).not.toHaveBeenCalled();
    }
    expect(screen.queryByText(/initial zeros are not NASA-recommended targets/)).not.toBeInTheDocument();
    expect(screen.queryByText(/not official NASA weights/)).not.toBeInTheDocument();
    expect(screen.queryByText("Weighting rationale and supporting evidence")).not.toBeInTheDocument();
  });

  it("preserves targets, terrain/context filters and limit in the existing search payload", async () => {
    const { user, onSearch } = setup();
    const values = [12, 25, 80, 10, 0.4];
    labels.forEach((label, index) => fireEvent.change(metric(label).getByRole("spinbutton", { name: /Target value/ }), { target: { value: values[index] } }));
    fireEvent.change(metric("Mean slope").getByRole("slider", { name: "Mean slope lower bound" }), { target: { value: 5 } });
    fireEvent.change(screen.getByRole("slider", { name: "Built-up fraction upper bound" }), { target: { value: 0.2 } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Maximum results (1–10)" }), { target: { value: 3 } });
    await user.selectOptions(selector(), "precision-landing");
    expect(onSearch).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch.mock.calls[0][0].request).toEqual({
      body: "custom", reference_id: null, limit: 3,
      target: {
        slope_mean_deg: { value: 12, weight: 1 }, slope_p90_deg: { value: 25, weight: 2.5 },
        terrain_relief_p95_p05_m: { value: 80, weight: 0.5 }, roughness_std_m: { value: 10, weight: 2 },
        flat_terrain_fraction: { value: 0.4, weight: 2.5 },
      },
      filters: { slope_mean_deg: { min: 5 }, landcover_built_up_fraction: { max: 0.2 } },
    });
  });

  it.each(["slider", "number"])("returns to Manual after a %s weight edit, but keeps the preset on target edits", async (control) => {
    const { user } = setup();
    await user.selectOptions(selector(), "rover-mobility");
    fireEvent.change(metric("Mean slope").getByRole("spinbutton", { name: /Target value/ }), { target: { value: 13 } });
    expect(selector()).toHaveValue("rover-mobility");
    const input = control === "slider"
      ? metric("Mean slope").getByRole("slider", { name: "Mean slope weight slider" })
      : metric("Mean slope").getByRole("spinbutton", { name: "Weight (≥ 0)" });
    fireEvent.change(input, { target: { value: 3 } });
    expect(selector()).toHaveValue("manual");
    expect(metric("Mean slope").getByRole("spinbutton", { name: "Weight (≥ 0)" })).toHaveValue(3);
    expect(metric("Mean slope").getByRole("slider", { name: "Mean slope weight slider" })).toHaveValue("3");
    expect(metric("90th percentile slope").getByRole("spinbutton", { name: "Weight (≥ 0)" })).toHaveValue(2.5);
  });

  it("keeps existing weights when Manual is selected and resets to neutral with Reset", async () => {
    const { user, onSearch } = setup();
    await user.selectOptions(selector(), "rover-mobility");
    await user.selectOptions(selector(), "manual");
    expect(metric("Mean slope").getByRole("spinbutton", { name: "Weight (≥ 0)" })).toHaveValue(2);
    await user.selectOptions(selector(), "geological-fieldwork");
    fireEvent.change(metric("Mean slope").getByRole("spinbutton", { name: /Target value/ }), { target: { value: 13 } });
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(selector()).toHaveValue("manual");
    for (const label of labels) expect(metric(label).getByRole("spinbutton", { name: "Weight (≥ 0)" })).toHaveValue(0);
    expect(metric("Mean slope").getByRole("spinbutton", { name: /Target value/ })).toHaveValue(0);
    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));
    expect(onSearch).not.toHaveBeenCalled();
  });

  it("lets the existing AOI target preset replace weights and submit once without research weights leaking", async () => {
    const { user, rerender, onSearch } = setup();
    await user.selectOptions(selector(), "rover-mobility");
    const preset = { nonce: 71, values: { slope_mean_deg: 8, roughness_std_m: 5 }, sourceLabel: "TEST ONLY AOI" };
    const tree = <I18nProvider initialLocale="en"><SearchForm {...props} preset={preset} onSearch={onSearch} /></I18nProvider>;
    rerender(tree);
    await waitFor(() => expect(onSearch).toHaveBeenCalledTimes(1));
    expect(selector()).toHaveValue("manual");
    expect(onSearch.mock.calls[0][0].request.target).toEqual({ slope_mean_deg: { value: 8, weight: 1 }, roughness_std_m: { value: 5, weight: 1 } });
    rerender(tree);
    expect(onSearch).toHaveBeenCalledTimes(1);
    await user.selectOptions(selector(), "precision-landing");
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(metric("Mean slope").getByRole("spinbutton", { name: /Target value/ })).toHaveValue(8);
  });

  it("keeps Custom research weights separate from reference weights", async () => {
    const { user, onSearch } = setup();
    await user.selectOptions(selector(), "rover-mobility");
    await user.click(screen.getByRole("radio", { name: "Moon" }));
    await user.click(screen.getByRole("button", { name: referenceFixture.display_name }));
    expect(screen.queryByRole("combobox", { name: "Research preset" })).not.toBeInTheDocument();
    expect(metric("Mean slope").getByRole("spinbutton", { name: "Weight (≥ 0)" })).toHaveValue(0);
    fireEvent.change(metric("Mean slope").getByRole("spinbutton", { name: "Weight (≥ 0)" }), { target: { value: 1 } });
    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));
    expect(onSearch.mock.calls[0][0].request).toMatchObject({ body: "Moon", reference_id: referenceFixture.reference_id, target: {}, weights: { slope_mean_deg: 1 } });
    await user.click(screen.getByRole("radio", { name: "Custom" }));
    expect(selector()).toHaveValue("rover-mobility");
    expect(metric("Mean slope").getByRole("spinbutton", { name: "Weight (≥ 0)" })).toHaveValue(2);
  });

  it("disables research profiles when capabilities lack a metric, without inventing it", async () => {
    setup({ features: featuresFixture });
    expect(selector()).toHaveValue("manual");
    for (const option of within(selector()).getAllByRole("option").slice(1)) expect(option).toBeDisabled();
    expect(selector()).toHaveAttribute("title", expect.stringContaining("API capabilities do not offer: flat_terrain_fraction"));
    expect(screen.queryByRole("group", { name: "Flat terrain fraction" })).not.toBeInTheDocument();
  });

  it("returns to Manual if refreshed capabilities remove a required metric", async () => {
    const { user, rerender, onSearch } = setup();
    await user.selectOptions(selector(), "rover-mobility");
    rerender(<I18nProvider initialLocale="en"><SearchForm {...props} features={featuresFixture} onSearch={onSearch} /></I18nProvider>);
    expect(selector()).toHaveValue("manual");
    expect(onSearch).not.toHaveBeenCalled();
  });

  it("localizes the compact selector and accessible name into Traditional Chinese", async () => {
    const { user } = setup({}, "zh-TW");
    const select = screen.getByRole("combobox", { name: "研究權重預設" });
    expect(within(select).getByRole("option", { name: "手動設定" })).toBeInTheDocument();
    await user.selectOptions(select, "precision-landing");
    expect(within(select).getByRole("option", { name: "精準登陸與危險地形辨識" })).toBeInTheDocument();
    expect(screen.queryByText("研究權重預設")).not.toBeInTheDocument();
    expect(screen.queryByText(/初始 0 值不是 NASA 建議的目標/)).not.toBeInTheDocument();
    await user.selectOptions(select, "geological-fieldwork");
    expect(select).toHaveValue("geological-fieldwork");
  });

  it.each([
    { score_role: "context_only" }, { value_kind: "category" }, { dimension_id: "D02" },
  ])("rejects a required feature with incompatible capability metadata: %j", (override) => {
    expect(unavailableResearchFeatures(features.map((feature) => feature.id === "flat_terrain_fraction" ? { ...feature, ...override } : feature))).toEqual(["flat_terrain_fraction"]);
  });

  it("does not opt additional evidence/context features into numerical scoring", () => {
    const weights = researchWeights(RESEARCH_PRESETS[0], [...features, {
      ...features[0], id: "synthetic-extra-evidence", dimension_id: "D02", score_role: "evidence_only",
    }]);
    expect(weights["synthetic-extra-evidence"]).toBe("0");
    expect(Object.values(weights).filter((weight) => Number(weight) > 0)).toHaveLength(5);
    expect(researchWeights(RESEARCH_PRESETS[0], featuresFixture)).toEqual({});
  });

  it("places the selector beside the dimension heading in the result-limit header", () => {
    setup();
    const heading = screen.getByRole("heading", { name: "Dimensions" });
    const header = heading.closest(".board-head")!;
    expect(header).toContainElement(selector());
    expect(header).toContainElement(screen.getByRole("spinbutton", { name: "Maximum results (1–10)" }));
    expect(heading.parentElement).toContainElement(selector());
    expect(screen.queryByText("Research preset")).not.toBeInTheDocument();
    expect(document.querySelector(".research-presets")).not.toBeInTheDocument();
  });
});
