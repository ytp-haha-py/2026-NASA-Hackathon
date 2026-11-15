import type { ContextFeatureOut, FeatureOut, FeatureRegistryOut } from "../api/client";
import { registrySimilarityFeature } from "./multidim";
import { DIMENSION_CODES, type DimensionCode } from "./six-dimensions";

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
  // SoilGrids (D06): bulk density, texture fractions, organic carbon
  "cg cm-3": { hi: 300, step: 1, dp: 0 },
  "g kg-1": { hi: 1000, step: 5, dp: 0 },
  "dg kg-1": { hi: 1000, step: 5, dp: 0 },
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

export type TypedDimensionSummary = {
  total: number;
  by_body: Record<string, number>;
  by_state: Record<string, number>;
};

export type RegistryFeatureOut = FeatureRegistryOut["features"][number];

export type DimensionRole = "similarity" | "filter" | "evidence_only" | "context_only";

/** Extra registry fields the D02–D05 filter contract adds additively to the committed schema. */
type RegistryFilterContract = {
  /** "range" selects the numeric dual slider; "category" renders a category select. */
  filter_kind?: "range" | "category" | null;
  /** Request key for a registered category filter. */
  category_filter_field?: string | null;
  /** Alternative integration-contract spelling for the category request key. */
  filter_field?: string | null;
  /** Registered category values (strings or objects with value/code + label). */
  categories?: unknown;
};

export type FilterKind = "range" | "category";

export type FilterDisabledReason =
  | "no_coverage"
  | "no_domain"
  | "category_filter_not_registered"
  | "category_values_not_registered";

export type FilterCategoryOption = {
  value: string;
  label: string;
};

/**
 * A D02–D05 filter-only control. It narrows the candidate set in `filters`; it is never a target,
 * weight or distance contribution. A non-null `disabledReason` means the UI must show the reason
 * and no usable slider (no fake control).
 */
export type DimensionFilterControl = {
  id: string;
  label: string;
  unit: string;
  kind: FilterKind;
  minValue: number | null;
  maxValue: number | null;
  categories: FilterCategoryOption[];
  /** Request key for a category filter; null until the backend registers one. */
  categoryField: string | null;
  disabledReason: FilterDisabledReason | null;
};

export type FormalDimensionCard = {
  code: DimensionCode;
  name: string;
  role: DimensionRole;
  /** Similarity features with target/range/weight controls (D01 and Earth D06 today). */
  features: FeatureOut[];
  /** Filter-only features with range/category controls (D02–D05 when the registry allows them). */
  filters: DimensionFilterControl[];
  total: number;
  byBody: Record<string, number>;
  byState: Record<string, number>;
  valueKinds: string[];
};

const DIMENSION_NAME: Record<DimensionCode, string> = {
  D01: "Terrain & Landforms",
  D02: "Geology & Mineralogy",
  D03: "Thermal Environment",
  D04: "Water & Ice",
  D05: "Solar Illumination & Radiation",
  D06: "Regolith & Surface Properties",
};

type RoleLike = { supported?: boolean; reason?: string | null } | undefined;

function roleFor(feature: RegistryFeatureOut, role: string): RoleLike {
  const roles = feature.roles as Record<string, RoleLike>;
  return roles[role];
}

/**
 * The registry currently names the filter role `hard_filter`; the D02–D05 integration contract
 * adds `filter`. Prefer the explicit `filter` entry, and fall back to `hard_filter` only when the
 * API does not publish a `filter` role at all. An explicit `filter.supported=false` always wins.
 */
export function filterRole(feature: RegistryFeatureOut): RoleLike {
  return roleFor(feature, "filter") ?? roleFor(feature, "hard_filter");
}

export function filterSupported(feature: RegistryFeatureOut): boolean {
  return filterRole(feature)?.supported === true;
}

function similaritySupported(feature: RegistryFeatureOut): boolean {
  return roleFor(feature, "similarity")?.supported === true;
}

function contextSupported(feature: RegistryFeatureOut): boolean {
  return roleFor(feature, "context")?.supported === true;
}

function contract(feature: RegistryFeatureOut): RegistryFilterContract {
  return feature as RegistryFeatureOut & RegistryFilterContract;
}

export function filterKind(feature: RegistryFeatureOut): FilterKind {
  const declared = contract(feature).filter_kind;
  if (declared === "range" || declared === "category") {
    return declared;
  }
  return feature.value_kind === "category" ||
    feature.value_kind === "detection" ||
    feature.value_kind === "process_evidence"
    ? "category"
    : "range";
}

function categoryField(feature: RegistryFeatureOut): string | null {
  const extension = contract(feature);
  for (const candidate of [extension.category_filter_field, extension.filter_field]) {
    if (typeof candidate === "string" && candidate.trim() !== "") {
      return candidate;
    }
  }
  return null;
}

function categoryOptions(feature: RegistryFeatureOut): FilterCategoryOption[] {
  const support = feature.support as Record<string, unknown>;
  const selector = feature.selector as Record<string, unknown>;
  const raw = contract(feature).categories ?? support.categories ?? selector.categories;
  if (!Array.isArray(raw)) {
    return [];
  }
  const options: FilterCategoryOption[] = [];
  for (const item of raw) {
    if (typeof item === "string") {
      options.push({ value: item, label: item });
      continue;
    }
    if (typeof item !== "object" || item === null) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const value =
      typeof record.value === "string"
        ? record.value
        : typeof record.code === "string"
          ? record.code
          : typeof record.id === "string"
            ? record.id
            : null;
    if (value !== null) {
      options.push({
        value,
        label: typeof record.label === "string" ? record.label : value,
      });
    }
  }
  return options;
}

function hasCoverage(feature: RegistryFeatureOut): boolean {
  return (feature.coverage?.observation_count ?? 0) > 0;
}

/**
 * Builds a filter-only control from the live registry, or null when the feature has no filter
 * role. Missing coverage/domain/category registration is reported explicitly, never replaced by
 * a guess or a fake slider.
 */
export function dimensionFilter(feature: RegistryFeatureOut): DimensionFilterControl | null {
  if (!filterSupported(feature)) {
    return null;
  }
  const kind = filterKind(feature);
  const minValue = feature.min_value;
  const maxValue = feature.max_value;
  const categories = kind === "category" ? categoryOptions(feature) : [];
  const categoryFieldName = kind === "category" ? categoryField(feature) : null;
  let disabledReason: FilterDisabledReason | null = null;
  if (!hasCoverage(feature)) {
    disabledReason = "no_coverage";
  } else if (kind === "range" && (minValue === null || maxValue === null || minValue > maxValue)) {
    disabledReason = "no_domain";
  } else if (kind === "category" && categoryFieldName === null) {
    disabledReason = "category_filter_not_registered";
  } else if (kind === "category" && categories.length === 0) {
    disabledReason = "category_values_not_registered";
  }
  return {
    id: feature.feature_id,
    label: feature.label,
    unit: feature.unit,
    kind,
    minValue,
    maxValue,
    categories,
    categoryField: categoryFieldName,
    disabledReason,
  };
}

/** Numeric slider domain for a range filter, or null when the registry has no usable domain. */
export function filterSlider(control: DimensionFilterControl): SliderRange | null {
  if (
    control.kind !== "range" ||
    control.minValue === null ||
    control.maxValue === null ||
    control.minValue > control.maxValue
  ) {
    return null;
  }
  return sliderRange({
    id: control.id,
    unit: control.unit,
    min_value: control.minValue,
    max_value: control.maxValue,
  });
}

/**
 * Build fixed D01–D06 cards from the formal ontology codes.
 *
 * Roles and controls come from the live feature registry, never from a per-dimension literal:
 * similarity features (D01 + Earth D06 today) get score controls, filter-only features (D02–D05)
 * get filter controls, and evidence/context features get neither. D01 capability features are
 * kept even when the registry is unavailable, so existing D01 search always works.
 */
export function dimensionCards(
  features: FeatureOut[],
  coverage: { by_dimension?: Record<string, TypedDimensionSummary> } | null,
  observations: Array<{ dimension_code: string | null; value_kind: string }> = [],
  registry: FeatureRegistryOut | null = null,
): FormalDimensionCard[] {
  const registryFeatures = registry?.features ?? [];
  return DIMENSION_CODES.map((code) => {
    const summary = coverage?.by_dimension?.[code];
    const dimensionRegistry = registryFeatures.filter((feature) => feature.dimension_id === code);
    const valueKinds = Array.from(
      new Set(
        observations
          .filter((observation) => observation.dimension_code === code)
          .map((observation) => observation.value_kind),
      ),
    ).sort();

    const cardFeatures = features.filter((feature) =>
      code === "D01"
        ? !/^D0[2-6]$/.test(feature.dimension_id)
        : feature.dimension_id === code,
    );
    const seen = new Set(cardFeatures.map((feature) => feature.id));
    for (const entry of dimensionRegistry) {
      if (!similaritySupported(entry) || seen.has(entry.feature_id)) {
        continue;
      }
      const mapped = registrySimilarityFeature(entry);
      if (mapped) {
        cardFeatures.push(mapped);
        seen.add(mapped.id);
      }
    }

    const filters = dimensionRegistry
      .filter((entry) => !similaritySupported(entry))
      .map(dimensionFilter)
      .filter((control): control is DimensionFilterControl => control !== null);

    const role: DimensionRole = cardFeatures.length
      ? "similarity"
      : filters.length
        ? "filter"
        : dimensionRegistry.some(contextSupported)
          ? "context_only"
          : "evidence_only";

    const registryObservations = dimensionRegistry.reduce(
      (sum, entry) => sum + (entry.coverage?.observation_count ?? 0),
      0,
    );
    return {
      code,
      name: DIMENSION_NAME[code],
      role,
      features: cardFeatures,
      filters,
      total: summary?.total ?? (registry ? registryObservations : code === "D01" ? features.length : 0),
      byBody: summary?.by_body ?? {},
      byState: summary?.by_state ?? {},
      valueKinds,
    };
  });
}
