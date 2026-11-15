import { fireEvent, waitFor } from "@testing-library/react";

/**
 * The custom target starts at 0 everywhere. Tests that exercise a ready-to-search form set the
 * former hypothetical example explicitly: mean slope 12° (weight 1) and relief 80 m (weight 0.5).
 * Inputs are found by id, so this works in every locale.
 */
const EXAMPLE_ROWS: Record<string, { value: string; weight: string }> = {
  slope_mean_deg: { value: "12", weight: "1" },
  terrain_relief_p95_p05_m: { value: "80", weight: "0.5" },
};

function input(id: string): HTMLInputElement {
  const element = document.getElementById(id);
  if (!(element instanceof HTMLInputElement)) {
    throw new Error(`Input #${id} was not rendered.`);
  }
  return element;
}

export async function applyExampleTarget() {
  // The header renders before /capabilities resolves; wait for the form's inputs.
  await waitFor(() => input("metric-slope_mean_deg-weight"));
  for (const [featureId, row] of Object.entries(EXAMPLE_ROWS)) {
    fireEvent.change(input(`metric-${featureId}-value`), { target: { value: row.value } });
    fireEvent.change(input(`metric-${featureId}-weight`), { target: { value: row.weight } });
  }
}
