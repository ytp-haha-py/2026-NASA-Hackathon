import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { App } from "./App";
import type { CoarseMapResults } from "./components/MapPanel";
import { emptyTypedAreaFixture, referenceFixture, searchResponseFixture, typedAreaFixture } from "./test/fixtures";
import { installMockFetch, jsonResponse, type FetchHandlers } from "./test/mock-api";
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

function requestUrls(fetchMock: ReturnType<typeof installMockFetch>): string[] {
  return fetchMock.mock.calls.map(([input]) =>
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url,
  );
}

async function renderReady(handlers: FetchHandlers = {}) {
  const fetchMock = installMockFetch(handlers);
  render(<App aoiPollIntervalMs={10} />);
  await screen.findByRole("button", { name: "Six-dimension comparison" });
  await applyExampleTarget();
  return fetchMock;
}

describe("six-dimension workspace integration", () => {
  it("loads typed observations for the selected candidate and keeps roles separate", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady({
      typedArea: () => jsonResponse(typedAreaFixture),
    });

    await user.click(
      screen.getByRole("button", { name: "Search indexed Earth candidates" }),
    );
    await user.click(
      await screen.findByRole("button", {
        name: "Select rank 1: Observed test window",
      }),
    );

    await user.click(screen.getByRole("button", { name: "Six-dimension comparison" }));

    expect(
      await screen.findByRole("heading", { name: "D01–D06 observations for one area" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Six-dimension comparison" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    const d06 = await screen.findByRole("region", {
      name: "Regolith & Surface Properties",
    });
    expect(within(d06).getByText("131.5 cg cm-3")).toBeInTheDocument();

    const d02 = screen.getByRole("region", { name: "Geology & Mineralogy" });
    expect(
      within(d02).getAllByText("Evidence only — no similarity").length,
    ).toBeGreaterThan(0);

    await waitFor(() => {
      expect(
        requestUrls(fetchMock).some((url) =>
          url.endsWith("/api/v1/typed/areas/earth-observed-1"),
        ),
      ).toBe(true);
    });
    expect(
      screen.getByRole("button", { name: /Candidate · Observed test window/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("uses a selected Moon reference as the six-dimension area", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady({
      references: () => jsonResponse([referenceFixture]),
      typedArea: () => jsonResponse(emptyTypedAreaFixture),
    });

    await user.click(screen.getByRole("radio", { name: /^Moon/ }));
    await user.click(screen.getByRole("button", { name: referenceFixture.display_name }));
    await user.click(screen.getByRole("button", { name: "Six-dimension comparison" }));

    expect(
      await screen.findByRole("button", {
        name: /Reference · Shackleton Rim \(LOLA Site04 ROI 2\)/,
      }),
    ).toHaveAttribute("aria-pressed", "true");

    await waitFor(() => {
      expect(
        requestUrls(fetchMock).some((url) =>
          url.endsWith(
            `/api/v1/typed/areas/${encodeURIComponent("moon-shackleton-rim-site04-roi2")}`,
          ),
        ),
      ).toBe(true);
    });
    expect(
      screen.getAllByText("No typed observation was returned for this dimension."),
    ).toHaveLength(6);
  });
});

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
    expect(resultsArea.getByText("Coarse and fine are separate methods")).toBeInTheDocument();
    expect(
      screen.getByText("local_coarse_index_not_global_exhaustive"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search coarse index" })).toBeInTheDocument();
  });
});
