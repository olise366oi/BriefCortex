export function BriefingLoading() {
  return (
    <div className="flex flex-col gap-4">
      <div className="clay-surface-grape p-4 flex flex-col gap-2.5 clay-pulse">
        <div className="h-4 w-1/3 rounded-lg bg-grape/30" />
        <div className="h-3 w-full rounded-md bg-grape/20" />
        <div className="h-3 w-5/6 rounded-md bg-grape/20" />
        <div className="h-10 rounded-xl bg-white/60 mt-1" />
      </div>

      <div className="flex flex-col gap-2">
        <SkeletonRow surface="clay-surface-sky" bar="bg-sky/40" />
        <SkeletonRow surface="clay-surface-mint" bar="bg-mint/40" />
        <SkeletonRow surface="clay-surface-grape" bar="bg-grape/40" />
        <SkeletonRow surface="clay-surface-butter" bar="bg-butter/50" />
        <SkeletonRow surface="clay-surface-coral" bar="bg-coral/40" />
      </div>
    </div>
  );
}

function SkeletonRow({ surface, bar }: { surface: string; bar: string }) {
  return (
    <div className={`${surface} p-2.5 flex items-center justify-between clay-pulse`}>
      <div className={`h-3 w-1/4 rounded ${bar}`} />
      <div className="h-3 w-1/2 rounded bg-white/70" />
    </div>
  );
}
