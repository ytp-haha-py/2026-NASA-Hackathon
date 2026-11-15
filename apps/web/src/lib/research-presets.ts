import type { FeatureOut } from "../api/client";
import type { MessageKey } from "../locales/en/messages";

/** Analyst-designed importance ratings, not NASA-published or validated weights. */
export const RESEARCH_FEATURE_IDS = [
  "slope_mean_deg",
  "slope_p90_deg",
  "terrain_relief_p95_p05_m",
  "roughness_std_m",
  "flat_terrain_fraction",
] as const;

type ResearchFeatureId = (typeof RESEARCH_FEATURE_IDS)[number];
export type ResearchPreset = {
  id: string;
  titleKey: MessageKey;
  /** Menu group; new scenarios join an existing group or add one. */
  groupKey: MessageKey;
  descriptionKey: MessageKey;
  contextKey: MessageKey;
  limitationKey: MessageKey;
  importance: Readonly<Record<ResearchFeatureId, number>>;
  reference: { label: string; url: string };
};

export const RESEARCH_PRESETS: readonly ResearchPreset[] = [
  {
    id: "rover-mobility",
    titleKey: "research.roverTitle",
    groupKey: "research.groupMobility",
    descriptionKey: "research.roverDescription",
    contextKey: "research.roverContext",
    limitationKey: "research.roverLimitation",
    importance: {
      slope_mean_deg: 4,
      slope_p90_deg: 5,
      terrain_relief_p95_p05_m: 2,
      roughness_std_m: 4,
      flat_terrain_fraction: 2,
    },
    reference: {
      label: "NASA JPL — ERNEST",
      url: "https://www.jpl.nasa.gov/news/nasa-testing-advanced-capabilities-for-moon-mars-rovers/",
    },
  },
  {
    id: "precision-landing",
    titleKey: "research.landingTitle",
    groupKey: "research.groupMobility",
    descriptionKey: "research.landingDescription",
    contextKey: "research.landingContext",
    limitationKey: "research.landingLimitation",
    importance: {
      slope_mean_deg: 2,
      slope_p90_deg: 5,
      terrain_relief_p95_p05_m: 1,
      roughness_std_m: 4,
      flat_terrain_fraction: 5,
    },
    reference: {
      label: "NASA — SPLICE",
      url: "https://www.nasa.gov/safe-and-precise-landing-integrated-capabilities-evolution-splice/",
    },
  },
  {
    id: "geological-fieldwork",
    titleKey: "research.fieldworkTitle",
    groupKey: "research.groupScience",
    descriptionKey: "research.fieldworkDescription",
    contextKey: "research.fieldworkContext",
    limitationKey: "research.fieldworkLimitation",
    importance: {
      slope_mean_deg: 3,
      slope_p90_deg: 2,
      terrain_relief_p95_p05_m: 5,
      roughness_std_m: 4,
      flat_terrain_fraction: 1,
    },
    reference: {
      label: "NASA — Artemis JETT / D-RATS",
      url: "https://www.nasa.gov/humans-in-space/nasa-to-practice-artemis-moonwalking-roving-operations-in-arizona-desert/",
    },
  },
];

/** Fail closed rather than silently applying a different, partial research profile. */
export function unavailableResearchFeatures(features: FeatureOut[]): readonly ResearchFeatureId[] {
  return RESEARCH_FEATURE_IDS.filter((id) => !features.some((feature) => (
    feature.id === id &&
    feature.dimension_id === "terrain_shape" &&
    feature.value_kind === "numeric" &&
    feature.score_role === "similarity"
  )));
}

/** r/2 fits the existing sliders; the backend normalizes the common factor away. */
export function researchWeights(
  preset: ResearchPreset,
  features: FeatureOut[],
): Record<string, string> {
  if (unavailableResearchFeatures(features).length) return {};
  const weights: Record<string, number> = preset.importance;
  return Object.fromEntries(features.map((feature) => [
    feature.id, String((weights[feature.id] ?? 0) / 2),
  ]));
}
