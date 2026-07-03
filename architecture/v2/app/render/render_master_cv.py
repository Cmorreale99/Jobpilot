"""app/render/ — Master CV renderer (JobPilot V2, M11).

Renders the canonical master CV JSON into a .docx using the template
extracted from the real CV. The LLM never touches formatting; this module
never touches content.

Input contract (see fixture_master_cv.json):
  name, tagline, contact_line: str
  education: [{institution, detail}]
  skills: [{name, skill_list: [str]}]          # key is skill_list, NOT items
  experiences / projects_and_hackathons:       # split by experience.section
      [{name, subtitle, dates, bullets: [str]}]

Upstream rule: bullets are built from APPROVED claims only. The context
builder queries claims WHERE status='approved', groups by experience,
and routes each experience by its user-assigned `section` column.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

from docxtpl import DocxTemplate

TEMPLATE = Path(__file__).parent / "templates" / "resume_template.docx"


def render(master_cv_json: Path, out_docx: Path, template: Path = TEMPLATE) -> Path:
    ctx = json.loads(Path(master_cv_json).read_text())
    tpl = DocxTemplate(str(template))
    tpl.render(ctx)
    tpl.save(str(out_docx))
    _assert_fidelity(out_docx)
    return out_docx


def _assert_fidelity(docx_path: Path) -> None:
    """M11 exit-test assertions: no unrendered tags, all text runs TNR."""
    xml = zipfile.ZipFile(docx_path).read("word/document.xml").decode()
    leftover = re.findall(r"\{\{|\{%", xml)
    if leftover:
        raise AssertionError(f"{len(leftover)} unrendered Jinja tags in output")
    runs = re.findall(r"<w:r\b.*?</w:r>", xml, re.S)
    text_runs = [r for r in runs if "<w:t" in r and "<w:tab/>" not in r]
    bad = [r for r in text_runs if "Times New Roman" not in r]
    if bad:
        raise AssertionError(f"{len(bad)} text runs not Times New Roman")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixture_master_cv.json")
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("master_cv_rendered.docx")
    tpl = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("resume_template.docx")
    print("rendered:", render(src, dst, tpl))
