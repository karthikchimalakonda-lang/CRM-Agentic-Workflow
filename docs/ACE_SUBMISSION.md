# ACE Product Usage submission draft

## Suggested title

Agentic CRM Automation on OCI: Supervisor Agent for Natural-Language Lead, Opportunity, Quote, and Case Creation

## Contribution description

I built an original agentic workflow on Oracle Cloud Infrastructure that turns natural-language sales and service requests into CRM records. A supervisor agent, implemented as an OCI Function, uses OCI Generative AI to extract intents and entities from chat, email, or voice-transcript text. It falls back to a deterministic parser when the model is unavailable. It then builds a dependency-aware action plan and invokes four specialist OCI Functions: lead creation, opportunity creation, quote generation, and case creation.

The agents pass record IDs along the chain (lead -> opportunity -> quote), so one sentence such as "new lead, wants pricing for 25 licenses" produces a linked lead, opportunity, and priced quote. The lead agent scores and qualifies prospects. The opportunity agent derives stage, win probability, and close date. The quote agent applies catalog pricing, volume tiers, tax, and a discount-approval guardrail. The case agent classifies priority and category, computes SLA deadlines, and routes to the right queue. Low-confidence requests and policy exceptions go to human review instead of being written to the CRM.

CRM credentials are held in OCI Vault and read through resource principals. The repository includes unit tests, a local dry-run harness, and evidence generated from synthetic requests.

## Evidence to attach

- Repository link: https://github.com/karthikchimalakonda-lang/CRM-Agentic-Workflow
- `evidence/01-unit-tests-passing.png` - 13 passing unit tests
- `evidence/02-supervisor-nlp-dispatch.png` - five NLP requests planned and dispatched
- `evidence/03-lead-opportunity-quote-chain.png` - linked lead -> opportunity -> quote with audit trail
- `evidence/04-quote-discount-guardrail.png` - discount above limit routed to approval
- `evidence/05-case-priority-sla.png` - P1 case with SLA deadline and routing

## Reviewer notes

All evidence comes from a local dry run (`CRM_DRY_RUN=true`) on synthetic data. Record IDs are deterministic placeholders, not CRM-issued identifiers. No client names, production identifiers, or credentials are included.
