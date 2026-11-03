export function getHostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function isUnverifiedClaim(claim: string): boolean {
  return /\[UNVERIFIED\]/i.test(claim);
}

export function stripUnverifiedTag(claim: string): string {
  return claim.replace(/\s*\[UNVERIFIED\]\s*/gi, " ").trim();
}
