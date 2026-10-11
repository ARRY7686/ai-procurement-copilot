# Architecture Decision Memo

## Decision

Ship **Architecture A: a single agent with deterministic tools and a code guard**. The staged variant used 55% more
LLM calls and about 1.8x the latency, and it did not get more cases right.

## Evidence

Test set: all 10 requests, covering every edge case in the brief, labelled with the expected action, approvals and
flags. Each architecture ran 3 times on `gemini-3.5-flash-lite` (`python evals/run_comparison.py --repeats 3`).

| Metric (30 runs each) | Single | Staged | Rules only |
|---|---:|---:|---:|
| All 4 quality criteria met | 27/30 | 27/30 | 27/30 |
| Correct next action | 27/30 | 27/30 | 27/30 |
| Policy followed | 30/30 | 30/30 | 27/30 |
| Escalation correct | 30/30 | 30/30 | 30/30 |
| Public minimum checks | 18/18 | 18/18 | 18/18 |
| Guard corrections | 0 | 3 | n/a |
| Avg LLM calls | 2.13 | 3.30 | 0 |
| Avg latency* | 9.6 s | 17.6 s | 0.02 s |

\*Includes free-tier rate-limit waits.

Failures:
- **Single:** redirected the $950 SignFlow training pack to the existing tool (3 of 3 runs).
- **Staged:** made the same mistake on SignFlow requests in 3 runs. It also once said PROCEED for source-code access,
  and twice added an unneeded Manager approver. The guard fixed all three.
- **Rules only:** never caught that the company-wide TaskFlow already covers Marketing's request. That judgement is
  why the LLM is there.

## Trade-offs

Code already owns the hard parts: thresholds, budget, review freshness, vendor conflicts and required reviews. The
LLM's job is narrow: read the need, judge overlap, write the next step. One agent with full context does this as well
as two, and is cheaper.

The hand-off in the staged design loses context. The reviewer sees a summary, not the tool calls, and it made every
policy-level slip in the final run.

## Risks / limitations

- I labelled the 10 cases myself, so this is a smoke test. Before production I would evaluate 100+ historical
  requests labelled by the procurement team.
- The remaining LLM error is over-redirecting same-vendor add-ons. Human review catches it. A "seats used" field in
  the catalog would let code decide this instead.
- The injection scan uses regex. The guard limits the damage either way: injected text cannot remove approvals or
  lower the action.
- Free-tier rate limits dominate latency. Production needs a paid quota.

## Why this is the right MVP

Code owns every number and rule. Every evidence row comes from a tool. A human makes every final decision. One agent
does the remaining judgement work. It matched the staged design on quality, with fewer moving parts, two-thirds of
the LLM calls and zero guard corrections.
