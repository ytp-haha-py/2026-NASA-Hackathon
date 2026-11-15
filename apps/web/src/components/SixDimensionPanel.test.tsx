import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../i18n/I18nProvider";
import {
  datasetFixture,
  emptyTypedAreaFixture,
  featuresFixture,
  searchResponseFixture,
} from "../test/fixtures";
import { errorResponse, installMockFetch, jsonResponse } from "../test/mock-api";
import {
  SixDimensionPanel,
  fineValuesFromSearchResult,
  type SixDimensionTarget,
} from "./SixDimensionPanel";

const selected = searchResponseFixture.results[0];

function target(): SixDimensionTarget {
  return {
    id: selected.location_id,
    label: selected.display_name,
    kind: "candidate",
    body: "Earth",
    fineValues: fineValuesFromSearchResult(selected.metrics, featuresFixture),
  };
}

function renderPanel(targets: SixDimensionTarget[] = [target()]) {
  return render(
    <I18nProvider initialLocale="en">
      <SixDimensionPanel
        features={featuresFixture}
        datasets={[datasetFixture]}
        targets={targets}
      />
    </I18nProvider>,
  );
}

describe("SixDimensionPanel", () => {
  it("renders D01–D06 from API records with state and role kept separate", async () => {
    installMockFetch();
    renderPanel();

    expect(
      await screen.findByRole("region", { name: "Geology & Mineralogy" }),
    ).toBeInTheDocument();

    const d01 = screen.getByRole("region", { name: "Terrain & Landforms" });
    expect(within(d01).getByText("Mean slope")).toBeInTheDocument();
    expect(within(d01).getByText("12.4 deg")).toBeInTheDocument();
    expect(
      within(d01).getAllByText("Similarity — same-support score input").length,
    ).toBeGreaterThan(0);

    const d02 = screen.getByRole("region", { name: "Geology & Mineralogy" });
    expect(within(d02).getAllByText("Unknown").length).toBeGreaterThan(0);
    expect(
      within(d02).getAllByText("Evidence only — no similarity").length,
    ).toBeGreaterThan(0);
    expect(within(d02).getByText(/No numeric value is displayed/)).toBeInTheDocument();
    expect(within(d02).queryByText("0")).not.toBeInTheDocument();
    expect(within(d02).getByText("DEMO / FIXTURE typed data — not a real observation."))
      .toBeInTheDocument();

    const d03 = screen.getByRole("region", { name: "Thermal Environment" });
    expect(
      within(d03).getAllByText("Context only — never scored").length,
    ).toBeGreaterThan(0);
    expect(within(d03).getByText("2024-01-01")).toBeInTheDocument();
    expect(within(d03).getByText("11.5 degree_C")).toBeInTheDocument();

    const d04 = screen.getByRole("region", { name: "Water & Ice" });
    expect(within(d04).getAllByText("Context only — never scored").length).toBeGreaterThan(0);
    expect(within(d04).getByText("0.21 fraction")).toBeInTheDocument();
    expect(within(d04).getByText(/no confirmed ice/)).toBeInTheDocument();

    const d05 = screen.getByRole("region", { name: "Solar Illumination & Radiation" });
    expect(within(d05).getAllByText("Not detected").length).toBeGreaterThan(0);
    expect(
      within(d05).getByText(/No numeric value is displayed because the API state is Not detected/),
    ).toBeInTheDocument();

    const d06 = screen.getByRole("region", { name: "Regolith & Surface Properties" });
    expect(within(d06).getByText("131.5 cg cm-3")).toBeInTheDocument();
    expect(within(d06).getByText("0 – 0.05 m")).toBeInTheDocument();

    expect(
      screen.getByText(/No six-dimension total score exists/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/six-dimension score:/i)).not.toBeInTheDocument();
  });

  it("renders every missing dimension explicitly when the area has no typed records", async () => {
    installMockFetch({
      typedArea: () => jsonResponse(emptyTypedAreaFixture),
    });
    renderPanel();

    expect(
      await screen.findAllByText("No typed observation was returned for this dimension."),
    ).toHaveLength(6);
    expect(screen.queryByText("131.5 cg cm-3")).not.toBeInTheDocument();
  });

  it("shows an explicit no-record state for HTTP 404 and still lists D01–D06", async () => {
    installMockFetch({
      typedArea: () =>
        errorResponse(404, "not_found", "Typed area earth-observed-1 was not found."),
    });
    renderPanel();

    expect(
      await screen.findByText(
        "No typed-v2 area record was stored for earth-observed-1. Nothing is substituted.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("No typed observation was returned for this dimension."),
    ).toHaveLength(6);
  });

  it("renders an API failure without inventing observations", async () => {
    installMockFetch({
      typedArea: () =>
        errorResponse(500, "internal_error", "Typed store unavailable."),
    });
    renderPanel();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Typed observations failed (internal_error).");
    expect(alert).toHaveTextContent("Typed store unavailable.");
    expect(screen.queryByText("131.5 cg cm-3")).not.toBeInTheDocument();
    expect(screen.queryByText("12.4 deg")).not.toBeInTheDocument();
  });

  it("renders state and role labels in Traditional Chinese", async () => {
    installMockFetch();
    render(
      <I18nProvider initialLocale="zh-TW">
        <SixDimensionPanel features={featuresFixture} targets={[target()]} />
      </I18nProvider>,
    );

    const d02 = await screen.findByRole("region", { name: "地質與礦物" });
    expect(within(d02).getAllByText("未知").length).toBeGreaterThan(0);
    expect(
      within(d02).getAllByText("僅供證據 — 不計相似度").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/本工具沒有六維總分/)).toBeInTheDocument();
  });

  it("does not fill a dimension when no area is selected", async () => {
    installMockFetch();
    renderPanel([]);

    expect(
      await screen.findByText(/Select a ranked Earth candidate, a Moon or Mars reference/),
    ).toBeInTheDocument();
    expect(screen.getByText("Typed record coverage in this database")).toBeInTheDocument();
    expect(
      screen.getAllByRole("region", { name: /Terrain|Geology|Thermal|Water|Solar|Regolith/ }),
    ).toHaveLength(6);
    expect(
      screen.getAllByText("No typed observation was returned for this dimension."),
    ).toHaveLength(6);
    expect(screen.queryByText("131.5 cg cm-3")).not.toBeInTheDocument();
  });
});
