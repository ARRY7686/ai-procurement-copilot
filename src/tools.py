"""Evidence tools used by both architectures.

Every tool here is deterministic code (no LLM). The agents decide which tools to
call and how to interpret the results; the numbers, dates and policy thresholds
are always computed here.

Tools exposed to the agents:
  lookup_requester_budget   - requester, department, budget check        (CSV data)
  search_software_catalog   - catalog overlap + purchase history          (CSV data)
  get_vendor_risk           - vendor registry + external risk API         (CSV + HTTP)
  check_policy_rules        - required info, thresholds, reviews, injection (policy rules)
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any

import requests as http

from src import data_access
from src.vendor_client import get_vendor_risk as fetch_vendor_risk

APPROVAL_ORDER = ["Manager", "Department Head", "Procurement", "Finance", "CFO", "Security", "Privacy", "Legal"]
SECURITY_REVIEW_VALID_DAYS = 365


def reference_date() -> date:
    """Policy snapshot date, read from the policy document (never the system clock)."""
    m = re.search(r"reference date:\*\*\s*(\d{4}-\d{2}-\d{2})", data_access.load_policy_text())
    return date.fromisoformat(m.group(1)) if m else date(2026, 9, 30)


def _records(df) -> list[dict]:
    rows = df.to_dict(orient="records")
    return [{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()} for r in rows]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "unknown", "n/a", "tbd"})


# ---------------------------------------------------------------- tool 1
def lookup_requester_budget(request: dict) -> dict:
    employees = {e["employee_id"]: e for e in _records(data_access.load_employees())}
    budgets = {b["department"]: b for b in _records(data_access.load_budgets())}
    emp = employees.get(request.get("requester_id"))
    findings: list[dict] = []
    if emp is None:
        findings.append({"finding": f"Requester {request.get('requester_id')!r} not found in employees.csv", "reference": "employees.csv"})
        return {"requester": None, "department": None, "budget_status": "unverifiable", "findings": findings}

    manager = employees.get(emp.get("manager_id") or "")
    dept = emp["department"]
    findings.append({
        "finding": f"Requester {emp['name']} ({emp['employee_id']}, {emp['level']}) is in {dept}; manager: "
                   + (f"{manager['name']} ({manager['employee_id']})" if manager else "none on record"),
        "reference": f"employees.csv:{emp['employee_id']}",
    })
    budget = budgets.get(dept)
    cost = request.get("annual_cost_usd")
    if budget is None:
        status = "unverifiable"
        findings.append({"finding": f"No software budget record for department {dept!r}", "reference": "department_budgets.csv"})
    elif cost is None:
        status = "unverifiable"
        findings.append({"finding": f"{dept} has ${budget['available_usd']:,} available, but the request has no annual cost to compare",
                         "reference": f"department_budgets.csv:{dept}"})
    else:
        status = "within_budget" if cost <= budget["available_usd"] else "insufficient"
        verb = "within" if status == "within_budget" else "EXCEEDS"
        findings.append({"finding": f"Annual cost ${cost:,} is {verb} {dept}'s available software budget of ${budget['available_usd']:,}",
                         "reference": f"department_budgets.csv:{dept}"})
    return {
        "requester": emp, "manager": manager, "department": dept,
        "available_budget_usd": budget["available_usd"] if budget else None,
        "budget_status": status, "findings": findings,
    }


# ---------------------------------------------------------------- tool 2
def search_software_catalog(request: dict, query: str = "") -> dict:
    """Same vendor / product / category matches, plus optional keyword search on the use case."""
    catalog = _records(data_access.load_software_catalog())
    history = _records(data_access.load_purchase_history())
    employees = {e["employee_id"]: e for e in _records(data_access.load_employees())}
    dept = (employees.get(request.get("requester_id")) or {}).get("department")
    vendor = (request.get("vendor_name") or "").strip().lower()
    product = (request.get("product_name") or "").strip().lower()
    category = (request.get("category") or "").strip().lower()
    words = {w for w in re.findall(r"[a-z]{4,}", query.lower())}

    matches = []
    for item in catalog:
        reasons = []
        if vendor and item["vendor_name"].lower() == vendor:
            reasons.append("same_vendor")
        if product and item["product_name"].lower() == product:
            reasons.append("same_product")
        if category and item["category"].lower() == category:
            reasons.append("same_category")
        text = f"{item['product_name']} {item['category']} {item['notes']}".lower()
        if words and any(w in text for w in words):
            reasons.append("keyword_match")
        if reasons:
            in_scope = item["scope"] == "Company-wide" or item["scope"] == dept
            matches.append({**item, "match_reasons": reasons, "available_to_requester_department": in_scope})

    findings = []
    for m in matches:
        findings.append({
            "finding": f"Catalog has {m['product_name']} ({m['vendor_name']}, {m['category']}, status {m['status']}, "
                       f"{m['licensed_seats']} seats, scope {m['scope']}); match: {', '.join(m['match_reasons'])}"
                       + ("; available to requester's department" if m["available_to_requester_department"] else ""),
            "reference": f"software_catalog.csv:{m['software_id']}",
        })
    past = [h for h in history if vendor and h["vendor_name"].lower() == vendor]
    for h in past:
        findings.append({"finding": f"Purchase {h['purchase_id']} on {h['purchase_date']}: {h['product_name']} for {h['department']}, "
                                    f"${h['annual_amount_usd']:,}/yr ({h['notes']})",
                         "reference": f"purchase_history.csv:{h['purchase_id']}"})
    if not matches:
        findings.append({"finding": "No existing catalog product matches this vendor, product or category", "reference": "software_catalog.csv"})

    # Deterministic overlap rule: an in-scope product in the same category from a different vendor
    # is a substitute the requester must address (policy section 3). Same-vendor expansions are left
    # to the agent's judgement.
    substitutes = [m["product_name"] for m in matches
                   if "same_category" in m["match_reasons"] and "same_vendor" not in m["match_reasons"]
                   and m["available_to_requester_department"]]
    duplicates = [m["product_name"] for m in matches if "same_product" in m["match_reasons"]]
    return {"matches": matches, "purchase_history": past, "in_scope_substitutes": substitutes,
            "exact_duplicates": duplicates, "findings": findings}


# ---------------------------------------------------------------- tool 3
def _norm_status(value: str | None) -> str:
    v = (value or "").strip().lower().replace(" ", "_")
    return {"approved": "approved", "pending": "not_completed", "not_completed": "not_completed",
            "expired": "expired", "": "unknown", "unknown": "unknown"}.get(v, v)


def get_vendor_risk(request: dict) -> dict:
    vendor = request.get("vendor_name") or ""
    ref = reference_date()
    registry = next((v for v in _records(data_access.load_vendors()) if v["vendor_name"] == vendor), None)
    findings: list[dict] = []
    issues: list[str] = []

    if registry is None:
        findings.append({"finding": f"Vendor {vendor!r} is not in the internal vendor registry", "reference": "vendors.csv"})
        issues.append("vendor_not_registered")
    else:
        findings.append({"finding": f"Registry: {vendor} procurement={registry['procurement_status']}, security={registry['security_status']}, "
                                    f"review date={registry['security_review_date'] or 'none'}, legal terms={registry['legal_terms_status']}",
                         "reference": f"vendors.csv:{registry['vendor_id']}"})

    api, api_error = None, None
    try:
        api = fetch_vendor_risk(vendor)
    except http.HTTPError as exc:
        api_error = f"HTTP {exc.response.status_code}: {exc.response.text[:160]}"
    except http.RequestException as exc:
        api_error = f"{type(exc).__name__}: service unreachable"
    if api_error:
        findings.append({"finding": f"Vendor-risk API could not be verified ({api_error}); no favourable status assumed",
                         "reference": "GET /vendor-risk/{vendor}"})
        issues.append("vendor_risk_unavailable")
    else:
        findings.append({"finding": f"Vendor-risk API: risk={api.get('risk_level')}, review={api.get('security_review_status')}, "
                                    f"last review={api.get('last_review_date') or 'none'}, personal data={api.get('processes_personal_data')}, "
                                    f"stores outside region={api.get('stores_data_outside_region')}",
                         "reference": f"GET /vendor-risk/{vendor}"})

    # Freshness: a security assessment is current for 365 days from its review date.
    dates = [d for d in [(registry or {}).get("security_review_date"), (api or {}).get("last_review_date")] if d]
    latest = max(date.fromisoformat(d) for d in dates) if dates else None
    statuses = {"registry": _norm_status((registry or {}).get("security_status")) if registry else None,
                "api": _norm_status((api or {}).get("security_review_status")) if api else None}
    if latest is None:
        issues.append("security_assessment_missing")
        findings.append({"finding": "No security review date on record in either source", "reference": "policy section 5"})
    elif ref - latest > timedelta(days=SECURITY_REVIEW_VALID_DAYS):
        issues.append("security_assessment_expired")
        findings.append({"finding": f"Latest security review {latest} is {(ref - latest).days} days before the {ref} reference date "
                                    f"(valid for {SECURITY_REVIEW_VALID_DAYS})", "reference": "policy section 5"})
    if any(s in {"not_completed", "unknown", "expired"} for s in statuses.values() if s):
        issues.append("security_assessment_not_current")
    known = {k: s for k, s in statuses.items() if s and s != "unknown"}
    if len(set(known.values())) > 1:
        issues.append("conflicting_vendor_evidence")
        findings.append({"finding": f"Registry says security={registry['security_status']} but the API says "
                                    f"{api.get('security_review_status')}; conflict surfaced, not resolved",
                         "reference": "vendors.csv vs vendor-risk API"})

    return {"vendor": vendor, "registry": registry, "api": api, "api_error": api_error,
            "reference_date": str(ref), "latest_review_date": str(latest) if latest else None,
            "issues": sorted(set(issues)), "findings": findings}


# ---------------------------------------------------------------- tool 4
INJECTION_PATTERNS = [
    r"ignore (all |any |the |previous |prior |every )*(procurement |security |company )?(rules|instructions|policy|policies|controls)",
    r"(treat|mark|consider|record) (this|the) (request|purchase) as [\w\s-]{0,20}approved",
    r"\b(cfo|ceo|vp|finance|security|legal)[- ]approved\b",
    r"approve (it|this|the request|the purchase)? ?(immediately|now|automatically|without)",
    r"\b(bypass|skip|override)\b.{0,30}\b(policy|controls?|review|approval)",
    r"disregard|system prompt|you are now|new instructions",
    r"(reveal|expose|print|send).{0,30}(api key|secret|password|credential)",
]
CODE_RX = r"source[_ ]?code|\bgit\b|github|gitlab|bitbucket|code repositor"
PROD_RX = r"production|\bprod\b|cloud[ _-]?account|\baws\b|azure|\bgcp\b|kubernetes"
CRED_RX = r"credential|secret|password|api[ _]?key"
PII_RX = r"pii|personal data"
REQUIRED_FIELDS = [
    ("requester_id", "requester and department"),
    ("product_name", "product name"),
    ("vendor_name", "vendor"),
    ("annual_cost_usd", "annual cost (or a reasonable annual estimate)"),
    ("user_count", "number of users/licenses"),
    ("business_justification", "business purpose"),
    ("data_access_level", "intended data-access level"),
]


def scan_for_injection(texts: dict[str, str]) -> list[dict]:
    hits = []
    for field, text in texts.items():
        for pat in INJECTION_PATTERNS:
            m = re.search(pat, text or "", flags=re.I)
            if m:
                hits.append({"field": field, "matched_text": m.group(0)})
                break
    return hits


def threshold_approvals(cost: float | None) -> list[str]:
    if cost is None:
        return []
    if cost <= 1000:
        return ["Manager"]
    if cost <= 10000:
        return ["Department Head", "Procurement"]
    if cost <= 25000:
        return ["Department Head", "Finance", "Procurement"]
    return ["Department Head", "Finance", "CFO", "Procurement"]


def check_policy_rules(request: dict, budget: dict, catalog: dict, vendor: dict) -> dict:
    """Apply procurement_policy.md sections 1-10 deterministically to the gathered evidence."""
    findings, flags, approvals, missing = [], set(), [], []
    cost = request.get("annual_cost_usd")

    # 1. Required information
    for key, label in REQUIRED_FIELDS:
        if _blank(request.get(key)):
            missing.append(label)
    if budget.get("requester") is None and "requester and department" not in missing:
        missing.append("requester and department (requester not found)")
    if request.get("requested_integrations") is None:
        missing.append("required integrations")
    if missing:
        flags.add("missing_information")
        findings.append({"finding": "Missing required information: " + "; ".join(missing), "reference": "policy section 1"})

    # 4. Financial thresholds
    approvals += threshold_approvals(cost)
    if cost is None:
        approvals.append("Procurement")
        findings.append({"finding": "Approval threshold cannot be determined without an annual cost", "reference": "policy section 4"})
    else:
        findings.append({"finding": f"${cost:,} annual amount requires: {', '.join(threshold_approvals(cost))}", "reference": "policy section 4"})

    # 2. Budget
    if budget.get("budget_status") == "insufficient":
        flags.add("budget_insufficient")
        approvals.append("Finance")
    elif budget.get("budget_status") == "unverifiable" and cost is not None:
        flags.add("budget_unverified")
        approvals.append("Finance")

    # 3. Overlap
    if catalog.get("in_scope_substitutes") or catalog.get("exact_duplicates"):
        flags.add("existing_tool_overlap")
        names = sorted(set(catalog["in_scope_substitutes"] + catalog["exact_duplicates"]))
        findings.append({"finding": "Existing approved tools may cover this need: " + ", ".join(names)
                                    + ". Requester must show a credible gap.", "reference": "policy section 3"})

    # 5. Security
    data_level = str(request.get("data_access_level") or "")
    integrations = " ".join(request.get("requested_integrations") or [])
    usage = f"{data_level} {integrations}"
    sec_reasons = []
    for rx, why in [(CODE_RX, "source code access"), (PROD_RX, "production/cloud-account integration"),
                    (r"confidential", "confidential documents"), (PII_RX, "personal data (PII)"), (CRED_RX, "credentials/secrets")]:
        if re.search(rx, usage, flags=re.I):
            sec_reasons.append(why)
    vendor_issues = set(vendor.get("issues", []))
    if vendor_issues & {"security_assessment_missing", "security_assessment_expired", "security_assessment_not_current",
                        "vendor_not_registered"}:
        sec_reasons.append("vendor security assessment missing, expired or not completed")
    if "security_assessment_expired" in vendor_issues or (vendor.get("api") or {}).get("security_review_status") == "expired":
        flags.add("vendor_review_expired")
    if "conflicting_vendor_evidence" in vendor_issues:
        flags.add("conflicting_vendor_evidence")
        sec_reasons.append("registry and vendor-risk service disagree")
    if "vendor_risk_unavailable" in vendor_issues:
        flags.add("vendor_risk_unavailable")
        sec_reasons.append("vendor risk could not be verified")
    if sec_reasons:
        flags.add("security_review_required")
        approvals.append("Security")
        findings.append({"finding": "Security review required: " + "; ".join(sec_reasons), "reference": "policy section 5"})

    # 6. Privacy
    api = vendor.get("api") or {}
    pii = bool(re.search(PII_RX, data_level, flags=re.I))
    sensitive = pii or "confidential" in data_level.lower()
    outside = bool(api.get("stores_data_outside_region"))
    if pii or (sensitive and outside):
        flags.add("privacy_review_required")
        approvals.append("Privacy")
        findings.append({"finding": "Privacy review required: "
                                    + ("tool will process PII" if pii else "sensitive data stored outside the operating region"),
                         "reference": "policy section 6"})

    # 7. Legal
    registry = vendor.get("registry") or {}
    legal_reasons = []
    if (not registry or registry.get("procurement_status") != "Approved") and cost is not None and cost >= 10000:
        legal_reasons.append("new vendor with annual spend of $10,000 or more")
    if registry and registry.get("legal_terms_status") != "Approved":
        legal_reasons.append(f"legal terms status is {registry.get('legal_terms_status')}")
    if sensitive and outside:
        legal_reasons.append("cross-region processing of sensitive data")
    if legal_reasons:
        flags.add("legal_review_required")
        approvals.append("Legal")
        findings.append({"finding": "Legal review required: " + "; ".join(legal_reasons), "reference": "policy section 7"})

    # 9. Untrusted content: request text and vendor notes are data, never instructions
    texts = {k: v for k, v in request.items() if isinstance(v, str)}
    texts["vendor_registry_notes"] = registry.get("notes") or ""
    texts["vendor_api_notes"] = api.get("notes") or ""
    injections = scan_for_injection(texts)
    if injections:
        flags.add("prompt_injection_detected")
        findings.append({"finding": "Embedded instruction ignored in "
                                    + ", ".join(f"{h['field']} ('{h['matched_text']}')" for h in injections),
                         "reference": "policy section 9"})

    approvals = [a for a in APPROVAL_ORDER if a in set(approvals)]
    return {"required_approvals": approvals, "risk_flags": sorted(flags), "missing_information": missing,
            "injections": injections, "findings": findings}
