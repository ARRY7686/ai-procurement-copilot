# Architecture comparison

Model: `gemini-3.5-flash-lite` | cases: 10 | repeats: 3

| Metric | single | staged | rules |
|---|---:|---:|---:|
| Cases meeting all 4 criteria | 27/30 | 27/30 | 27/30 |
| Correct next action | 27/30 | 27/30 | 27/30 |
| Policy followed (approvals + flags) | 30/30 | 30/30 | 27/30 |
| Human escalation correct | 30/30 | 30/30 | 30/30 |
| Evidence grounded in tool results | 30/30 | 30/30 | 30/30 |
| Public cases meeting minimum checks | 18/18 | 18/18 | 18/18 |
| Agent gathered all evidence itself | 30/30 | 30/30 | 0/30 |
| Guard corrections (LLM policy misses) | 0 | 3 | 0 |
| Avg latency (ms) | 9586 | 17587 | 22 |
| p95 latency (ms) | 35871 | 96539 | 33 |
| Avg LLM calls | 2.13 | 3.3 | 0 |
| Avg tool calls | 5 | 5.1 | 4 |

## Failures and guard corrections
- single L-10 (REQ-1010): action=REUSE_EXISTING; criteria not met
- single L-10 (REQ-1010): action=REUSE_EXISTING; criteria not met
- single L-10 (REQ-1010): action=REUSE_EXISTING; criteria not met
- staged L-01 (REQ-1001): action=REUSE_EXISTING; criteria not met
- staged L-03 (REQ-1003): action=REVIEW; action PROCEED -> REVIEW (rules require escalation)
- staged L-04 (REQ-1004): action=REVIEW; dropped approver not required by the threshold table: Manager
- staged L-01 (REQ-1001): action=REUSE_EXISTING; criteria not met
- staged L-04 (REQ-1004): action=REVIEW; dropped approver not required by the threshold table: Manager
- staged L-10 (REQ-1010): action=REUSE_EXISTING; criteria not met
- rules L-08 (REQ-1008): action=PROCEED; criteria not met
- rules L-08 (REQ-1008): action=PROCEED; criteria not met
- rules L-08 (REQ-1008): action=PROCEED; criteria not met
