"""
Regenerate the evidence screenshots from a real local dry run.

Runs the unit tests and the demo runner, then renders their console output and
selected result JSON into terminal-style PNG images in evidence/.

    python tools/render_evidence.py
"""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
WRAP = 118
FONT_CANDIDATES = ["C:/Windows/Fonts/consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                   "/System/Library/Fonts/Menlo.ttc"]
COLORS = {"bg": (24, 26, 33), "bar": (44, 47, 58), "text": (220, 223, 228), "title": (150, 156, 170),
          "ok": (126, 211, 132), "warn": (240, 190, 90), "err": (240, 110, 110), "accent": (110, 180, 245)}


def font(size):
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def line_color(line):
    stripped = line.strip()
    if stripped.startswith("!") or "Pending Approval" in line or "needs_human_review" in line:
        return COLORS["warn"]
    if " ok" in line[-4:] or stripped.startswith("OK") or "created" in line or "status=completed" in line:
        return COLORS["ok"]
    if "FAIL" in line or "ERROR" in line:
        return COLORS["err"]
    if stripped.startswith(">") or stripped.startswith("$"):
        return COLORS["accent"]
    return COLORS["text"]


def render(name, title, text):
    lines = []
    for raw in text.rstrip().splitlines():
        lines.extend(textwrap.wrap(raw, WRAP, subsequent_indent="    ") or [""])
    body_font, title_font = font(15), font(14)
    line_height, pad, bar = 21, 18, 34
    width = 1240
    height = bar + pad * 2 + line_height * len(lines)
    image = Image.new("RGB", (width, height), COLORS["bg"])
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, bar], fill=COLORS["bar"])
    for i, color in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
        draw.ellipse([14 + i * 20, 11, 26 + i * 20, 23], fill=color)
    draw.text((86, 9), title, font=title_font, fill=COLORS["title"])
    y = bar + pad
    for line in lines:
        draw.text((pad, y), line, font=body_font, fill=line_color(line))
        y += line_height
    image.save(EVIDENCE / name)
    print(f"wrote evidence/{name}")


def run(command):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env)
    return completed.stdout + completed.stderr


def main():
    tests = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    tests = re.sub(r"\.\.\. INFO:[^\n]*\n(?:INFO:[^\n]*\n)*ok", "... ok", tests)
    render("01-unit-tests-passing.png", "local dry run - unit tests",
           "$ python -m unittest discover -s tests -v\n" + tests)

    demo = run([sys.executable, "local_runner/run_demo.py"])
    demo = "\n".join(l for l in demo.splitlines() if not l.startswith("INFO:"))
    render("02-supervisor-nlp-dispatch.png", "local dry run - supervisor agent processing NLP requests",
           "$ python local_runner/run_demo.py\n\n" + demo)

    chain = json.loads((EVIDENCE / "run-output" / "CRM-DEMO-001.json").read_text(encoding="utf-8"))
    excerpt = {
        "status": chain["status"],
        "plan": chain["plan"],
        "lead": {k: chain["results"]["lead"][k] for k in ("record_id", "score", "qualification")},
        "opportunity": {"record_id": chain["results"]["opportunity"]["record_id"],
                        "source_lead_id": chain["results"]["opportunity"]["record"]["source_lead_id"],
                        "stage": chain["results"]["opportunity"]["stage"],
                        "expected_close_date": chain["results"]["opportunity"]["record"]["expected_close_date"]},
        "quote": {"record_id": chain["results"]["quote"]["record_id"],
                  "opportunity_id": chain["results"]["quote"]["record"]["opportunity_id"],
                  "grand_total": chain["results"]["quote"]["grand_total"],
                  "quote_status": chain["results"]["quote"]["quote_status"]},
        "audit": chain["audit"],
    }
    render("03-lead-opportunity-quote-chain.png", "evidence/run-output/CRM-DEMO-001.json (excerpt)",
           json.dumps(excerpt, indent=2))

    approval = json.loads((EVIDENCE / "run-output" / "CRM-DEMO-003.json").read_text(encoding="utf-8"))
    record = approval["results"]["quote"]["record"]
    render("04-quote-discount-guardrail.png", "evidence/run-output/CRM-DEMO-003.json - quote record",
           json.dumps({"review_items": approval["review_items"], "quote": record}, indent=2))

    service = json.loads((EVIDENCE / "run-output" / "CRM-DEMO-002.json").read_text(encoding="utf-8"))
    render("05-case-priority-sla.png", "evidence/run-output/CRM-DEMO-002.json - case record",
           json.dumps({"understanding": service["understanding"], "case": service["results"]["case"]["record"],
                       "review_items": service["review_items"]}, indent=2))


if __name__ == "__main__":
    main()
