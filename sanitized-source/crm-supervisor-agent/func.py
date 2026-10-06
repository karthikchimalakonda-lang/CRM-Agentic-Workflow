"""
CRM Supervisor Agent - OCI Functions
====================================
Accepts a free-text sales or service request, converts it into a structured
action plan, and coordinates the specialist CRM agents:

    lead-creation-agent  ->  opportunity-creation-agent  ->  quote-generation-agent
    case-creation-agent  (independent branch)

Planning uses OCI Generative AI when GENAI_MODEL_ID is configured and falls
back to a deterministic rule-based parser otherwise. Each specialist agent is
invoked through OCI Functions when its function OCID is configured, or loaded
in-process for local dry-run demonstrations.
"""

import importlib.util
import io
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger("crm-supervisor-agent")
logging.basicConfig(level=logging.INFO)

GENAI_MODEL_ID = os.getenv("GENAI_MODEL_ID", "")
GENAI_COMPARTMENT_ID = os.getenv("GENAI_COMPARTMENT_ID", "")
GENAI_ENDPOINT = os.getenv("GENAI_ENDPOINT", "")
FUNCTIONS_INVOKE_ENDPOINT = os.getenv("FUNCTIONS_INVOKE_ENDPOINT", "")
MIN_PLAN_CONFIDENCE = float(os.getenv("MIN_PLAN_CONFIDENCE", "0.6"))
MAX_REQUEST_CHARS = int(os.getenv("MAX_REQUEST_CHARS", "4000"))

AGENTS = {
    "lead": {"folder": "lead-creation-agent", "env": "LEAD_AGENT_FUNCTION_ID"},
    "opportunity": {"folder": "opportunity-creation-agent", "env": "OPPORTUNITY_AGENT_FUNCTION_ID"},
    "quote": {"folder": "quote-generation-agent", "env": "QUOTE_AGENT_FUNCTION_ID"},
    "case": {"folder": "case-creation-agent", "env": "CASE_AGENT_FUNCTION_ID"},
}

# Vocabulary used only to recognise product mentions; pricing is owned by the quote agent.
PRODUCT_LEXICON = json.loads(os.getenv("PRODUCT_LEXICON_JSON", "null") or "null") or {
    "ANL-PRO": ["analytics pro", "analytics"],
    "SVC-SEAT": ["service cloud seats", "service cloud seat", "service cloud", "service seats"],
    "INT-HUB": ["integration hub"],
    "ONB-PKG": ["onboarding package", "onboarding", "implementation package"],
}

INTENT_PATTERNS = {
    "lead": r"\blead\b|\bprospect\b",
    "opportunity": r"\bopportunit(?:y|ies)\b|\bdeal\b",
    "quote": r"\bquote\b|\bquotation\b|\bpricing\b|\bproposal\b",
    "case": r"\bcase\b|\bticket\b|\bcomplain|\boutage\b|\bis down\b|\bnot working\b|\bis wrong\b|\berror\b",
}
PROBLEM_PATTERN = r"down|not working|wrong|error|fail|outage|cannot|can't|broken|complain|incorrect"
COMPANY_SUFFIX = r"(?:Ltd|Inc|LLC|Co|Corp|Group|GmbH|PLC)"
COMPANY_STOPWORDS = {"Customer", "Account", "Client", "New", "Lead", "Prepare", "Create", "Please"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10}


# --------------------------------------------------------------------------- #
# Natural-language understanding
# --------------------------------------------------------------------------- #
def detect_intents(text):
    return [name for name, pattern in INTENT_PATTERNS.items() if re.search(pattern, text, re.I)]


def extract_company(text):
    match = re.search(rf"\b([A-Z][\w&]*(?:\s+[A-Z][\w&]*)*\s+{COMPANY_SUFFIX})\b", text)
    if not match:
        match = re.search(r"\b(?:customer|account|for|at|from)\s+([A-Z][\w&]+(?:\s+[A-Z][\w&]+){0,3})", text)
    if not match:
        return None
    words = match.group(1).split()
    while words and words[0] in COMPANY_STOPWORDS:
        words.pop(0)
    return " ".join(words) or None


def extract_contact(text, email, company):
    excluded = set((company or "").split()) | COMPANY_STOPWORDS | {"Contact"}
    product_words = {word for names in PRODUCT_LEXICON.values() for alias in names for word in alias.split()}
    candidates = [
        f"{first} {last}"
        # Lookahead keeps matches overlapping, so "Contact Alex Morgan" still yields "Alex Morgan".
        for first, last in re.findall(r"(?=\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b)", text)
        if not {first, last} & excluded and not {first.lower(), last.lower()} & product_words
    ]
    if email:
        local = re.sub(r"[._-]+", " ", email.split("@")[0]).lower()
        for name in candidates:
            if name.lower() == local:
                return name
        if not candidates and " " in local:
            return local.title()
    return candidates[0] if candidates else None


def extract_products(text):
    found, taken = [], []
    aliases = sorted(
        ((alias, code) for code, names in PRODUCT_LEXICON.items() for alias in names),
        key=lambda item: -len(item[0]),
    )
    for alias, code in aliases:
        pattern = rf"(?:\b(\d+|an?|one|two|three|five|ten)\s+)?{re.escape(alias)}\b"
        for match in re.finditer(pattern, text, re.I):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            if any(item["product_code"] == code for item in found):
                continue
            raw_qty = (match.group(1) or "1").lower()
            quantity = int(raw_qty) if raw_qty.isdigit() else WORD_NUMBERS.get(raw_qty, 1)
            found.append({"product_code": code, "mention": match.group(0).strip(), "quantity": quantity})
            taken.append(span)
    return found


def extract_amount(text):
    match = re.search(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?\b", text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    multiplier = {"k": 1_000, "m": 1_000_000}.get((match.group(2) or "").lower(), 1)
    return round(value * multiplier, 2)


def extract_timeline_days(text, today):
    lowered = text.lower()
    explicit = re.search(r"\b(?:in|within)\s+(\d+)\s+(day|week|month)s?\b", lowered)
    if explicit:
        return int(explicit.group(1)) * {"day": 1, "week": 7, "month": 30}[explicit.group(2)]
    by_date = re.search(r"\bby\s+(\d{4}-\d{2}-\d{2})\b", lowered)
    if by_date:
        return max((date.fromisoformat(by_date.group(1)) - today).days, 0)
    for phrase, days in (("this month", 21), ("next month", 30), ("this quarter", 45), ("next quarter", 90),
                         ("this year", 180), ("next year", 365)):
        if phrase in lowered:
            return days
    return None


def extract_source(text, channel):
    lowered = text.lower()
    for keyword, source in (("referral", "referral"), ("referred", "referral"), ("partner", "partner"),
                            ("event", "event"), ("webinar", "event"), ("website", "web"), ("web form", "web")):
        if keyword in lowered:
            return source
    return channel or "unknown"


def extract_issue(text):
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    problems = [s for s in sentences if re.search(PROBLEM_PATTERN, s, re.I)]
    return " ".join(problems) if problems else text.strip()


def rule_based_plan(text, channel, today):
    email_match = EMAIL_RE.search(text)
    phone_match = PHONE_RE.search(EMAIL_RE.sub(" ", text))
    email = email_match.group(0) if email_match else None
    company = extract_company(text)
    discount = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:discount|off)", text, re.I)
    entities = {
        "company": company,
        "contact_name": extract_contact(text, email, company),
        "email": email,
        "phone": phone_match.group(0).strip() if phone_match else None,
        "budget": extract_amount(text),
        "products": extract_products(text),
        "discount_pct": float(discount.group(1)) if discount else None,
        "timeline_days": extract_timeline_days(text, today),
        "source": extract_source(text, channel),
        "urgent": bool(re.search(r"\burgent\b|\basap\b|\bcritical\b|\bimmediately\b", text, re.I)),
        "issue_description": None,
    }
    intents = detect_intents(text)
    if "case" in intents:
        entities["issue_description"] = extract_issue(text)
    signals = [entities[k] for k in ("company", "contact_name", "email", "budget", "timeline_days",
                                     "issue_description")]
    confidence = 0.0 if not intents else min(0.5 + 0.08 * sum(1 for s in signals if s) +
                                             (0.05 if entities["products"] else 0), 0.95)
    return {"planner": "rules", "intents": intents, "entities": entities, "confidence": round(confidence, 2)}


def genai_plan(text, channel, today):
    """Ask OCI Generative AI for the same plan schema the rule parser produces."""
    import oci
    from oci.generative_ai_inference import GenerativeAiInferenceClient
    from oci.generative_ai_inference import models as genai_models

    instruction = (
        "You are a CRM request planner. Return only JSON with keys: intents (subset of "
        '["lead","opportunity","quote","case"]), entities {company, contact_name, email, phone, budget, '
        "products [{product_code, mention, quantity}], discount_pct, timeline_days, source, urgent, "
        f"issue_description}}, confidence (0-1). Known product codes: {json.dumps(PRODUCT_LEXICON)}. "
        f"Today is {today.isoformat()}. Request channel: {channel}. Use null for unknown values.\n\nRequest:\n{text}"
    )
    signer = oci.auth.signers.get_resource_principals_signer()
    client = GenerativeAiInferenceClient(config={}, signer=signer, service_endpoint=GENAI_ENDPOINT)
    details = genai_models.ChatDetails(
        compartment_id=GENAI_COMPARTMENT_ID,
        serving_mode=genai_models.OnDemandServingMode(model_id=GENAI_MODEL_ID),
        chat_request=genai_models.GenericChatRequest(
            api_format=genai_models.BaseChatRequest.API_FORMAT_GENERIC,
            messages=[genai_models.UserMessage(content=[genai_models.TextContent(text=instruction)])],
            max_tokens=800,
            temperature=0,
        ),
    )
    reply = client.chat(details).data.chat_response.choices[0].message.content[0].text
    plan = json.loads(reply[reply.find("{"): reply.rfind("}") + 1])
    plan["intents"] = [i for i in plan.get("intents", []) if i in AGENTS]
    plan["planner"] = "oci-generative-ai"
    return plan


def understand(text, channel, today):
    if GENAI_MODEL_ID and GENAI_COMPARTMENT_ID:
        try:
            return genai_plan(text, channel, today)
        except Exception as exc:  # fall back rather than fail the request
            logger.warning("Generative AI planning failed, using rule-based planner: %s", exc)
    return rule_based_plan(text, channel, today)


# --------------------------------------------------------------------------- #
# Planning and dispatch
# --------------------------------------------------------------------------- #
def build_steps(understanding, context):
    intents, ent = set(understanding["intents"]), understanding["entities"]
    steps = []
    if "lead" in intents:
        steps.append({"step": "lead", "depends_on": None, "payload": {
            "company": ent.get("company"), "contact_name": ent.get("contact_name"), "email": ent.get("email"),
            "phone": ent.get("phone"), "budget": ent.get("budget"), "timeline_days": ent.get("timeline_days"),
            "source": ent.get("source"), "products_of_interest": [p["product_code"] for p in ent.get("products") or []],
        }})
    if intents & {"opportunity", "quote"}:
        steps.append({"step": "opportunity", "depends_on": "lead" if "lead" in intents else None, "payload": {
            "account_name": ent.get("company"), "contact_name": ent.get("contact_name"),
            "amount": ent.get("budget"), "timeline_days": ent.get("timeline_days"),
            "products": ent.get("products") or [], "quote_requested": "quote" in intents,
            "lead_source": ent.get("source"),
        }})
    if "quote" in intents:
        steps.append({"step": "quote", "depends_on": "opportunity", "payload": {
            "account_name": ent.get("company"), "contact_name": ent.get("contact_name"),
            "lines": ent.get("products") or [], "requested_discount_pct": ent.get("discount_pct"),
        }})
    if "case" in intents:
        steps.append({"step": "case", "depends_on": None, "payload": {
            "account_name": ent.get("company"), "contact_name": ent.get("contact_name"),
            "email": ent.get("email"), "description": ent.get("issue_description"),
            "urgent": ent.get("urgent"), "channel": context["channel"],
        }})
    for step in steps:
        step["payload"].update({"correlation_id": context["correlation_id"],
                                "reference_date": context["reference_date"], "requested_by": context["requested_by"]})
    return steps


def _load_local_agent(folder):
    path = Path(__file__).resolve().parent.parent / folder / "func.py"
    spec = importlib.util.spec_from_file_location(folder.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke_agent(name, payload):
    agent = AGENTS[name]
    function_id = os.getenv(agent["env"], "")
    if function_id:
        import oci
        from oci.functions import FunctionsInvokeClient

        signer = oci.auth.signers.get_resource_principals_signer()
        client = FunctionsInvokeClient(config={}, signer=signer, service_endpoint=FUNCTIONS_INVOKE_ENDPOINT)
        result = client.invoke_function(function_id, invoke_function_body=json.dumps(payload))
        return json.loads(result.data.text)
    return _load_local_agent(agent["folder"]).process(payload)


def link_dependency(step, results):
    upstream = results.get(step["depends_on"]) if step["depends_on"] else None
    if step["depends_on"] and (not upstream or upstream.get("status") == "failed"):
        return False
    if step["step"] == "opportunity" and upstream:
        step["payload"]["lead_id"] = upstream.get("record_id")
        step["payload"]["lead_score"] = upstream.get("score")
    if step["step"] == "quote" and upstream:
        step["payload"]["opportunity_id"] = upstream.get("record_id")
    return True


def process(payload):
    started = datetime.now(timezone.utc)
    text = (payload.get("request_text") or "").strip()
    context = {
        "correlation_id": payload.get("correlation_id") or f"CRM-{uuid.uuid4().hex[:10].upper()}",
        "channel": payload.get("channel", "chat"),
        "requested_by": payload.get("requested_by", "sales-assistant"),
        "reference_date": payload.get("reference_date") or date.today().isoformat(),
    }
    if not text:
        return {"status": "rejected", "reason": "request_text is required", **context}
    if len(text) > MAX_REQUEST_CHARS:
        return {"status": "rejected", "reason": f"request_text exceeds {MAX_REQUEST_CHARS} characters", **context}

    understanding = understand(text, context["channel"], date.fromisoformat(context["reference_date"]))
    steps = build_steps(understanding, context)
    audit = [{"event": "planned", "planner": understanding["planner"], "intents": understanding["intents"],
              "confidence": understanding["confidence"]}]

    if not steps or understanding["confidence"] < MIN_PLAN_CONFIDENCE:
        return {"status": "needs_human_review", "reason": "No confident CRM action could be planned",
                **context, "understanding": understanding, "results": {}, "audit": audit}

    results = {}
    for step in steps:
        if not link_dependency(step, results):
            results[step["step"]] = {"status": "skipped", "reason": f"dependency '{step['depends_on']}' unavailable"}
            audit.append({"event": "skipped", "agent": step["step"]})
            continue
        try:
            results[step["step"]] = invoke_agent(step["step"], step["payload"])
        except Exception as exc:
            logger.exception("Agent %s failed", step["step"])
            results[step["step"]] = {"status": "failed", "error": str(exc)}
        audit.append({"event": "dispatched", "agent": step["step"], "status": results[step["step"]].get("status"),
                      "record_id": results[step["step"]].get("record_id")})

    review_items = [f"{name}: {flag}" for name, res in results.items() for flag in res.get("review_flags", [])]
    failed = [name for name, res in results.items() if res.get("status") in ("failed", "skipped")]
    status = "partial" if failed else ("completed_with_review" if review_items else "completed")
    return {
        "status": status,
        **context,
        "understanding": understanding,
        "plan": [{"step": s["step"], "depends_on": s["depends_on"]} for s in steps],
        "results": results,
        "review_items": review_items,
        "audit": audit,
        "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
    }


def handler(ctx, data: io.BytesIO = None):
    from fdk import response

    try:
        payload = json.loads(data.getvalue() or b"{}") if data else {}
    except ValueError:
        payload = {}
    result = process(payload)
    return response.Response(
        ctx,
        response_data=json.dumps(result),
        headers={"Content-Type": "application/json"},
        status_code=400 if result["status"] == "rejected" else 200,
    )
