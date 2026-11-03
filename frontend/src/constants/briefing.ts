// The backend caps retrieval at SEARCH_BUDGET_SECONDS (45s) and then runs one
// or more LLM calls, so the poll window has to outlast both phases. A brief
// that keeps a user waiting longer than this is a failure they should see.
export const POLL_INTERVAL_MS = 1500;
export const POLL_TIMEOUT_MS = 180_000;
