import { describe, expect, it } from "vitest";
import type { FeatureOut } from "../api/client";
import { dimensionCards } from "./dimensions";

function feature(id: string, dimensionId = "terrain_shape"): FeatureOut {
  return {
    id,
    label: id,
    unit: "degree",
    description: "",
    method: "",
    method_version: "terrain-metrics-1.0.0",
    min_value: 0,
    max_value: 90,
    scale: 10,
    scale_rationale: "",
    extraction_scale_m: 90,
    valid_domain: "",
    missing_policy: "",
    dimension_id: dimensionId,
    value_kind: "numeric",
    score_role: "similarity",
    spatial_support: "",
    temporal_support: "",
    provider_coverage_by_body: {},
    comparability_gate: "",
  } as FeatureOut;
}

describe("fixed formal dimension cards", () => {
  it("always returns D01–D06 in ontology order even when API features are reversed", () => {
    const cards = dimensionCards([feature("slope_p90_deg"), feature("slope_mean_deg")], null);
    expect(cards.map((card) => card.code)).toEqual([
      "D01",
      "D02",
      "D03",
      "D04",
      "D05",
      "D06",
    ]);
    expect(cards[0].features.map((item) => item.id)).toEqual([
      "slope_p90_deg",
      "slope_mean_deg",
    ]);
  });

  it("does not invent a slot from the API order when a dimension is missing", () => {
    const cards = dimensionCards([feature("slope_mean_deg")], {
      by_dimension: {
        D06: { total: 300, by_body: { Earth: 300 }, by_state: { detected: 300 } },
      },
    });
    expect(cards.find((card) => card.code === "D06")).toMatchObject({
      total: 300,
      role: "evidence_only",
      byBody: { Earth: 300 },
    });
    expect(cards.find((card) => card.code === "D02")?.total).toBe(0);
  });

  it("keeps a non-D01 API feature out of D01 and reports typed kinds", () => {
    const cards = dimensionCards([feature("odd_feature", "other")], {
      by_dimension: {
        D02: { total: 2, by_body: { Mars: 2 }, by_state: { unknown: 2 } },
      },
    }, [
      { dimension_code: "D02", value_kind: "category" },
      { dimension_code: "D02", value_kind: "detection" },
      { dimension_code: "D02", value_kind: "category" },
    ]);
    expect(cards[0].features).toHaveLength(1);
    expect(cards[1]).toMatchObject({
      code: "D02",
      total: 2,
      role: "evidence_only",
      byBody: { Mars: 2 },
      valueKinds: ["category", "detection"],
    });
  });
});
