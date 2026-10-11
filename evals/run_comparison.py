"""Run every labeled case on each architecture and write the comparison used in the decision memo.

    python evals/run_comparison.py                     # single + staged + rules, 1 repeat
    python evals/run_comparison.py --repeats 3         # repeat to see run-to-run variance

Outputs: evals/results/comparison_cases.csv (one row per case x architecture x repeat)
         evals/results/summary.md            (aggregate table)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.run_public_evals import evaluate as public_evaluate  # noqa: E402
from mock_api.server import ensure_running  # noqa: E402
from src.agents import run  # noqa: E402
from src.data_access import get_request  # noqa: E402
from src.llm import model_name  # noqa: E402

OUT = ROOT / "evals" / "results"
EVIDENCE_TOOLS = {"lookup_requester_budget", "search_software_catalog", "get_vendor_risk"}


def score(case: dict, decision, trace: dict) -> dict:
    approvals, flags = set(decision.required_approvals), set(decision.risk_flags)
    req, opt = set(case["approvals_required"]), set(case["approvals_optional"])
    action_ok = trace["action"] in case["expected_actions"]
    policy_ok = req <= approvals <= req | opt and set(case["flags_required"]) <= flags and not flags & set(case["flags_forbidden"])
    escalation_ok = decision.human_review_required and (req & {"Security", "Privacy", "Legal", "Finance"}) <= approvals
    tool_names = {n.split(":")[-1] for n in decision.telemetry.tool_names}
    grounded = all(e.source in tool_names | {"copilot"} for e in decision.evidence)
    return {"correct_next_action": action_ok, "grounded_evidence": grounded, "policy_followed": policy_ok,
            "human_escalation_correct": escalation_ok}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architectures", nargs="+", default=["single", "staged", "rules"])
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    cases = json.loads((ROOT / "evals" / "labeled_cases.json").read_text(encoding="utf-8"))
    public = {c["request_id"]: c for c in json.loads((ROOT / "evals" / "public_cases.json").read_text(encoding="utf-8"))}
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with ensure_running():
        for arch in args.architectures:
            for rep in range(args.repeats):
                for case in cases:
                    decision, trace = run(get_request(case["request_id"]), arch)
                    s = score(case, decision, trace)
                    pub = public.get(case["request_id"])
                    pub_fail = public_evaluate(decision, pub["expectations"]) if pub else []
                    llm_out = trace["llm_output"] or {}
                    rows.append({
                        "case_id": case["case_id"], "request_id": case["request_id"], "architecture": arch, "repeat": rep,
                        **s, "all_criteria_met": all(s.values()),
                        "public_case": pub["case_id"] if pub else "", "public_min_checks_passed": (not pub_fail) if pub else "",
                        "latency_ms": trace["latency_ms"], "llm_calls": decision.telemetry.llm_calls,
                        "tool_calls": decision.telemetry.tool_calls,
                        "agent_gathered_all_evidence": EVIDENCE_TOOLS <= set(trace["agent_tool_calls"]),
                        "guard_corrections": len(trace["guard_corrections"]),
                        "action": trace["action"], "llm_action": llm_out.get("action", ""),
                        "notes": "; ".join(trace["guard_corrections"]) + (f" LLM_ERROR: {trace['llm_error']}" if trace["llm_error"] else ""),
                    })
                    r = rows[-1]
                    print(f"{arch:7s} r{rep} {case['case_id']} {r['action']:16s} all_ok={r['all_criteria_met']!s:5s} "
                          f"{r['latency_ms']:8.0f} ms llm={r['llm_calls']} tools={r['tool_calls']} fixes={r['guard_corrections']}")

    with (OUT / "comparison_cases.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    def agg(arch: str) -> dict:
        rs = [r for r in rows if r["architecture"] == arch]
        n = len(rs)
        pubs = [r for r in rs if r["public_case"]]
        return {
            "Cases meeting all 4 criteria": f"{sum(r['all_criteria_met'] for r in rs)}/{n}",
            "Correct next action": f"{sum(r['correct_next_action'] for r in rs)}/{n}",
            "Policy followed (approvals + flags)": f"{sum(r['policy_followed'] for r in rs)}/{n}",
            "Human escalation correct": f"{sum(r['human_escalation_correct'] for r in rs)}/{n}",
            "Evidence grounded in tool results": f"{sum(r['grounded_evidence'] for r in rs)}/{n}",
            "Public cases meeting minimum checks": f"{sum(r['public_min_checks_passed'] is True for r in pubs)}/{len(pubs)}",
            "Agent gathered all evidence itself": f"{sum(r['agent_gathered_all_evidence'] for r in rs)}/{n}",
            "Guard corrections (LLM policy misses)": sum(r["guard_corrections"] for r in rs),
            "Avg latency (ms)": round(statistics.mean(r["latency_ms"] for r in rs)),
            "p95 latency (ms)": round(sorted(r["latency_ms"] for r in rs)[max(0, int(0.95 * n) - 1)]),
            "Avg LLM calls": round(statistics.mean(r["llm_calls"] for r in rs), 2),
            "Avg tool calls": round(statistics.mean(r["tool_calls"] for r in rs), 2),
        }

    archs = args.architectures
    table = {a: agg(a) for a in archs}
    lines = [f"# Architecture comparison\n", f"Model: `{model_name()}` | cases: {len(cases)} | repeats: {args.repeats}\n",
             "| Metric | " + " | ".join(archs) + " |", "|---|" + "---:|" * len(archs)]
    for metric in table[archs[0]]:
        lines.append(f"| {metric} | " + " | ".join(str(table[a][metric]) for a in archs) + " |")
    misses = [f"- {r['architecture']} {r['case_id']} ({r['request_id']}): action={r['action']}; {r['notes'] or 'criteria not met'}"
              for r in rows if not r["all_criteria_met"] or r["guard_corrections"]]
    lines += ["", "## Failures and guard corrections", *(misses or ["- none"])]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
