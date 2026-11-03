import { ButtonHTMLAttributes } from "react";

type ClayButtonVariant = "grape" | "coral";

interface ClayButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ClayButtonVariant;
}

const variantClass: Record<ClayButtonVariant, string> = {
  grape: "clay-btn-grape",
  coral: "clay-btn-coral",
};

export function ClayButton({
  variant = "grape",
  className = "",
  children,
  ...props
}: ClayButtonProps) {
  return (
    <button
      className={`${variantClass[variant]} font-display font-extrabold ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
