import type { MultiDimSearchRequest, NumericTarget, SearchRequest } from "../api/client";

export type SearchSubmission = {
  request: SearchRequest;
  /** Set when the query uses features beyond D01; the multidimensional endpoint is used. */
  multidim?: MultiDimSearchRequest;
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
