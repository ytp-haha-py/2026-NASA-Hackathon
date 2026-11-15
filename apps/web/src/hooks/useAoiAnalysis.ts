import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  analyzeArea,
  errorCode,
  errorMessage,
  getAreaFeatures,
  getJob,
  isAbortError,
  type AoiAnalyzeRequest,
  type AoiAnalyzeResponse,
  type JobOut,
  type LocationOut,
} from "../api/client";

export type AoiErrorState = {
  message: string;
  code: string | null;
  status: number | null;
  details: { [key: string]: unknown }[];
};

type JobStart = Pick<
  AoiAnalyzeResponse,
  "job_id" | "area_id" | "status" | "provider_status" | "cached"
>;

export type AoiAnalysis = {
  submitting: boolean;
  analyzeResponse: JobStart | null;
  job: JobOut | null;
  location: LocationOut | null;
  requestError: AoiErrorState | null;
  pollError: AoiErrorState | null;
  featureError: AoiErrorState | null;
  status: JobOut["status"] | null;
  areaId: string | null;
  busy: boolean;
  submit: (request: AoiAnalyzeRequest) => void;
  submitJob: (start: (signal: AbortSignal) => Promise<JobStart>) => void;
  retry: () => void;
};

function toAoiError(error: unknown): AoiErrorState {
  return {
    message: errorMessage(error),
    code: errorCode(error),
    status: error instanceof ApiError ? error.status : null,
    details: error instanceof ApiError ? error.details : [],
  };
}

export function useAoiAnalysis(pollIntervalMs = 1000): AoiAnalysis {
  const [submitting, setSubmitting] = useState(false);
  const [analyzeResponse, setAnalyzeResponse] = useState<JobStart | null>(null);
  const [job, setJob] = useState<JobOut | null>(null);
  const [location, setLocation] = useState<LocationOut | null>(null);
  const [requestError, setRequestError] = useState<AoiErrorState | null>(null);
  const [pollError, setPollError] = useState<AoiErrorState | null>(null);
  const [featureError, setFeatureError] = useState<AoiErrorState | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [pollKey, setPollKey] = useState(0);
  const submitController = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => submitController.current?.abort();
  }, []);

  const submitJob = useCallback((start: (signal: AbortSignal) => Promise<JobStart>) => {
    submitController.current?.abort();
    const controller = new AbortController();
    submitController.current = controller;

    setSubmitting(true);
    setAnalyzeResponse(null);
    setJob(null);
    setLocation(null);
    setRequestError(null);
    setPollError(null);
    setFeatureError(null);
    setJobId(null);

    start(controller.signal)
      .then((response) => {
        if (controller.signal.aborted) {
          return;
        }
        setAnalyzeResponse(response);
        setSubmitting(false);
        setJobId(response.job_id);
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught)) {
          return;
        }
        setRequestError(toAoiError(caught));
        setSubmitting(false);
      });
  }, []);

  const submit = useCallback((request: AoiAnalyzeRequest) => {
    submitJob((signal) => analyzeArea(request, signal));
  }, [submitJob]);

  useEffect(() => {
    if (!jobId) {
      return;
    }

    const controller = new AbortController();
    const activeJobId = jobId;
    let stopped = false;
    let timer: number | undefined;

    async function loadFeatures(areaId: string) {
      try {
        const payload = await getAreaFeatures(areaId, controller.signal);
        if (!stopped) {
          setLocation(payload);
          setFeatureError(null);
        }
      } catch (caught: unknown) {
        if (stopped || isAbortError(caught)) {
          return;
        }
        setFeatureError(toAoiError(caught));
      }
    }

    async function tick() {
      try {
        const nextJob = await getJob(activeJobId, controller.signal);
        if (stopped) {
          return;
        }
        setJob(nextJob);
        setPollError(null);

        if (nextJob.status === "failed") {
          return;
        }
        if (nextJob.status === "completed" || nextJob.status === "partial") {
          if (nextJob.area_id) {
            await loadFeatures(nextJob.area_id);
          } else {
            setFeatureError({
              message: "The job completed without an area identifier.",
              code: "missing_area_id",
              status: null,
              details: [],
            });
          }
          return;
        }

        timer = window.setTimeout(tick, pollIntervalMs);
      } catch (caught: unknown) {
        if (stopped || isAbortError(caught)) {
          return;
        }
        setPollError(toAoiError(caught));
      }
    }

    void tick();

    return () => {
      stopped = true;
      controller.abort();
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [jobId, pollIntervalMs, pollKey]);

  const retry = useCallback(() => {
    setPollError(null);
    setFeatureError(null);
    setPollKey((key) => key + 1);
  }, []);

  const status = job?.status ?? analyzeResponse?.status ?? null;
  const areaId = job?.area_id ?? analyzeResponse?.area_id ?? null;
  const busy =
    submitting || status === "queued" || status === "running";

  return {
    submitting,
    analyzeResponse,
    job,
    location,
    requestError,
    pollError,
    featureError,
    status,
    areaId,
    busy,
    submit,
    submitJob,
    retry,
  };
}
