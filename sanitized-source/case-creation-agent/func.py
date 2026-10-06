"""
Case Creation Agent - OCI Functions
===================================
Opens a customer service case from a natural-language problem description.
Classifies category and priority, computes the SLA response deadline, selects
the support queue, and records customer sentiment for prioritisation.

CRM_DRY_RUN=true (default) returns the prepared case without calling the CRM.
"""

import base64
import hashlib
import io
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, time, timedelta, timezone, date

logger = logging.getLogger("case-creation-agent")

CRM_DRY_RUN = os.getenv("CRM_DRY_RUN", "true").lower() != "false"
CRM_BASE_URL = os.getenv("CRM_BASE_URL", "https://crm.example.invalid")
CRM_CASE_PATH = os.getenv("CRM_CASE_PATH", "/api/v1/cases")
CRM_SECRET_OCID = os.getenv("CRM_SECRET_OCID", "")
CRM_TIMEOUT_SECONDS = int(os.getenv("CRM_TIMEOUT_SECONDS", "20"))

PRIORITY_RULES = [
    ("P1", r"\bdown\b|outage|all users|data loss|security|breach|production stopped"),
    ("P2", r"cannot|can't|unable|failed|failing|error|not working|broken"),
    ("P4", r"question|how (?:do|to)|information|feature request|would like"),
]
SLA_HOURS = {"P1": 4, "P2": 8, "P3": 24, "P4": 72}
CATEGORIES = [
    ("Billing", r"invoice|billing|charge|payment|refund|credit"),
    ("Access", r"login|log in|password|access|locked|sso|permission"),
    ("Technical", r"down|outage|error|fail|not working|broken|dashboard|integration|slow|performance"),
    ("Product Inquiry", r"question|how (?:do|to)|feature|information"),
]
QUEUES = {"Billing": "billing-support", "Access": "identity-support", "Technical": "technical-support-l2",
          "Product Inquiry": "customer-success", "General": "service-desk"}
NEGATIVE_WORDS = r"frustrat|angry|unacceptable|again|still|wrong|disappoint|escalat|urgent"


def classify(description, urgent):
    category = next((name for name, pattern in CATEGORIES if re.search(pattern, description, re.I)), "General")
    priority = next((level for level, pattern in PRIORITY_RULES if re.search(pattern, description, re.I)), "P3")
    if urgent and priority in ("P3", "P4"):
        priority = "P2"
    negatives = len(re.findall(NEGATIVE_WORDS, description, re.I))
    sentiment = "negative" if negatives >= 2 else "concerned" if negatives == 1 else "neutral"
    return category, priority, sentiment


def subject_from(description):
    first = re.split(r"(?<=[.!?])\s+", description.strip())[0]
    return first if len(first) <= 80 else first[:77].rstrip() + "..."


def _crm_token():
    import oci

    signer = oci.auth.signers.get_resource_principals_signer()
    bundle = oci.secrets.SecretsClient(config={}, signer=signer).get_secret_bundle(CRM_SECRET_OCID).data
    return base64.b64decode(bundle.secret_bundle_content.content).decode()


def write_record(body, seed):
    if CRM_DRY_RUN:
        return "CASE-" + hashlib.sha1(seed.encode()).hexdigest()[:8].upper(), "dry-run"
    request = urllib.request.Request(
        CRM_BASE_URL.rstrip("/") + CRM_CASE_PATH,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_crm_token()}"},
    )
    with urllib.request.urlopen(request, timeout=CRM_TIMEOUT_SECONDS) as reply:
        created = json.loads(reply.read() or b"{}")
    return str(created.get("id") or created.get("caseNumber")), "live"


def process(payload):
    description = (payload.get("description") or "").strip()
    if len(description) < 10:
        return {"agent": "case", "status": "failed", "errors": ["a problem description is required"]}

    flags = []
    if not payload.get("account_name"):
        flags.append("account not identified - assign before customer contact")
    category, priority, sentiment = classify(description, payload.get("urgent"))
    if priority == "P1":
        flags.append("P1 case - notify duty manager")

    # Anchor the SLA to the reference date at 09:00 UTC so dry runs are reproducible.
    if payload.get("reference_date"):
        opened = datetime.combine(date.fromisoformat(payload["reference_date"]), time(9, 0), tzinfo=timezone.utc)
    else:
        opened = datetime.now(timezone.utc).replace(microsecond=0)
    record = {
        "subject": subject_from(description),
        "description": description,
        "account_name": payload.get("account_name"),
        "contact": {"name": payload.get("contact_name"), "email": payload.get("email")},
        "channel": payload.get("channel"),
        "category": category,
        "priority": priority,
        "sentiment": sentiment,
        "queue": QUEUES[category],
        "opened_at": opened.isoformat(),
        "response_due_at": (opened + timedelta(hours=SLA_HOURS[priority])).isoformat(),
        "external_reference": payload.get("correlation_id"),
    }
    record_id, mode = write_record(record, f"{payload.get('correlation_id')}|{record['subject']}")
    logger.info("Case %s created in %s mode, %s %s", record_id, mode, priority, category)
    return {
        "agent": "case",
        "status": "created",
        "mode": mode,
        "record_id": record_id,
        "priority": priority,
        "category": category,
        "queue": record["queue"],
        "response_due_at": record["response_due_at"],
        "review_flags": flags,
        "record": record,
    }


def handler(ctx, data: io.BytesIO = None):
    from fdk import response

    try:
        payload = json.loads(data.getvalue() or b"{}") if data else {}
    except ValueError:
        payload = {}
    result = process(payload)
    return response.Response(ctx, response_data=json.dumps(result), headers={"Content-Type": "application/json"},
                             status_code=422 if result["status"] == "failed" else 200)
