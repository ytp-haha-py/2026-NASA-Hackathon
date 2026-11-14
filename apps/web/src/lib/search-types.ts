import type { NumericTarget, SearchRequest } from "../api/client";

export type SearchSubmission = {
  request: SearchRequest;
  target: NumericTarget;
  body: "custom" | "Moon" | "Mars";
  referenceId: string | null;
  limit: number;
};

export type SearchTargetPreset = {
  nonce: number;
  values: NumericTarget;
  sourceLabel: string;
};
