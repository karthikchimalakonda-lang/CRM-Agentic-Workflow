# Sanitized source

Each folder is an independently deployable OCI Function (Python 3.11, FDK).

| Folder | Role | Invoked by |
| --- | --- | --- |
| `crm-supervisor-agent` | NLP planning, orchestration, guardrails, audit | API Gateway |
| `lead-creation-agent` | Lead validation, scoring, qualification | Supervisor |
| `opportunity-creation-agent` | Opportunity stage, probability, close date | Supervisor |
| `quote-generation-agent` | Catalog pricing, discounts, approval policy, totals | Supervisor |
| `case-creation-agent` | Case category, priority, SLA, queue routing | Supervisor |

Every function exposes `process(payload: dict) -> dict` for local testing, plus the FDK `handler(ctx, data)` entry point. All endpoints, OCIDs, and secrets come from environment variables. Defaults are placeholders, and `CRM_DRY_RUN` defaults to `true`.
