import clsx from "clsx";

import { companyInitial } from "@/lib";

interface BriefingAvatarProps {
  companyName: string;
  active?: boolean;
  size?: "sm" | "md";
}

const sizeClass = {
  sm: "w-7 h-7 text-xs",
  md: "w-10 h-10 text-base",
};

export function BriefingAvatar({
  companyName,
  active = false,
  size = "md",
}: BriefingAvatarProps) {
  return (
    <div
      className={clsx(
        "flex items-center justify-center font-display font-extrabold shrink-0",
        sizeClass[size],
        active ? "clay-avatar-active" : "clay-avatar-pastel"
      )}
    >
      {companyInitial(companyName)}
    </div>
  );
}
