export interface BriefingRequest {
  company_name: string;
  meeting_time: string;
  /** The order the pipeline must read the brief in. */
  meeting_purpose: string;
  /**
   * The caller's own wording for the company. Needed when a name alone cannot
   * identify it: the registry may return several unrelated entities, or two
   * businesses may trade under the same name.
   */
  company_description?: string;
  /**
   * Exchange ticker, when the caller knows it. Without one no quote is fetched,
   * which is correct for a private company and avoids guessing a symbol.
   */
  ticker?: string;
}

export interface BriefingAcceptedResponse {
  id: string;
  status: string;
}

export type BriefingJobStatus =
  | "pending"
  | "processing"
  | "completed"
  | "failed";

export interface Claim {
  claim: string;
  source_url: string;
  /** ISO date of the report, or empty when the source carries none. */
  date?: string;
  /** Earliest date the same event was reported, when it spans several reports. */
  first_seen?: string;
  /** True for developments a reader preparing for this meeting needs. */
  key_event?: boolean;
}

export interface ClaimModule {
  items: Claim[];
}

export interface ExecutiveSummary {
  summary: string;
  recommendation: string;
  confidence: string;
}

/** The assessment between the facts and the advice. */
export interface Analysis {
  assessment: string;
  risks: string[];
}

export interface RevisionNote {
  change: string;
  why: string;
}

/** A claim the audit removed. Shown as an explanation, never as a finding. */
export interface AuditRemoval {
  section: string;
  claim: string;
  verdict: string;
  remedy: string;
}

/**
 * A fact about the company found inside an article about someone else, with no
 * follow-up reporting behind it. A lead, not a finding.
 */
export interface LooseClaim {
  claim: string;
  source_url: string;
  article_subject?: string | null;
}

export interface BriefingReport {
  /** Every reported development, ordered: key events first, then newest. */
  news: ClaimModule;
  /** Job postings: intent, not outcome. */
  hires: ClaimModule;
  /** What the evidence amounts to, and where the risk sits. */
  analysis?: Analysis;
  executive_summary: ExecutiveSummary;
  /** Only present on a later brief; absent on a first one. */
  changes_since_previous?: RevisionNote[];
  /** Only present when the audit rejected something. */
  removed_claims?: AuditRemoval[];
  /**
   * Stated intent about the future — CEO pledges, targets. Never read into the
   * analysis: a promise is not evidence that something happened.
   */
  forward_looking?: LooseClaim[];
  /** Market price, timestamped. A quote, not reporting. */
  quote?: LooseClaim | null;
  /** Only present when the evidence held facts from other companies' articles. */
  possible_facts?: LooseClaim[];
}

export interface EvidenceConflict {
  conflict_type: string;
  fact_a: string;
  source_a: string;
  fact_b: string;
  source_b: string;
  description: string;
}

export interface EvidenceItem {
  content: string;
  source_url: string;
  publish_date?: string | null;
  also_reported_by: string[];
}

export interface IncidentalItem {
  content: string;
  source_url: string;
  publish_date?: string | null;
  provenance: "corroborated" | "unverified";
  corroborating_url?: string | null;
  article_subject?: string | null;
}

export interface EvidenceSummary {
  hard_fact_count: number;
  opinion_count: number;
  conflict_count: number;
  conflicts: EvidenceConflict[];
  hard_facts: EvidenceItem[];
  analyst_opinions: EvidenceItem[];
  incidental_facts: IncidentalItem[];
}

export interface BriefingStatusResponse {
  id: string;
  status: BriefingJobStatus;
  company_name: string;
  meeting_time: string;
  meeting_purpose: string;
  report: BriefingReport | null;
  evidence: EvidenceSummary | null;
  error: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BriefingListItem {
  id: string;
  company_name: string;
  meeting_time: string;
  meeting_purpose: string;
  status: BriefingJobStatus;
  created_at: string;
}

export interface BriefingListResponse {
  items: BriefingListItem[];
}

export type BriefingUiState = "empty" | "loading" | "ready" | "error";

export interface BriefingFormValues {
  companyName: string;
  meetingTime: string;
  meetingPurpose: string;
}

export interface BriefingFormErrors {
  companyName?: string;
  meetingTime?: string;
  meetingPurpose?: string;
}
