"use client";

import { useBriefing } from "@/hooks";

import { BriefingEmpty } from "./briefing-empty";
import { BriefingError } from "./briefing-error";
import { BriefingForm } from "./briefing-form";
import { BriefingHeader } from "./briefing-header";
import { BriefingLoading } from "./briefing-loading";
import { BriefingReportView } from "./briefing-report";

export function Briefing() {
  const {
    form,
    errors,
    uiState,
    status,
    errorMessage,
    isLoading,
    updateField,
    handleSubmit,
    retry,
  } = useBriefing();

  return (
    <div className="min-h-screen relative p-4 sm:p-8 flex flex-col items-center">
      <div className="blob-bg-1" />
      <div className="blob-bg-2" />

      <main className="relative z-10 w-full max-w-[1376px] flex flex-col gap-8">
        <BriefingHeader />

        <div className="grid grid-cols-12 gap-7 items-start">
          <BriefingForm
            form={form}
            errors={errors}
            isLoading={isLoading}
            onChange={updateField}
            onSubmit={handleSubmit}
          />

          <section className="col-span-12 lg:col-span-8 flex flex-col gap-6">
            {uiState === "empty" ? <BriefingEmpty /> : null}
            {uiState === "loading" ? <BriefingLoading /> : null}
            {uiState === "error" ? (
              <BriefingError message={errorMessage} onRetry={retry} />
            ) : null}
            {uiState === "ready" && status?.report ? (
              <BriefingReportView
                companyName={status.company_name}
                meetingPurpose={status.meeting_purpose}
                meetingTime={status.meeting_time}
                report={status.report}
              />
            ) : null}
          </section>
        </div>
      </main>
    </div>
  );
}
