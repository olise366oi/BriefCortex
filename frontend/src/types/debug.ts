export interface DebugRunRequest {
  company_name: string;
  meeting_purpose?: string;
  meeting_in_days?: number;
}

export interface DebugFact {
  content: string;
  source_url: string;
  publish_date: string | null;
  also_reported_by: string[];
}

export interface DebugIdentity {
  resolved: boolean;
  name?: string;
  jurisdiction?: string | null;
  legal_form?: string | null;
  status?: string | null;
  confident?: boolean;
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

export interface DebugRunResponse {
  company_name: string;
  identity: DebugIdentity;
  counts: Record<string, number>;
  coverage: DebugCoverage;
  hard_facts: DebugFact[];
  opinions: DebugFact[];
  hiring: DebugFact[];
  incidental: DebugFact[];
  conflicts: Array<Record<string, unknown>>;
  report: Record<string, unknown> | null;
  timings: Record<string, number>;
  error: string | null;
}
