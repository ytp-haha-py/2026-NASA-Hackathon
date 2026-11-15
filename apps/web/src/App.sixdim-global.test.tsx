import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { App } from "./App";
import type { CoarseMapResults } from "./components/MapPanel";
import { searchResponseFixture } from "./test/fixtures";
import { installMockFetch, type FetchHandlers } from "./test/mock-api";
import { applyExampleTarget } from "./test/example-target";

vi.mock("./components/MapPanel", () => ({
  MapPanel: ({ results, onSelect, searchScope, coarse }: {
    results: typeof searchResponseFixture.results;
    onSelect: (id: string) => void;
    searchScope: ReactNode;
    coarse?: CoarseMapResults;
  }) => (
    <div data-testid="map-stub">
      {searchScope}
      {coarse?.content ?? `${results.length} windows plotted`}
      {results.map((result) => (
        <button key={result.location_id} onClick={() => onSelect(result.location_id)}>
          Select rank {result.rank}: {result.display_name}
        </button>
      ))}
    </div>
  ),
}));

async function renderReady(handlers: FetchHandlers = {}) {
  const fetchMock = installMockFetch(handlers);
  render(<App aoiPollIntervalMs={10} />);
  await screen.findByRole("heading", { name: "Describe a terrain-shape target" });
  await applyExampleTarget();
  return fetchMock;
}

describe("global discovery integration", () => {
  it("loads coverage and labels fixture data without claiming global coverage", async () => {
    const user = userEvent.setup();
    await renderReady();

    const resultsArea = within(screen.getByTestId("map-stub"));
    await user.click(await resultsArea.findByRole("button", { name: "Expanded search" }));
    await user.click(resultsArea.getByText("Coarse search settings and coverage"));
    expect(screen.queryByRole("button", { name: "Global coarse screening and refinement" }))
      .not.toBeInTheDocument();

    expect(await screen.findByText("Verified coarse coverage")).toBeInTheDocument();
    expect(
      screen.getByText("DEMO / FIXTURE index — this is not a real global survey."),
    ).toBeInTheDocument();
    expect(screen.getByText("Coarse and fine are separate methods")).toBeInTheDocument();
    expect(
      screen.getByText("local_coarse_index_not_global_exhaustive"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search coarse index" })).toBeInTheDocument();
  });
});
