"use client";

/** The V2 claims review queue: P/A/R review cards with evidence and decisions.
 *
 * Each card shows Problem / Action / Result side-by-side with the cited evidence
 * quotes and click-through source links (commit, repo, Drive file). Actions mirror
 * the review API: approve, reject (reason required), and — for the Missing-Results
 * queue — supplying the impact statement, which approves as user-attested.
 */

import { useState } from "react";

import { api, type ClaimCard, type ClaimEvidence, type Experience } from "@/lib/api";
import { EmptyRow, SectionHead } from "@/components/ledger";

function EvidenceQuote({ evidence }: { evidence: ClaimEvidence }) {
  const quote = evidence.outcome_quote ?? evidence.chunk_text ?? "";
  const label = evidence.source_type ?? "evidence";
  return (
    <blockquote className="mt-1 border-l-2 border-rule pl-2 font-mono text-xs text-annotation">
      “{quote.length > 220 ? `${quote.slice(0, 220)}…` : quote}”
      <span className="ml-1 whitespace-nowrap">
        —{" "}
        {evidence.source_url ? (
          <a
            href={evidence.source_url}
            target="_blank"
            rel="noreferrer"
            className="underline-offset-2 hover:underline"
          >
            {label} ↗
          </a>
        ) : (
          label
        )}
      </span>
    </blockquote>
  );
}

function ParColumn({
  label,
  text,
  meta,
  evidence,
}: {
  label: string;
  text: string | null;
  meta?: string;
  evidence: ClaimEvidence[];
}) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[0.65rem] uppercase tracking-[0.14em] text-annotation">
        {label}
        {meta ? <span className="ml-1 normal-case tracking-normal">· {meta}</span> : null}
      </p>
      <p className="mt-1 text-sm">{text ?? <span className="text-annotation">—</span>}</p>
      {evidence.map((e) => (
        <EvidenceQuote key={`${e.evidence_id}:${e.field}`} evidence={e} />
      ))}
    </div>
  );
}

function MissingResultForm({
  claimId,
  busy,
  onResolve,
}: {
  claimId: number;
  busy: boolean;
  onResolve: (id: number, text: string, kind: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("quantified");
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Supply the impact (e.g. cut effort from 10h to 1h)…"
        className="min-w-0 flex-1 border border-rule bg-transparent px-2 py-1 font-mono text-xs"
      />
      <select
        value={kind}
        onChange={(e) => setKind(e.target.value)}
        className="border border-rule bg-transparent px-1 py-1 font-mono text-xs"
      >
        <option value="quantified">quantified</option>
        <option value="qualitative_evidenced">qualitative</option>
      </select>
      <button
        className="stamp text-viridian"
        disabled={busy || !text.trim()}
        onClick={() => onResolve(claimId, text.trim(), kind)}
      >
        Attest + approve
      </button>
    </div>
  );
}

export function ClaimsReviewSection({
  claims,
  experiences,
  missingOnly,
  onToggleMissing,
  onChanged,
}: {
  claims: ClaimCard[] | null;
  experiences: Experience[] | null;
  missingOnly: boolean;
  onToggleMissing: (value: boolean) => void;
  onChanged: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const experienceName = (id: number) =>
    experiences?.find((e) => e.id === id)?.name ?? `experience ${id}`;

  const run = async (id: number, action: () => Promise<unknown>) => {
    setBusyId(id);
    setActionError(null);
    try {
      await action();
      setRejectingId(null);
      setReason("");
      await onChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "The action failed.");
    } finally {
      setBusyId(null);
    }
  };

  const resolve = (id: number, text: string, kind: string) =>
    run(id, () => api.resolveMissingResult(id, text, kind));

  return (
    <section aria-label="Claims awaiting review">
      <div className="flex items-baseline justify-between">
        <SectionHead
          title={missingOnly ? "Claims review — missing results" : "Claims review"}
          count={claims?.length ?? 0}
          accent
        />
        <button
          className="stamp ml-4 shrink-0 text-ink"
          onClick={() => onToggleMissing(!missingOnly)}
        >
          {missingOnly ? "Show all pending" : "Missing-Results queue"}
        </button>
      </div>
      {actionError && <p className="py-1 text-sm text-carmine">{actionError}</p>}
      {claims && claims.length === 0 && (
        <EmptyRow>
          {missingOnly
            ? "No claims are waiting on an impact statement."
            : "Nothing Claude-extracted awaits review. New claims land here after extraction."}
        </EmptyRow>
      )}
      <ul className="divide-y divide-rule">
        {(claims ?? []).map((claim) => (
          <li key={claim.id} className="py-4">
            <p className="font-mono text-xs text-annotation">
              {experienceName(claim.experience_id)}
              {claim.validation_flags.length > 0 && (
                <span className="ml-2 text-carmine">
                  ⚑ {claim.validation_flags.length} validation flag
                  {claim.validation_flags.length > 1 ? "s" : ""}
                </span>
              )}
            </p>
            {claim.validation_flags.map((flag) => (
              <p key={flag} className="mt-0.5 font-mono text-xs text-carmine/80">
                {flag}
              </p>
            ))}
            <div className="mt-2 grid gap-4 sm:grid-cols-3">
              <ParColumn
                label="Problem"
                text={claim.problem_text}
                meta={
                  [claim.problem_cost_dimension, claim.problem_inefficiency]
                    .filter(Boolean)
                    .join(", ") || undefined
                }
                evidence={claim.evidence.filter((e) => e.field === "problem")}
              />
              <ParColumn
                label="Action"
                text={claim.action_text}
                meta={claim.action_tools.join(", ") || undefined}
                evidence={claim.evidence.filter((e) => e.field === "action")}
              />
              <ParColumn
                label="Result"
                text={claim.result_text}
                meta={`${claim.result_kind} · ${claim.result_status}`}
                evidence={claim.evidence.filter((e) => e.field === "result")}
              />
            </div>
            {claim.result_kind === "missing" && (
              <MissingResultForm
                claimId={claim.id}
                busy={busyId === claim.id}
                onResolve={resolve}
              />
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                className="stamp text-viridian"
                disabled={busyId === claim.id}
                onClick={() => run(claim.id, () => api.approveClaim(claim.id))}
              >
                Approve
              </button>
              {rejectingId === claim.id ? (
                <>
                  <input
                    autoFocus
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Reason (required — it becomes labeled data)…"
                    className="min-w-0 flex-1 border border-rule bg-transparent px-2 py-1 font-mono text-xs"
                  />
                  <button
                    className="stamp text-carmine"
                    disabled={busyId === claim.id || !reason.trim()}
                    onClick={() => run(claim.id, () => api.rejectClaim(claim.id, reason.trim()))}
                  >
                    Confirm reject
                  </button>
                  <button
                    className="stamp text-annotation"
                    onClick={() => {
                      setRejectingId(null);
                      setReason("");
                    }}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  className="stamp text-annotation"
                  disabled={busyId === claim.id}
                  onClick={() => setRejectingId(claim.id)}
                >
                  Reject…
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
