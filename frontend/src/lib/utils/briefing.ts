export function companyInitial(companyName: string): string {
  const trimmed = companyName.trim();
  if (!trimmed) return "?";
  return trimmed.charAt(0).toUpperCase();
}

export function briefingLabel(
  companyName: string,
  meetingPurpose: string
): string {
  return `${companyName} — ${meetingPurpose}`;
}
