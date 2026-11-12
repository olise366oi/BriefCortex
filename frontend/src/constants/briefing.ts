// The backend caps retrieval at SEARCH_BUDGET_SECONDS (45s) and then runs the
// reading and generation stages on top of it: a busy run takes four minutes.
// Polling therefore has no wall-clock deadline — a fixed one timed the UI out
// while the backend was still working and delivered. A long run instead raises a
// non-blocking notice; only a terminal status or repeated transport failures
// stop the poll.
export const POLL_INTERVAL_MS = 1500;
export const POLL_SLOW_NOTICE_MS = 300_000;

// Consecutive transport failures tolerated before the UI reports an error. A
// backend restart or a proxy hiccup should not discard a running brief.
export const POLL_MAX_TRANSPORT_FAILURES = 5;
