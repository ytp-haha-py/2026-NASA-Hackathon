import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n/I18nProvider";
import { featuresFixture, searchResponseFixture } from "../test/fixtures";
import { RankingOverlay } from "./RankingOverlay";

function renderOverlay(onSelect = vi.fn()) {
  render(
    <I18nProvider initialLocale="en">
      <RankingOverlay
        response={searchResponseFixture}
        features={featuresFixture}
        target={{ slope_mean_deg: 12 }}
        selectedId={null}
        onSelect={onSelect}
      />
    </I18nProvider>,
  );
  return onSelect;
}

describe("RankingOverlay", () => {
  it("lists the returned ranks compactly and selects a candidate", async () => {
    const user = userEvent.setup();
    const onSelect = renderOverlay();
    const region = screen.getByRole("region", { name: "Ranked candidates" });

    const first = within(region).getByRole("button", {
      name: "Show rank 1 on the map: Observed test window",
    });
    await user.click(first);
    expect(onSelect).toHaveBeenCalledWith("earth-observed-1");

    // Synthetic candidates keep their DEMO label in the compact list too.
    expect(within(region).getByText("DEMO / SYNTHETIC")).toBeInTheDocument();
    expect(within(region).queryByRole("table")).not.toBeInTheDocument();
  });

  it("expands into a table whose dimension columns can be collapsed", async () => {
    const user = userEvent.setup();
    renderOverlay();

    await user.click(screen.getByRole("button", { name: "Show values" }));
    const table = screen.getByRole("table", {
      name: "Target and ranked candidate values by dimension",
    });
    expect(within(table).getByRole("columnheader", { name: /Mean slope/ })).toBeInTheDocument();

    const toggle = within(table).getByRole("button", {
      name: "Show or hide Terrain shape columns",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(
      within(table).queryByRole("columnheader", { name: /Mean slope/ }),
    ).not.toBeInTheDocument();
  });
});
