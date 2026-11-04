export interface BriefingRequest {
  company_name: string;
  meeting_time: string;
  meeting_purpose: string;
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
}

export interface ClaimModule {
  items: Claim[];
}

export interface ExecutiveSummary {
  summary: string;
  recommendation: string;
  confidence: string;
}

export interface BriefingReport {
  news: ClaimModule;
  hires: ClaimModule;
  launches: ClaimModule;
  open_questions: ClaimModule;
  risks: ClaimModule;
  executive_summary: ExecutiveSummary;
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
