"""
Quote Generation Agent - OCI Functions
======================================
Builds a priced quote for an opportunity: resolves requested products against
the price catalog, applies volume discounts, enforces the discount-approval
policy, and calculates tax, totals, and quote validity.

CRM_DRY_RUN=true (default) returns the prepared quote without calling the CRM.
"""

import base64
import hashlib
import io
import json
import logging
import os
import urllib.request
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

logger = logging.getLogger("quote-generation-agent")

CRM_DRY_RUN = os.getenv("CRM_DRY_RUN", "true").lower() != "false"
CRM_BASE_URL = os.getenv("CRM_BASE_URL", "https://crm.example.invalid")
CRM_QUOTE_PATH = os.getenv("CRM_QUOTE_PATH", "/api/v1/quotes")
CRM_SECRET_OCID = os.getenv("CRM_SECRET_OCID", "")
CRM_TIMEOUT_SECONDS = int(os.getenv("CRM_TIMEOUT_SECONDS", "20"))
CURRENCY = os.getenv("CRM_CURRENCY", "USD")
TAX_RATE = Decimal(os.getenv("QUOTE_TAX_RATE", "0.08"))
MAX_AUTO_DISCOUNT_PCT = Decimal(os.getenv("MAX_AUTO_DISCOUNT_PCT", "15"))
QUOTE_VALIDITY_DAYS = int(os.getenv("QUOTE_VALIDITY_DAYS", "30"))

# Synthetic list prices; production deployments load the catalog from CATALOG_JSON.
CATALOG = json.loads(os.getenv("CATALOG_JSON", "null") or "null") or {
    "ANL-PRO": {"name": "Analytics Pro", "unit": "license / year", "unit_price": "1200.00"},
    "SVC-SEAT": {"name": "Service Cloud Seat", "unit": "seat / year", "unit_price": "850.00"},
    "INT-HUB": {"name": "Integration Hub", "unit": "instance / year", "unit_price": "15000.00"},
    "ONB-PKG": {"name": "Onboarding Package", "unit": "package", "unit_price": "5000.00"},
}
VOLUME_TIERS = [(100, Decimal("10")), (50, Decimal("5"))]  # (min quantity, discount %)
CENT = Decimal("0.01")


def volume_discount(quantity):
    for minimum, pct in VOLUME_TIERS:
        if quantity >= minimum:
            return pct
    return Decimal("0")


def price_lines(lines, requested_pct):
    priced, unresolved = [], []
    for number, line in enumerate(lines, start=1):
        item = CATALOG.get(line.get("product_code"))
        quantity = int(line.get("quantity") or 0)
        if not item or quantity <= 0:
            unresolved.append(line)
            continue
        list_amount = Decimal(item["unit_price"]) * quantity
        discount_pct = max(volume_discount(quantity), requested_pct)
        net = (list_amount * (1 - discount_pct / 100)).quantize(CENT, ROUND_HALF_UP)
        priced.append({
            "line": number,
            "product_code": line["product_code"],
            "description": item["name"],
            "unit": item["unit"],
            "quantity": quantity,
            "unit_price": item["unit_price"],
            "discount_pct": str(discount_pct),
            "net_amount": str(net),
        })
    return priced, unresolved


def _crm_token():
    import oci

    signer = oci.auth.signers.get_resource_principals_signer()
    bundle = oci.secrets.SecretsClient(config={}, signer=signer).get_secret_bundle(CRM_SECRET_OCID).data
    return base64.b64decode(bundle.secret_bundle_content.content).decode()


def write_record(body, seed):
    if CRM_DRY_RUN:
        return "QUOTE-" + hashlib.sha1(seed.encode()).hexdigest()[:8].upper(), "dry-run"
    request = urllib.request.Request(
        CRM_BASE_URL.rstrip("/") + CRM_QUOTE_PATH,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_crm_token()}"},
    )
    with urllib.request.urlopen(request, timeout=CRM_TIMEOUT_SECONDS) as reply:
        created = json.loads(reply.read() or b"{}")
    return str(created.get("id") or created.get("quoteId")), "live"


def process(payload):
    if not payload.get("opportunity_id"):
        return {"agent": "quote", "status": "failed", "errors": ["opportunity_id is required"]}
    if not payload.get("lines"):
        return {"agent": "quote", "status": "failed", "errors": ["no quotable products were identified"]}

    flags = []
    requested_pct = Decimal(str(payload.get("requested_discount_pct") or 0))
    lines, unresolved = price_lines(payload["lines"], requested_pct)
    if not lines:
        return {"agent": "quote", "status": "failed", "errors": ["none of the requested products are in the catalog"]}
    if unresolved:
        flags.append(f"{len(unresolved)} product line(s) could not be priced")

    approval_required = requested_pct > MAX_AUTO_DISCOUNT_PCT
    if approval_required:
        flags.append(f"requested discount {requested_pct}% exceeds {MAX_AUTO_DISCOUNT_PCT}% auto-approval limit")

    subtotal = sum(Decimal(line["net_amount"]) for line in lines)
    list_total = sum(Decimal(line["unit_price"]) * line["quantity"] for line in lines)
    tax = (subtotal * TAX_RATE).quantize(CENT, ROUND_HALF_UP)
    today = date.fromisoformat(payload.get("reference_date") or date.today().isoformat())
    record = {
        "opportunity_id": payload["opportunity_id"],
        "account_name": payload.get("account_name"),
        "contact_name": payload.get("contact_name"),
        "currency": CURRENCY,
        "lines": lines,
        "list_total": str(list_total.quantize(CENT)),
        "discount_total": str((list_total - subtotal).quantize(CENT)),
        "subtotal": str(subtotal.quantize(CENT)),
        "tax_rate": str(TAX_RATE),
        "tax": str(tax),
        "grand_total": str((subtotal + tax).quantize(CENT)),
        "valid_until": (today + timedelta(days=QUOTE_VALIDITY_DAYS)).isoformat(),
        "status": "Pending Approval" if approval_required else "Ready to Send",
        "external_reference": payload.get("correlation_id"),
    }
    record_id, mode = write_record(record, f"{payload.get('correlation_id')}|{payload['opportunity_id']}")
    logger.info("Quote %s created in %s mode, total %s", record_id, mode, record["grand_total"])
    return {
        "agent": "quote",
        "status": "created",
        "mode": mode,
        "record_id": record_id,
        "quote_status": record["status"],
        "grand_total": record["grand_total"],
        "approval_required": approval_required,
        "unresolved_lines": unresolved,
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
