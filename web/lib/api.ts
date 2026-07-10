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

/** A 409 refusal's machine-readable findings (bundle selection / bullet generation). */
export interface ApiViolation {
  code: string;
  message: string;
  next_action: string | null;
}

function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "violations" in detail) {
    const violations = (detail as { violations: ApiViolation[] }).violations;
    if (Array.isArray(violations) && violations.length > 0) {
      return violations.map((v) => v.message ?? v.code).join("; ");
    }
  }
  return fallback;
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
    throw new Error(detailMessage(detail, response.statusText));
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

export interface RosterOverlap {
  entity_a: { id: number; name: string };
  entity_b: { id: number; name: string };
  shared_outcome_quotes: string[];
  shared_chunk_texts: string[];
}

export interface UnassignedEvidence {
  id: number;
  source_type: string;
  source_ref: string;
  chunk_text: string;
}

export interface StoryEvidence {
  source_type: string;
  source_ref: string;
  source_url: string | null;
  chunk_text: string;
}

export interface StoryProblem {
  text: string | null;
  presence: string;
  support: string;
  evidence: StoryEvidence[];
}

export interface StoryActionComponent {
  component_id: string;
  summary: string;
  tools: string[];
  claim_ids: number[];
  evidence: StoryEvidence[];
}

export interface StoryResultComponent {
  component_id: string;
  text: string;
  outcome_quote: string | null;
  claim_ids: number[];
  evidence: StoryEvidence[];
}

export interface StoryReadiness {
  problem: string;
  actions: number;
  result: string;
  resume_ready: boolean;
  missing: string[];
  blockers: string[];
}

export interface StoryQuestion {
  kind: string;
  component: string;
  text: string;
  quotes: string[];
}

export interface StoryProblemSpace {
  id: string;
  label: string | null;
  scope: string | null;
}

export interface StoryBullet {
  text: string;
  problem_space_id: string;
  bundle_id: string;
  action_candidate_id: string;
  result_candidate_id: string;
  claim_ids: number[];
}

export interface BulletFollowUp {
  kind: string;
  component: string;
  text: string;
  options: string[];
  next_action: string;
}

export interface BulletResponse {
  bullet: StoryBullet | null;
  follow_up: BulletFollowUp | null;
}

export interface StoryCard {
  id: number;
  experience_id: number;
  experience_name: string | null;
  section: string | null;
  problem_space: StoryProblemSpace;
  bundle_status: string | null;
  selected_action_id: string | null;
  selected_result_id: string | null;
  review_status: string;
  reviewed_at: string | null;
  decision_note: string | null;
  readiness: StoryReadiness;
  problem: StoryProblem | null;
  actions: StoryActionComponent[];
  results: StoryResultComponent[];
  questions: StoryQuestion[];
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
  rosterOverlaps: () => request<RosterOverlap[]>(`/roster/overlaps?user_id=${USER_ID}`),
  rosterUnassigned: () =>
    request<{ count: number; items: UnassignedEvidence[] }>(
      `/roster/unassigned?user_id=${USER_ID}`,
    ),
  assignEvidence: (evidenceId: number, experienceId: number) =>
    request<{ id: number; experience_id: number; source_ref: string }>(
      `/roster/evidence/${evidenceId}/assign`,
      { method: "POST", body: JSON.stringify({ experience_id: experienceId }) },
    ),
  stories: (status = "pending_review") =>
    request<StoryCard[]>(`/stories?user_id=${USER_ID}&status=${status}`),
  synthesizeStories: () =>
    request<{ synthesized: number[]; quarantined: number[]; skipped: number[] }>(
      `/stories/synthesize?user_id=${USER_ID}`,
      { method: "POST" },
    ),
  approveStory: (id: number) => request<StoryCard>(`/stories/${id}/approve`, { method: "POST" }),
  answerStory: (id: number, component: string, text: string) =>
    request<StoryCard>(`/stories/${id}/answer`, {
      method: "POST",
      body: JSON.stringify({ component, text }),
    }),
  excludeStory: (id: number, reason: string) =>
    request<StoryCard>(`/stories/${id}/exclude`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  selectStory: (id: number, selectedActionId: string, selectedResultId: string) =>
    request<StoryCard>(`/stories/${id}/select`, {
      method: "POST",
      body: JSON.stringify({
        selected_action_id: selectedActionId,
        selected_result_id: selectedResultId,
      }),
    }),
  storyBullet: (id: number) => request<BulletResponse>(`/stories/${id}/bullet`, { method: "POST" }),
};

export const masterCvDownloadUrl = `${API_URL}/master-cv/download?user_id=${USER_ID}`;

export const oauthStartUrl = (provider: "google" | "github") =>
  `${API_URL}/oauth/${provider}/start?user_id=${USER_ID}`;
