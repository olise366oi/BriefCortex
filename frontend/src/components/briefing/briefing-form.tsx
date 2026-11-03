"use client";

import { FormEvent, ReactNode } from "react";

import { ClayButton } from "@/components/ui/clay-button";
import type { BriefingFormErrors, BriefingFormValues } from "@/types";

interface BriefingFormProps {
  form: BriefingFormValues;
  errors: BriefingFormErrors;
  isLoading: boolean;
  onChange: <K extends keyof BriefingFormValues>(
    key: K,
    value: BriefingFormValues[K]
  ) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

export function BriefingForm({
  form,
  errors,
  isLoading,
  onChange,
  onSubmit,
}: BriefingFormProps) {
  return (
    <aside className="col-span-12 lg:col-span-4 lg:sticky lg:top-6">
      <form
        onSubmit={onSubmit}
        className="clay-surface-white p-7 flex flex-col gap-6"
      >
        <h2 className="font-display font-extrabold text-2xl text-ink leading-tight">
          New brief
        </h2>

        <div className="flex flex-col gap-5">
          <Field
            label="Company name"
            error={errors.companyName}
            invalid={Boolean(errors.companyName)}
          >
            <input
              type="text"
              value={form.companyName}
              onChange={(e) => onChange("companyName", e.target.value)}
              disabled={isLoading}
              className="bg-transparent border-none outline-none font-display font-bold text-lg text-ink w-full p-0"
              placeholder="NIO"
            />
          </Field>

          <Field
            label="Meeting time"
            error={errors.meetingTime}
            invalid={Boolean(errors.meetingTime)}
          >
            <input
              type="datetime-local"
              value={form.meetingTime}
              onChange={(e) => onChange("meetingTime", e.target.value)}
              disabled={isLoading}
              className="bg-transparent border-none outline-none font-body font-medium text-sm text-ink w-full p-0"
            />
          </Field>

          <Field
            label="Meeting purpose"
            error={errors.meetingPurpose}
            invalid={Boolean(errors.meetingPurpose)}
          >
            <textarea
              value={form.meetingPurpose}
              onChange={(e) => onChange("meetingPurpose", e.target.value)}
              disabled={isLoading}
              rows={3}
              className="bg-transparent border-none outline-none font-body text-sm font-medium text-ink resize-none leading-relaxed w-full p-0"
              placeholder="Sourcing logistics services, aiming to lower costs"
            />
          </Field>

          <ClayButton
            type="submit"
            disabled={isLoading}
            className={`w-full py-3.5 px-6 text-base flex items-center justify-center gap-2.5 mt-1 ${
              isLoading ? "opacity-70 cursor-not-allowed" : "cursor-pointer"
            }`}
          >
            {isLoading ? (
              <>
                <Spinner />
                <span>Generating...</span>
              </>
            ) : (
              "Generate brief"
            )}
          </ClayButton>
        </div>
      </form>
    </aside>
  );
}

function Field({
  label,
  error,
  invalid,
  children,
}: {
  label: string;
  error?: string;
  invalid?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label className="font-body font-semibold text-xs text-ink uppercase tracking-wider">
        {label}
      </label>
      <div
        className={`clay-input px-4 py-3 ${
          invalid ? "bg-red-50/50 ring-1 ring-red-200" : ""
        }`}
      >
        {children}
      </div>
      {error ? (
        <span className="font-body font-semibold text-xs text-red-500 pl-1">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function Spinner() {
  return (
    <svg className="animate-spin w-4 h-4 text-white" fill="none" viewBox="0 0 24 24">
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
        fill="currentColor"
      />
    </svg>
  );
}
