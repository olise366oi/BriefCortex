import { getHostname } from "@/lib";
import type { EvidenceSummary } from "@/types";

import { SectionCard } from "./section-card";

interface EvidencePanelProps {
  evidence: EvidenceSummary;
}

/**
 * Provenance beside the brief: what the evidence contained, and what it
 * contradicted. Conflicts are computed by the deterministic engine and never
 * reach the model, so this is the only place a reader can see them.
 */
export function EvidencePanel({ evidence }: EvidencePanelProps) {
  const hasConflicts = evidence.conflicts.length > 0;

  return (
    <div className="flex flex-col gap-5">
      <dl className="grid grid-cols-3 gap-3">
        <Stat label="Hard facts" value={evidence.hard_fact_count} />
        <Stat label="Opinions" value={evidence.opinion_count} />
        <Stat label="Conflicts" value={evidence.conflict_count} tone={hasConflicts} />
      </dl>

      {hasConflicts ? (
        <div className="clay-surface-coral p-5 flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <span className="clay-token bg-coral text-white font-display font-extrabold text-xs px-2.5 py-0.5">
              {evidence.conflicts.length}
            </span>
            <h3 className="font-display font-extrabold text-lg text-ink">
              Evidence conflicts
            </h3>
          </div>
          <p className="font-body text-xs text-ink/70">
            Two sources assert incompatible facts. Neither is used as leverage
            until you decide which holds.
          </p>
          <div className="flex flex-col gap-3">
            {evidence.conflicts.map((conflict, index) => (
              <div
                className="clay-input p-3.5 bg-white/80 flex flex-col gap-1.5"
                key={`${conflict.conflict_type}-${index}`}
              >
                <span className="font-display font-bold text-xs uppercase tracking-wider text-coral">
                  {conflict.conflict_type}
                </span>
                <ConflictSide label="A" text={conflict.fact_a} source={conflict.source_a} />
                <ConflictSide label="B" text={conflict.fact_b} source={conflict.source_b} />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {evidence.hard_facts.length > 0 ? (
        <div className="clay-surface-white p-5 flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <h3 className="font-display font-extrabold text-lg text-ink">
              Evidence behind the brief
            </h3>
            <span className="clay-token bg-grape text-white font-display font-extrabold text-xs px-2.5 py-0.5">
              {evidence.hard_facts.length}
            </span>
          </div>
          <div className="flex flex-col gap-2">
            {evidence.hard_facts.map((item, index) => (
              <div
                className="clay-input p-3 bg-white/80 flex flex-col gap-1.5"
                key={`${item.source_url}-${index}`}
              >
                <p className="font-body text-sm font-medium text-ink">
                  {item.content}
                </p>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <SourceChip url={item.source_url} primary />
                  {item.also_reported_by.map((url) => (
                    <SourceChip key={url} url={url} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {evidence.incidental_facts.length > 0 ? (
        <SectionCard
          title="Also reported"
          tone="butter"
          items={evidence.incidental_facts.map((item) => ({
            claim: buildIncidentalLabel(item),
            source_url: item.corroborating_url ?? item.source_url,
          }))}
          icon={
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z" />
            </svg>
          }
        />
      ) : null}
    </div>
  );
}

function buildIncidentalLabel(item: {
  content: string;
  provenance: string;
  article_subject?: string | null;
}): string {
  const tag =
    item.provenance === "corroborated" ? "[CORROBORATED]" : "[UNVERIFIED]";
  const context = item.article_subject ? ` (${item.article_subject})` : "";
  return `${item.content} ${tag}${context}`;
}

function SourceChip({ url, primary = false }: { url: string; primary?: boolean }) {
  return (
    <a
      className={`clay-source-chip px-2.5 py-1 no-underline flex items-center gap-1 ${
        primary ? "bg-grape/10" : ""
      }`}
      href={url}
      rel="noopener noreferrer"
      target="_blank"
    >
      <span className="font-mono text-[11px] text-ink/70">
        {getHostname(url)}
      </span>
      <span className="text-ink/50 text-[11px]">↗</span>
    </a>
  );
}

function ConflictSide({
  label,
  text,
  source,
}: {
  label: string;
  text: string;
  source: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <p className="font-body text-sm font-medium text-ink">
        <span className="font-display font-extrabold text-xs text-ink/60 mr-1.5">
          {label}
        </span>
        {text}
      </p>
      <a
        className="font-mono text-xs text-ink/50 no-underline hover:text-grape"
        href={source}
        rel="noopener noreferrer"
        target="_blank"
      >
        {getHostname(source)} ↗
      </a>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = false,
}: {
  label: string;
  value: number;
  tone?: boolean;
}) {
  return (
    <div className="clay-pill bg-white px-3 py-2 flex flex-col">
      <dt className="font-body font-semibold text-[10px] uppercase tracking-wider text-ink/50">
        {label}
      </dt>
      <dd
        className={`font-display font-extrabold text-lg ${
          tone ? "text-coral" : "text-ink"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}
