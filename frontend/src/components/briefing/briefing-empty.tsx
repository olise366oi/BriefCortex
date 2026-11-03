export function BriefingEmpty() {
  return (
    <div className="clay-surface-grape p-10 flex flex-col items-center justify-center text-center gap-4 min-h-[280px]">
      <div className="w-16 h-16 rounded-[22px] bg-gradient-to-br from-[#DECFFF] to-[#C9B4FF] flex flex-col items-center justify-center shadow-[4px_6px_12px_rgba(124,92,255,0.2),-3px_-3px_8px_rgba(255,255,255,0.9),inset_2px_2px_4px_rgba(255,255,255,0.8)]">
        <div className="flex items-center gap-2.5 mb-0.5">
          <div className="w-1.5 h-1.5 rounded-full bg-ink" />
          <div className="w-1.5 h-1.5 rounded-full bg-ink" />
        </div>
        <div className="w-3.5 h-1.5 border-b-2 border-ink rounded-full" />
      </div>
      <p className="font-display font-bold text-base text-ink">
        Your brief will appear here.
      </p>
    </div>
  );
}
