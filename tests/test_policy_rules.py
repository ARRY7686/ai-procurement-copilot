from __future__ import annotations

import unittest
from unittest import mock

import requests
from fastapi.testclient import TestClient

from mock_api.app import app
from src import tools
from src.agents import ToolBox, guard
from src.data_access import get_request

CLIENT = TestClient(app)


def fake_vendor_api(vendor_name: str, timeout_seconds: float = 3.0) -> dict:
    r = CLIENT.get(f"/vendor-risk/{vendor_name}")
    if r.status_code != 200:
        resp = requests.Response()
        resp.status_code, resp._content = r.status_code, r.content
        raise requests.HTTPError(response=resp)
    return r.json()


@mock.patch("src.tools.fetch_vendor_risk", fake_vendor_api)
class PolicyRuleTests(unittest.TestCase):
    def decide(self, request: dict):
        box = ToolBox(request)
        decision, action, _ = guard(request, box, None)
        return decision, action

    def test_threshold_boundaries(self):
        self.assertEqual(tools.threshold_approvals(1000), ["Manager"])
        self.assertEqual(tools.threshold_approvals(1000.01), ["Department Head", "Procurement"])
        self.assertEqual(tools.threshold_approvals(10000), ["Department Head", "Procurement"])
        self.assertIn("Finance", tools.threshold_approvals(25000))
        self.assertIn("CFO", tools.threshold_approvals(25000.01))

    def test_reference_date_comes_from_policy(self):
        self.assertEqual(str(tools.reference_date()), "2026-09-30")

    def test_expired_and_conflicting_vendor(self):
        d, action = self.decide(get_request("REQ-1007"))
        self.assertEqual(action, "REVIEW")
        for flag in ["vendor_review_expired", "conflicting_vendor_evidence", "security_review_required"]:
            self.assertIn(flag, d.risk_flags)

    def test_vendor_api_outage_is_not_favourable(self):
        d, action = self.decide(get_request("REQ-1009"))
        self.assertIn("vendor_risk_unavailable", d.risk_flags)
        self.assertIn("Security", d.required_approvals)
        self.assertNotEqual(action, "PROCEED")

    def test_injection_and_missing_info(self):
        d, action = self.decide(get_request("REQ-1006"))
        self.assertEqual(action, "CLARIFY")
        self.assertIn("prompt_injection_detected", d.risk_flags)
        self.assertNotIn("CFO", d.required_approvals)
        self.assertTrue(d.human_review_required)

    def test_guard_never_lets_agent_downgrade(self):
        req = get_request("REQ-1005")
        llm_out = {"action": "PROCEED", "required_approvals": ["Manager"], "risk_flags": [], "missing_information": [],
                   "recommendation": "Approve.", "next_step": "Buy it."}
        d, action, corrections = guard(req, ToolBox(req), llm_out)
        self.assertEqual(action, "BUDGET_EXCEPTION")
        self.assertTrue({"Finance", "Security", "Privacy", "Legal"} <= set(d.required_approvals))
        self.assertTrue(corrections)

    def test_low_value_request_stays_simple(self):
        d, action = self.decide(get_request("REQ-1001"))
        self.assertEqual((action, d.required_approvals, d.risk_flags), ("PROCEED", ["Manager"], []))


if __name__ == "__main__":
    unittest.main()
