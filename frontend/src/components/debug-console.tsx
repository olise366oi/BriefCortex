"use client";

import { useState } from "react";

import { runDebug } from "@/api/debug";
import type { DebugIdentityCandidate, DebugRunResponse } from "@/types";

/**
 * Temporary console for watching a run while the pipeline is being built.
 *
 * It submits a company, waits for the full trace, and prints everything the
 * pipeline decided: identity match, per-lane counts, coverage verdict, window,
 * and the report. Not linked from the app; delete it once the report view
 * carries the same detail.
 */
export function DebugConsole() {
  const [company, setCompany] = useState("Zydus Lifesciences");
  const [purpose, setPurpose] = useState("");
  const [meetingTime, setMeetingTime] = useState(defaultMeetingTime());
  const [jurisdiction, setJurisdiction] = useState<string | null>(null);
  const [result, setResult] = useState<DebugRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function handleRun(chosenJurisdiction: string | null = jurisdiction) {
    if (!company.trim()) {
      setError("Company name is required.");
      return;
    }

    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const payload = await runDebug({
        company_name: company.trim(),
        meeting_purpose: purpose.trim(),
        // The backend takes a meeting date, not a day count: this console simply
        // defaults it to six days ahead.
        meeting_time: toIso(meetingTime),
        jurisdiction: chosenJurisdiction ?? undefined,
      });
      setResult(payload);
      setJurisdiction(chosenJurisdiction);
    } catch {
      setError("Request failed. Check the backend log.");
    } finally {
      setRunning(false);
    }
  }

  const candidates = result?.identity_candidates ?? [];
  const needsChoice = Boolean(result?.needs_disambiguation) && candidates.length > 0;

  return (
    <div className="min-h-screen bg-slate-100 p-6 font-mono text-[13px] text-slate-800">
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="space-y-1">
          <h1 className="text-lg font-bold">Pipeline console</h1>
          <p className="text-slate-500">
            Runs one briefing and prints the full decision trace. Development
            tool only.
          </p>
        </header>

        <div className="flex flex-wrap gap-2">
          <input
            className="min-w-[220px] flex-1 rounded border border-slate-300 px-3 py-2"
            placeholder="company name"
            value={company}
            onChange={(event) => {
              setCompany(event.target.value);
              setJurisdiction(null);
            }}
          />
          <input
            className="min-w-[200px] flex-1 rounded border border-slate-300 px-3 py-2"
            placeholder="meeting purpose (optional)"
            value={purpose}
            onChange={(event) => setPurpose(event.target.value)}
          />
          <input
            className="rounded border border-slate-300 px-3 py-2"
            type="datetime-local"
            value={meetingTime}
            onChange={(event) => setMeetingTime(event.target.value)}
          />
          <button
            className="rounded bg-slate-800 px-5 py-2 text-white disabled:opacity-50"
            disabled={running}
            onClick={() => void handleRun()}
            type="button"
          >
            {running ? "Running..." : "Run"}
          </button>
        </div>

        <p className="text-xs text-slate-500">
          A full run takes 1 to 3 minutes: retrieval, then the model. The page
          updates when it finishes.
        </p>

        {error ? <p className="text-red-600">{error}</p> : null}

        {needsChoice ? (
          <Block title="Which company did you mean?">
            <p className="text-slate-600">
              The name matched more than one registered entity. Pick the one you
              mean, then run again.
            </p>
            <div className="mt-2 flex flex-col gap-2">
              {candidates.map((candidate) => (
                <button
                  className="rounded border border-slate-300 bg-white px-3 py-2 text-left hover:border-slate-500"
                  disabled={running}
                  key={`${candidate.name}-${candidate.jurisdiction}`}
                  onClick={() => void handleRun(candidate.jurisdiction ?? null)}
                  type="button"
                >
                  <span className="font-bold">{candidate.name}</span>
                  <span className="ml-2 text-slate-500">
                    {candidate.jurisdiction ?? "?"} · {candidate.status ?? "?"} ·
                    score {candidate.confidence.toFixed(2)}
                  </span>
                </button>
              ))}
            </div>
          </Block>
        ) : null}

        {result ? (
          <div className="space-y-5">
            <Block title="Counts">
              <pre className="whitespace-pre-wrap">{formatJson(result.counts)}</pre>
            </Block>

            <Block title="Timings">
              <pre className="whitespace-pre-wrap">{formatJson(result.timings)}</pre>
            </Block>

            <Block title="Identity">
              <pre className="whitespace-pre-wrap">{formatJson(result.identity)}</pre>
              {candidates.length > 0 ? (
                <pre className="mt-2 whitespace-pre-wrap text-slate-500">
                  candidates: {formatJson(candidates)}
                </pre>
              ) : null}
            </Block>

            <Block title="Evidence window">
              <pre className="whitespace-pre-wrap">{formatJson(result.window)}</pre>
            </Block>

            <Block title={`Coverage (${result.coverage?.level ?? "none"})`}>
              <pre className="whitespace-pre-wrap">{formatJson(result.coverage)}</pre>
            </Block>

            {result.error ? (
              <Block title="Error">
                <p className="text-red-600">{result.error}</p>
              </Block>
            ) : null}

            <FactLane title={`Hard facts (${result.hard_facts.length})`} facts={result.hard_facts} />
            <FactLane title={`Hiring (${result.hiring.length})`} facts={result.hiring} />
            <FactLane title={`Opinions (${result.opinions.length})`} facts={result.opinions} />
            <FactLane
              title={`Incidental (${result.incidental.length})`}
              facts={result.incidental}
            />

            {result.conflicts.length > 0 ? (
              <Block title={`Conflicts (${result.conflicts.length})`}>
                <pre className="whitespace-pre-wrap">{formatJson(result.conflicts)}</pre>
              </Block>
            ) : null}

            {result.report ? (
              <Block title="Report">
                <ReportView report={result.report} />
              </Block>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function defaultMeetingTime(): string {
  const date = new Date();
  date.setDate(date.getDate() + 6);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toIso(localValue: string): string | undefined {
  const date = new Date(localValue);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function FactLane({
  title,
  facts,
}: {
  title: string;
  facts: DebugRunResponse["hard_facts"];
}) {
  return (
    <Block title={title}>
      {facts.length === 0 ? (
        <p className="text-slate-500">(empty)</p>
      ) : (
        <ol className="space-y-1.5">
          {facts.map((fact, index) => (
            <li key={`${fact.source_url}-${index}`} className="flex flex-col">
              <span>{fact.content}</span>
              <span className="text-[11px] text-slate-500">
                {fact.source_url}
                {fact.also_reported_by.length > 0
                  ? ` +${fact.also_reported_by.length} more`
                  : ""}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Block>
  );
}

function ReportView({ report }: { report: Record<string, unknown> }) {
  const summary = report.executive_summary as
    | { summary?: string; recommendation?: string; confidence?: string }
    | undefined;

  const sections = ["news", "hires", "launches", "open_questions", "risks"] as const;

  return (
    <div className="space-y-3">
      {summary ? (
        <div className="space-y-1">
          <p className="font-bold">Summary</p>
          <p className="whitespace-pre-wrap">{summary.summary}</p>
          <p className="font-bold">Recommendation</p>
          <p className="whitespace-pre-wrap">{summary.recommendation}</p>
          <p className="font-bold">Confidence</p>
          <p className="whitespace-pre-wrap">{summary.confidence}</p>
        </div>
      ) : null}

      {sections.map((name) => {
        const section = report[name] as {
          items?: Array<{ claim: string; source_url: string }>;
        };
        const items = section?.items ?? [];
        return (
          <div key={name} className="space-y-1">
            <p className="font-bold">
              {name} ({items.length})
            </p>
            {items.length === 0 ? (
              <p className="text-slate-500">(empty)</p>
            ) : (
              <ol className="space-y-1">
                {items.map((item, index) => (
                  <li key={`${name}-${index}`}>
                    <span>{item.claim}</span>
                    <span className="ml-2 text-[11px] text-slate-500">
                      {item.source_url}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded border border-slate-300 bg-white p-4 space-y-2">
      <h2 className="font-bold text-slate-900">{title}</h2>
      {children}
    </section>
  );
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
