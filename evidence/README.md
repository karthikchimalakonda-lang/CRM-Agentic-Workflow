# Demonstration evidence

These images were rendered from an actual local dry run of this repository (`python tools/render_evidence.py`) using the synthetic requests in `samples/nlp-requests.json`. No OCI tenancy or CRM system was contacted. Record IDs are deterministic dry-run placeholders.

| File | Demonstrates |
| --- | --- |
| `01-unit-tests-passing.png` | All 13 unit tests passing: NLU extraction, planning, ID chaining, scoring, pricing, guardrails, SLA. |
| `02-supervisor-nlp-dispatch.png` | Five natural-language requests interpreted by the supervisor and dispatched to the right agents, including the human-review path. |
| `03-lead-opportunity-quote-chain.png` | One request producing a linked lead -> opportunity -> quote, with the supervisor audit trail. |
| `04-quote-discount-guardrail.png` | Auto-quote with three resolved catalog lines. A 22% discount exceeds the 15% limit, so the quote is set to Pending Approval. |
| `05-case-priority-sla.png` | Extracted entities and a P1 Technical case with queue routing and a 4-hour SLA deadline. |
| `run-output/CRM-DEMO-00*.json` | Full supervisor responses for each sample request. |

All content in this folder is synthetic and contains no client names, credentials, OCIDs, or production identifiers.
