import { ClayButton } from "@/components/ui/clay-button";

interface BriefingErrorProps {
  message?: string | null;
  onRetry: () => void;
}

export function BriefingError({ message, onRetry }: BriefingErrorProps) {
  return (
    <div className="clay-surface-coral p-5 flex flex-col items-center text-center gap-3">
      <p className="font-display font-extrabold text-base text-ink">
        {message || "Couldn't generate the brief."}
      </p>
      <ClayButton
        type="button"
        variant="coral"
        onClick={onRetry}
        className="py-2 px-5 text-xs cursor-pointer"
      >
        Try again
      </ClayButton>
    </div>
  );
}
