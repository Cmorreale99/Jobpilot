"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { api, type ApplicationDetail } from "@/lib/api";
import { EmptyRow, SectionHead, statusInk } from "@/components/ledger";

export default function ApplicationFolio() {
  const params = useParams<{ id: string }>();
  const applicationId = Number(params.id);
  const [application, setApplication] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setApplication(await api.application(applicationId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "This application could not be loaded.");
    }
  }, [applicationId]);

  useEffect(() => {
    if (Number.isFinite(applicationId)) void load();
  }, [applicationId, load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The action failed.");
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
      {!application && !error && <EmptyRow>Loading the folio…</EmptyRow>}

      {application && (
        <>
          <header className="mt-4 border-b-2 border-ink pb-4">
            <h1 className="font-masthead text-3xl font-medium tracking-tight">
              {application.job_title}
              <span className="italic text-annotation"> · {application.job_company}</span>
            </h1>
            <div className="mt-3 flex flex-wrap items-baseline gap-3">
              <span className={`stamped ${statusInk(application.status)}`}>
                {application.status}
              </span>
              {application.allowed_transitions.map((next) => (
                <button
                  key={next}
                  className="stamp text-ink"
                  disabled={busy}
                  onClick={() => act(() => api.transitionApplication(application.id, next))}
                >
                  {next}
                </button>
              ))}
              <span className="font-mono text-xs text-annotation">
                tailored from CV v{application.master_cv_version}
              </span>
            </div>
          </header>

          <section aria-label="Fit summary">
            <SectionHead title="Fit" />
            <p className="text-sm">{application.materials.summary}</p>
          </section>

          <section aria-label="Evidence highlights">
            <SectionHead title="Evidence highlights" count={application.materials.highlights.length} />
            <ul className="divide-y divide-rule">
              {application.materials.highlights.map((highlight) => (
                <li key={highlight} className="py-2 text-sm">
                  {highlight}
                </li>
              ))}
            </ul>
          </section>

          <section aria-label="Cover letter">
            <SectionHead title="Cover letter" />
            <pre className="whitespace-pre-wrap bg-manila px-4 py-3 font-sans text-sm leading-relaxed">
              {application.materials.cover_letter}
            </pre>
          </section>

          <section aria-label="Outreach draft">
            <SectionHead
              title="Outreach"
              accent={application.outreach?.status === "drafted"}
            />
            {!application.outreach && (
              <EmptyRow>No outreach draft for this application.</EmptyRow>
            )}
            {application.outreach && (
              <div>
                <div className="flex flex-wrap items-baseline gap-3">
                  <span className={`stamped ${statusInk(application.outreach.status)}`}>
                    {application.outreach.status}
                  </span>
                  {application.outreach.status === "drafted" && (
                    <>
                      <button
                        className="stamp text-viridian"
                        disabled={busy}
                        onClick={() => act(() => api.approveDraft(application.outreach!.id))}
                      >
                        Approve
                      </button>
                      <button
                        className="stamp text-annotation"
                        disabled={busy}
                        onClick={() => act(() => api.discardDraft(application.outreach!.id))}
                      >
                        Discard
                      </button>
                    </>
                  )}
                </div>
                <p className="mt-3 font-mono text-xs text-annotation">
                  to:{" "}
                  {application.outreach.contact
                    ? `${application.outreach.contact.name}${
                        application.outreach.contact.title
                          ? ` (${application.outreach.contact.title})`
                          : ""
                      } — found via ${application.outreach.contact.source}`
                    : "the hiring team (no researched contact)"}
                </p>
                <p className="mt-2 text-sm font-medium">{application.outreach.subject}</p>
                <pre className="mt-2 whitespace-pre-wrap bg-manila px-4 py-3 font-sans text-sm leading-relaxed">
                  {application.outreach.body}
                </pre>
                <p className="mt-2 font-mono text-xs text-annotation/80">
                  Approving queues it for sending once the mail client lands — nothing sends
                  automatically.
                </p>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}
