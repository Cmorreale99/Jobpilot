"use client";

/** The V3 project-story review queue — one card per confirmed project, not 148 claims.
 *
 * Each card shows the Problem / Actions / Result the heuristic selected (claim text
 * verbatim) with the cited evidence quotes under every component, a derived readiness
 * badge, and the targeted questions for whatever is missing. The reviewer answers a
 * gap (a story-scoped attestation), approves (only through the resume-ready gate — an
 * incomplete story's Approve is replaced by its blockers), or excludes with a reason.
 */

import { useState } from "react";

import { api, type StoryCard, type StoryEvidence, type StoryQuestion } from "@/lib/api";
import { EmptyRow, SectionHead } from "@/components/ledger";

function EvidenceQuote({ evidence }: { evidence: StoryEvidence }) {
  const quote = evidence.chunk_text ?? "";
  const label = evidence.source_type ?? "evidence";
  return (
    <blockquote className="mt-1 border-l-2 border-rule pl-2 font-mono text-xs text-annotation">
      “{quote.length > 200 ? `${quote.slice(0, 200)}…` : quote}”
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

function AnswerForm({
  component,
  busy,
  onAnswer,
}: {
  component: string;
  busy: boolean;
  onAnswer: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const placeholder =
    component === "problem"
      ? "Describe the real problem this project solved…"
      : "Supply the measurable or observable outcome…";
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 flex-1 border border-rule bg-transparent px-2 py-1 font-mono text-xs"
      />
      <button
        className="stamp text-viridian"
        disabled={busy || !text.trim()}
        onClick={() => onAnswer(text.trim())}
      >
        Answer
      </button>
    </div>
  );
}

function Component({
  label,
  meta,
  children,
}: {
  label: string;
  meta: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[0.65rem] uppercase tracking-[0.14em] text-annotation">
        {label}
        <span className="ml-1 normal-case tracking-normal">· {meta}</span>
      </p>
      {children}
    </div>
  );
}

const ANSWERABLE = new Set(["missing_problem", "missing_result"]);

export function StoryReviewSection({
  stories,
  onChanged,
}: {
  stories: StoryCard[] | null;
  onChanged: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [excludingId, setExcludingId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [synthesizing, setSynthesizing] = useState(false);

  const run = async (id: number, action: () => Promise<unknown>) => {
    setBusyId(id);
    setActionError(null);
    try {
      await action();
      setExcludingId(null);
      setReason("");
      await onChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "The action failed.");
    } finally {
      setBusyId(null);
    }
  };

  const synthesize = async () => {
    setSynthesizing(true);
    setActionError(null);
    try {
      await api.synthesizeStories();
      await onChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Synthesis failed.");
    } finally {
      setSynthesizing(false);
    }
  };

  const question = (story: StoryCard, q: StoryQuestion, index: number) => (
    <div key={`${q.kind}:${index}`} className="mt-3 border-l-2 border-carmine/30 pl-2">
      <p className="text-sm text-carmine/90">{q.text}</p>
      {q.quotes.map((quote, i) => (
        <p key={i} className="mt-0.5 font-mono text-xs text-annotation">
          “{quote}”
        </p>
      ))}
      {ANSWERABLE.has(q.kind) && (
        <AnswerForm
          component={q.component}
          busy={busyId === story.id}
          onAnswer={(text) => run(story.id, () => api.answerStory(story.id, q.component, text))}
        />
      )}
    </div>
  );

  return (
    <section aria-label="Project stories awaiting review">
      <div className="flex items-baseline justify-between">
        <SectionHead title="Project stories" count={stories?.length ?? 0} accent />
        <button
          className="stamp ml-4 shrink-0 text-ink"
          disabled={synthesizing}
          onClick={synthesize}
        >
          {synthesizing ? "Synthesizing…" : "Synthesize"}
        </button>
      </div>
      {actionError && <p className="py-1 text-sm text-carmine">{actionError}</p>}
      {stories && stories.length === 0 && (
        <EmptyRow>
          No project stories in review. Synthesize builds one card per confirmed roster
          entity — the story review that replaces clicking through raw claims.
        </EmptyRow>
      )}
      <ul className="divide-y divide-rule">
        {(stories ?? []).map((story) => (
          <li key={story.id} className="py-4">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <p className="min-w-0 flex-1 text-sm font-medium">
                {story.experience_name ?? `experience ${story.experience_id}`}
                {story.section && (
                  <span className="ml-2 font-mono text-xs text-annotation">
                    {story.section.replace(/_/g, " ")}
                  </span>
                )}
              </p>
              {story.readiness.resume_ready ? (
                <span className="stamped text-viridian">resume-ready</span>
              ) : (
                <span className="stamped text-carmine">
                  needs {story.readiness.missing.join(" + ") || "review"}
                </span>
              )}
            </div>

            <div className="mt-2 grid gap-4 sm:grid-cols-3">
              <Component label="Problem" meta={story.readiness.problem}>
                <p className="mt-1 text-sm">
                  {story.problem?.text ?? <span className="text-annotation">—</span>}
                </p>
                {(story.problem?.evidence ?? []).map((e, i) => (
                  <EvidenceQuote key={i} evidence={e} />
                ))}
              </Component>

              <Component label="Actions" meta={String(story.readiness.actions)}>
                {story.actions.length === 0 && <p className="mt-1 text-sm text-annotation">—</p>}
                {story.actions.map((action) => (
                  <div key={action.component_id} className="mt-1">
                    <p className="text-sm">
                      {action.summary}
                      {action.tools.length > 0 && (
                        <span className="font-mono text-xs text-annotation">
                          {" "}
                          · {action.tools.join(", ")}
                        </span>
                      )}
                    </p>
                    {action.evidence.map((e, i) => (
                      <EvidenceQuote key={i} evidence={e} />
                    ))}
                  </div>
                ))}
              </Component>

              <Component label="Result" meta={story.readiness.result}>
                {story.results.length === 0 && <p className="mt-1 text-sm text-annotation">—</p>}
                {story.results.map((result) => (
                  <div key={result.component_id} className="mt-1">
                    <p className="text-sm">{result.text}</p>
                    {result.evidence.map((e, i) => (
                      <EvidenceQuote key={i} evidence={e} />
                    ))}
                  </div>
                ))}
              </Component>
            </div>

            {story.questions.map((q, index) => question(story, q, index))}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              {story.readiness.resume_ready ? (
                <button
                  className="stamp text-viridian"
                  disabled={busyId === story.id}
                  onClick={() => run(story.id, () => api.approveStory(story.id))}
                >
                  Approve
                </button>
              ) : (
                <span className="font-mono text-xs text-carmine/80">
                  {story.readiness.blockers.join("; ") || "not resume-ready"}
                </span>
              )}
              {excludingId === story.id ? (
                <>
                  <input
                    autoFocus
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Reason (portfolio / low-value / duplicate)…"
                    className="min-w-0 flex-1 border border-rule bg-transparent px-2 py-1 font-mono text-xs"
                  />
                  <button
                    className="stamp text-carmine"
                    disabled={busyId === story.id || !reason.trim()}
                    onClick={() => run(story.id, () => api.excludeStory(story.id, reason.trim()))}
                  >
                    Confirm exclude
                  </button>
                  <button
                    className="stamp text-annotation"
                    onClick={() => {
                      setExcludingId(null);
                      setReason("");
                    }}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  className="stamp text-annotation"
                  disabled={busyId === story.id}
                  onClick={() => setExcludingId(story.id)}
                >
                  Exclude…
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
