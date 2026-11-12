"use client";

import { useBriefing } from "@/hooks";

import { BriefingEmpty } from "./briefing-empty";
import { BriefingError } from "./briefing-error";
import { BriefingForm } from "./briefing-form";
import { BriefingLoading } from "./briefing-loading";
import { BriefingReportView } from "./briefing-report";
import { BriefingsSidePanel } from "./briefings-side-panel";

export function Briefing() {
  const {
    form,
    errors,
    uiState,
    status,
    errorMessage,
    notice,
    briefings,
    activeId,
    isLoading,
    updateField,
    handleSubmit,
    retry,
    selectBriefing,
    startNewMeeting,
    cloneBriefing,
  } = useBriefing();

  return (
    <div className="min-h-screen relative p-4 sm:p-8 flex flex-col items-center">
      <div className="blob-bg-1" />
      <div className="blob-bg-2" />

      <main className="relative z-10 w-full max-w-[1400px] flex gap-6 items-stretch">
        <BriefingsSidePanel
          items={briefings}
          activeId={activeId}
          onSelect={(id) => {
            void selectBriefing(id);
          }}
          onNewMeeting={startNewMeeting}
          onClone={cloneBriefing}
        />

        <div className="flex-1 min-w-0 grid grid-cols-12 gap-7 items-start">
          <BriefingForm
            form={form}
            errors={errors}
            isLoading={isLoading}
            onChange={updateField}
            onSubmit={handleSubmit}
          />

          <section className="col-span-12 lg:col-span-8 flex flex-col gap-6">
            {uiState === "empty" ? <BriefingEmpty /> : null}
            {uiState === "loading" ? <BriefingLoading notice={notice} /> : null}
            {uiState === "error" ? (
              <BriefingError message={errorMessage} onRetry={retry} />
            ) : null}
            {uiState === "ready" && status?.report ? (
              <BriefingReportView
                companyName={status.company_name}
                meetingPurpose={status.meeting_purpose}
                meetingTime={status.meeting_time}
                report={status.report}
                evidence={status.evidence}
              />
            ) : null}
          </section>
        </div>
      </main>
    </div>
  );
}
