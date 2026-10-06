"""
Local dry-run of the CRM agentic workflow.

Runs every request in samples/nlp-requests.json through the supervisor agent,
which dispatches the specialist agents in-process (no OCI function OCIDs set)
with CRM_DRY_RUN enabled. Writes one result file per request to
evidence/run-output/ and prints a console summary.

    python local_runner/run_demo.py
"""

import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "evidence" / "run-output"

os.environ.setdefault("CRM_DRY_RUN", "true")
for name in ("LEAD", "OPPORTUNITY", "QUOTE", "CASE"):
    os.environ.pop(f"{name}_AGENT_FUNCTION_ID", None)
os.environ.pop("GENAI_MODEL_ID", None)


def load_supervisor():
    path = ROOT / "sanitized-source" / "crm-supervisor-agent" / "func.py"
    spec = importlib.util.spec_from_file_location("crm_supervisor_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summarise(result):
    lines = [f"[{result['correlation_id']}] status={result['status']}"]
    understanding = result.get("understanding", {})
    lines.append(f"  planner={understanding.get('planner')} intents={understanding.get('intents')} "
                 f"confidence={understanding.get('confidence')}")
    for name, res in result.get("results", {}).items():
        detail = {
            "lead": lambda r: f"score={r.get('score')} {r.get('qualification')}",
            "opportunity": lambda r: f"stage={r.get('stage')} win={r.get('win_probability_pct')}%",
            "quote": lambda r: f"total={r.get('grand_total')} {r.get('quote_status')}",
            "case": lambda r: f"{r.get('priority')} {r.get('category')} queue={r.get('queue')}",
        }[name](res) if res.get("status") == "created" else res.get("errors") or res.get("reason")
        lines.append(f"  -> {name:<11} {res.get('status'):<8} {res.get('record_id') or '-':<15} {detail}")
    for item in result.get("review_items", []):
        lines.append(f"  ! review: {item}")
    if result.get("reason"):
        lines.append(f"  ! {result['reason']}")
    return "\n".join(lines)


def main():
    supervisor = load_supervisor()
    requests = json.loads((ROOT / "samples" / "nlp-requests.json").read_text(encoding="utf-8"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for request in requests:
        result = supervisor.process(request)
        result.pop("duration_ms", None)
        (OUTPUT_DIR / f"{request['correlation_id']}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"> {request['request_text']}")
        print(summarise(result))
        print()


if __name__ == "__main__":
    main()
