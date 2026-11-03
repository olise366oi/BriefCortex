"use client";

import clsx from "clsx";
import { ButtonHTMLAttributes, forwardRef } from "react";

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  sunk?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton({ sunk = false, className, children, ...props }, ref) {
    return (
      <button
        ref={ref}
        type="button"
        className={clsx(
          sunk ? "clay-row-btn-sunk" : "clay-row-btn",
          "shrink-0",
          className
        )}
        {...props}
      >
        {children}
      </button>
    );
  }
);
