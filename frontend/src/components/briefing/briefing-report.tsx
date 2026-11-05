import { formatMeetingTime, getHostname } from "@/lib";
import type {
  BriefingReport as BriefingReportType,
  EvidenceSummary,
} from "@/types";

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
  const analysis = report.analysis;
  const changes = report.changes_since_previous ?? [];
  const removed = report.removed_claims ?? [];
  const possible = report.possible_facts ?? [];

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

      {/*
        The reader sees what the company has done before it sees a judgement. A
        recommendation that arrives first is read as a conclusion the facts have
        to support, rather than as one opinion drawn from them.
      */}
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
          title="Hiring"
          tone="mint"
          items={report.hires.items}
          icon={
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5s-3 1.34-3 3 1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z" />
            </svg>
          }
        />
      </div>

      {/*
        The assessment: what the news and postings amount to, and where the risk
        sits. It sits before the recommendation so the advice is a conclusion
        rather than an assertion.
      */}
      {analysis && (analysis.assessment || analysis.risks.length > 0) ? (
        <div className="clay-surface-white p-7 flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-2xl bg-white font-display font-extrabold text-base flex items-center justify-center shadow-[3px_4px_8px_rgba(91,195,255,0.2)] text-grape">
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M3 3h18v2H3V3zm0 4h18v2H3V7zm0 4h12v2H3v-2zm0 4h18v2H3v-2zm0 4h12v2H3v-2z" />
              </svg>
            </div>
            <h3 className="font-display font-extrabold text-lg text-ink">Analysis</h3>
          </div>
          {analysis.assessment ? (
            <p className="font-body text-sm text-ink leading-relaxed">
              {analysis.assessment}
            </p>
          ) : null}
          {analysis.risks.length > 0 ? (
            <div className="flex flex-col gap-2">
              <span className="font-display font-bold text-xs uppercase tracking-wider text-coral">
                Risks
              </span>
              {analysis.risks.map((risk, index) => (
                <span
                  key={`${risk}-${index}`}
                  className="font-body text-sm text-ink/80 leading-relaxed"
                >
                  {risk}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="clay-surface-grape p-7 flex flex-col gap-5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="font-display font-extrabold text-xl text-ink">
            Recommendation
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

      {/*
        Only rendered when a later brief exists to differ from. A first brief
        that announced "no changes" was comparing itself with nothing.
      */}
      {changes.length > 0 ? (
        <div className="clay-surface-butter p-7 flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-2xl bg-white font-display font-extrabold text-base flex items-center justify-center shadow-[3px_4px_8px_rgba(255,209,102,0.25)] text-ink">
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z" />
              </svg>
            </div>
            <h3 className="font-display font-extrabold text-lg text-ink">
              Changes since the previous brief
            </h3>
          </div>
          <div className="flex flex-col gap-3">
            {changes.map((note) => (
              <div
                key={`${note.change}-${note.why}`}
                className="clay-input p-3.5 bg-white/80 flex flex-col gap-1"
              >
                <span className="font-body text-sm font-semibold text-ink">
                  {note.change}
                </span>
                <span className="font-body text-xs text-ink/70">{note.why}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/*
        Facts found inside articles about other companies, with no follow-up
        reporting behind them. Kept out of the findings above because the article
        is not about this company; shown with its link so a reader can judge it.
      */}
      {possible.length > 0 ? (
        <div className="clay-surface-white p-7 flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-2xl bg-white font-display font-extrabold text-base flex items-center justify-center shadow-[3px_4px_8px_rgba(124,92,255,0.18)] text-grape">
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z" />
              </svg>
            </div>
            <div className="flex flex-col">
              <h3 className="font-display font-extrabold text-lg text-ink">
                Possible facts
              </h3>
              <span className="font-body text-xs text-ink/60">
                Found in articles about other companies; not confirmed
              </span>
            </div>
            <span className="clay-token font-display font-extrabold text-xs px-2.5 py-0.5 bg-grape text-white">
              {possible.length}
            </span>
          </div>
          <div className="flex flex-col gap-3">
            {possible.map((item, index) => (
              <div
                key={`${item.source_url}-${index}`}
                className="clay-input p-3.5 bg-white/80 flex flex-col gap-1.5"
              >
                <span className="font-body text-sm font-medium text-ink">
                  {item.claim}
                </span>
                {item.article_subject ? (
                  <span className="font-body text-xs text-ink/60">
                    from an article about {item.article_subject}
                  </span>
                ) : null}
                <a
                  className="clay-source-chip px-3 py-1 flex items-center gap-1.5 self-start no-underline"
                  href={item.source_url}
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  <span className="font-mono text-xs text-ink/70">
                    {getHostname(item.source_url)}
                  </span>
                  <span className="text-ink/60 text-xs">↗</span>
                </a>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/*
        What the audit rejected. Listed so the brief does not look cleaner than
        the evidence allows: the reader is told something was removed, and why,
        instead of the removal being silent.
      */}
      {removed.length > 0 ? (
        <div className="clay-input p-4 bg-white/70 flex flex-col gap-2">
          <span className="font-display font-bold text-xs uppercase tracking-wider text-coral">
            Removed before delivery ({removed.length})
          </span>
          {removed.map((item, index) => (
            <span key={`${item.claim}-${index}`} className="font-body text-xs text-ink/70">
              {item.section}: {item.claim} — {item.remedy}
            </span>
          ))}
        </div>
      ) : null}

      {evidence ? <EvidencePanel evidence={evidence} /> : null}
    </div>
  );
}
