import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n/I18nProvider";
import {
  globalCoverageFixture,
  globalEmptySearchResponseFixture,
  globalNoIndexCoverageFixture,
} from "../test/fixtures";
import { errorResponse, installMockFetch, jsonResponse } from "../test/mock-api";
import { GlobalPanel } from "./GlobalPanel";

vi.mock("./GlobalMap", () => ({
  GlobalMap: ({
    results,
    selectedId,
  }: {
    results: { candidate_id: string }[];
    selectedId: string | null;
  }) => (
    <div data-testid="global-map-stub" data-selected={selectedId ?? ""}>
      {results.length} coarse candidates plotted
    </div>
  ),
}));

function renderPanel() {
  return render(
    <I18nProvider initialLocale="en">
      <GlobalPanel />
    </I18nProvider>,
  );
}

async function searchCoarse() {
  const user = userEvent.setup();
  await user.type(
    await screen.findByLabelText("Coarse mean slope (250 m source) Target value"),
    "10",
  );
  await user.click(screen.getByRole("button", { name: "Search coarse index" }));
  return user;
}

describe("GlobalPanel", () => {
  it("shows coverage, coarse candidates, candidate detail and a plan-only refine result", async () => {
    const fetchMock = installMockFetch();
    renderPanel();

    expect(await screen.findByText("Verified coarse coverage")).toBeInTheDocument();
    expect(screen.getByText("fixture-coarse-2026-11")).toBeInTheDocument();
    expect(screen.getByText("DEMO / FIXTURE index — this is not a real global survey."))
      .toBeInTheDocument();
    expect(
      screen.getByText("250 m source → 5 km coarse candidate (coarse-terrain-250m-1.0.0)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("90 m source → about 1.98 km fine window (terrain-metrics-1.0.0)"),
    ).toBeInTheDocument();

    const user = await searchCoarse();

    expect(await screen.findByTestId("global-result-summary")).toHaveTextContent(
      "2 coarse candidates from 9 eligible rows (12 indexed; 3 excluded)",
    );
    expect(screen.getByText("This is not a global Top-10.")).toBeInTheDocument();
    expect(screen.getByTestId("global-map-stub")).toHaveTextContent(
      "2 coarse candidates plotted",
    );
    const cards = screen.getAllByTestId(/^global-candidate-fixture-candidate-/);
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("fixture-candidate-001");
    expect(cards[1]).toHaveTextContent("fixture-candidate-002");

    await user.click(
      screen.getByRole("button", { name: "Inspect coarse candidate fixture-candidate-001" }),
    );

    expect(await screen.findByText("Selected coarse candidate")).toBeInTheDocument();
    const detail = await screen.findByTestId("global-candidate-detail");
    expect(within(detail).getByText("fixture-candidate-001")).toBeInTheDocument();
    expect(within(detail).getByText(/5,000 m window/)).toBeInTheDocument();
    expect(within(detail).getByText("geomorpho250-fixture")).toBeInTheDocument();
    expect(within(detail).getByText("fixture_coarse_row")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create refine plan" }));

    const plan = await screen.findByTestId("refine-plan");
    expect(within(plan).getByText("PLAN ONLY — no execution result exists."))
      .toBeInTheDocument();
    expect(within(plan).getByText("fixture-candidate-001:fine:0")).toBeInTheDocument();
    expect(
      within(plan).getByText(/Fine execution is not offered because the API does not return/),
    ).toBeInTheDocument();
    expect(within(plan).queryByRole("button", { name: /execute/i })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalled();

    // Selecting another candidate replaces the detail and clears the previous plan.
    await user.click(
      screen.getByRole("button", { name: "Inspect coarse candidate fixture-candidate-002" }),
    );
    const nextDetail = await screen.findByTestId("global-candidate-detail");
    await waitFor(() => {
      expect(within(nextDetail).getByText("fixture-candidate-002")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("refine-plan")).not.toBeInTheDocument();
  });

  it("shows a partial-coverage label and exclusion counts without upgrading it to global coverage", async () => {
    installMockFetch({
      globalCoverage: () =>
        jsonResponse({
          ...globalCoverageFixture,
          snapshot_id: "partial-fixture-coarse",
          label: "partial_coverage_not_global",
          row_count: 3,
          note: "Only part of the requested region is indexed.",
        }),
    });
    renderPanel();

    expect(await screen.findByText("partial_coverage_not_global")).toBeInTheDocument();
    expect(screen.getByText("Only part of the requested region is indexed."))
      .toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/never rescaled or compared as percentages/))
      .toBeInTheDocument();
  });

  it("rejects an empty target without calling the search endpoint", async () => {
    const fetchMock = installMockFetch();
    renderPanel();
    await screen.findByText("Verified coarse coverage");

    await userEvent.setup().click(
      screen.getByRole("button", { name: "Search coarse index" }),
    );

    expect(
      await screen.findByText("Enter at least one finite coarse target value."),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/api/v1/global/search"),
      ),
    ).toBe(false);
  });

  it("shows the explicit no_index state without fabricating a search or candidates", async () => {
    installMockFetch({ globalCoverage: () => jsonResponse(globalNoIndexCoverageFixture) });
    renderPanel();

    expect(await screen.findByText("No verified global index")).toBeInTheDocument();
    expect(
      screen.getByText(/No candidate rows are fabricated, and no global ranking is claimed/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Search coarse index" }))
      .not.toBeInTheDocument();
    expect(screen.queryByTestId("global-candidate-fixture-candidate-001"))
      .not.toBeInTheDocument();
  });

  it("shows an empty result state with exclusion counts", async () => {
    installMockFetch({
      globalSearch: () => jsonResponse(globalEmptySearchResponseFixture),
    });
    renderPanel();

    await searchCoarse();

    expect(await screen.findByText("No eligible coarse candidates")).toBeInTheDocument();
    expect(screen.getByText("12 candidate rows were excluded.")).toBeInTheDocument();
    expect(screen.queryByTestId("global-map-stub")).not.toBeInTheDocument();
    expect(screen.queryByTestId("global-candidate-fixture-candidate-001"))
      .not.toBeInTheDocument();
  });

  it("shows a 409 coarse-search failure and retries the same request", async () => {
    const fetchMock = installMockFetch({
      globalSearch: () =>
        errorResponse(409, "no_indexed_data", "No verified coarse index is configured."),
    });
    renderPanel();

    await searchCoarse();

    const alert = (await screen.findByText("Coarse search failed.")).closest(
      "div.alert",
    ) as HTMLElement;
    expect(alert).toHaveTextContent("Coarse search failed.");
    expect(alert).toHaveTextContent("no_indexed_data");
    expect(alert).toHaveTextContent("No verified coarse index is configured.");
    expect(screen.queryByTestId("global-result-summary")).not.toBeInTheDocument();

    const before = fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith("/api/v1/global/search"),
    ).length;
    await userEvent.setup().click(
      within(alert).getByRole("button", { name: "Search coarse index" }),
    );
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input).endsWith("/api/v1/global/search"),
        ).length,
      ).toBe(before + 1);
    });
  });

  it("renders the coverage and fixture warning in Traditional Chinese", async () => {
    installMockFetch();
    render(
      <I18nProvider initialLocale="zh-TW">
        <GlobalPanel />
      </I18nProvider>,
    );

    expect(await screen.findByText("已驗證的粗篩覆蓋")).toBeInTheDocument();
    expect(
      screen.getByText("示範／測試索引 — 這不是真實全球普查。"),
    ).toBeInTheDocument();
    expect(screen.getByText("250 m 粗篩索引 → 5 km 候選")).toBeInTheDocument();
  });

  it("retries coverage after an API failure and never substitutes a candidate list", async () => {
    let attempts = 0;
    installMockFetch({
      globalCoverage: () => {
        attempts += 1;
        if (attempts === 1) {
          throw new TypeError("Failed to fetch");
        }
        return jsonResponse({
          ...globalNoIndexCoverageFixture,
          note: "Second coverage attempt.",
        });
      },
    });
    renderPanel();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Global coverage failed.");
    expect(alert).toHaveTextContent("Failed to fetch");
    expect(screen.queryByTestId("global-result-summary")).not.toBeInTheDocument();

    await userEvent.setup().click(
      within(alert).getByRole("button", { name: "Retry global coverage" }),
    );

    expect(await screen.findByText("No verified global index")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });
});
