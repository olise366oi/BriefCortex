"use client";

import clsx from "clsx";
import { useRef, useState } from "react";

import { briefingLabel } from "@/lib";
import type { BriefingListItem } from "@/types";

import { BriefingAvatar } from "./briefing-avatar";
import { BriefingRowMenu } from "./briefing-row-menu";

interface BriefingsSidePanelProps {
  items: BriefingListItem[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewMeeting: () => void;
  onClone: (item: BriefingListItem) => void;
}

function LogoMark({ size = "md" }: { size?: "sm" | "md" }) {
  const box = size === "sm" ? "w-10 h-10 rounded-[16px]" : "w-11 h-11 rounded-[16px]";
  const icon = size === "sm" ? "w-5 h-5" : "w-6 h-6";

  return (
    <div
      className={clsx(
        box,
        "bg-gradient-to-br from-[#8C6BFF] to-[#6744EE] flex items-center justify-center text-white shadow-[4px_6px_14px_rgba(124,92,255,0.35),-2px_-2px_6px_rgba(255,255,255,0.9)]"
      )}
    >
      <svg className={clsx(icon, "text-white")} fill="currentColor" viewBox="0 0 24 24">
        <path d="M12 2C6.48 2 2 6.48 2 12c0 2.85 1.2 5.42 3.12 7.24l.02.02c.38.35.88.55 1.4.55.57 0 1.11-.24 1.49-.66l.04-.04c.73-.83 1.95-.98 2.86-.34.78.55 1.73.87 2.76.87 2.61 0 4.8-1.74 5.48-4.13.25-.87 1.05-1.46 1.96-1.43 2.7.09 4.87-2.12 4.87-4.82 0-5.52-4.48-9.97-10-9.97zm-3.5 10c-.83 0-1.5-.67-1.5-1.5S7.67 9 8.5 9s1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm7 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z" />
      </svg>
    </div>
  );
}

export function BriefingsSidePanel({
  items,
  activeId,
  onSelect,
  onNewMeeting,
  onClone,
}: BriefingsSidePanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const hoveringRef = useRef(false);

  const closeMenu = () => {
    setMenuOpenId(null);
    if (!hoveringRef.current) {
      setExpanded(false);
    }
  };

  return (
    <div
      className="relative shrink-0 self-stretch"
      onMouseEnter={() => {
        hoveringRef.current = true;
        setExpanded(true);
      }}
      onMouseLeave={() => {
        hoveringRef.current = false;
        // Keep panel open while the clone popover is active (it portals outside).
        if (menuOpenId) return;
        setExpanded(false);
      }}
    >
      {/* Collapsed rail keeps layout width stable */}
      <aside className="w-[76px] h-full min-h-[640px] clay-rail py-5 px-3 flex flex-col items-center justify-start gap-4">
        <LogoMark />
        <button
          type="button"
          title="New meeting"
          onClick={onNewMeeting}
          className="w-11 h-11 rounded-full clay-btn-grape flex items-center justify-center text-white text-xl font-display font-extrabold cursor-pointer"
        >
          +
        </button>
        <div className="w-8 h-[2px] bg-[#E2DDF5] rounded-full my-1" />
        <div className="flex flex-col items-center gap-3 w-full">
          {items.map((item) => {
            const active = item.id === activeId;
            return (
              <button
                key={item.id}
                type="button"
                title={briefingLabel(item.company_name, item.meeting_purpose)}
                onClick={() => onSelect(item.id)}
                className="relative w-full flex items-center justify-center"
              >
                {active ? (
                  <span className="absolute left-[-12px] top-1.5 bottom-1.5 w-1 rounded-r-full bg-grape" />
                ) : null}
                <BriefingAvatar companyName={item.company_name} active={active} />
              </button>
            );
          })}
        </div>
      </aside>

      {/* Expanded floating panel overlays content without shifting layout */}
      {expanded ? (
        <aside className="absolute left-0 top-0 bottom-0 w-[300px] clay-floating-panel p-5 flex flex-col gap-5 z-30">
          <div className="flex items-center gap-3">
            <LogoMark size="sm" />
            <span className="font-display font-extrabold text-xl text-ink tracking-tight">
              BriefCortex
            </span>
          </div>

          <button
            type="button"
            onClick={onNewMeeting}
            className="clay-btn-grape w-full py-3 px-4 font-display font-extrabold text-sm flex items-center justify-center gap-1.5 cursor-pointer"
          >
            <span>+</span> New meeting
          </button>

          <div className="flex flex-col gap-3 min-h-0 flex-1">
            <h2 className="font-display font-extrabold text-base text-ink">Briefings</h2>
            <div className="flex flex-col gap-2.5 overflow-y-auto pr-1">
              {items.length === 0 ? (
                <p className="font-body text-sm text-ink/60 px-1">No briefings yet.</p>
              ) : (
                items.map((item) => {
                  const active = item.id === activeId;
                  return (
                    <div
                      key={item.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => {
                        closeMenu();
                        onSelect(item.id);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          closeMenu();
                          onSelect(item.id);
                        }
                      }}
                      className={clsx(
                        "relative px-3 py-2 flex items-center gap-2.5 cursor-pointer group",
                        active ? "clay-row-active" : "clay-row-normal"
                      )}
                    >
                      <BriefingAvatar
                        companyName={item.company_name}
                        active={active}
                        size="sm"
                      />
                      <div
                        className={clsx(
                          "font-body text-[14px] whitespace-nowrap overflow-hidden mask-fade-right flex-1",
                          active
                            ? "font-semibold text-grape"
                            : "font-medium text-ink"
                        )}
                      >
                        {briefingLabel(item.company_name, item.meeting_purpose)}
                      </div>
                      <BriefingRowMenu
                        open={menuOpenId === item.id}
                        onToggle={() =>
                          setMenuOpenId((current) =>
                            current === item.id ? null : item.id
                          )
                        }
                        onClone={() => {
                          onClone(item);
                          closeMenu();
                          setExpanded(false);
                        }}
                        onClose={closeMenu}
                      />
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </aside>
      ) : null}
    </div>
  );
}
