"use client";

import { useState } from "react";

import { getBriefing } from "@/api";
import type { BriefingStatusResponse } from "@/types";

/**
 * Throwaway harness for the evidence payload.
 *
 * It exists so the provenance fields can be exercised end to end before the
 * real report view is finished. Not linked from the app and not meant to ship;
 * delete it once the evidence panel lives in the report view.
 */
export function EvidenceProbe() {
  const [briefingId, setBriefingId] = useState("");
  const [result, setResult] = useState<BriefingStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLoad() {
    if (!briefingId.trim()) {
      setError("Paste a briefing id first.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      setResult(await getBriefing(briefingId.trim()));
    } catch {
      setError("Could not load that briefing.");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const evidence = result?.evidence ?? null;

  return (
    <div className="min-h-screen bg-slate-100 p-8 font-mono text-sm text-slate-800">
      <div className="mx-auto max-w-4xl space-y-6">
        <header className="space-y-1">
          <h1 className="text-xl font-bold">Evidence probe</h1>
          <p className="text-slate-500">
            Read-only check of the `evidence` payload on GET
            /api/briefings/{"{id}"}.
          </p>
        </header>

        <div className="flex gap-2">
          <input
            className="w-full rounded border border-slate-300 px-3 py-2"
            placeholder="briefing id"
            value={briefingId}
            onChange={(event) => setBriefingId(event.target.value)}
          />
          <button
            className="rounded bg-slate-800 px-4 py-2 text-white disabled:opacity-50"
            disabled={loading}
            onClick={() => void handleLoad()}
            type="button"
          >
            {loading ? "Loading..." : "Load"}
          </button>
        </div>

        {error ? <p className="text-red-600">{error}</p> : null}

        {result ? (
          <section className="space-y-4">
            <dl className="grid grid-cols-3 gap-3">
              <Stat label="status" value={result.status} />
              <Stat label="report" value={result.report ? "present" : "absent"} />
              <Stat label="evidence" value={evidence ? "present" : "absent"} />
            </dl>

            {evidence ? (
              <>
                <dl className="grid grid-cols-3 gap-3">
                  <Stat label="hard facts" value={String(evidence.hard_fact_count)} />
                  <Stat label="opinions" value={String(evidence.opinion_count)} />
                  <Stat label="conflicts" value={String(evidence.conflict_count)} />
                </dl>

                <Block title={`Conflicts (${evidence.conflicts.length})`}>
                  {evidence.conflicts.length === 0 ? (
                    <p className="text-slate-500">No conflicts detected.</p>
                  ) : (
                    <ul className="space-y-3">
                      {evidence.conflicts.map((conflict, index) => (
                        <li
                          className="rounded border border-slate-300 bg-white p-3"
                          key={`${conflict.conflict_type}-${index}`}
                        >
                          <p className="font-bold text-slate-900">
                            {conflict.conflict_type}
                          </p>
                          <p className="text-xs text-slate-500">
                            {conflict.description}
                          </p>
                          <p className="mt-2">A: {conflict.fact_a}</p>
                          <p className="text-xs text-slate-500">
                            {conflict.source_a}
                          </p>
                          <p className="mt-1">B: {conflict.fact_b}</p>
                          <p className="text-xs text-slate-500">
                            {conflict.source_b}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </Block>

                <Block title={`Analyst opinions (${evidence.analyst_opinions.length})`}>
                  {evidence.analyst_opinions.length === 0 ? (
                    <p className="text-slate-500">None present.</p>
                  ) : (
                    <ul className="space-y-2">
                      {evidence.analyst_opinions.slice(0, 20).map((opinion, index) => (
                        <li key={`${opinion.source_url}-${index}`}>
                          <p>{opinion.content}</p>
                          <p className="text-xs text-slate-500">
                            {opinion.publish_date ?? "undated"} — {opinion.source_url}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </Block>
              </>
            ) : (
              <p className="text-slate-500">
                This briefing was produced before the evidence payload existed,
                or it is still processing.
              </p>
            )}
          </section>
        ) : null}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-300 bg-white p-3">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="font-bold text-slate-900">{value}</dd>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h2 className="font-bold text-slate-900">{title}</h2>
      {children}
    </div>
  );
}
