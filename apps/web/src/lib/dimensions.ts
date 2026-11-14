import type { ContextFeatureOut, FeatureOut } from "../api/client";

/** Number of similarity-dimension slots the board shows (D1…D6). */
export const DIMENSION_SLOTS = 6;

export type DimensionGroup = {
  /** 1-based slot number, rendered as D1…D6. */
  slot: number;
  dimensionId: string | null;
  features: FeatureOut[];
};

/**
 * Groups similarity features by `dimension_id` (API order) and pads the list with empty
 * placeholder slots up to {@link DIMENSION_SLOTS}. Placeholders carry no features and never
 * render fake metrics or values.
 */
export function dimensionSlots(features: FeatureOut[]): DimensionGroup[] {
  const groups: DimensionGroup[] = [];
  for (const feature of features) {
    const existing = groups.find((group) => group.dimensionId === feature.dimension_id);
    if (existing) {
      existing.features.push(feature);
    } else {
      groups.push({ slot: groups.length + 1, dimensionId: feature.dimension_id, features: [feature] });
    }
  }
  while (groups.length < DIMENSION_SLOTS) {
    groups.push({ slot: groups.length + 1, dimensionId: null, features: [] });
  }
  return groups;
}

export type SliderRange = {
  /** Lowest slider position (the feature domain minimum). */
  lo: number;
  /** Highest slider position. May be below the domain maximum; see `open`. */
  hi: number;
  step: number;
  /** Decimal places for display. */
  dp: number;
  /**
   * True when `hi` is a practical UI cap below the registry maximum. A thumb at `hi` then means
   * "no upper bound" and is shown as "hi+", so no real candidate is silently cut off.
   */
  open: boolean;
};

/**
 * Practical slider ranges per unit. Registry domains are physical limits (relief up to 20 km),
 * which would make a slider unusable; values above the cap stay reachable through the number input
 * (targets) or by leaving the upper thumb at the end (filters = unbounded).
 */
const UNIT_RANGES: Record<string, { hi: number; step: number; dp: number }> = {
  degree: { hi: 60, step: 0.5, dp: 1 },
  meter: { hi: 1000, step: 10, dp: 0 },
  fraction: { hi: 1, step: 0.05, dp: 2 },
};

const FEATURE_RANGES: Record<string, { hi: number; step: number; dp: number }> = {
  slope_p90_deg: { hi: 90, step: 0.5, dp: 1 },
  terrain_relief_p95_p05_m: { hi: 2000, step: 10, dp: 0 },
  roughness_std_m: { hi: 200, step: 1, dp: 0 },
};

export function sliderRange(feature: Pick<FeatureOut, "id" | "unit" | "min_value" | "max_value">): SliderRange {
  const preset = FEATURE_RANGES[feature.id] ?? UNIT_RANGES[feature.unit];
  if (!preset) {
    const span = feature.max_value - feature.min_value;
    return { lo: feature.min_value, hi: feature.max_value, step: span / 100, dp: 2, open: false };
  }
  const hi = Math.min(preset.hi, feature.max_value);
  return { lo: feature.min_value, hi, step: preset.step, dp: preset.dp, open: hi < feature.max_value };
}

/** Land-cover fractions are always 0..1. */
export const FRACTION_RANGE: SliderRange = { lo: 0, hi: 1, step: 0.05, dp: 2, open: false };

/** Context filters shown first in the land-cover column; the rest sit behind "more". */
export const PRIMARY_CONTEXT_IDS = [
  "landcover_bare_sparse_fraction",
  "landcover_built_up_fraction",
  "landcover_tree_cover_fraction",
  "landcover_water_fraction",
];

export function orderContextFeatures(features: ContextFeatureOut[]): {
  primary: ContextFeatureOut[];
  more: ContextFeatureOut[];
} {
  const primary = PRIMARY_CONTEXT_IDS.map((id) => features.find((f) => f.feature_id === id)).filter(
    (feature): feature is ContextFeatureOut => feature !== undefined,
  );
  const more = features.filter((feature) => !PRIMARY_CONTEXT_IDS.includes(feature.feature_id));
  return { primary, more };
}

/** A range filter is only sent when it is narrower than the full slider span. */
export function rangeToFilter(
  range: [number, number],
  slider: SliderRange,
): { min?: number; max?: number } | null {
  const filter: { min?: number; max?: number } = {};
  if (range[0] > slider.lo) {
    filter.min = range[0];
  }
  // An upper thumb at the slider end is unbounded (open cap) or the domain maximum: no max sent.
  if (range[1] < slider.hi) {
    filter.max = range[1];
  }
  return Object.keys(filter).length > 0 ? filter : null;
}

export function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}
