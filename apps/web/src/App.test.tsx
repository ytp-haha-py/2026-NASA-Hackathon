import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  typedCoverageFixture,
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
import { applyExampleTarget } from "./test/example-target";

vi.mock("./components/MapPanel", () => ({
  MapPanel: ({
    results,
    selectedId,
    onSelect,
  }: {
    results: typeof searchResponseFixture.results;
    selectedId: string | null;
    onSelect: (id: string) => void;
  }) => (
    <div data-testid="map-stub" data-selected={selectedId ?? ""}>
      {results.length} windows plotted
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
  await user.click(screen.getByRole("button", { name: "Analyze a new area" }));
}

describe("search form", () => {
  it("opens the target map and remembers the points selected for each body", async () => {
    const user = userEvent.setup();
    const marsReference = {
      ...referenceFixture,
      body: "Mars",
      reference_id: "mars-test-reference",
      display_name: "Jezero test reference",
      lat: 18.5,
      lon: 77.5,
    };
    await renderReady({ references: () => jsonResponse([referenceFixture, marsReference]) });

    const [moonPreset, marsPreset, customPreset] = within(
      screen.getByRole("radiogroup", { name: "Body preset" }),
    ).getAllByRole("radio");
    expect(moonPreset).toHaveAccessibleName(/^Moon/);
    expect(marsPreset).toHaveAccessibleName(/^Mars/);
    expect(customPreset).toHaveAccessibleName("Custom");

    const search = screen.getByRole("button", { name: "Search indexed Earth candidates" });
    const target = within(screen.getByRole("region", { name: "Search target" }));
    await user.click(moonPreset);
    const activeReference = target.getByRole("status");
    expect(search).toBeDisabled();
    expect(activeReference).toHaveTextContent("Choose a reference point on the map");
    const moonMap = within(screen.getByRole("region", { name: "Moon reference sites map" }));
    await user.click(moonMap.getByRole("button", { name: referenceFixture.display_name }));
    expect(activeReference).toHaveTextContent(referenceFixture.display_name);
    expect(moonMap.getByRole("button", { name: referenceFixture.display_name })).toHaveAttribute(
      "aria-pressed", "true",
    );
    expect(search).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Collapse map" }));
    expect(screen.queryByRole("region", { name: "Moon reference sites map" })).not.toBeInTheDocument();
    expect(activeReference).toHaveTextContent(referenceFixture.display_name);
    await user.click(moonPreset);
    expect(screen.getByRole("region", { name: "Moon reference sites map" })).toBeVisible();

    await user.click(marsPreset);
    expect(search).toBeDisabled();
    await user.click(screen.getByRole("button", { name: marsReference.display_name }));
    expect(activeReference).toHaveTextContent(marsReference.display_name);
    await user.click(moonPreset);
    expect(activeReference).toHaveTextContent(referenceFixture.display_name);
    await user.click(marsPreset);
    expect(activeReference).toHaveTextContent(marsReference.display_name);

    await user.click(customPreset);
    expect(screen.queryByRole("region", { name: "Mars reference sites map" })).not.toBeInTheDocument();
    expect(customPreset).toHaveAttribute("aria-checked", "true");
  });

  it("builds a 422-safe request body from the hypothetical example target", async () => {
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

    expect(screen.getByRole("radio", { name: /^Moon/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /^Mars/ })).toBeDisabled();
    expect(
      screen.getByText(/Moon and Mars presets are unavailable/),
    ).toBeInTheDocument();
  });

  it("enables a body preset only when a matching reference record exists", async () => {
    await renderReady({
      references: () => jsonResponse([referenceFixture]),
    });

    const user = userEvent.setup();
    expect(screen.getByRole("radio", { name: /^Moon/ })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /^Mars/ })).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /^Moon/ }));
    expect(screen.getByRole("radio", { name: /^Moon/ })).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("button", { name: referenceFixture.display_name }),
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

    const user = await runSearch();

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

    await user.click(screen.getByRole("button", { name: "Select rank 1: Observed test window" }));
    const observedCard = screen.getByTestId("result-earth-observed-1");
    expect(within(observedCard).getByText("Observed")).toBeInTheDocument();
    expect(within(observedCard).getByText("0.037")).toBeInTheDocument();
    expect(within(observedCard).getByText("12.4 deg")).toBeInTheDocument();
    expect(within(observedCard).getByText("12 deg")).toBeInTheDocument();
    expect(within(observedCard).getByText("+0.4 deg")).toBeInTheDocument();

    expect(screen.queryByTestId("result-earth-synthetic-2")).not.toBeInTheDocument();
    await user.click(within(screen.getByTestId("map-stub")).getByRole("button", { name: /^Select rank 2:/ }));
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

    // Land-cover filters are range sliders; only narrowed bounds are sent.
    fireEvent.change(
      screen.getByRole("slider", { name: "Bare / sparse vegetation fraction lower bound" }),
      { target: { value: "0.4" } },
    );
    fireEvent.change(screen.getByRole("slider", { name: "Built-up fraction upper bound" }), {
      target: { value: "0.1" },
    });

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

    await user.click(screen.getByRole("radio", { name: /^Moon/ }));
    await user.click(screen.getByRole("button", { name: referenceFixture.display_name }));

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
    expect(fetchMock.mock.calls.some(([url, init]) => init?.method === "POST" && /\/api\/v1\/search(?:\/multidimensional)?$/.test(String(url)))).toBe(false);

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
    // A metric is compared once its weight is above 0.
    const weight = within(group).getByLabelText("Weight (≥ 0)");
    await user.clear(weight);
    await user.type(weight, "1");
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
    const mars = screen.getByRole("radio", { name: /^Mars/ });
    expect(mars).toBeEnabled();
    await user.click(mars);
    await user.click(screen.getByRole("button", { name: "TEST ONLY Jezero CTX reference" }));

    // Reference mode starts with every weight at 0; opt one metric in before searching.
    const slopeWeight = within(screen.getByRole("group", { name: "Mean slope" })).getByLabelText(
      "Weight (≥ 0)",
    );
    await user.clear(slopeWeight);
    await user.type(slopeWeight, "1");

    await runSearch();
    await screen.findByTestId("coverage-headline");
    expect(searchRequestFromCalls(fetchMock)).toMatchObject({
      body: "Mars",
      reference_id: "mars-jezero-ctx-window-01",
      target: {},
      weights: { slope_mean_deg: 1 },
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
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
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

describe("dimension board", () => {
  it("sends a narrowed terrain range as a hard filter and leaves full ranges out", async () => {
    const fetchMock = await renderReady();
    const slopeGroup = screen.getByRole("group", { name: "Mean slope" });
    fireEvent.change(within(slopeGroup).getByRole("slider", { name: "Mean slope lower bound" }), {
      target: { value: "5" },
    });
    fireEvent.change(within(slopeGroup).getByRole("slider", { name: "Mean slope upper bound" }), {
      target: { value: "20" },
    });

    await runSearch();
    await screen.findByTestId("coverage-headline");
    expect(searchRequestFromCalls(fetchMock)?.filters).toEqual({
      slope_mean_deg: { min: 5, max: 20 },
    });
  });

  it("shows fixed D01–D06 cards with real typed status and no scoring controls without a registry", async () => {
    await renderReady();
    const board = screen.getByRole("region", { name: "Dimensions" });
    const d01 = within(board).getByRole("region", { name: "D01 Terrain & Landforms" });
    expect(within(d01).getByRole("slider", { name: /Mean slope weight slider/ })).toBeInTheDocument();

    for (const code of ["D02", "D03", "D04", "D05", "D06"] as const) {
      const card = within(board).getByTestId(`dimension-card-${code}`);
      expect(await within(card).findByText(/typed observations/)).toBeInTheDocument();
      expect(within(card).queryByRole("slider")).not.toBeInTheDocument();
      expect(within(card).queryByRole("spinbutton")).not.toBeInTheDocument();
      expect(within(card).getByRole("button", { name: "View observations" })).toBeInTheDocument();
    }

    // Land cover is a filter section, never a numbered similarity dimension or a weight.
    const context = within(board).getByRole("region", { name: /Land-cover filter/ });
    expect(within(context).queryByLabelText("Weight (≥ 0)")).not.toBeInTheDocument();
  });

  it("shows typed-data failure with retry and recovers without fake evidence", async () => {
    let attempts = 0;
    const user = userEvent.setup();
    await renderReady({
      typedCoverage: () => {
        attempts += 1;
        return attempts === 1
          ? errorResponse(503, "service_unavailable", "typed coverage temporarily unavailable")
          : jsonResponse(typedCoverageFixture);
      },
    });
    const board = screen.getByRole("region", { name: "Dimensions" });
    const alert = await within(board).findByRole("alert");
    expect(alert).toHaveTextContent("Typed dimension data failed to load.");
    expect(alert).toHaveTextContent("typed coverage temporarily unavailable");
    await user.click(within(alert).getByRole("button", { name: "Retry typed data" }));
    expect(
      await within(board).findByTestId("dimension-total-D02"),
    ).toHaveTextContent("typed observations");
    expect(attempts).toBe(2);
  });

  it("opens the six-dimension workspace from its own section without a board shortcut", async () => {
    const user = userEvent.setup();
    await renderReady({ references: () => jsonResponse([referenceFixture]) });
    await user.click(screen.getByRole("radio", { name: "Moon" }));
    await user.click(screen.getByRole("button", { name: referenceFixture.display_name }));
    const board = within(screen.getByRole("region", { name: "Dimensions" }));
    expect(board.queryByRole("button", { name: "Open six dimensions" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Six-dimension comparison" }));
    expect(await screen.findByRole("heading", { name: "D01–D06 observations for one area" }))
      .toBeInTheDocument();

  });

  it("shows read-only reference values in Moon mode and editable targets in custom mode", async () => {
    const user = userEvent.setup();
    await renderReady({ references: () => jsonResponse([referenceFixture]) });

    const customGroup = screen.getByRole("group", { name: "Mean slope" });
    const slopeTarget = within(customGroup).getByLabelText("Target value (degree)");
    await user.clear(slopeTarget);
    await user.type(slopeTarget, "7.5");
    expect(slopeTarget).toHaveValue(7.5);

    await user.click(screen.getByRole("radio", { name: /^Moon/ }));
    await user.click(screen.getByRole("button", { name: referenceFixture.display_name }));
    const moonGroup = screen.getByRole("group", { name: "Mean slope" });
    expect(within(moonGroup).queryByLabelText("Target value (degree)")).not.toBeInTheDocument();
    expect(within(moonGroup).getByTitle("Reference value (read-only)")).toBeInTheDocument();
  });
});
