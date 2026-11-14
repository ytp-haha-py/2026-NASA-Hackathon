import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import {
  analyzeQueuedFixture,
  capabilitiesFixture,
  aoiLocationFixture,
  emptySearchResponseFixture,
  jobCompletedFixture,
  jobFailedFixture,
  jobPartialFixture,
  jobRunningFixture,
  noCoverageCapabilitiesFixture,
  referenceFixture,
  searchResponseFixture,
} from "./test/fixtures";
import {
  contextUrlFromCalls,
  errorResponse,
  installMockFetch,
  jsonResponse,
  locationCallCount,
  searchRequestFromCalls,
  type FetchHandlers,
} from "./test/mock-api";

vi.mock("./components/MapPanel", () => ({
  MapPanel: ({
    results,
    selectedId,
  }: {
    results: { location_id: string }[];
    selectedId: string | null;
  }) => (
    <div data-testid="map-stub" data-selected={selectedId ?? ""}>
      {results.length} windows plotted
    </div>
  ),
}));

async function renderReady(handlers: FetchHandlers = {}) {
  const fetchMock = installMockFetch(handlers);
  render(<App aoiPollIntervalMs={10} />);
  await screen.findByRole("heading", { name: "Describe a terrain-shape target" });
  return fetchMock;
}

async function runSearch() {
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Search indexed Earth candidates" }),
  );
  return user;
}

function deferredResponse() {
  let resolve!: (response: Response) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<Response>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function requestUrls(fetchMock: ReturnType<typeof installMockFetch>): string[] {
  return fetchMock.mock.calls.map(([input]) =>
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url,
  );
}

async function openAoiTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: "Analyze AOI" }));
}

describe("search form", () => {
  it("builds a 422-safe request body from the prefilled hypothetical target", async () => {
    const fetchMock = await renderReady();
    expect(
      screen.getByText(/Hypothetical example, not a NASA measurement/),
    ).toBeInTheDocument();

    await runSearch();

    await screen.findByTestId("coverage-headline");
    const body = searchRequestFromCalls(fetchMock);

    expect(body).toEqual({
      body: "custom",
      reference_id: null,
      target: {
        slope_mean_deg: { value: 12, weight: 1 },
        terrain_relief_p95_p05_m: { value: 80, weight: 0.5 },
      },
      filters: {},
      limit: 10,
    });
    expect(body?.target).not.toHaveProperty("slope_p90_deg");
    expect(body?.target).not.toHaveProperty("roughness_std_m");
  });

  it("rejects out-of-domain values and zero total weight without calling search", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady();

    const slopeGroup = screen.getByRole("group", { name: "Mean slope" });
    const slopeValue = within(slopeGroup).getByLabelText("Target value (degree)");
    await user.clear(slopeValue);
    await user.type(slopeValue, "95");

    await runSearch();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Mean slope value: Enter a value between 0 and 90 degree.",
    );
    expect(searchRequestFromCalls(fetchMock)).toBeNull();

    await user.clear(slopeValue);
    await user.type(slopeValue, "12");

    await user.clear(within(slopeGroup).getByLabelText("Weight (≥ 0)"));
    await user.type(within(slopeGroup).getByLabelText("Weight (≥ 0)"), "0");

    const reliefGroup = screen.getByRole("group", {
      name: "Terrain relief (p95-p05)",
    });
    await user.clear(within(reliefGroup).getByLabelText("Weight (≥ 0)"));
    await user.type(within(reliefGroup).getByLabelText("Weight (≥ 0)"), "0");

    await runSearch();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Choose at least one metric with a positive weight.",
    );
    expect(searchRequestFromCalls(fetchMock)).toBeNull();
  });

  it("disables Moon and Mars presets when no reference records exist", async () => {
    await renderReady();

    const moon = screen.getByRole("option", { name: /Moon reference/ });
    const mars = screen.getByRole("option", { name: /Mars reference/ });

    expect((moon as HTMLOptionElement).disabled).toBe(true);
    expect((mars as HTMLOptionElement).disabled).toBe(true);
    expect(
      screen.getByText(/Moon and Mars presets are unavailable/),
    ).toBeInTheDocument();
  });

  it("enables a body preset only when a matching reference record exists", async () => {
    await renderReady({
      references: () => jsonResponse([referenceFixture]),
    });

    const moon = screen.getByRole("option", { name: /Moon reference/ });
    const mars = screen.getByRole("option", { name: /Mars reference/ });

    expect((moon as HTMLOptionElement).disabled).toBe(false);
    expect((mars as HTMLOptionElement).disabled).toBe(true);
    expect(
      screen.getByRole("option", {
        name: /Moon · Shackleton Rim \(LOLA Site04 ROI 2\)/,
      }),
    ).toBeInTheDocument();
  });
});

describe("search states and results", () => {
  it("shows a loading status, then honest coverage and synthetic warnings", async () => {
    let resolveSearch: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveSearch = resolve;
    });
    const fetchMock = await renderReady({ search: () => pending });

    await runSearch();

    expect(
      await screen.findByText("Searching searchable Earth regions…"),
    ).toBeInTheDocument();

    await act(async () => {
      resolveSearch?.(jsonResponse(searchResponseFixture));
    });

    expect(await screen.findByTestId("coverage-headline")).toHaveTextContent(
      "Top 2 among 4 indexed regions",
    );
    expect(screen.getByText(/This is not a global top 10/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "1 of 2 returned record is DEMO / SYNTHETIC",
    );

    const observedCard = screen.getByTestId("result-earth-observed-1");
    expect(within(observedCard).getByText("Observed")).toBeInTheDocument();
    expect(within(observedCard).getByText("0.037")).toBeInTheDocument();
    expect(within(observedCard).getByText("12.4 deg")).toBeInTheDocument();
    expect(within(observedCard).getByText("12 deg")).toBeInTheDocument();
    expect(within(observedCard).getByText("+0.4 deg")).toBeInTheDocument();

    const syntheticCard = screen.getByTestId("result-earth-synthetic-2");
    expect(within(syntheticCard).getByText("DEMO / SYNTHETIC")).toBeInTheDocument();

    expect(
      screen.getByText("1 excluded candidate — show reasons"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Missing required metric: 90th percentile slope/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalled();
  });

  it("renders a functional HTTP 409 no-indexed-data empty state", async () => {
    await renderReady({
      search: () =>
        errorResponse(
          409,
          "no_indexed_data",
          "The Earth candidate index is empty. Run the documented seed/ingestion steps first.",
        ),
    });

    await runSearch();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No indexed Earth data (HTTP 409)");
    expect(alert).toHaveTextContent(/does not invent fallback data|index is empty/);
    expect(screen.queryByTestId("coverage-headline")).not.toBeInTheDocument();
  });

  it("renders a zero-eligible empty state with exclusion reasons", async () => {
    await renderReady({
      search: () => jsonResponse(emptySearchResponseFixture),
    });

    await runSearch();

    expect(
      await screen.findByText("No eligible indexed candidates"),
    ).toBeInTheDocument();
    expect(screen.getByText(/This does not mean no real place/)).toBeInTheDocument();
    expect(screen.getByText(/3 candidates were excluded/)).toBeInTheDocument();
  });

  it("renders a network search error instead of substituting results", async () => {
    await renderReady({
      search: () => {
        throw new TypeError("Failed to fetch");
      },
    });

    await runSearch();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Search failed");
    expect(alert).toHaveTextContent("Failed to fetch");
    expect(screen.queryByTestId("coverage-headline")).not.toBeInTheDocument();
  });

  it("shows a no-coverage state when the index reports zero windows", async () => {
    await renderReady({
      capabilities: () => jsonResponse(noCoverageCapabilitiesFixture),
      references: () => jsonResponse([]),
      datasets: () => jsonResponse([]),
    });

    expect(
      screen.getByRole("heading", { name: "0 searchable Earth regions" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/No searchable Earth candidates are available/),
    ).toBeInTheDocument();
    expect(screen.getByText(/No geographic coverage extent is available/)).toBeInTheDocument();
  });
});

describe("selection, map state and context export", () => {
  it("updates detail and map/list selection without duplicating repeated clicks", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady();

    await runSearch();

    const selectButton = await screen.findByRole("button", {
      name: "Select rank 1: Observed test window",
    });
    await user.click(selectButton);

    expect(
      await screen.findByRole("heading", { name: "Selected Earth candidate" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Observed terrain-shape features")).toBeInTheDocument();
    expect(screen.getByTestId("map-stub")).toHaveAttribute(
      "data-selected",
      "earth-observed-1",
    );
    expect(screen.getByTestId("result-earth-observed-1")).toHaveAttribute(
      "data-selected",
      "true",
    );
    expect(
      within(
        screen.getByTestId("result-earth-observed-1"),
      ).getByRole("button", { name: "Select rank 1: Observed test window" }),
    ).toHaveAttribute("aria-pressed", "true");

    await user.click(selectButton);

    expect(locationCallCount(fetchMock, "earth-observed-1")).toBe(1);
    expect(
      screen.getAllByRole("heading", { name: "Selected Earth candidate" }),
    ).toHaveLength(1);
  });

  it("requests the grounded context export with the current target and offers no chat UI", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady();

    await runSearch();
    await user.click(
      await screen.findByRole("button", {
        name: "Select rank 1: Observed test window",
      }),
    );

    await waitFor(() => {
      expect(contextUrlFromCalls(fetchMock)).not.toBeNull();
    });

    const contextUrl = contextUrlFromCalls(fetchMock);
    expect(contextUrl).not.toBeNull();
    const parsed = new URL(contextUrl as string, "http://localhost");
    expect(JSON.parse(parsed.searchParams.get("target") as string)).toEqual({
      slope_mean_deg: 12,
      terrain_relief_p95_p05_m: 80,
    });

    expect(
      await screen.findByRole("button", { name: "Download JSON" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy JSON" })).toBeInTheDocument();
    expect(screen.getByText("This app is not a chatbot")).toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: /chat|message|ask/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /send|ask|chat/i }),
    ).not.toBeInTheDocument();
  });

  it("shows context export failures without inventing an export", async () => {
    const user = userEvent.setup();
    await renderReady({
      context: () =>
        errorResponse(404, "not_found", "Location no longer exists."),
    });

    await runSearch();
    await user.click(
      await screen.findByRole("button", {
        name: "Select rank 1: Observed test window",
      }),
    );

    expect(await screen.findByText(/Context export failed/)).toBeInTheDocument();
    expect(screen.getByText(/Location no longer exists/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Download JSON" }),
    ).not.toBeInTheDocument();
  });

  it("shows Earth land-cover context separately and never as similarity", async () => {
    const user = userEvent.setup();
    await renderReady();

    await runSearch();
    await user.click(
      await screen.findByRole("button", {
        name: "Select rank 1: Observed test window",
      }),
    );

    const contextHeading = await screen.findByRole("heading", {
      name: /Earth land-cover context \(ESA WorldCover 2021\)/,
    });
    expect(contextHeading).toHaveTextContent(/context only/);
    expect(contextHeading).toHaveTextContent(/NOT mineralogy\/geology/);
    expect(contextHeading).toHaveTextContent(/excluded from the similarity score/);

    const contextSection = contextHeading.closest("section");
    expect(contextSection).not.toBeNull();
    const contextScope = within(contextSection as HTMLElement);
    expect(
      contextScope.getByText("Land cover: Bare / sparse vegetation fraction"),
    ).toBeInTheDocument();
    expect(contextScope.getByText("50.0%")).toBeInTheDocument();
    expect(contextScope.getAllByText("ESA WorldCover").length).toBeGreaterThan(0);
    expect(contextScope.getByText(/never entered into/)).toBeInTheDocument();
    expect(contextScope.queryByText("Close")).not.toBeInTheDocument();

    const comparisonHeading = screen.getByRole("heading", {
      name: "Requested target comparison",
    });
    const comparisonSection = comparisonHeading.closest("section");
    expect(comparisonSection).not.toBeNull();
    expect(
      within(comparisonSection as HTMLElement).getByText("Mean slope"),
    ).toBeInTheDocument();
    expect(
      within(comparisonSection as HTMLElement).queryByText(/Land cover:/),
    ).not.toBeInTheDocument();

    expect(
      screen.getByText("ESA WorldCover 10 m 2021 v200 land cover"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CC BY 4.0" })).toBeInTheDocument();
  });
});

describe("context filters and Moon reference mode", () => {
  it("sends optional land-cover filters and displays them", async () => {
    const user = userEvent.setup();
    const filteredResponse = {
      ...searchResponseFixture,
      filters_applied: {
        landcover_bare_sparse_fraction: { min: 0.4 },
        landcover_built_up_fraction: { max: 0.1 },
      },
    };
    const fetchMock = await renderReady({
      search: () => jsonResponse(filteredResponse),
    });

    await user.click(
      screen.getByRole("checkbox", {
        name: /Bare \/ sparse vegetation fraction/,
      }),
    );
    const minInput = screen.getByLabelText("Minimum fraction (0–1)");
    await user.clear(minInput);
    await user.type(minInput, "0.4");

    await user.click(
      screen.getByRole("checkbox", { name: /Built-up fraction/ }),
    );
    const maxInput = screen.getByLabelText("Maximum fraction (0–1)");
    await user.clear(maxInput);
    await user.type(maxInput, "0.1");

    await runSearch();

    await screen.findByTestId("coverage-headline");
    const body = searchRequestFromCalls(fetchMock);
    expect(body?.filters).toEqual({
      landcover_bare_sparse_fraction: { min: 0.4 },
      landcover_built_up_fraction: { max: 0.1 },
    });
    expect(
      screen.getByText(/Context filters:/).textContent,
    ).toContain("Land cover: Bare / sparse vegetation fraction: min 0.4");
    expect(screen.getByText(/Context filters:/).textContent).toContain(
      "Land cover: Built-up fraction: max 0.1",
    );
  });

  it("performs a Moon reference-mode search and shows the LOLA caveat", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady({
      references: () => jsonResponse([referenceFixture]),
      search: () => jsonResponse(searchResponseFixture),
    });

    await user.selectOptions(screen.getByLabelText("Body preset"), "Moon");

    expect(await screen.findByText(/LOLA reference caveat/)).toBeInTheDocument();
    expect(
      screen.getByText(/about 90% of its 5 m pixels are interpolated/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", {
        name: "Reference target observations supplied by GET /references",
      }),
    ).toBeInTheDocument();

    // Reference mode starts with every weight at 0, so nothing is compared yet.
    await runSearch();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Set at least one weight above 0. Metrics with weight 0 are not compared.",
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/search"))).toBe(false);

    const slopeGroup = screen.getByRole("group", { name: "Mean slope" });
    const slopeWeight = within(slopeGroup).getByLabelText("Weight (≥ 0)");
    expect(slopeWeight).toHaveValue(0);
    await user.clear(slopeWeight);
    await user.type(slopeWeight, "1");

    await runSearch();

    await screen.findByTestId("coverage-headline");
    const body = searchRequestFromCalls(fetchMock);
    expect(body).toEqual({
      body: "Moon",
      reference_id: "moon-shackleton-rim-site04-roi2",
      target: {},
      weights: {
        slope_mean_deg: 1,
        slope_p90_deg: 0,
        terrain_relief_p95_p05_m: 0,
        roughness_std_m: 0,
      },
      filters: {},
      limit: 10,
    });
  });
});

describe("flat terrain fraction", () => {
  it("accepts the additive fraction feature supplied by the API", async () => {
    const user = userEvent.setup();
    const flat = {
      ...capabilitiesFixture.features[0],
      id: "flat_terrain_fraction", label: "Flat terrain fraction", unit: "fraction",
      min_value: 0, max_value: 1, scale: 0.25,
      method_version: "flat-terrain-fraction-1.0.0",
    };
    const fetchMock = await renderReady({
      capabilities: () => jsonResponse({ ...capabilitiesFixture, features: [...capabilitiesFixture.features, flat] }),
      search: () => jsonResponse(searchResponseFixture),
    });
    const group = await screen.findByRole("group", { name: "Flat terrain fraction" });
    await user.click(within(group).getByRole("checkbox"));
    const value = within(group).getByRole("spinbutton", { name: /Target value/ });
    await user.type(value, "0.5");
    await runSearch();
    await screen.findByTestId("coverage-headline");
    expect(searchRequestFromCalls(fetchMock)?.target?.flat_terrain_fraction).toEqual({ value: 0.5, weight: 1 });
  });
});

describe("Mars reference mode", () => {
  it("uses a supplied Mars reference through the existing search form", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady({
      references: () => jsonResponse([{
        ...referenceFixture,
        reference_id: "mars-jezero-ctx-window-01",
        body: "Mars",
        display_name: "TEST ONLY Jezero CTX reference",
        notes: "TEST ONLY API fixture",
        quality_flags: ["stereo_dem_mosaic"],
      }]),
      search: () => jsonResponse(searchResponseFixture),
    });
    const mars = screen.getByRole("option", { name: /Mars reference/ });
    expect((mars as HTMLOptionElement).disabled).toBe(false);
    await user.selectOptions(screen.getByLabelText("Body preset"), "Mars");
    await runSearch();
    await screen.findByTestId("coverage-headline");
    expect(searchRequestFromCalls(fetchMock)).toMatchObject({
      body: "Mars",
      reference_id: "mars-jezero-ctx-window-01",
      target: {},
    });
  });
});

describe("AOI analysis", () => {
  it("validates AOI bbox limits client-side before submitting", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady();
    await openAoiTab(user);

    const east = screen.getByLabelText("East longitude (°)");
    await user.clear(east);
    await user.type(east, "-13.6449");

    await user.click(screen.getByRole("button", { name: "Analyze this Earth AOI" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("AOI too small");
    expect(
      requestUrls(fetchMock).filter((url) => url.endsWith("/areas/analyze")),
    ).toHaveLength(0);
  });

  it("shows server 422 AOI errors with codes and details", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady({
      analyze: () =>
        errorResponse(422, "aoi_too_large", "AOI sides must be at most 2000 m.", [
          { reason: "side_too_long", max_side_m: 2000 },
        ]),
    });
    await openAoiTab(user);

    await user.click(screen.getByRole("button", { name: "Analyze this Earth AOI" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("AOI request failed");
    expect(alert).toHaveTextContent("AOI sides must be at most 2000 m.");
    expect(alert).toHaveTextContent("aoi_too_large");
    expect(within(alert).getByText("Validation details")).toBeInTheDocument();
    expect(
      requestUrls(fetchMock).filter((url) => url.includes("/api/v1/jobs/")),
    ).toHaveLength(0);
  });

  it("polls queued → running → completed and shows provider status", async () => {
    const user = userEvent.setup();
    const jobDeferreds = [deferredResponse(), deferredResponse()];
    let jobCalls = 0;
    await renderReady({
      analyze: () => jsonResponse(analyzeQueuedFixture, 202),
      job: () => {
        const deferred =
          jobDeferreds[Math.min(jobCalls, jobDeferreds.length - 1)];
        jobCalls += 1;
        return deferred.promise;
      },
      areaFeatures: () => jsonResponse(aoiLocationFixture),
    });

    await openAoiTab(user);
    await user.click(screen.getByRole("button", { name: "Analyze this Earth AOI" }));

    expect(await screen.findByText("AOI analysis queued…")).toBeInTheDocument();
    await waitFor(() => expect(jobCalls).toBe(1));

    await act(async () => {
      jobDeferreds[0].resolve(jsonResponse(jobRunningFixture));
    });
    expect(await screen.findByText("AOI analysis running…")).toBeInTheDocument();

    await waitFor(() => expect(jobCalls).toBe(2));
    await act(async () => {
      jobDeferreds[1].resolve(jsonResponse(jobCompletedFixture));
    });

    expect(await screen.findByTestId("aoi-record")).toBeInTheDocument();
    const jobPanel = screen.getByTestId("aoi-job-panel");
    expect(within(jobPanel).getByText("completed")).toBeInTheDocument();
    expect(within(jobPanel).getAllByText("verified")).toHaveLength(2);
    expect(
      within(jobPanel).getByText("copernicus-dem-glo90"),
    ).toBeInTheDocument();
    expect(within(jobPanel).getByText("source_nodata_undefined")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "On-demand Earth AOI test" }),
    ).toBeInTheDocument();
  });

  it("renders a partial job with failed provider status and still shows features", async () => {
    const user = userEvent.setup();
    await renderReady({
      analyze: () => jsonResponse(analyzeQueuedFixture, 202),
      job: () => jsonResponse(jobPartialFixture),
      areaFeatures: () =>
        jsonResponse({ ...aoiLocationFixture, context_observations: [] }),
    });
    await openAoiTab(user);

    await user.click(screen.getByRole("button", { name: "Analyze this Earth AOI" }));

    expect(await screen.findByText("Partial result")).toBeInTheDocument();
    expect(screen.getByTestId("aoi-record")).toBeInTheDocument();
    const jobPanel = screen.getByTestId("aoi-job-panel");
    expect(within(jobPanel).getByText("partial")).toBeInTheDocument();
    expect(within(jobPanel).getByText("failed")).toBeInTheDocument();
    expect(
      within(jobPanel).getByText(/ESA WorldCover window read failed in test/),
    ).toBeInTheDocument();
    expect(within(jobPanel).getByText("worldcover_context_provider_failed")).toBeInTheDocument();
  });

  it("renders a failed job with its error and does not show a feature record", async () => {
    const user = userEvent.setup();
    await renderReady({
      analyze: () => jsonResponse(analyzeQueuedFixture, 202),
      job: () => jsonResponse(jobFailedFixture),
      areaFeatures: () => jsonResponse(aoiLocationFixture),
    });
    await openAoiTab(user);

    await user.click(screen.getByRole("button", { name: "Analyze this Earth AOI" }));

    expect(await screen.findByText("AOI analysis failed")).toBeInTheDocument();
    expect(
      screen.getByText(/Copernicus DEM window read failed in test/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("source_fetch_failed").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("aoi-record")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Compare this AOI against indexed Earth candidates",
      }),
    ).not.toBeInTheDocument();
  });

  it("uses completed AOI features as the next search target", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady({
      analyze: () => jsonResponse(analyzeQueuedFixture, 202),
      job: () => jsonResponse(jobCompletedFixture),
      areaFeatures: () => jsonResponse(aoiLocationFixture),
    });
    await openAoiTab(user);
    await user.click(screen.getByRole("button", { name: "Analyze this Earth AOI" }));
    await screen.findByTestId("aoi-record");

    await user.click(
      screen.getByRole("button", { name: "Use AOI features as search target" }),
    );

    await waitFor(() => expect(searchRequestFromCalls(fetchMock)).not.toBeNull());
    const body = searchRequestFromCalls(fetchMock);
    expect(body?.body).toBe("custom");
    expect(body?.target).toEqual(
      Object.fromEntries(
        aoiLocationFixture.features.map((feature) => [
          feature.feature_id,
          { value: feature.value, weight: 1 },
        ]),
      ),
    );
    expect(screen.getByRole("tab", { name: "Search target" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(await screen.findByTestId("coverage-headline")).toBeInTheDocument();
  });

  it("compares a completed AOI by calling the no-body compare endpoint", async () => {
    const user = userEvent.setup();
    const fetchMock = await renderReady({
      analyze: () => jsonResponse(analyzeQueuedFixture, 202),
      job: () => jsonResponse(jobCompletedFixture),
      areaFeatures: () => jsonResponse(aoiLocationFixture),
      compare: () => jsonResponse(searchResponseFixture),
    });
    await openAoiTab(user);
    await user.click(screen.getByRole("button", { name: "Analyze this Earth AOI" }));
    await screen.findByTestId("aoi-record");

    await user.click(
      screen.getByRole("button", {
        name: "Compare this AOI against indexed Earth candidates",
      }),
    );

    await waitFor(() => {
      expect(
        requestUrls(fetchMock).some((url) => url.endsWith("/compare")),
      ).toBe(true);
    });
    const compareCall = fetchMock.mock.calls.find(([input]) =>
      (typeof input === "string" ? input : input.toString()).endsWith("/compare"),
    );
    expect(compareCall?.[1]).toMatchObject({ method: "POST" });
    expect((compareCall?.[1] as RequestInit | undefined)?.body).toBeUndefined();
    expect(await screen.findByTestId("coverage-headline")).toHaveTextContent(
      "Top 2 among 4 indexed regions",
    );
  });
});
