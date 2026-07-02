/** Shared ledger furniture: section heads, empty rows, status inks. */

export function SectionHead({
  title,
  count,
  accent = false,
}: {
  title: string;
  count?: number;
  accent?: boolean;
}) {
  return (
    <div className="mt-10 mb-3 flex items-baseline gap-2">
      <h2
        className={`font-mono text-xs font-medium uppercase tracking-[0.14em] ${
          accent ? "text-carmine" : "text-annotation"
        }`}
      >
        {title}
        {count !== undefined && <span className="ml-2">({count})</span>}
      </h2>
      <div
        className={`h-px flex-1 translate-y-[-0.2em] ${accent ? "bg-carmine/30" : "bg-rule"}`}
      />
    </div>
  );
}

export function EmptyRow({ children }: { children: React.ReactNode }) {
  return <p className="py-3 text-sm text-annotation">{children}</p>;
}

/** Status → ink color. Red is reserved for states awaiting the operator. */
export function statusInk(status: string): string {
  switch (status) {
    case "drafted":
    case "detected":
      return "text-carmine";
    case "approved":
    case "applied":
    case "interviewing":
    case "offer":
    case "sent":
    case "scheduled":
    case "completed":
      return "text-viridian";
    default:
      return "text-annotation"; // rejected / ignored / discarded / cancelled: closed entries
  }
}
