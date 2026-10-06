"""
Opportunity Creation Agent - OCI Functions
==========================================
Creates a sales opportunity from the supervisor plan, linking it to the lead
created upstream when one exists. Derives the sales stage, win probability,
expected close date, and weighted revenue.

CRM_DRY_RUN=true (default) returns the prepared record without calling the CRM.
"""

import base64
import hashlib
import io
import json
import logging
import os
import urllib.request
from datetime import date, timedelta

logger = logging.getLogger("opportunity-creation-agent")

CRM_DRY_RUN = os.getenv("CRM_DRY_RUN", "true").lower() != "false"
CRM_BASE_URL = os.getenv("CRM_BASE_URL", "https://crm.example.invalid")
CRM_OPPORTUNITY_PATH = os.getenv("CRM_OPPORTUNITY_PATH", "/api/v1/opportunities")
CRM_SECRET_OCID = os.getenv("CRM_SECRET_OCID", "")
CRM_TIMEOUT_SECONDS = int(os.getenv("CRM_TIMEOUT_SECONDS", "20"))
DEFAULT_CYCLE_DAYS = int(os.getenv("DEFAULT_SALES_CYCLE_DAYS", "120"))
CURRENCY = os.getenv("CRM_CURRENCY", "USD")

STAGE_PROBABILITY = {"Prospecting": 10, "Qualification": 25, "Needs Analysis": 40, "Proposal": 60}


def derive_stage(payload):
    if payload.get("quote_requested") and payload.get("products"):
        return "Proposal"
    if payload.get("amount") and payload.get("timeline_days") is not None:
        return "Needs Analysis"
    if payload.get("lead_id") or payload.get("amount"):
        return "Qualification"
    return "Prospecting"


def adjusted_probability(stage, payload):
    probability = STAGE_PROBABILITY[stage]
    if (payload.get("lead_score") or 0) >= 75:
        probability += 10
    if payload.get("lead_source") in ("referral", "partner"):
        probability += 5
    return min(probability, 90)


def _crm_token():
    import oci

    signer = oci.auth.signers.get_resource_principals_signer()
    bundle = oci.secrets.SecretsClient(config={}, signer=signer).get_secret_bundle(CRM_SECRET_OCID).data
    return base64.b64decode(bundle.secret_bundle_content.content).decode()


def write_record(body, seed):
    if CRM_DRY_RUN:
        return "OPTY-" + hashlib.sha1(seed.encode()).hexdigest()[:8].upper(), "dry-run"
    request = urllib.request.Request(
        CRM_BASE_URL.rstrip("/") + CRM_OPPORTUNITY_PATH,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_crm_token()}"},
    )
    with urllib.request.urlopen(request, timeout=CRM_TIMEOUT_SECONDS) as reply:
        created = json.loads(reply.read() or b"{}")
    return str(created.get("id") or created.get("opportunityId")), "live"


def process(payload):
    if not payload.get("account_name") and not payload.get("lead_id"):
        return {"agent": "opportunity", "status": "failed", "errors": ["account_name or lead_id is required"]}

    flags = []
    today = date.fromisoformat(payload.get("reference_date") or date.today().isoformat())
    days = payload.get("timeline_days")
    if days is None:
        days = DEFAULT_CYCLE_DAYS
        flags.append(f"close date defaulted to {DEFAULT_CYCLE_DAYS}-day sales cycle")
    amount = payload.get("amount")
    if not amount and not payload.get("quote_requested"):
        flags.append("amount not captured - confirm with account owner")

    stage = derive_stage(payload)
    probability = adjusted_probability(stage, payload)
    product_codes = [p["product_code"] for p in payload.get("products") or []]
    record = {
        "name": f"{payload.get('account_name') or 'New account'} - {', '.join(product_codes) or 'solution'}",
        "account_name": payload.get("account_name"),
        "primary_contact": payload.get("contact_name"),
        "source_lead_id": payload.get("lead_id"),
        "stage": stage,
        "win_probability_pct": probability,
        "amount": amount,
        "currency": CURRENCY,
        "amount_source": "request" if amount else ("quote" if payload.get("quote_requested") else "pending"),
        "weighted_amount": round(amount * probability / 100, 2) if amount else None,
        "expected_close_date": (today + timedelta(days=days)).isoformat(),
        "product_codes": product_codes,
        "external_reference": payload.get("correlation_id"),
    }
    record_id, mode = write_record(record, f"{payload.get('correlation_id')}|{record['name']}")
    logger.info("Opportunity %s created in %s mode at stage %s", record_id, mode, stage)
    return {
        "agent": "opportunity",
        "status": "created",
        "mode": mode,
        "record_id": record_id,
        "stage": stage,
        "win_probability_pct": probability,
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
