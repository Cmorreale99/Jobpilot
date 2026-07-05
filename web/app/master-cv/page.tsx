"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { api, type MasterCvSummary, type SnapshotExperience } from "@/lib/api";
import { EmptyRow, SectionHead } from "@/components/ledger";

const SECTION_TITLES: Record<string, string> = {
  professional_experience: "Professional experience",
  projects_hackathons: "Projects & hackathons",
};

function ExperienceBlock({ experience }: { experience: SnapshotExperience }) {
  return (
    <section aria-label={experience.name}>
      <SectionHead
        title={`${experience.name}${experience.dates ? ` · ${experience.dates}` : ""}`}
        count={experience.claims.length}
      />
      {experience.subtitle && (
        <p className="font-mono text-xs text-annotation">{experience.subtitle}</p>
      )}
      <ul className="divide-y divide-rule">
        {experience.claims.map((claim) => (
          <li key={claim.claim_id} className="py-3">
            {claim.problem && (
              <p className="text-sm text-annotation">
                <span className="font-mono text-xs uppercase tracking-wide">problem</span>{" "}
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
                <span className="font-mono text-xs uppercase tracking-wide">result</span>{" "}
                {claim.result}
                {claim.result_status === "user_attested" ? " (attested)" : ""}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function MasterCvFolio() {
  const [cv, setCv] = useState<MasterCvSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .masterCv()
      .then((data) => {
        if (data === null) setError("No Master CV yet — approve claims to build one.");
        else setCv(data);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the CV."));
  }, []);

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
              {cv.claim_count} approved claims
              {cv.created_at ? ` · built ${cv.created_at.slice(0, 10)}` : ""} · every claim was
              human-approved and traces to evidence; nothing is invented
            </p>
          </header>

          {Object.entries(cv.sections).map(([section, experiences]) =>
            experiences.length > 0 ? (
              <section key={section} aria-label={SECTION_TITLES[section] ?? section}>
                <h2 className="mt-8 font-masthead text-xl font-medium tracking-tight">
                  {SECTION_TITLES[section] ?? section}
                </h2>
                {experiences.map((experience) => (
                  <ExperienceBlock key={experience.experience_id} experience={experience} />
                ))}
              </section>
            ) : null,
          )}
        </>
      )}
    </main>
  );
}
