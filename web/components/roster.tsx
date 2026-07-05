"use client";

/** The project roster review — the load-bearing human step of V2.1.
 *
 * Detection proposes real-world entities (employer roles, projects) from the evidence;
 * this section is where the human reshapes them: confirm, rename, merge duplicates
 * (four resume versions → one employer), and discard junk (a degree form is not
 * career experience). "Assign evidence" then scopes every chunk to a confirmed
 * entity, and claim extraction runs inside those boundaries only.
 */

import { useState } from "react";

import { api, type RosterEntity } from "@/lib/api";
import { EmptyRow, SectionHead, statusInk } from "@/components/ledger";

function RenameForm({
  entity,
  busy,
  onSave,
}: {
  entity: RosterEntity;
  busy: boolean;
  onSave: (id: number, name: string, dates: string) => Promise<void>;
}) {
  const [name, setName] = useState(entity.name);
  const [dates, setDates] = useState(entity.dates ?? "");
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Entity name (e.g. Wellington Management — Technology Intern)"
        className="min-w-0 flex-1 border border-rule bg-transparent px-2 py-1 font-mono text-xs"
      />
      <input
        value={dates}
        onChange={(e) => setDates(e.target.value)}
        placeholder="Dates (e.g. Jun–Aug 2025)"
        className="w-40 border border-rule bg-transparent px-2 py-1 font-mono text-xs"
      />
      <button
        className="stamp text-viridian"
        disabled={busy || !name.trim()}
        onClick={() => onSave(entity.id, name.trim(), dates.trim())}
      >
        Save
      </button>
    </div>
  );
}

export function RosterSection({
  roster,
  onChanged,
}: {
  roster: RosterEntity[] | null;
  onChanged: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | "section" | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const visible = (roster ?? []).filter(
    (e) => e.status === "proposed" || e.status === "confirmed",
  );
  const proposed = visible.filter((e) => e.status === "proposed");
  const confirmed = visible.filter((e) => e.status === "confirmed");

  const run = async (busy: number | "section", action: () => Promise<unknown>) => {
    setBusyId(busy);
    setActionError(null);
    try {
      await action();
      setRenamingId(null);
      await onChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "The action failed.");
    } finally {
      setBusyId(null);
    }
  };

  const detect = () =>
    run("section", async () => {
      const report = await api.detectRoster();
      setMessage(
        `Detection read ${report.documents} sources and proposed ${report.proposed.length} new entities.`,
      );
    });

  const assign = () =>
    run("section", async () => {
      const report = await api.assignRoster();
      setMessage(
        `Assigned ${report.assigned}/${report.chunks} evidence chunks (${report.unassigned} left honestly unassigned).`,
      );
    });

  const rename = (id: number, name: string, dates: string) =>
    run(id, () => api.editRosterEntity(id, { name, dates: dates || undefined }));

  return (
    <section aria-label="Project roster">
      <div className="flex items-baseline justify-between">
        <SectionHead title="Project roster" count={proposed.length} accent={proposed.length > 0} />
        <div className="ml-4 flex shrink-0 gap-2">
          <button className="stamp text-ink" disabled={busyId === "section"} onClick={detect}>
            Detect from sources
          </button>
          <button
            className="stamp text-ink"
            disabled={busyId === "section" || confirmed.length === 0}
            onClick={assign}
          >
            Assign evidence
          </button>
        </div>
      </div>
      <p className="py-1 font-mono text-xs text-annotation">
        Real employers and projects — not files. Confirm what&apos;s real, merge duplicates,
        discard junk; extraction only runs inside confirmed boundaries.
      </p>
      {message && <p className="py-1 font-mono text-xs text-annotation">{message}</p>}
      {actionError && <p className="py-1 text-sm text-carmine">{actionError}</p>}
      {roster && visible.length === 0 && (
        <EmptyRow>No roster yet. Detect from sources to propose your entities.</EmptyRow>
      )}
      <ul className="divide-y divide-rule">
        {visible.map((entity) => (
          <li key={entity.id} className="py-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2">
              <div className="min-w-0 flex-1">
                <span className="text-sm font-medium">{entity.name}</span>
                <span className="text-sm text-annotation">
                  {" "}
                  · {entity.kind === "employer_role" ? "employer role" : "project"}
                  {entity.dates ? ` · ${entity.dates}` : ""}
                  {entity.status === "confirmed" && entity.assigned_chunks > 0
                    ? ` · ${entity.assigned_chunks} chunks`
                    : ""}
                </span>
                {entity.aliases.length > 0 && (
                  <p className="mt-0.5 font-mono text-xs text-annotation/80">
                    aka {entity.aliases.join(" · ")}
                  </p>
                )}
              </div>
              <span className={`stamped ${statusInk(entity.status)}`}>{entity.status}</span>
              <div className="flex flex-wrap gap-2">
                {entity.status === "proposed" && (
                  <button
                    className="stamp text-viridian"
                    disabled={busyId === entity.id}
                    onClick={() => run(entity.id, () => api.confirmRosterEntity(entity.id))}
                  >
                    Confirm
                  </button>
                )}
                <button
                  className="stamp text-ink"
                  disabled={busyId === entity.id}
                  onClick={() => setRenamingId(renamingId === entity.id ? null : entity.id)}
                >
                  Rename…
                </button>
                {confirmed.some((t) => t.id !== entity.id) && (
                  <select
                    className="border border-rule bg-transparent px-1 py-1 font-mono text-xs"
                    value=""
                    disabled={busyId === entity.id}
                    onChange={(e) => {
                      const target = Number(e.target.value);
                      if (target) {
                        void run(entity.id, () => api.mergeRosterEntities(entity.id, target));
                      }
                    }}
                  >
                    <option value="">merge into…</option>
                    {confirmed
                      .filter((t) => t.id !== entity.id)
                      .map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                  </select>
                )}
                <button
                  className="stamp text-annotation"
                  disabled={busyId === entity.id}
                  onClick={() => run(entity.id, () => api.discardRosterEntity(entity.id))}
                >
                  Discard
                </button>
              </div>
            </div>
            {renamingId === entity.id && (
              <RenameForm entity={entity} busy={busyId === entity.id} onSave={rename} />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
