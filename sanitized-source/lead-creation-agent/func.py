"""
Lead Creation Agent - OCI Functions
===================================
Validates the lead details planned by the CRM supervisor, scores the lead,
derives a de-duplication key, and creates the lead record in the CRM.

CRM_DRY_RUN=true (default) returns the prepared record without calling the CRM.
"""

import base64
import hashlib
import io
import json
import logging
import os
import re
import urllib.request

logger = logging.getLogger("lead-creation-agent")

CRM_DRY_RUN = os.getenv("CRM_DRY_RUN", "true").lower() != "false"
CRM_BASE_URL = os.getenv("CRM_BASE_URL", "https://crm.example.invalid")
CRM_LEAD_PATH = os.getenv("CRM_LEAD_PATH", "/api/v1/leads")
CRM_SECRET_OCID = os.getenv("CRM_SECRET_OCID", "")
CRM_TIMEOUT_SECONDS = int(os.getenv("CRM_TIMEOUT_SECONDS", "20"))
QUALIFY_THRESHOLD = int(os.getenv("LEAD_QUALIFY_THRESHOLD", "60"))

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
SOURCE_POINTS = {"referral": 20, "partner": 15, "event": 15, "web": 10, "email": 8, "chat": 8}


def score_lead(lead):
    score, reasons = 0, []
    budget = lead.get("budget") or 0
    if budget >= 100_000:
        score, reasons = score + 30, reasons + ["budget >= 100k"]
    elif budget >= 25_000:
        score, reasons = score + 20, reasons + ["budget >= 25k"]
    elif budget > 0:
        score, reasons = score + 10, reasons + ["budget stated"]

    days = lead.get("timeline_days")
    if days is not None:
        points = 25 if days <= 90 else 15 if days <= 180 else 5
        score, reasons = score + points, reasons + [f"timeline {days} days"]

    if lead.get("email") and EMAIL_RE.match(lead["email"]):
        score, reasons = score + 15, reasons + ["valid email"]
    if lead.get("company"):
        score, reasons = score + 10, reasons + ["company identified"]
    if lead.get("products_of_interest"):
        score, reasons = score + 10, reasons + ["product interest"]
    source_points = SOURCE_POINTS.get(lead.get("source") or "", 0)
    if source_points:
        score, reasons = score + source_points, reasons + [f"source {lead['source']}"]
    return min(score, 100), reasons


def dedupe_key(lead):
    basis = (lead.get("email") or f"{lead.get('contact_name')}|{lead.get('company')}").strip().lower()
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _crm_token():
    import oci

    signer = oci.auth.signers.get_resource_principals_signer()
    bundle = oci.secrets.SecretsClient(config={}, signer=signer).get_secret_bundle(CRM_SECRET_OCID).data
    return base64.b64decode(bundle.secret_bundle_content.content).decode()


def write_record(body, seed):
    if CRM_DRY_RUN:
        return "LEAD-" + hashlib.sha1(seed.encode()).hexdigest()[:8].upper(), "dry-run"
    request = urllib.request.Request(
        CRM_BASE_URL.rstrip("/") + CRM_LEAD_PATH,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_crm_token()}"},
    )
    with urllib.request.urlopen(request, timeout=CRM_TIMEOUT_SECONDS) as reply:
        created = json.loads(reply.read() or b"{}")
    return str(created.get("id") or created.get("leadId")), "live"


def process(payload):
    errors, flags = [], []
    if not payload.get("company") and not payload.get("contact_name"):
        errors.append("company or contact_name is required")
    email = payload.get("email")
    if email and not EMAIL_RE.match(email):
        errors.append(f"invalid email: {email}")
    if errors:
        return {"agent": "lead", "status": "failed", "errors": errors}

    if not email and not payload.get("phone"):
        flags.append("no contact channel captured (email/phone)")
    score, reasons = score_lead(payload)
    qualification = "Sales Qualified" if score >= QUALIFY_THRESHOLD else "Nurture"
    key = dedupe_key(payload)
    record = {
        "name": f"{payload.get('company') or payload.get('contact_name')} - inbound interest",
        "company": payload.get("company"),
        "contact": {"name": payload.get("contact_name"), "email": email, "phone": payload.get("phone")},
        "source": payload.get("source"),
        "estimated_budget": payload.get("budget"),
        "timeline_days": payload.get("timeline_days"),
        "products_of_interest": payload.get("products_of_interest") or [],
        "score": score,
        "qualification": qualification,
        "dedupe_key": key,
        "external_reference": payload.get("correlation_id"),
    }
    record_id, mode = write_record(record, f"{payload.get('correlation_id')}|{key}")
    logger.info("Lead %s created in %s mode (score %s)", record_id, mode, score)
    return {
        "agent": "lead",
        "status": "created",
        "mode": mode,
        "record_id": record_id,
        "score": score,
        "score_reasons": reasons,
        "qualification": qualification,
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
