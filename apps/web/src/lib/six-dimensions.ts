import type { TypedObservationOut } from "../api/client";

/**
 * Formal D01–D06 codes and display names. Names are copied from the committed ontology
 * (`contracts/ontology-v1.json`, `docs/ONTOLOGY.md`) and are UI metadata only. Observations,
 * values, states and roles are always taken from the API at render time.
 */
export const DIMENSION_CODES = ["D01", "D02", "D03", "D04", "D05", "D06"] as const;

export type DimensionCode = (typeof DIMENSION_CODES)[number];

export const DIMENSION_NAMES: Record<DimensionCode, string> = {
  D01: "Terrain & Landforms",
  D02: "Geology & Mineralogy",
  D03: "Thermal Environment",
  D04: "Water & Ice",
  D05: "Solar Illumination & Radiation",
  D06: "Regolith & Surface Properties",
};

const CODE_SET = new Set<string>(DIMENSION_CODES);

export function asDimensionCode(value: string | null | undefined): DimensionCode | null {
  if (value && CODE_SET.has(value)) {
    return value as DimensionCode;
  }
  return null;
}

export type SizedObservationGroups = {
  byDimension: Record<DimensionCode, TypedObservationOut[]>;
  contextOnly: TypedObservationOut[];
  unassigned: TypedObservationOut[];
};

/** Groups typed records by formal dimension code; context records stay separate. */
export function groupTypedObservations(
  observations: TypedObservationOut[],
): SizedObservationGroups {
  const byDimension = Object.fromEntries(
    DIMENSION_CODES.map((code) => [code, [] as TypedObservationOut[]]),
  ) as Record<DimensionCode, TypedObservationOut[]>;
  const contextOnly: TypedObservationOut[] = [];
  const unassigned: TypedObservationOut[] = [];
  for (const observation of observations) {
    const code = asDimensionCode(observation.dimension_code);
    if (code) {
      byDimension[code].push(observation);
    } else if (observation.score_role === "context_only") {
      contextOnly.push(observation);
    } else {
      unassigned.push(observation);
    }
  }
  return { byDimension, contextOnly, unassigned };
}

const FIXTURE_PATTERN = /(?:^|[-_/])(fixture|synthetic|demo|test)(?:$|[-_/])/i;

/**
 * A visible marker for fixture/test records. This checks identifiers, notes and quality flags
 * only; it never changes a value or converts an unverified row into an observed one.
 */
export function looksLikeFixture(...parts: (string | null | undefined)[]): boolean {
  return parts.some((part) => (part ? FIXTURE_PATTERN.test(part) : false));
}

export function observationLooksLikeFixture(observation: TypedObservationOut): boolean {
  return looksLikeFixture(
    observation.area_id,
    observation.observation_id,
    observation.notes,
    ...observation.quality_flags,
  );
}
