import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReferenceOut } from "../api/client";
import { I18nProvider } from "../i18n/I18nProvider";
import { contextFeaturesFixture, featuresFixture, referenceFixture } from "../test/fixtures";
import { installMockFetch, jsonResponse } from "../test/mock-api";
import { SearchForm } from "./SearchForm";

// Synthetic software fixtures shaped like GET /search/features; not observed data.
const role = (supported: boolean, reason: string | null = null) => ({
  supported,
  reason,
  constraints: [],
});

function registryEntry(overrides: Record<string, unknown>) {
  return {
    feature_id: "x",
    dimension_id: "D03",
    dimension_label: "Thermal Environment",
    label: "x",
    unit: "degree_C",
    value_kind: "numeric",
    body_scope: ["Mars"],
    score_role: "evidence_only",
    roles: {
      similarity: role(false),
      filter: role(false),
      hard_filter: role(false),
      category_match: role(false),
      evidence: role(true),
      uncertainty: role(false),
      context: role(false),
    },
    min_value: null,
    max_value: null,
    scale: null,
    scale_rationale: null,
    selector: {},
    source: {},
    support: {},
    method_versions: ["test"],
    measurement_family: null,
    missing_policy: "exclude",
    qa_rules: [],
    comparability_gates: [],
    registry_version: "test",
    uncertainty_feature_ids: [],
    unsupported_reason: null,
    coverage: null,
    ...overrides,
  };
}

const marsReference: ReferenceOut = {
  ...referenceFixture,
  reference_id: "mars-jezero-ctx-window-07",
  body: "Mars",
  display_name: "Jezero CTX expansion window 07",
  lat: 18.5,
  lon: 77.5,
  features: [
    {
      ...referenceFixture.features[0],
      feature_id: "mars_slope_mean_deg",
      label: "Mars mean slope",
      value: 11.696354,
      unit: "degree",
    },
  ],
};

function registryPayload(overrides: Record<string, unknown> = {}) {
  return {
    registry_version: "test",
    data_version: "test",
    filters: {},
    join_audit: {},
    audit_summary: {},
    notes: [],
    features: (overrides.features as unknown[]) ?? [
      registryEntry({
        feature_id: "mars_slope_mean_deg",
        dimension_id: "D01",
        dimension_label: "Terrain & Landforms",
        label: "Mars mean slope",
        unit: "degree",
        value_kind: "numeric",
        body_scope: ["Mars"],
        score_role: "similarity",
        roles: {
          similarity: role(true),
          filter: role(true),
          evidence: role(true),
        },
        min_value: 0,
        max_value: 90,
        scale: 10,
      }),
      registryEntry({
        feature_id: "surface_air_temperature_2m_c",
        dimension_id: "D03",
        label: "Surface air temperature",
        unit: "degree_C",
        value_kind: "timeseries",
        body_scope: ["Mars"],
        roles: {
          similarity: role(false),
          filter: role(true),
          hard_filter: role(false),
          evidence: role(true),
        },
        min_value: -100,
        max_value: 60,
        scale: 20,
        filter_kind: "range",
        coverage: {
          observation_count: 3,
          area_count: 3,
          by_body: { Mars: 3 },
          by_state: { detected: 3 },
          value_kinds: ["timeseries"],
          units: ["degree_C"],
          method_versions: ["test"],
          dataset_ids: ["test"],
          numeric_observation_count: 0,
          observed_numeric_min: null,
          observed_numeric_max: null,
          area_ids: [],
          joined_candidate_count: 0,
          joined_candidate_ids: [],
          gated_candidate_count: 0,
          gated_candidate_ids: [],
          rejected_observation_count: 0,
        },
      }),
      registryEntry({
        feature_id: "root_zone_soil_wetness_fraction",
        dimension_id: "D04",
        label: "Root-zone soil wetness",
        unit: "fraction",
        value_kind: "timeseries",
        body_scope: ["Mars"],
        roles: {
          similarity: role(false),
          filter: role(true),
          hard_filter: role(false),
          evidence: role(true),
        },
        min_value: 0,
        max_value: 1,
        scale: 0.25,
        filter_kind: "range",
        coverage: {
          observation_count: 2,
          area_count: 2,
          by_body: { Mars: 2 },
          by_state: { detected: 2 },
          value_kinds: ["timeseries"],
          units: ["fraction"],
          method_versions: ["test"],
          dataset_ids: ["test"],
          numeric_observation_count: 0,
          observed_numeric_min: null,
          observed_numeric_max: null,
          area_ids: [],
          joined_candidate_count: 0,
          joined_candidate_ids: [],
          gated_candidate_count: 0,
          gated_candidate_ids: [],
          rejected_observation_count: 0,
        },
      }),
      registryEntry({
        feature_id: "geologic_unit",
        dimension_id: "D02",
        label: "Geologic unit",
        unit: "text",
        value_kind: "category",
        body_scope: ["Mars"],
        roles: {
          similarity: role(false),
          filter: role(true),
          hard_filter: role(false),
          evidence: role(true),
        },
        filter_kind: "category",
        coverage: {
          observation_count: 2,
          area_count: 2,
          by_body: { Mars: 2 },
          by_state: { detected: 2 },
          value_kinds: ["category"],
          units: ["text"],
          method_versions: ["test"],
          dataset_ids: ["test"],
          numeric_observation_count: 0,
          observed_numeric_min: null,
          observed_numeric_max: null,
          category_value_counts: { "Basalt, Holocene": 1 },
          area_ids: [],
          joined_candidate_count: 0,
          joined_candidate_ids: [],
          gated_candidate_count: 0,
          gated_candidate_ids: [],
          rejected_observation_count: 0,
        },
      }),
      registryEntry({
        feature_id: "solar_elevation_deg",
        dimension_id: "D05",
        label: "Solar elevation",
        unit: "degree",
        value_kind: "numeric",
        body_scope: ["Mars"],
        roles: {
          similarity: role(false),
          filter: role(false),
          hard_filter: role(false),
          evidence: role(true),
        },
        unsupported_reason:
          "Time-dependent SPICE geometry; not a static surface property and not filterable.",
        coverage: {
          observation_count: 5,
          area_count: 3,
          by_body: { Mars: 5 },
          by_state: { detected: 5 },
          value_kinds: ["numeric"],
          units: ["degree"],
          method_versions: ["test"],
          dataset_ids: ["test"],
          numeric_observation_count: 5,
          observed_numeric_min: -10,
          observed_numeric_max: 30,
          area_ids: [],
          joined_candidate_count: 0,
          joined_candidate_ids: [],
          gated_candidate_count: 0,
          gated_candidate_ids: [],
          rejected_observation_count: 0,
        },
      }),
    ],
  };
}

function setup(features = registryPayload()) {
  installMockFetch({ searchFeatures: () => jsonResponse(features) });
  const onSearch = vi.fn();
  render(
    <I18nProvider initialLocale="en">
      <SearchForm
        features={featuresFixture}
        contextFeatures={contextFeaturesFixture}
        references={[marsReference]}
        referencesError={null}
        loading={false}
        onSearch={onSearch}
      />
    </I18nProvider>,
  );
  return { onSearch, user: userEvent.setup() };
}

async function selectMarsReference(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("radio", { name: /^Mars/ }));
  await user.click(screen.getByRole("button", { name: marsReference.display_name }));
}

describe("D02–D05 filter controls in each dimension card", () => {
  it("renders Mars D01 weight controls and D03/D04 range filters without adding them as targets", async () => {
    const { onSearch, user } = setup();
    await selectMarsReference(user);

    const d01 = screen.getByTestId("dimension-card-D01");
    const marsMetric = within(await within(d01).findByRole("group", { name: "Mars mean slope" }));
    const weight = marsMetric.getByRole("spinbutton", { name: "Weight (≥ 0)" });
    expect(weight).toBeEnabled();
    fireEvent.change(weight, { target: { value: "1" } });

    const d03 = screen.getByTestId("dimension-card-D03");
    const d03Lower = await within(d03).findByRole("slider", {
      name: "Surface air temperature lower bound",
    });
    fireEvent.change(d03Lower, { target: { value: "-20" } });
    expect(within(d03).queryByRole("spinbutton", { name: "Weight (≥ 0)" })).not.toBeInTheDocument();

    const d04 = screen.getByTestId("dimension-card-D04");
    const d04Upper = await within(d04).findByRole("slider", {
      name: "Root-zone soil wetness upper bound",
    });
    fireEvent.change(d04Upper, { target: { value: "0.4" } });

    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));

    expect(onSearch).toHaveBeenCalledTimes(1);
    const submission = onSearch.mock.calls[0][0];
    expect(submission.request).toMatchObject({
      body: "Mars",
      reference_id: marsReference.reference_id,
      target: {},
      weights: { mars_slope_mean_deg: 1 },
      filters: {
        surface_air_temperature_2m_c: { min: -20 },
        root_zone_soil_wetness_fraction: { max: 0.4 },
      },
    });
    expect(submission.request.target).not.toHaveProperty("surface_air_temperature_2m_c");
    expect(submission.request.weights).not.toHaveProperty("surface_air_temperature_2m_c");
    expect(submission.multidim?.targets ?? {}).not.toHaveProperty(
      "surface_air_temperature_2m_c",
    );
  });

  it("disables the D02 category filter for a Moon/Mars reference (Earth-only)", async () => {
    const { user } = setup();
    await selectMarsReference(user);

    const d02 = screen.getByTestId("dimension-card-D02");
    const select = await within(d02).findByRole("combobox", {
      name: "Geologic unit category filter",
    });
    expect(select).toBeDisabled();
    expect(within(d02).queryByRole("slider")).not.toBeInTheDocument();
    expect(within(d02).getByText(/Category filters are Earth-only/)).toBeInTheDocument();
  });

  it("sends the selected D02 category for an Earth custom target", async () => {
    const { onSearch, user } = setup();

    const slope = screen.getByRole("group", { name: "Mean slope" });
    fireEvent.change(within(slope).getByLabelText("Weight (≥ 0)"), {
      target: { value: "1" },
    });

    const d02 = screen.getByTestId("dimension-card-D02");
    const select = await within(d02).findByRole("combobox", {
      name: "Geologic unit category filter",
    });
    expect(select).toBeEnabled();
    await user.selectOptions(select, "Basalt, Holocene");
    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));

    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch.mock.calls[0][0].multidim?.category_filters).toEqual({
      geologic_unit: ["Basalt, Holocene"],
    });
  });

  it("shows an evidence reason instead of a slider when the registry has no filter role", async () => {
    const { user } = setup(
      registryPayload({
        features: [
          registryEntry({
            feature_id: "thermal_inertia",
            dimension_id: "D03",
            label: "Thermal inertia",
            unit: "J m-2 K-1 s-0.5",
            value_kind: "numeric",
            body_scope: ["Mars"],
            roles: {
              similarity: role(false),
              filter: role(false, "No validated thermal filter."),
              hard_filter: role(false),
              evidence: role(true),
            },
            unsupported_reason:
              "THEMIS thermal inertia uses a different method and support; no filter is registered.",
            coverage: {
              observation_count: 1,
              area_count: 1,
              by_body: { Mars: 1 },
              by_state: { detected: 1 },
              value_kinds: ["numeric"],
              units: ["J m-2 K-1 s-0.5"],
              method_versions: ["test"],
              dataset_ids: ["test"],
              numeric_observation_count: 1,
              observed_numeric_min: 200,
              observed_numeric_max: 200,
              area_ids: [],
              joined_candidate_count: 0,
              joined_candidate_ids: [],
              gated_candidate_count: 0,
              gated_candidate_ids: [],
              rejected_observation_count: 0,
            },
          }),
        ],
      }),
    );
    await selectMarsReference(user);

    const d03 = screen.getByTestId("dimension-card-D03");
    expect(await within(d03).findByText("Thermal inertia")).toBeInTheDocument();
    expect(within(d03).queryByRole("slider")).not.toBeInTheDocument();
    expect(
      within(d03).getByText(/no filter is registered/),
    ).toBeInTheDocument();
  });

  it("shows the D05 evidence reason and never renders a slider for it", async () => {
    const { user } = setup();
    await selectMarsReference(user);

    const d05 = screen.getByTestId("dimension-card-D05");
    expect(
      await within(d05).findByText(/Time-dependent SPICE geometry/),
    ).toBeInTheDocument();
    expect(within(d05).queryByRole("slider")).not.toBeInTheDocument();
    expect(within(d05).queryByRole("combobox")).not.toBeInTheDocument();
  });
});
