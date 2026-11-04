export interface DebugRunRequest {
  company_name: string;
  meeting_purpose?: string;
  /** ISO 8601 meeting date. Pass this, or meeting_in_days, never both. */
  meeting_time?: string;
  /** Convenience for the console: how far ahead the meeting is. */
  meeting_in_days?: number;
  /** Set once the caller has chosen between ambiguous identity candidates. */
  jurisdiction?: string;
}

export interface DebugFact {
  content: string;
  source_url: string;
  publish_date: string | null;
  also_reported_by: string[];
}

export interface DebugIdentityCandidate {
  name: string;
  jurisdiction: string | null;
  legal_form: string | null;
  status: string | null;
  confidence: number;
}

export interface DebugCoverage {
  level: string;
  total_facts: number;
  distinct_sources: number;
  can_support_leverage: boolean;
  lanes_missing: string[];
  notes: string[];
  checks_to_run: string[];
}

export interface DebugWindow {
  window_start: string;
  window_end: string;
  available_end: string;
  fetch_start: string;
  fetch_end: string;
  fetch_span_days: number;
  pending_days: number;
  is_complete: boolean;
  preliminary: boolean;
}

export interface DebugRunResponse {
  company_name: string;
  identity: Record<string, unknown>;
  identity_candidates: DebugIdentityCandidate[];
  /** True when the name matched near-tied registered entities: pick one. */
  needs_disambiguation: boolean;
  counts: Record<string, number>;
  coverage: DebugCoverage;
  window: DebugWindow;
  hard_facts: DebugFact[];
  opinions: DebugFact[];
  hiring: DebugFact[];
  incidental: DebugFact[];
  conflicts: Array<Record<string, unknown>>;
  report: Record<string, unknown> | null;
  timings: Record<string, number>;
  error: string | null;
}
