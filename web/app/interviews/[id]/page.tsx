"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { api, type InterviewDetail } from "@/lib/api";
import { EmptyRow, SectionHead, statusInk } from "@/components/ledger";

export default function InterviewFolio() {
  const params = useParams<{ id: string }>();
  const interviewId = Number(params.id);
  const [interview, setInterview] = useState<InterviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setInterview(await api.interview(interviewId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "This interview could not be loaded.");
    }
  }, [interviewId]);

  useEffect(() => {
    if (Number.isFinite(interviewId)) void load();
  }, [interviewId, load]);

  const move = async (stage: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.transitionInterview(interviewId, stage);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The stage change failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-5 pb-24 pt-10">
      <nav className="font-mono text-xs text-annotation">
        <Link href="/" className="underline-offset-2 hover:underline">
          ← Ledger
        </Link>
      </nav>

      {error && <p className="mt-6 text-sm text-carmine">{error}</p>}
      {!interview && !error && <EmptyRow>Loading the interview…</EmptyRow>}

      {interview && (
        <>
          <header className="mt-4 border-b-2 border-ink pb-4">
            <h1 className="font-masthead text-3xl font-medium tracking-tight">
              {interview.company}
              <span className="italic text-annotation">
                {" "}
                · {interview.job_title ?? "role not stated"}
              </span>
            </h1>
            <div className="mt-3 flex flex-wrap items-baseline gap-3">
              <span className={`stamped ${statusInk(interview.stage)}`}>{interview.stage}</span>
              {interview.allowed_transitions.map((next) => (
                <button
                  key={next}
                  className="stamp text-ink"
                  disabled={busy}
                  onClick={() => move(next)}
                >
                  {next}
                </button>
              ))}
              {interview.received_at && (
                <span className="font-mono text-xs text-annotation">
                  invite received {interview.received_at.slice(0, 10)}
                </span>
              )}
            </div>
          </header>

          <section aria-label="Prep packet">
            <SectionHead title="Prep packet" />
            {!interview.prep_packet && (
              <EmptyRow>
                No prep packet yet — the nightly scan generates one when it records the
                interview.
              </EmptyRow>
            )}
            {interview.prep_packet && (
              <pre className="whitespace-pre-wrap bg-manila px-4 py-3 font-sans text-sm leading-relaxed">
                {interview.prep_packet}
              </pre>
            )}
          </section>
        </>
      )}
    </main>
  );
}
