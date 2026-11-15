import { fireEvent, render, screen } from "@testing-library/react";
import L from "leaflet";
import { expect, it, vi } from "vitest";
import { referenceFixture } from "../test/fixtures";
import { hasMapCoordinates, PlanetMap } from "./PlanetMap";

it("zooms into nearby points before accepting a pointer selection", () => {
  const size = vi.spyOn(L.Map.prototype, "getSize").mockReturnValue(L.point(700, 240));
  const fit = vi.spyOn(L.Map.prototype, "fitBounds");
  const select = vi.fn();
  const sites = [
    referenceFixture,
    {
      ...referenceFixture,
      reference_id: "moon-next",
      display_name: "Nearby test point",
      lon: (referenceFixture.lon ?? 0) + 0.0014,
    },
  ].filter(hasMapCoordinates);

  try {
    render(
      <PlanetMap
        body="Moon"
        references={sites}
        selectedReferenceId={null}
        onSelectReference={select}
        regionLabel="Moon reference sites"
      />,
    );
    const first = screen.getByRole("button", { name: referenceFixture.display_name });
    const second = screen.getByRole("button", { name: "Nearby test point" });

    fit.mockClear();
    fireEvent.click(first, { detail: 1 });
    expect(fit).toHaveBeenCalledTimes(1);
    expect(select).not.toHaveBeenCalled();

    fireEvent.click(second, { detail: 1 });
    expect(select).toHaveBeenCalledExactlyOnceWith("moon-next");
  } finally {
    size.mockRestore();
    fit.mockRestore();
  }
});
