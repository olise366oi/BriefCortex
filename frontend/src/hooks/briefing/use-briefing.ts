"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { createBriefing, getBriefing, listBriefings } from "@/api";
import {
  POLL_INTERVAL_MS,
  POLL_MAX_TRANSPORT_FAILURES,
  POLL_SLOW_NOTICE_MS,
} from "@/constants";
import {
  isoToDatetimeLocalValue,
  toDatetimeLocalValue,
  toIsoMeetingTime,
} from "@/lib";
import type {
  BriefingFormErrors,
  BriefingFormValues,
  BriefingListItem,
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

function applyStatusToUi(next: BriefingStatusResponse): {
  uiState: BriefingUiState;
  errorMessage: string | null;
} {
  if (next.status === "completed" && next.report) {
    return { uiState: "ready", errorMessage: null };
  }
  if (next.status === "failed") {
    return {
      uiState: "error",
      errorMessage: next.error || "Couldn't generate the brief.",
    };
  }
  if (next.status === "pending" || next.status === "processing") {
    return { uiState: "loading", errorMessage: null };
  }
  return { uiState: "empty", errorMessage: null };
}

export function useBriefing() {
  const [form, setForm] = useState<BriefingFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<BriefingFormErrors>({});
  const [uiState, setUiState] = useState<BriefingUiState>("empty");
  const [status, setStatus] = useState<BriefingStatusResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [briefings, setBriefings] = useState<BriefingListItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  // Non-blocking status line for a run that is simply long; not an error.
  const [notice, setNotice] = useState<string | null>(null);

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef(false);
  // Consecutive transport failures. A restart or a proxy hiccup should not
  // discard a brief the backend is still writing.
  const failuresRef = useRef(0);

  const clearPoll = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const refreshBriefings = useCallback(async () => {
    try {
      const response = await listBriefings();
      setBriefings(response.items);
    } catch {
      // Keep existing list if refresh fails.
    }
  }, []);

  useEffect(() => {
    void refreshBriefings();
  }, [refreshBriefings]);

  useEffect(() => {
    return () => {
      abortRef.current = true;
      clearPoll();
    };
  }, [clearPoll]);

  const pollStatus = useCallback(
    async (briefingId: string, startedAt: number) => {
      if (abortRef.current) return;

      try {
        const next = await getBriefing(briefingId);
        if (abortRef.current) return;

        failuresRef.current = 0;
        setStatus(next);
        setActiveId(next.id);

        if (next.status === "completed" && next.report) {
          setNotice(null);
          setUiState("ready");
          clearPoll();
          void refreshBriefings();
          return;
        }

        if (next.status === "failed") {
          setNotice(null);
          setUiState("error");
          setErrorMessage(next.error || "Couldn't generate the brief.");
          clearPoll();
          void refreshBriefings();
          return;
        }

        // No wall-clock deadline: the backend keeps working and the brief is
        // saved when it finishes. Long runs get a notice, not an error.
        if (Date.now() - startedAt > POLL_SLOW_NOTICE_MS) {
          setNotice(
            "Still working — a large evidence set can take several minutes. " +
              "The brief is saved when it finishes; you can also reopen it from the list."
          );
        }

        pollTimerRef.current = setTimeout(() => {
          void pollStatus(briefingId, startedAt);
        }, POLL_INTERVAL_MS);
      } catch {
        if (abortRef.current) return;

        failuresRef.current += 1;
        if (failuresRef.current > POLL_MAX_TRANSPORT_FAILURES) {
          setUiState("error");
          setErrorMessage("Lost contact with the backend while the brief was running.");
          clearPoll();
          return;
        }

        // Retry rather than giving up: the job may still be completing.
        pollTimerRef.current = setTimeout(() => {
          void pollStatus(briefingId, startedAt);
        }, POLL_INTERVAL_MS);
      }
    },
    [clearPoll, refreshBriefings]
  );

  const startNewMeeting = useCallback(() => {
    abortRef.current = true;
    clearPoll();
    abortRef.current = false;
    setActiveId(null);
    setStatus(null);
    setForm({
      companyName: "",
      meetingTime: toDatetimeLocalValue(),
      meetingPurpose: "",
    });
    setErrors({});
    setErrorMessage(null);
    setNotice(null);
    failuresRef.current = 0;
    setUiState("empty");
  }, [clearPoll]);

  const selectBriefing = useCallback(
    async (briefingId: string) => {
      abortRef.current = false;
      clearPoll();
      setActiveId(briefingId);
      setErrors({});
      setErrorMessage(null);
      setNotice(null);
      failuresRef.current = 0;
      setUiState("loading");

      try {
        const next = await getBriefing(briefingId);
        if (abortRef.current) return;

        setStatus(next);
        setForm({
          companyName: next.company_name,
          meetingTime: isoToDatetimeLocalValue(next.meeting_time),
          meetingPurpose: next.meeting_purpose,
        });

        const mapped = applyStatusToUi(next);
        setUiState(mapped.uiState);
        setErrorMessage(mapped.errorMessage);

        if (next.status === "pending" || next.status === "processing") {
          await pollStatus(briefingId, Date.now());
        }
      } catch {
        setUiState("error");
        setErrorMessage("Couldn't load the brief.");
      }
    },
    [clearPoll, pollStatus]
  );

  const cloneBriefing = useCallback(
    (item: BriefingListItem) => {
      abortRef.current = true;
      clearPoll();
      abortRef.current = false;
      setActiveId(null);
      setStatus(null);
      setForm({
        companyName: item.company_name,
        meetingTime: isoToDatetimeLocalValue(item.meeting_time),
        meetingPurpose: item.meeting_purpose,
      });
      setErrors({});
      setErrorMessage(null);
      setUiState("empty");
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
    setNotice(null);
    failuresRef.current = 0;
    setStatus(null);
    setActiveId(null);

    try {
      const accepted = await createBriefing({
        company_name: form.companyName.trim(),
        meeting_time: toIsoMeetingTime(form.meetingTime),
        meeting_purpose: form.meetingPurpose.trim(),
      });

      setActiveId(accepted.id);
      await refreshBriefings();
      await pollStatus(accepted.id, Date.now());
    } catch {
      setUiState("error");
      setErrorMessage("Couldn't generate the brief.");
    }
  }, [clearPoll, form, pollStatus, refreshBriefings]);

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
    notice,
    briefings,
    activeId,
    isLoading: uiState === "loading",
    updateField,
    handleSubmit,
    retry,
    selectBriefing,
    startNewMeeting,
    cloneBriefing,
  };
}
