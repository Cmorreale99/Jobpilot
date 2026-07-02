"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  api,
  oauthStartUrl,
  type Application,
  type Interview,
  type MasterCvSummary,
  type MatchesResponse,
  type OutreachDraft,
} from "@/lib/api";
import { EmptyRow, SectionHead, statusInk } from "@/components/ledger";

export default function Dashboard() {
  const [queue, setQueue] = useState<OutreachDraft[] | null>(null);
  const [matches, setMatches] = useState<MatchesResponse | null>(null);
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [interviews, setInterviews] = useState<Interview[] | null>(null);
  const [masterCv, setMasterCv] = useState<MasterCvSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [q, m, a, i, cv] = await Promise.all([
        api.queue(),
        api.matches(),
        api.applications(),
        api.interviews(),
        api.masterCv(),
      ]);
      setQueue(q);
      setMatches(m);
      setApplications(a);
      setInterviews(i);
      setMasterCv(cv);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The API is not reachable.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="mx-auto max-w-3xl px-5 pb-24 pt-10">
      <Masthead masterCv={masterCv} />

      {error && (
        <p className="mt-6 border border-carmine/40 bg-carmine-wash px-4 py-3 text-sm text-carmine">
          Can&apos;t reach the pipeline API: {error}. Start it with{" "}
          <code className="font-mono">uv run uvicorn app.main:app --reload</code>, then reload.
        </p>
      )}

      <QueueSection queue={queue} onChanged={load} />
      <InterviewsSection interviews={interviews} onChanged={load} />
      <MatchesSection matches={matches} />
      <ApplicationsSection applications={applications} onChanged={load} />
      <AccountsSection masterCv={masterCv} />
    </main>
  );
}

function InterviewsSection({
  interviews,
  onChanged,
}: {
  interviews: Interview[] | null;
  onChanged: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const active = (interviews ?? []).filter(
    (i) => i.stage === "detected" || i.stage === "scheduled",
  );
  const closed = (interviews ?? []).length - active.length;

  const move = async (id: number, stage: string) => {
    setBusyId(id);
    setActionError(null);
    try {
      await api.transitionInterview(id, stage);
      await onChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "The stage change failed.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section aria-label="Interviews">
      <SectionHead
        title="Interviews"
        count={active.length}
        accent={active.some((i) => i.stage === "detected")}
      />
      {actionError && <p className="py-1 text-sm text-carmine">{actionError}</p>}
      {interviews && active.length === 0 && (
        <EmptyRow>
          No open interviews. The nightly scan reads only invite-matching mail.
          {closed > 0 ? ` (${closed} closed)` : ""}
        </EmptyRow>
      )}
      <ul className="divide-y divide-rule">
        {active.map((interview) => (
          <li
            key={interview.id}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-2 py-3"
          >
            <div className="min-w-0 flex-1">
              <Link
                href={`/interviews/${interview.id}`}
                className="text-sm font-medium underline-offset-2 hover:underline"
              >
                {interview.company}
              </Link>
              <span className="text-sm text-annotation">
                {" "}
                · {interview.job_title ?? "role not stated"}
                {interview.has_prep_packet ? " · prep packet ready" : ""}
              </span>
            </div>
            <span className={`stamped ${statusInk(interview.stage)}`}>{interview.stage}</span>
            <div className="flex gap-2">
              {interview.allowed_transitions.map((next) => (
                <button
                  key={next}
                  className="stamp text-ink"
                  disabled={busyId === interview.id}
                  onClick={() => move(interview.id, next)}
                >
                  {next}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Masthead({ masterCv }: { masterCv: MasterCvSummary | null }) {
  return (
    <header className="border-b-2 border-ink pb-4">
      <h1 className="font-masthead text-4xl font-medium tracking-tight">
        JobPilot <span className="italic text-annotation">— pipeline ledger</span>
      </h1>
      <p className="mt-2 font-mono text-xs text-annotation">
        {masterCv
          ? `Master CV v${masterCv.version} · ${masterCv.claim_count} claims from ${masterCv.source_count} sources`
          : "No Master CV yet — the nightly run builds one from your connected sources."}
      </p>
    </header>
  );
}

function QueueSection({
  queue,
  onChanged,
}: {
  queue: OutreachDraft[] | null;
  onChanged: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const act = async (id: number, action: "approve" | "discard") => {
    setBusyId(id);
    setActionError(null);
    try {
      await (action === "approve" ? api.approveDraft(id) : api.discardDraft(id));
      await onChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "The action failed.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section aria-label="Outreach awaiting approval">
      <SectionHead title="For approval" count={queue?.length ?? 0} accent />
      {actionError && <p className="py-1 text-sm text-carmine">{actionError}</p>}
      {queue && queue.length === 0 && (
        <EmptyRow>Nothing waits on you. The nightly run adds new drafts here.</EmptyRow>
      )}
      <ul className="divide-y divide-rule">
        {(queue ?? []).map((draft) => (
          <li key={draft.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-2 py-3">
            <div className="min-w-0 flex-1">
              <Link
                href={`/applications/${draft.application_id}`}
                className="text-sm font-medium underline-offset-2 hover:underline"
              >
                {draft.subject}
              </Link>
              <p className="mt-0.5 font-mono text-xs text-annotation">
                to {draft.contact ? draft.contact.name : "the hiring team"} ·{" "}
                {draft.job_company ?? "—"}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                className="stamp text-viridian"
                disabled={busyId === draft.id}
                onClick={() => act(draft.id, "approve")}
              >
                Approve
              </button>
              <button
                className="stamp text-annotation"
                disabled={busyId === draft.id}
                onClick={() => act(draft.id, "discard")}
              >
                Discard
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function MatchesSection({ matches }: { matches: MatchesResponse | null }) {
  return (
    <section aria-label="Top matches">
      <SectionHead
        title={
          matches?.master_cv_version
            ? `Top matches — CV v${matches.master_cv_version}`
            : "Top matches"
        }
        count={matches?.matches.length ?? 0}
      />
      {matches && matches.matches.length === 0 && (
        <EmptyRow>No ranked matches yet. They appear after the nightly matching run.</EmptyRow>
      )}
      <ol className="divide-y divide-rule">
        {(matches?.matches ?? []).map((match) => (
          <li key={`${match.job.source}:${match.job.external_id}`} className="py-3">
            <div className="flex items-baseline">
              <span className="w-8 shrink-0 font-mono text-xs text-annotation">
                {String(match.rank).padStart(2, "0")}
              </span>
              <span className="text-sm font-medium">
                {match.job.url ? (
                  <a
                    href={match.job.url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline-offset-2 hover:underline"
                  >
                    {match.job.title}
                  </a>
                ) : (
                  match.job.title
                )}
                <span className="text-annotation"> · {match.job.company}</span>
              </span>
              <span className="leader" aria-hidden />
              <span className="shrink-0 font-mono text-sm">{match.score.toFixed(2)}</span>
            </div>
            <p className="mt-1 pl-8 text-sm text-annotation">{match.rationale}</p>
            {match.matched_terms.length > 0 && (
              <p className="mt-1 pl-8 font-mono text-xs text-annotation/80">
                {match.matched_terms.slice(0, 8).join(" · ")}
              </p>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function ApplicationsSection({
  applications,
  onChanged,
}: {
  applications: Application[] | null;
  onChanged: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const move = async (id: number, status: string) => {
    setBusyId(id);
    setActionError(null);
    try {
      await api.transitionApplication(id, status);
      await onChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "The status change failed.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section aria-label="Applications">
      <SectionHead title="Applications" count={applications?.length ?? 0} />
      {actionError && <p className="py-1 text-sm text-carmine">{actionError}</p>}
      {applications && applications.length === 0 && (
        <EmptyRow>No applications yet. Drafting starts from the top matches.</EmptyRow>
      )}
      <ul className="divide-y divide-rule">
        {(applications ?? []).map((application) => (
          <li
            key={application.id}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-2 py-3"
          >
            <div className="min-w-0 flex-1">
              <Link
                href={`/applications/${application.id}`}
                className="text-sm font-medium underline-offset-2 hover:underline"
              >
                {application.job_title}
              </Link>
              <span className="text-sm text-annotation"> · {application.job_company}</span>
            </div>
            <span className={`stamped ${statusInk(application.status)}`}>
              {application.status}
            </span>
            <div className="flex gap-2">
              {application.allowed_transitions.map((next) => (
                <button
                  key={next}
                  className="stamp text-ink"
                  disabled={busyId === application.id}
                  onClick={() => move(application.id, next)}
                >
                  {next}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function AccountsSection({ masterCv }: { masterCv: MasterCvSummary | null }) {
  return (
    <section aria-label="Evidence sources">
      <SectionHead title="Evidence sources" />
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 py-2 text-sm">
        <span className="text-annotation">
          {masterCv
            ? `${masterCv.source_count} sources on record`
            : "Connect accounts so the pipeline can read your career evidence:"}
        </span>
        <a className="stamp inline-block text-ink" href={oauthStartUrl("google")}>
          Connect Google Drive
        </a>
        <a className="stamp inline-block text-ink" href={oauthStartUrl("github")}>
          Connect GitHub
        </a>
      </div>
      <p className="pt-1 font-mono text-xs text-annotation/80">
        Read-only. Drive is scoped to your approved career-docs folder; GitHub to your own
        repositories.
      </p>
    </section>
  );
}
