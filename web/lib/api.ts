/** Typed client for the JobPilot API (FastAPI, CORS-enabled for this origin). */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const USER_ID = process.env.NEXT_PUBLIC_USER_ID ?? "u1";

export interface Contact {
  name: string;
  title: string | null;
  email: string | null;
  source: string;
}

export interface OutreachDraft {
  id: number;
  application_id: number;
  status: string;
  subject: string;
  body: string;
  contact: Contact | null;
  job_title: string | null;
  job_company: string | null;
}

export interface Materials {
  summary: string;
  highlights: string[];
  cover_letter: string;
}

export interface Application {
  id: number;
  status: string;
  job_source: string;
  job_external_id: string;
  job_title: string;
  job_company: string;
  job_url: string | null;
  job_canonical_url: string | null;
  master_cv_version: number;
  materials: Materials;
  allowed_transitions: string[];
}

export interface ApplicationDetail extends Application {
  outreach: {
    id: number;
    status: string;
    subject: string;
    body: string;
    contact: Contact | null;
  } | null;
}

export interface Match {
  rank: number;
  score: number;
  rationale: string;
  matched_terms: string[];
  job: {
    source: string;
    external_id: string;
    title: string;
    company: string;
    location: string | null;
    url: string | null;
    canonical_url: string | null;
    remote: boolean;
  };
}

export interface MatchesResponse {
  master_cv_version: number | null;
  matches: Match[];
}

export interface SnapshotClaim {
  claim_id: number;
  problem: string | null;
  action: string;
  result: string | null;
  result_kind: string;
  result_status: string;
}

export interface SnapshotExperience {
  experience_id: number;
  name: string;
  subtitle: string | null;
  dates: string | null;
  sort_order: number;
  claims: SnapshotClaim[];
}

export interface MasterCvSummary {
  version: number;
  created_at: string | null;
  claim_count: number;
  claims: {
    problem: string | null;
    action: string;
    result: string | null;
    source_type: string;
    source_ref: string;
  }[];
  sections: Record<string, SnapshotExperience[]>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response.json().then(
      (body) => body?.detail ?? response.statusText,
      () => response.statusText,
    );
    throw new Error(typeof detail === "string" ? detail : response.statusText);
  }
  return response.json() as Promise<T>;
}

export interface Interview {
  id: number;
  company: string;
  job_title: string | null;
  stage: string;
  received_at: string | null;
  gmail_message_id: string;
  evidence_quote: string;
  has_prep_packet: boolean;
  allowed_transitions: string[];
}

export interface InterviewDetail extends Interview {
  prep_packet: string | null;
}

export interface ClaimEvidence {
  evidence_id: number;
  field: string;
  outcome_quote: string | null;
  source_type: string | null;
  source_ref: string | null;
  source_url: string | null;
  chunk_text: string | null;
}

export interface ClaimCard {
  id: number;
  experience_id: number;
  status: string;
  problem_text: string | null;
  problem_cost_dimension: string | null;
  problem_inefficiency: string | null;
  action_text: string;
  action_tools: string[];
  result_text: string | null;
  result_kind: string;
  result_status: string;
  result_metric_json: Record<string, unknown> | null;
  validation_flags: string[];
  review_note: string | null;
  evidence: ClaimEvidence[];
}

export interface Experience {
  id: number;
  name: string;
  subtitle: string | null;
  dates: string | null;
  section: string;
  sort_order: number;
}

export interface RosterEntity {
  id: number;
  name: string;
  kind: string;
  status: string;
  section: string;
  subtitle: string | null;
  dates: string | null;
  aliases: string[];
  sort_order: number;
  merged_into_id: number | null;
  assigned_chunks: number;
}

export interface MasterCvArtifact {
  id: number;
  kind: string;
  master_cv_version: number | null;
  file_path: string;
}

export const api = {
  queue: () => request<OutreachDraft[]>(`/outreach/queue?user_id=${USER_ID}`),
  approveDraft: (id: number) =>
    request<OutreachDraft>(`/outreach/${id}/approve`, { method: "POST" }),
  discardDraft: (id: number) =>
    request<OutreachDraft>(`/outreach/${id}/discard`, { method: "POST" }),
  sendDraft: (id: number) => request<OutreachDraft>(`/outreach/${id}/send`, { method: "POST" }),
  matches: () => request<MatchesResponse>(`/matches?user_id=${USER_ID}`),
  applications: () => request<Application[]>(`/applications?user_id=${USER_ID}`),
  application: (id: number) => request<ApplicationDetail>(`/applications/${id}`),
  transitionApplication: (id: number, status: string) =>
    request<Application>(`/applications/${id}/transition`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
  masterCv: () =>
    request<MasterCvSummary>(`/master-cv/latest?user_id=${USER_ID}`).catch(() => null),
  interviews: () => request<Interview[]>(`/interviews?user_id=${USER_ID}`),
  interview: (id: number) => request<InterviewDetail>(`/interviews/${id}`),
  transitionInterview: (id: number, stage: string) =>
    request<Interview>(`/interviews/${id}/transition`, {
      method: "POST",
      body: JSON.stringify({ stage }),
    }),
  confirmInterview: (id: number) =>
    request<Interview>(`/interviews/${id}/confirm`, { method: "POST" }),
  claimsQueue: (missingResults = false) =>
    request<ClaimCard[]>(
      `/claims/queue?user_id=${USER_ID}&missing_results=${missingResults}`,
    ),
  approveClaim: (id: number) => request<ClaimCard>(`/claims/${id}/approve`, { method: "POST" }),
  rejectClaim: (id: number, reason: string) =>
    request<ClaimCard>(`/claims/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  resolveMissingResult: (id: number, resultText: string, resultKind: string) =>
    request<ClaimCard>(`/claims/${id}/edit-approve`, {
      method: "POST",
      body: JSON.stringify({ result_text: resultText, result_kind: resultKind }),
    }),
  experiences: () => request<Experience[]>(`/experiences?user_id=${USER_ID}`),
  renderMasterCv: () =>
    request<MasterCvArtifact>(`/master-cv/render?user_id=${USER_ID}`, { method: "POST" }),
  roster: () => request<RosterEntity[]>(`/roster?user_id=${USER_ID}`),
  detectRoster: () =>
    request<{ documents: number; proposed: RosterEntity[] }>(
      `/roster/detect?user_id=${USER_ID}`,
      { method: "POST" },
    ),
  assignRoster: () =>
    request<{ chunks: number; assigned: number; unassigned: number }>(
      `/roster/assign?user_id=${USER_ID}`,
      { method: "POST" },
    ),
  confirmRosterEntity: (id: number) =>
    request<RosterEntity>(`/roster/${id}/confirm`, { method: "POST" }),
  discardRosterEntity: (id: number) =>
    request<RosterEntity>(`/roster/${id}/discard`, { method: "POST" }),
  editRosterEntity: (id: number, edits: Partial<Pick<RosterEntity, "name" | "dates">>) =>
    request<RosterEntity>(`/roster/${id}`, { method: "PATCH", body: JSON.stringify(edits) }),
  mergeRosterEntities: (sourceId: number, targetId: number) =>
    request<RosterEntity>(`/roster/merge`, {
      method: "POST",
      body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
    }),
};

export const masterCvDownloadUrl = `${API_URL}/master-cv/download?user_id=${USER_ID}`;

export const oauthStartUrl = (provider: "google" | "github") =>
  `${API_URL}/oauth/${provider}/start?user_id=${USER_ID}`;
