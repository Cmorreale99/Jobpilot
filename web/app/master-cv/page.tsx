"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { api, type MasterCvSummary } from "@/lib/api";
import { EmptyRow, SectionHead } from "@/components/ledger";

export default function MasterCvFolio() {
  const [cv, setCv] = useState<MasterCvSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .masterCv()
      .then((data) => {
        if (data === null) setError("No Master CV yet — the nightly run builds one.");
        else setCv(data);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the CV."));
  }, []);

  const sourceTitle = (ref: string) =>
    cv?.sources.find((s) => s.external_ref === ref)?.title ?? ref;

  const claimsBySource = new Map<string, MasterCvSummary["claims"]>();
  for (const claim of cv?.claims ?? []) {
    const key = claim.source_ref;
    claimsBySource.set(key, [...(claimsBySource.get(key) ?? []), claim]);
  }

  return (
    <main className="mx-auto max-w-3xl px-5 pb-24 pt-10">
      <nav className="font-mono text-xs text-annotation">
        <Link href="/" className="underline-offset-2 hover:underline">
          ← Ledger
        </Link>
      </nav>

      {error && <p className="mt-6 text-sm text-carmine">{error}</p>}
      {!cv && !error && <EmptyRow>Loading the Master CV…</EmptyRow>}

      {cv && (
        <>
          <header className="mt-4 border-b-2 border-ink pb-4">
            <h1 className="font-masthead text-3xl font-medium tracking-tight">
              Master CV <span className="italic text-annotation">— version {cv.version}</span>
            </h1>
            <p className="mt-2 font-mono text-xs text-annotation">
              {cv.claim_count} claims from {cv.source_count} sources
              {cv.created_at ? ` · built ${cv.created_at.slice(0, 10)}` : ""} · every claim
              traces to a source; nothing is invented
            </p>
          </header>

          {[...claimsBySource.entries()].map(([ref, claims]) => (
            <section key={ref} aria-label={sourceTitle(ref)}>
              <SectionHead
                title={`${claims[0].source_type} · ${sourceTitle(ref)}`}
                count={claims.length}
              />
              <ul className="divide-y divide-rule">
                {claims.map((claim, index) => (
                  <li key={`${ref}-${index}`} className="py-3">
                    {claim.problem && (
                      <p className="text-sm text-annotation">
                        <span className="font-mono text-xs uppercase tracking-wide">
                          problem
                        </span>{" "}
                        {claim.problem}
                      </p>
                    )}
                    <p className="text-sm font-medium">
                      <span className="font-mono text-xs uppercase tracking-wide text-annotation">
                        action
                      </span>{" "}
                      {claim.action}
                    </p>
                    {claim.result && (
                      <p className="text-sm text-viridian">
                        <span className="font-mono text-xs uppercase tracking-wide">
                          result
                        </span>{" "}
                        {claim.result}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </main>
  );
}
