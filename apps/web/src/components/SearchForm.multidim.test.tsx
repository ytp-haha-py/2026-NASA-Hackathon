import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n/I18nProvider";
import { contextFeaturesFixture, featuresFixture } from "../test/fixtures";
import { installMockFetch, jsonResponse } from "../test/mock-api";
import { SearchForm } from "./SearchForm";

// Synthetic registry fixture shaped like GET /search/features; not observed data.
const role = (supported: boolean) => ({ supported, reason: null, constraints: [] });
function registryEntry(overrides: Record<string, unknown>) {
  return {
    feature_id: "x",
    dimension_id: "D06",
    dimension_label: "Regolith & Surface Properties",
    label: "x",
    unit: "g kg-1",
    value_kind: "numeric",
    body_scope: ["Earth"],
    score_role: "similarity",
    roles: {
      similarity: role(true),
      hard_filter: role(true),
      category_match: role(false),
      evidence: role(true),
      uncertainty: role(true),
      context: role(false),
    },
    min_value: 0,
    max_value: 1000,
    scale: 200,
    scale_rationale: null,
    selector: {},
    source: {},
    support: {},
    method_versions: ["soilgrids-test"],
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
const registry = {
  registry_version: "test",
  data_version: "test",
  filters: {},
  join_audit: {},
  audit_summary: {},
  notes: [],
  features: [
    registryEntry({ feature_id: "soil_clay_0_5cm_mean", label: "soil clay 0 5cm mean" }),
    registryEntry({
      feature_id: "context_d2300",
      dimension_id: "D02",
      label: "context d2300",
      unit: "dimensionless_spectral_parameter",
      body_scope: ["Mars"],
      roles: { similarity: role(false), hard_filter: role(false), evidence: role(true) },
      min_value: null,
      max_value: null,
      scale: null,
      coverage: {
        observation_count: 2,
        by_body: { Mars: 2 },
        observed_numeric_min: 0.006,
        observed_numeric_max: 0.0128,
      },
    }),
  ],
};

function setup() {
  installMockFetch({ searchFeatures: () => jsonResponse(registry) });
  const onSearch = vi.fn();
  render(
    <I18nProvider initialLocale="en">
      <SearchForm
        features={featuresFixture}
        contextFeatures={contextFeaturesFixture}
        references={[]}
        referencesError={null}
        loading={false}
        onSearch={onSearch}
      />
    </I18nProvider>,
  );
  return { onSearch, user: userEvent.setup() };
}

describe("D02–D06 in the dimension board", () => {
  it("gives registry similarity features the same controls as D01 and searches them multidimensionally", async () => {
    const { onSearch, user } = setup();
    const d06 = screen.getByTestId("dimension-card-D06");
    const clay = within(await within(d06).findByRole("group", { name: "Clay (0–5 cm)" }));
    fireEvent.change(clay.getByRole("spinbutton", { name: "Weight (≥ 0)" }), { target: { value: 1 } });
    fireEvent.change(clay.getByRole("spinbutton", { name: /Target value/ }), { target: { value: 200 } });

    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));

    expect(onSearch).toHaveBeenCalledTimes(1);
    const submission = onSearch.mock.calls[0][0];
    expect(submission.multidim).toMatchObject({
      candidate_body: "Earth",
      targets: { soil_clay_0_5cm_mean: { value: 200, weight: 1 } },
    });
  });

  it("keeps D01-only searches on the existing endpoint", async () => {
    const { onSearch, user } = setup();
    await within(screen.getByTestId("dimension-card-D06")).findByRole("group", { name: "Clay (0–5 cm)" });
    const slope = within(screen.getByRole("group", { name: "Mean slope" }));
    fireEvent.change(slope.getByRole("spinbutton", { name: "Weight (≥ 0)" }), { target: { value: 1 } });
    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));
    expect(onSearch.mock.calls[0][0].multidim).toBeUndefined();
  });

  it("refuses land-cover filters together with regolith targets instead of dropping them", async () => {
    const { onSearch, user } = setup();
    const clay = within(
      await within(screen.getByTestId("dimension-card-D06")).findByRole("group", { name: "Clay (0–5 cm)" }),
    );
    fireEvent.change(clay.getByRole("spinbutton", { name: "Weight (≥ 0)" }), { target: { value: 1 } });
    fireEvent.change(clay.getByRole("spinbutton", { name: /Target value/ }), { target: { value: 200 } });
    fireEvent.change(screen.getByRole("slider", { name: "Built-up fraction upper bound" }), {
      target: { value: 0.2 },
    });
    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));
    expect(onSearch).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/Land-cover filters can't be combined/);
  });

  it("lists observed D02–D05 features read-only with bodies and counts", async () => {
    setup();
    const d02 = screen.getByTestId("dimension-card-D02");
    expect(await within(d02).findByText("Context d2300")).toBeInTheDocument();
    expect(within(d02).getByText("2 obs.")).toBeInTheDocument();
    expect(within(d02).queryByRole("slider")).not.toBeInTheDocument();
  });
});
