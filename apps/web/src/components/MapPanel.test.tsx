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

  it("offers an area-selection tool on Earth only, and says Moon/Mars are browse-only", async () => {
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

    await user.click(screen.getByRole("button", { name: "Moon" }));
    expect(screen.queryByRole("button", { name: /Select area|Cancel selection/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Moon and Mars maps are browse-only/)).toBeInTheDocument();
  });
});
