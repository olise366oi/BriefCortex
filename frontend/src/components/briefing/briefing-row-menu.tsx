"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { IconButton } from "@/components/ui/icon-button";

interface BriefingRowMenuProps {
  open: boolean;
  onToggle: () => void;
  onClone: () => void;
  onClose: () => void;
}

interface MenuPosition {
  top: number;
  left: number;
}

export function BriefingRowMenu({
  open,
  onToggle,
  onClone,
  onClose,
}: BriefingRowMenuProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState<MenuPosition | null>(null);

  useLayoutEffect(() => {
    if (!open || !buttonRef.current) {
      setPosition(null);
      return;
    }

    const rect = buttonRef.current.getBoundingClientRect();
    setPosition({
      top: rect.top + rect.height / 2,
      left: rect.right + 10,
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target)) return;
      const portal = document.getElementById("briefing-clone-menu");
      if (portal?.contains(target)) return;
      onClose();
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [onClose, open]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <IconButton
        ref={buttonRef}
        sunk={open}
        title="More"
        aria-label="More actions"
        aria-expanded={open}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onToggle();
        }}
        onMouseDown={(event) => {
          event.preventDefault();
          event.stopPropagation();
        }}
      >
        ⋯
      </IconButton>

      {open && position
        ? createPortal(
            <button
              id="briefing-clone-menu"
              type="button"
              className="clay-popover fixed z-[80] py-2 px-3 flex items-center gap-2 cursor-pointer border border-[#E9E1FF] whitespace-nowrap"
              style={{
                top: position.top,
                left: position.left,
                transform: "translateY(-50%)",
              }}
              onMouseDown={(event) => {
                event.preventDefault();
                event.stopPropagation();
              }}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onClone();
                onClose();
              }}
            >
              <svg
                className="w-4 h-4 text-grape shrink-0"
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                viewBox="0 0 24 24"
              >
                <rect height="13" rx="2" ry="2" width="13" x="9" y="9" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
              <span className="font-display font-bold text-sm text-ink">Clone</span>
            </button>,
            document.body
          )
        : null}
    </div>
  );
}
