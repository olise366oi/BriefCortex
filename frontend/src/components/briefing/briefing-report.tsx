import { formatMeetingTime } from "@/lib";
import type { BriefingReport as BriefingReportType, EvidenceSummary } from "@/types";

import { EvidencePanel } from "./evidence-panel";
import { SectionCard } from "./section-card";

interface BriefingReportProps {
  companyName: string;
  meetingPurpose: string;
  meetingTime: string;
  report: BriefingReportType;
  evidence?: EvidenceSummary | null;
}

function capitalizeConfidence(value: string): string {
  if (!value) return value;
  return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
}

export function BriefingReportView({
  companyName,
  meetingPurpose,
  meetingTime,
  report,
  evidence,
}: BriefingReportProps) {
  const { executive_summary: summary } = report;

  return (
    <div className="flex flex-col gap-6">
      <div className="clay-surface-white p-7 flex flex-col gap-2">
        <h1 className="font-display font-extrabold text-3xl text-ink leading-tight">
          {companyName}
        </h1>
        <p className="font-body text-base font-medium text-ink">{meetingPurpose}</p>
        <p className="font-body text-sm text-ink/70">
          {formatMeetingTime(meetingTime)}
        </p>
      </div>

      <div className="clay-surface-grape p-7 flex flex-col gap-5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="font-display font-extrabold text-xl text-ink">
            Executive summary
          </h2>
          <div className="clay-pill bg-[#FFF9EB] px-4 py-1.5 flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-butter" />
            <span className="font-body font-bold text-xs text-ink">
              Confidence: {capitalizeConfidence(summary.confidence)}
            </span>
          </div>
        </div>

        <p className="font-body text-base font-medium text-ink leading-relaxed">
          {summary.summary}
        </p>

        <div className="clay-input p-4 bg-white/70 flex flex-col gap-1.5">
          <span className="font-display font-bold text-xs uppercase tracking-wider text-grape">
            Recommendation
          </span>
          <p className="font-body text-sm font-semibold text-ink leading-normal">
            {summary.recommendation}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-4">
        <SectionCard
          title="News"
          tone="sky"
          items={report.news.items}
          icon={
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-5 14H4v-4h11v4zm0-5H4V9h11v4zm5 5h-4V9h4v9z" />
            </svg>
          }
        />
        <SectionCard
          title="Hires"
          tone="mint"
          items={report.hires.items}
          icon={
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5s-3 1.34-3 3 1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z" />
            </svg>
          }
        />
        <SectionCard
          title="Launches"
          tone="grape"
          items={report.launches.items}
          icon={
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M13.13 2.51c.36-.78 1.38-.78 1.74 0l1.97 4.25c.15.32.44.54.79.59l4.63.67c.86.12 1.2 1.18.58 1.79l-3.35 3.27c-.26.25-.37.61-.31.96l.79 4.61c.15.86-.76 1.52-1.53 1.12l-4.14-2.18c-.31-.16-.69-.16-1 0l-4.14 2.18c-.77.4-1.68-.26-1.53-1.12l.79-4.61c.06-.35-.05-.71-.31-.96L4.2 9.81c-.62-.61-.28-1.67.58-1.79l4.63-.67c.35-.05.64-.27.79-.59l1.97-4.25h.96z" />
            </svg>
          }
        />
        <SectionCard
          title="Open questions"
          tone="butter"
          items={report.open_questions.items}
          icon={
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 16h-2v-2h2v2zm1.07-7.75l-.9.92C12.45 11.9 12 12.5 12 14h-2v-.5c0-1.1.45-2.1 1.17-2.83l1.24-1.26c.37-.36.59-.86.59-1.41 0-1.1-.9-2-2-2s-2 .9-2 2H7c0-2.76 2.24-5 5-5s5 2.24 5 5c0 1.04-.42 1.99-1.07 2.75z" />
            </svg>
          }
        />
        <SectionCard
          title="Risks"
          tone="coral"
          items={report.risks.items}
          icon={
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" />
            </svg>
          }
        />
      </div>

      {evidence ? <EvidencePanel evidence={evidence} /> : null}
    </div>
  );
}
