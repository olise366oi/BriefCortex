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

export interface BriefingStatusResponse {
  id: string;
  status: BriefingJobStatus;
  company_name: string;
  meeting_time: string;
  meeting_purpose: string;
  report: BriefingReport | null;
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
