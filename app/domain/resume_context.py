"""Builds the renderer's exact input contract from a profile + APPROVED claims.

The contract (see ``tests/fixtures/fixture_master_cv.json``, verified against the
frozen renderer):

    name, tagline, contact_line: str
    education: [{institution, detail}]
    skills:    [{name, skill_list: [str]}]      # the key is skill_list, NOT items —
                                                # "items" collides with the dict method
                                                # in Jinja and breaks the join filter
    experiences / projects_and_hackathons: [{name, subtitle, dates, bullets: [str]}]

Rules encoded here:

* **Approved claims only** — the same defense-in-depth filter as the snapshot builder;
  the service layer's query is the primary gate.
* Experiences route by their user-assigned ``section`` and order by ``sort_order``.
* Bullets derive from real claim text (action + evidenced result), never invented.
* ``None`` never reaches the template (docxtpl would print the string "None"):
  absent subtitle/dates render as empty strings.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.claims import Claim, ClaimStatus, Experience, ExperienceSection


class ResumeProfileError(ValueError):
    """Raised when the profile data does not match the renderer contract."""


@dataclass(frozen=True)
class ResumeProfile:
    """The non-claim resume data (header, education, skills) — supplied by the user."""

    name: str
    tagline: str = ""
    contact_line: str = ""
    education: tuple[dict[str, str], ...] = ()
    skills: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResumeProfile:
        """Validate profile JSON against the contract (the skill_list gotcha included)."""
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ResumeProfileError("profile needs a non-empty 'name'")
        education = []
        for entry in data.get("education", []):
            if not isinstance(entry, dict) or "institution" not in entry:
                raise ResumeProfileError("each education entry needs an 'institution'")
            education.append(
                {
                    "institution": str(entry["institution"]),
                    "detail": str(entry.get("detail", "")),
                }
            )
        skills = []
        for entry in data.get("skills", []):
            if not isinstance(entry, dict) or "name" not in entry:
                raise ResumeProfileError("each skills entry needs a 'name'")
            if "skill_list" not in entry:
                raise ResumeProfileError(
                    "each skills entry needs 'skill_list' (NOT 'items' — that key "
                    "collides with the dict method in Jinja and breaks the template)"
                )
            if not isinstance(entry["skill_list"], list):
                raise ResumeProfileError("'skill_list' must be a list of strings")
            skills.append(
                {"name": str(entry["name"]), "skill_list": [str(s) for s in entry["skill_list"]]}
            )
        return cls(
            name=name.strip(),
            tagline=str(data.get("tagline", "")),
            contact_line=str(data.get("contact_line", "")),
            education=tuple(education),
            skills=tuple(skills),
        )


def _sentence(text: str) -> str:
    stripped = text.strip()
    return stripped if stripped.endswith((".", "!", "?")) else stripped + "."


def render_claim_bullet(claim: Claim) -> str:
    """One resume bullet from one approved claim: the action, then its evidenced result.

    Deterministic and content-preserving — no rewriting, no invention. A missing
    Result simply renders as the action alone.
    """
    parts = [claim.action_text]
    if (claim.result_text or "").strip():
        parts.append(claim.result_text or "")
    return " ".join(_sentence(part) for part in parts)


def build_resume_context(
    profile: ResumeProfile,
    experiences: Sequence[Experience],
    claims: Sequence[Claim],
) -> dict[str, Any]:
    """The renderer's exact JSON contract, built from approved claims only."""
    approved = [c for c in claims if c.status is ClaimStatus.APPROVED]
    claims_by_experience: dict[int, list[Claim]] = {}
    for claim in approved:
        claims_by_experience.setdefault(claim.experience_id, []).append(claim)

    groups: dict[ExperienceSection, list[dict[str, Any]]] = {
        ExperienceSection.PROFESSIONAL_EXPERIENCE: [],
        ExperienceSection.PROJECTS_HACKATHONS: [],
    }
    for experience in sorted(experiences, key=lambda e: (e.sort_order, e.id)):
        experience_claims = claims_by_experience.get(experience.id)
        if not experience_claims:
            continue
        groups[experience.section].append(
            {
                "name": experience.name,
                "subtitle": experience.subtitle or "",
                "dates": experience.dates or "",
                "bullets": [
                    render_claim_bullet(c) for c in sorted(experience_claims, key=lambda c: c.id)
                ],
            }
        )

    return _with_profile(profile, groups)


def _with_profile(
    profile: ResumeProfile,
    groups: dict[ExperienceSection, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Assemble the renderer contract from the profile header + the two section groups."""
    return {
        "name": profile.name,
        "tagline": profile.tagline,
        "contact_line": profile.contact_line,
        "education": [dict(entry) for entry in profile.education],
        "skills": [
            {"name": entry["name"], "skill_list": list(entry["skill_list"])}
            for entry in profile.skills
        ],
        "experiences": groups[ExperienceSection.PROFESSIONAL_EXPERIENCE],
        "projects_and_hackathons": groups[ExperienceSection.PROJECTS_HACKATHONS],
    }


def build_story_resume_context(
    profile: ResumeProfile, snapshot_content: dict[str, Any]
) -> dict[str, Any]:
    """The renderer's exact JSON contract, built from a story-shaped snapshot (V3 Phase 4).

    The story snapshot (``domain/story_snapshot.py``) already carries each experience's
    render bullets (P/A/R derived verbatim, gate- and duplicate-checked). This reads that
    frozen version — never live rows — and maps it into the same ``{name, subtitle, dates,
    bullets}`` shape ``build_resume_context`` produces for approved claims, so the frozen
    renderer is untouched. Section keys map ``professional_experience`` → ``experiences`` and
    ``projects_hackathons`` → ``projects_and_hackathons``.
    """
    sections: dict[str, Any] = snapshot_content.get("sections", {})
    groups: dict[ExperienceSection, list[dict[str, Any]]] = {
        ExperienceSection.PROFESSIONAL_EXPERIENCE: [],
        ExperienceSection.PROJECTS_HACKATHONS: [],
    }
    for section in ExperienceSection:
        for entry in sections.get(section.value, []):
            groups[section].append(
                {
                    "name": entry.get("name", ""),
                    "subtitle": entry.get("subtitle") or "",
                    "dates": entry.get("dates") or "",
                    "bullets": [bullet["text"] for bullet in entry.get("bullets", [])],
                }
            )
    return _with_profile(profile, groups)
