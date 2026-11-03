export function BriefingHeader() {
  return (
    <header className="w-full flex items-center px-2 py-1">
      <div className="flex items-center gap-4">
        <div className="w-12 h-12 rounded-[20px] bg-gradient-to-br from-[#8C6BFF] to-[#6744EE] flex items-center justify-center text-white font-display text-2xl font-extrabold shadow-[6px_8px_18px_rgba(124,92,255,0.35),-4px_-4px_10px_rgba(255,255,255,0.9),inset_2px_2px_4px_rgba(255,255,255,0.6),inset_-2px_-3px_5px_rgba(40,16,130,0.3)]">
          <svg className="w-7 h-7 text-white" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 2C6.48 2 2 6.48 2 12c0 2.85 1.2 5.42 3.12 7.24l.02.02c.38.35.88.55 1.4.55.57 0 1.11-.24 1.49-.66l.04-.04c.73-.83 1.95-.98 2.86-.34.78.55 1.73.87 2.76.87 2.61 0 4.8-1.74 5.48-4.13.25-.87 1.05-1.46 1.96-1.43 2.7.09 4.87-2.12 4.87-4.82 0-5.52-4.48-9.97-10-9.97zm-3.5 10c-.83 0-1.5-.67-1.5-1.5S7.67 9 8.5 9s1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm7 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z" />
          </svg>
        </div>
        <span className="font-display font-extrabold text-3xl text-ink tracking-tight">
          BriefCortex
        </span>
      </div>
    </header>
  );
}
