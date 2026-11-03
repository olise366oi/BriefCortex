import { ReactNode } from "react";

import {
  getHostname,
  isUnverifiedClaim,
  stripUnverifiedTag,
} from "@/lib";
import type { Claim } from "@/types";

type SectionTone = "sky" | "mint" | "grape" | "butter" | "coral";

interface SectionCardProps {
  title: string;
  tone: SectionTone;
  icon: ReactNode;
  items: Claim[];
}

const surfaceClass: Record<SectionTone, string> = {
  sky: "clay-surface-sky",
  mint: "clay-surface-mint",
  grape: "clay-surface-grape",
  butter: "clay-surface-butter",
  coral: "clay-surface-coral",
};

const tokenClass: Record<SectionTone, string> = {
  sky: "bg-sky text-white",
  mint: "bg-mint text-white",
  grape: "bg-grape text-white",
  butter: "bg-butter text-ink",
  coral: "bg-coral text-white",
};

const iconShadow: Record<SectionTone, string> = {
  sky: "shadow-[3px_4px_8px_rgba(91,195,255,0.2),-2px_-2px_5px_rgba(255,255,255,0.9)] text-sky",
  mint: "shadow-[3px_4px_8px_rgba(52,211,160,0.2),-2px_-2px_5px_rgba(255,255,255,0.9)] text-mint",
  grape:
    "shadow-[3px_4px_8px_rgba(124,92,255,0.2),-2px_-2px_5px_rgba(255,255,255,0.9)] text-grape",
  butter:
    "shadow-[3px_4px_8px_rgba(255,206,92,0.22),-2px_-2px_5px_rgba(255,255,255,0.9)] text-[#B8860B]",
  coral:
    "shadow-[3px_4px_8px_rgba(255,122,107,0.22),-2px_-2px_5px_rgba(255,255,255,0.9)] text-coral",
};

export function SectionCard({ title, tone, icon, items }: SectionCardProps) {
  return (
    <div className={`${surfaceClass[tone]} p-5 flex flex-col gap-4`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className={`w-9 h-9 rounded-2xl bg-white font-display font-extrabold text-base flex items-center justify-center ${iconShadow[tone]}`}
          >
            {icon}
          </div>
          <h3 className="font-display font-extrabold text-lg text-ink">{title}</h3>
        </div>
        <span
          className={`clay-token font-display font-extrabold text-xs px-2.5 py-0.5 ${tokenClass[tone]}`}
        >
          {items.length}
        </span>
      </div>

      <div className="flex flex-col gap-3">
        {items.map((item) => {
          const unverified = isUnverifiedClaim(item.claim);
          return (
            <div
              key={`${item.source_url}-${item.claim}`}
              className="clay-input p-3.5 bg-white/80 flex items-center justify-between gap-3"
            >
              <div className="flex items-center gap-2.5 flex-wrap">
                <span className="font-body text-sm font-medium text-ink">
                  {stripUnverifiedTag(item.claim)}
                </span>
                {unverified ? (
                  <span className="clay-token bg-butter text-ink font-body font-bold text-xs px-2.5 py-0.5">
                    Unverified
                  </span>
                ) : null}
              </div>
              <a
                className="clay-source-chip px-3 py-1 flex items-center gap-1.5 shrink-0 no-underline"
                href={item.source_url}
                rel="noopener noreferrer"
                target="_blank"
              >
                <span className="font-mono text-xs text-ink/70">
                  {getHostname(item.source_url)}
                </span>
                <span className="text-ink/60 text-xs">↗</span>
              </a>
            </div>
          );
        })}
      </div>
    </div>
  );
}
