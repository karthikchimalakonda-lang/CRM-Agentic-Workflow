"""Unit tests for the CRM agentic workflow (local dry-run, standard library only).

    python -m unittest discover -s tests -v
"""

import importlib.util
import os
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "sanitized-source"
os.environ["CRM_DRY_RUN"] = "true"
os.environ.pop("GENAI_MODEL_ID", None)


def load(folder):
    spec = importlib.util.spec_from_file_location(folder.replace("-", "_"), SOURCE / folder / "func.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


supervisor = load("crm-supervisor-agent")
lead = load("lead-creation-agent")
quote = load("quote-generation-agent")
case = load("case-creation-agent")

REF = "2026-10-05"


class SupervisorTests(unittest.TestCase):
    def run_request(self, text):
        return supervisor.process({"request_text": text, "correlation_id": "T-1", "reference_date": REF})

    def test_lead_opportunity_quote_chain(self):
        result = self.run_request(
            "New lead: Priya Raman from Example Retail Ltd (priya.raman@example.com) wants 60 Analytics Pro "
            "licenses, budget $75,000, next quarter. Send a quote with 8% discount.")
        self.assertEqual([s["step"] for s in result["plan"]], ["lead", "opportunity", "quote"])
        entities = result["understanding"]["entities"]
        self.assertEqual(entities["company"], "Example Retail Ltd")
        self.assertEqual(entities["contact_name"], "Priya Raman")
        self.assertEqual(entities["budget"], 75000)
        self.assertEqual(entities["timeline_days"], 90)
        opportunity = result["results"]["opportunity"]
        self.assertEqual(opportunity["record"]["source_lead_id"], result["results"]["lead"]["record_id"])
        self.assertEqual(result["results"]["quote"]["record"]["opportunity_id"], opportunity["record_id"])

    def test_quote_request_auto_creates_opportunity(self):
        result = self.run_request("Prepare a quote for Example Manufacturing Co: 120 Service Cloud seats.")
        self.assertEqual([s["step"] for s in result["plan"]], ["opportunity", "quote"])
        self.assertEqual(result["results"]["quote"]["status"], "created")

    def test_contact_name_extraction(self):
        self.assertIsNone(supervisor.extract_contact("120 Service Cloud seats for Example Co", None, "Example Co"))
        self.assertEqual(supervisor.extract_contact("Contact Alex Morgan, alex.morgan@example.org.",
                                                    "alex.morgan@example.org", None), "Alex Morgan")

    def test_case_only_request(self):
        result = self.run_request("Customer Sample Health Inc reports the dashboard is down for all users. Open a case.")
        self.assertEqual([s["step"] for s in result["plan"]], ["case"])
        self.assertEqual(result["results"]["case"]["priority"], "P1")

    def test_ambiguous_request_goes_to_human_review(self):
        result = self.run_request("Can you check on the thing we discussed yesterday?")
        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["results"], {})

    def test_empty_request_rejected(self):
        self.assertEqual(supervisor.process({"request_text": "  "})["status"], "rejected")

    def test_product_word_quantities(self):
        products = supervisor.extract_products("1 Integration Hub and an onboarding package")
        self.assertEqual({p["product_code"]: p["quantity"] for p in products}, {"INT-HUB": 1, "ONB-PKG": 1})


class AgentTests(unittest.TestCase):
    def test_lead_scoring_and_qualification(self):
        result = lead.process({"company": "Example Retail Ltd", "email": "a.b@example.com", "budget": 120000,
                               "timeline_days": 60, "source": "referral", "products_of_interest": ["ANL-PRO"]})
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["qualification"], "Sales Qualified")

    def test_lead_rejects_invalid_email(self):
        self.assertEqual(lead.process({"company": "X Ltd", "email": "not-an-email"})["status"], "failed")

    def test_quote_totals_and_volume_discount(self):
        result = quote.process({"opportunity_id": "OPTY-1", "reference_date": REF,
                                "lines": [{"product_code": "ANL-PRO", "quantity": 60}]})
        line = result["record"]["lines"][0]
        self.assertEqual(line["discount_pct"], "5")          # 50+ volume tier
        self.assertEqual(line["net_amount"], "68400.00")     # 60 x 1200 x 0.95
        self.assertEqual(result["grand_total"], "73872.00")  # + 8% tax
        self.assertFalse(result["approval_required"])

    def test_quote_discount_above_limit_requires_approval(self):
        result = quote.process({"opportunity_id": "OPTY-1", "requested_discount_pct": 22,
                                "lines": [{"product_code": "INT-HUB", "quantity": 1}]})
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["quote_status"], "Pending Approval")

    def test_quote_requires_opportunity(self):
        self.assertEqual(quote.process({"lines": [{"product_code": "ANL-PRO", "quantity": 1}]})["status"], "failed")

    def test_case_billing_priority_and_sla(self):
        result = case.process({"description": "Their last billing invoice is wrong.", "account_name": "X Ltd",
                               "reference_date": REF})
        self.assertEqual((result["category"], result["priority"]), ("Billing", "P3"))
        self.assertEqual(result["response_due_at"], "2026-10-06T09:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
