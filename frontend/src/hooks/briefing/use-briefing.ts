"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { createBriefing, getBriefingStatus } from "@/api";
import { POLL_INTERVAL_MS, POLL_TIMEOUT_MS } from "@/constants";
import { toDatetimeLocalValue, toIsoMeetingTime } from "@/lib";
import type {
  BriefingFormErrors,
  BriefingFormValues,
  BriefingStatusResponse,
  BriefingUiState,
} from "@/types";

const EMPTY_FORM: BriefingFormValues = {
  companyName: "",
  meetingTime: toDatetimeLocalValue(),
  meetingPurpose: "",
};

function validateForm(values: BriefingFormValues): BriefingFormErrors {
  const errors: BriefingFormErrors = {};

  if (!values.companyName.trim()) {
    errors.companyName = "Required";
  }
  if (!values.meetingTime.trim()) {
    errors.meetingTime = "Required";
  }
  if (!values.meetingPurpose.trim()) {
    errors.meetingPurpose = "Required";
  }

  return errors;
}

export function useBriefing() {
  const [form, setForm] = useState<BriefingFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<BriefingFormErrors>({});
  const [uiState, setUiState] = useState<BriefingUiState>("empty");
  const [status, setStatus] = useState<BriefingStatusResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef(false);

  const clearPoll = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      abortRef.current = true;
      clearPoll();
    };
  }, [clearPoll]);

  const pollStatus = useCallback(
    async (requestId: string, startedAt: number) => {
      if (abortRef.current) return;

      try {
        const next = await getBriefingStatus(requestId);
        if (abortRef.current) return;

        setStatus(next);

        if (next.status === "completed" && next.report) {
          setUiState("ready");
          clearPoll();
          return;
        }

        if (next.status === "failed") {
          setUiState("error");
          setErrorMessage(next.error || "Couldn't generate the brief.");
          clearPoll();
          return;
        }

        if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          setUiState("error");
          setErrorMessage("Brief generation timed out. Try again.");
          clearPoll();
          return;
        }

        pollTimerRef.current = setTimeout(() => {
          void pollStatus(requestId, startedAt);
        }, POLL_INTERVAL_MS);
      } catch {
        if (abortRef.current) return;
        setUiState("error");
        setErrorMessage("Couldn't generate the brief.");
        clearPoll();
      }
    },
    [clearPoll]
  );

  const submitBriefing = useCallback(async () => {
    const nextErrors = validateForm(form);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }

    abortRef.current = false;
    clearPoll();
    setUiState("loading");
    setErrorMessage(null);
    setStatus(null);

    try {
      const accepted = await createBriefing({
        company_name: form.companyName.trim(),
        meeting_time: toIsoMeetingTime(form.meetingTime),
        meeting_purpose: form.meetingPurpose.trim(),
      });

      await pollStatus(accepted.request_id, Date.now());
    } catch {
      setUiState("error");
      setErrorMessage("Couldn't generate the brief.");
    }
  }, [clearPoll, form, pollStatus]);

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      void submitBriefing();
    },
    [submitBriefing]
  );

  const updateField = useCallback(
    <K extends keyof BriefingFormValues>(key: K, value: BriefingFormValues[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }));
      setErrors((prev) => ({ ...prev, [key]: undefined }));
    },
    []
  );

  const retry = useCallback(() => {
    void submitBriefing();
  }, [submitBriefing]);

  return {
    form,
    errors,
    uiState,
    status,
    errorMessage,
    isLoading: uiState === "loading",
    updateField,
    handleSubmit,
    retry,
  };
}
