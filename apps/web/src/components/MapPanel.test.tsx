import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import L from "leaflet";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SearchResultOut } from "../api/client";
import { I18nProvider } from "../i18n/I18nProvider";
import { searchResponseFixture } from "../test/fixtures";
import { MapPanel } from "./MapPanel";

const results: SearchResultOut[] = searchResponseFixture.results;

function renderMap(selectedId: string | null = null) {
  const view = (id: string | null) => (
    <I18nProvider initialLocale="en">
      <MapPanel results={results} selectedId={id} onSelect={vi.fn()} />
    </I18nProvider>
  );
  const utils = render(view(selectedId));
  return { ...utils, select: (id: string | null) => utils.rerender(view(id)) };
}

describe("MapPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("offers map, terrain and satellite basemaps", async () => {
    const user = userEvent.setup();
    renderMap();
    const group = screen.getByRole("group", { name: "Basemap" });
    expect(group).toBeInTheDocument();

    const satellite = screen.getByRole("button", { name: "Satellite" });
    expect(screen.getByRole("button", { name: "Map" })).toHaveAttribute("aria-pressed", "true");
    await user.click(satellite);
    expect(satellite).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Terrain" })).toHaveAttribute("aria-pressed", "false");
  });

  it("zooms to a selected window and back to all results", async () => {
    const user = userEvent.setup();
    const fly = vi.spyOn(L.Map.prototype, "flyToBounds").mockReturnThis();
    const fit = vi.spyOn(L.Map.prototype, "fitBounds").mockReturnThis();
    const { select } = renderMap();
    expect(fly).not.toHaveBeenCalled();

    const target = results[1];
    select(target.location_id);
    expect(fly).toHaveBeenCalledTimes(1);
    const bounds = fly.mock.calls[0][0] as L.LatLngBounds;
    expect(bounds.getSouth()).toBeCloseTo(target.bbox.south);
    expect(bounds.getNorth()).toBeCloseTo(target.bbox.north);
    expect(bounds.getWest()).toBeCloseTo(target.bbox.west);
    expect(bounds.getEast()).toBeCloseTo(target.bbox.east);

    fit.mockClear();
    await user.click(screen.getByRole("button", { name: "Show all results" }));
    expect(fit).toHaveBeenCalledTimes(1);
  });

  it("hides the show-all control when nothing is plotted", () => {
    render(
      <I18nProvider initialLocale="en">
        <MapPanel results={[]} selectedId={null} onSelect={vi.fn()} />
      </I18nProvider>,
    );
    expect(screen.queryByRole("button", { name: "Show all results" })).not.toBeInTheDocument();
  });

  it("fits indexed candidates across the date line and ignores invalid bounds", () => {
    const fit = vi.spyOn(L.Map.prototype, "fitBounds").mockReturnThis();
    const added = vi.spyOn(L.LayerGroup.prototype, "addLayer");
    const onSelect = vi.fn();
    const [candidate] = results;
    const datedResults = [
      { ...candidate, lat: 0.05, lon: 179.9, bbox: { west: 179.8, east: -179.9, south: 0, north: 0.1 } },
      { ...candidate, location_id: "opposite", lat: 0.05, lon: -179.6, bbox: { west: -179.7, east: -179.5, south: 0, north: 0.1 } },
      { ...candidate, location_id: "invalid", bbox: { west: 25, east: 26, south: 10, north: 5 } },
    ];
    const view = render(
      <I18nProvider initialLocale="en">
        <MapPanel results={datedResults} selectedId={null} onSelect={onSelect} />
      </I18nProvider>,
    );
    const bounds = fit.mock.calls.at(-1)?.[0] as L.LatLngBounds;
    expect(bounds.getEast() - bounds.getWest()).toBeCloseTo(0.7);
    const markers = view.container.querySelectorAll<HTMLElement>(".rank-pin");
    expect(markers).toHaveLength(2);
    const firstMarker = added.mock.calls.map(([layer]) => layer).find((layer) => layer instanceof L.Marker);
    expect(firstMarker).toBeInstanceOf(L.Marker);
    firstMarker?.fire("click");
    expect(onSelect).toHaveBeenCalledWith(candidate.location_id);
  });

  it("keeps area selection and basemaps on the Earth results map", async () => {
    const user = userEvent.setup();
    render(
      <I18nProvider initialLocale="en">
        <MapPanel results={[]} selectedId={null} onSelect={vi.fn()} onDrawBbox={vi.fn()} />
      </I18nProvider>,
    );
    const draw = screen.getByRole("button", { name: "Select area" });
    expect(draw).toHaveAttribute("aria-pressed", "false");
    await user.click(draw);
    expect(screen.getByRole("button", { name: "Cancel selection" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText(/Drag on the map to draw the area to analyze/)).toBeInTheDocument();

    expect(screen.getByRole("group", { name: "Basemap" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /^(Earth|Moon|Mars)$/ })).not.toBeInTheDocument();
  });
});
