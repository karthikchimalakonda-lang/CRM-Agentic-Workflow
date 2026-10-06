# Evidence checklist

## Before publishing or submitting

- [x] Source reviewed for client names, customer data, production URLs, OCIDs, namespaces, tokens, API keys, and passwords.
- [x] All endpoints and IDs are environment-variable placeholders (`*.example.invalid`, empty OCIDs).
- [x] Companies, contacts, emails (`example.com/.org/.net`), products, and prices are synthetic.
- [x] No private keys, OCI config, `.env`, or OAuth files in the repository (`.gitignore` enforces this).
- [x] Unit tests pass and evidence was regenerated from the current code.
- [ ] Repository is public or accessible to ACE reviewers.
- [ ] README and Mermaid diagram render correctly on GitHub.

## Optional additional evidence after an OCI deployment

Mask OCIDs, tenancy, compartment, and endpoint details in every screenshot.

1. OCI Functions application showing the five function names.
2. Supervisor invocation from OCI Cloud Shell or API Gateway with a synthetic request.
3. OCI Logging entries showing the supervisor dispatching agents.
4. Configuration page showing environment-variable names (values masked).
5. CRM sandbox records created by the workflow, with synthetic data.

## Suggested demo flow (under 3 minutes)

1. State the problem: one sentence of intent becomes several manual CRM entries.
2. Show the architecture diagram and the supervisor-agent pattern.
3. Submit the combined lead + quote + case request and walk through the plan and linked IDs.
4. Show the discount guardrail and the low-confidence human-review path.
5. Close with the OCI services used and the operational benefit.
