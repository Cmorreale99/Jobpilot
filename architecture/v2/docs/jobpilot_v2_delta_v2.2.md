# JobPilot V2 — Delta v2.2 (schema refinements + template spec)

Applies on top of outline v2.1. Three schema changes, one status change, one
template pack.

---

## 1. Problem: inefficiency alongside cost dimension

```
claims
  problem_cost_dimension  (money|time|risk|quality|revenue)      -- unchanged
  problem_inefficiency    (manual|slow|not_working|integration_difficulty|not_user_friendly)  -- NEW, nullable
```

Validator rule update: a Problem passes if it declares a cost dimension, an
inefficiency, or both. "Manual Excel workflow for ~160 professionals" is a
valid Problem via `manual` even before the $8M figure quantifies it.

## 2. Result must resolve the declared pain point (coupling rule)

Result is quantified or explicitly qualitative-but-evidenced **and must solve
a pain point declared in the Problem**. Enforcement:

- `result_metric_json` gains a `resolves` tag naming which declared dimension
  or inefficiency the outcome addresses (`resolves: "manual"`,
  `resolves: "time"`).
- Deterministic check: `resolves` must appear among the claim's declared
  `problem_cost_dimension` / `problem_inefficiency` values. A Problem about
  `slow` with a Result about revenue fails validation → review queue with a
  "result does not address stated problem" flag.
- This kills the non-sequitur PAR (real problem, real result, no causal link)
  which neither the outcome-statement rule nor human skimming reliably catches.

## 3. Section assignment (Projects & Hackathons)

```
experiences
  section     (professional_experience | projects_hackathons)   -- NEW, user-assigned
  sort_order  int                                               -- NEW
```

- The review layer gets a section picker per experience: user chooses what
  renders under PROFESSIONAL EXPERIENCE vs PROJECTS AND HACKATHONS, and order.
- The renderer context builder routes approved claims by `section`; the
  template renders both groups with identical entry formatting.

## 4. Status: model research done; eval deferred

Multi-agent research workflow: **complete**. Golden-set eval, tailored
resumes, grades: **V3**, unchanged. The shortlist sits in
`research/model_comparison.md` until V3 Phase 0 runs the eval harness against
review-queue-derived golden data.

---

## 5. Template pack (M11 deliverables — built from the real CV)

| File | What it is |
|---|---|
| `resume_template.docx` | docxtpl template built from the actual 2026 CV — every paragraph cloned from the original, content swapped for Jinja tags. Goes to `templates/`. |
| `render_master_cv.py` | Renderer module for `app/render/` with fidelity assertions (no leftover tags, all text runs TNR). |
| `fixture_master_cv.json` | The renderer input contract with sample data. |
| `rendered_sample.docx` | Proof render from the fixture — visually verified against the original. |

### Formatting facts extracted from the real CV (now locked in the template)

- Page: US Letter; margins 0.5" top/bottom, 0.7" left/right
- Name: Times New Roman 16pt bold (Title style, blue bottom rule under the
  title block)
- Tagline: TNR 12pt bold, same Title block
- Contact line: TNR 11pt, centered under the rule
- Section headings (EDUCATION / SKILLS / PROFESSIONAL EXPERIENCE / PROJECTS
  AND HACKATHONS): TNR 12pt, bold + blue via Heading1 style
- Skills rows: bold category name + " — " + comma-joined skills, TNR 11pt
- Entry headers: bold name + " — subtitle", dates flush right
- Bullets: hanging indent (720/360 twips), TNR 11pt, tight 40/40 spacing
- **Note: body text is 11pt TNR in the actual document** — the earlier V1
  note said "size 12"; the template copies the real document, so 11pt wins.
  Change the template if 12pt is actually wanted.

### One deliberate deviation

The original right-aligns dates with hand-counted literal tabs (2–5 per line,
varying by name length). The template replaces this with a **right tab stop at
the right margin** — output is pixel-identical, but alignment no longer breaks
when a company name is longer or shorter than the original's. This is the only
structural change; everything else is cloned XML.

### Template context (what the DB must produce)

```json
{
  "name": "...", "tagline": "...", "contact_line": "...",
  "education":  [{"institution": "...", "detail": "..."}],
  "skills":     [{"name": "...", "skill_list": ["...", "..."]}],
  "experiences":              [{"name": "...", "subtitle": "Role | Location", "dates": "...", "bullets": ["..."]}],
  "projects_and_hackathons":  [{"name": "...", "subtitle": "Event (placement) | Role", "dates": "...", "bullets": ["..."]}]
}
```

Gotcha encoded in the contract: the skills key is `skill_list`, not `items` —
`items` collides with the dict method in Jinja and breaks the join filter.
