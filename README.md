# AI Procurement Request Copilot

FDE Assessment 3. The copilot reads a software purchase request, gathers evidence with tools, applies the
procurement policy in code, and recommends the next action. It never approves or buys anything: every decision
ends with a human reviewer.

Output for every request: **recommendation | evidence | approvals required | missing information | risk flags | next step**.

## Run it

```bash
# Windows PowerShell              # macOS / Linux
./start.ps1                       ./start.sh
```

The start script creates `.venv`, installs `requirements.txt`, copies `.env.example` to `.env` if needed, and
starts the vendor-risk mock API (`:8001`) and the UI (`http://127.0.0.1:8501`).

Put your Gemini key(s) in `.env`:

```
GEMINI_API_KEYS=key1,key2         # one or more AI Studio keys, rotated on 429/5xx
MODEL_NAME=gemini-3.8-flash
```

The app still runs without a working key. It falls back to the deterministic rules and says so in the evidence panel.

To do it by hand: `python -m venv .venv`, activate it, `pip install -r requirements.txt`, `python verify_setup.py`,
then `python run_local.py`.

## Product workflow

1. Pick an existing request, or enter a new one, in the sidebar. Choose the `single` or `staged` architecture.
2. **Analyse request.** The agent calls tools, then the code guard applies the policy floor.
3. Read the **recommendation** (one of five actions), **next step**, **approvals**, **risk flags**, **missing
   information**, and the **evidence** table. Every evidence row comes from a tool and has a file, record or policy
   reference.
4. **Human review.** The reviewer records "send to approvers", "request info", "reject" or "override (exception)",
   with a comment. This goes to `outputs/human_reviews.jsonl`.

Actions: `PROCEED` (standard approvers), `REVIEW` (Security/Privacy/Legal first), `BUDGET_EXCEPTION`,
`CLARIFY` (missing info), `REUSE_EXISTING` (an existing tool covers the need).

## Architecture

See [docs/architecture.md](docs/architecture.md) for the diagrams, the AI/code/human split, failure handling,
assumptions and the starter-pack fixes.

- **A - single agent** (`src/agents.py::run_single`). One Gemini function-calling agent with 4 tools. It finishes
  with a `submit_decision` call whose arguments are the structured decision.
- **B - staged** (`run_staged`). An analyst agent with 3 evidence tools produces a structured evidence pack. Code then
  runs the policy engine. A policy/risk reviewer (one JSON-schema call, no tools) makes the decision.
- **Shared code guard** (`guard`). Approvals and flags are the union of rules and agent output. Missing info forces
  `CLARIFY`. The agent can escalate but never de-escalate. Human review is always required.

| Tool | Deterministic | Data |
|---|---|---|
| `lookup_requester_budget` | yes | employees, department budgets |
| `search_software_catalog(query)` | yes | catalog, purchase history |
| `get_vendor_risk` | yes (HTTP) | vendor registry + mock vendor-risk API |
| `check_policy_rules` | yes | thresholds, reviews, freshness, required fields, injection scan |

## Evaluation

```bash
python evals/run_public_evals.py --architecture single     # starter harness, 6 public cases
python evals/run_public_evals.py --architecture staged
python evals/run_comparison.py --repeats 3                 # all 10 requests, single vs staged vs rules-only
python -m unittest discover -s tests                       # data, mock API and policy-rule tests
```

`evals/labeled_cases.json` labels all 10 requests with:
- the expected action;
- required and acceptable approvals;
- required and forbidden flags.

Between them the labels cover every edge case in the brief. Each run is scored on:
- correct next action;
- grounded evidence;
- policy followed;
- correct human escalation;
- latency;
- LLM and tool calls;
- **guard corrections**: how often the LLM alone would have broken policy.

`rules` is the reference point with no LLM.

### Results (final run, `gemini-3.5-flash-lite`, 10 cases x 3 repeats)

| Metric | Single | Staged | Rules only |
|---|---:|---:|---:|
| All 4 quality criteria met | 27/30 | 27/30 | 27/30 |
| Correct next action | 27/30 | 27/30 | 27/30 |
| Policy followed (approvals + flags) | 30/30 | 30/30 | 27/30 |
| Human escalation correct | 30/30 | 30/30 | 30/30 |
| Public cases meeting minimum checks | 18/18 | 18/18 | 18/18 |
| Guard corrections (LLM policy misses) | 0 | 3 | n/a |
| Avg LLM calls | 2.13 | 3.30 | 0 |
| Avg tool calls | 5.0 | 5.1 | 4 |
| Avg latency (incl. free-tier rate-limit waits) | 9.6 s | 17.6 s | 0.02 s |

Full table and failure list: [evals/results/summary.md](evals/results/summary.md). Per-case rows:
`evals/results/comparison_cases.csv`.

**Run history.** Earlier runs are kept as `*_run1.*` and `*_run2.*`.
- **Run 1:** 13 runs fell back to rules-only on free-tier quota errors. The single agent redirected SignFlow add-ons
  to the existing tool.
- **Fixes after run 1:** the client now waits as long as the API asks before retrying. The prompt now says that
  add-ons and extra seats are expansions of an existing tool, not duplicates.
- **Run 2:** the staged reviewer added a CFO approval to a $22,000 request, below the $25,000 CFO threshold.
- **Fix after run 2:** the guard now takes business approvers from the threshold table only. The agent may add
  Security, Privacy or Legal reviews.

## Architecture decision

**Ship the single agent.** It matches the staged variant on quality (27/30 each), with about 35% fewer LLM calls,
about half the latency, and no guard corrections. Rules-only shows why there is an LLM at all: it can't tell that an
existing tool already solves the need. Full memo: [docs/architecture_decision.md](docs/architecture_decision.md).

## Known limitations

- Ten labeled cases make a small test set, and I labelled them myself. Treat the metrics as a smoke test, not a
  benchmark.
- The overlap judgement for same-vendor expansions is the LLM's. Code flags only different-vendor substitutes in
  the same category, and exact duplicates.
- The injection scan uses regex. It catches direct instructions, not paraphrased or encoded ones. The guard limits
  the damage either way: injected text can't remove approvals or lower the action.
- The data-sensitivity triggers match on keywords in `data_access_level` and integrations. New labels may need new
  patterns.
- There is no auth or persistence beyond a JSONL review log. Data is synthetic.
