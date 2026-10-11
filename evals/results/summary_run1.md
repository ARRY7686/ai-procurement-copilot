# Architecture comparison

Model: `gemini-3.5-flash-lite` | cases: 10 | repeats: 3

| Metric | single | staged | rules |
|---|---:|---:|---:|
| Cases meeting all 4 criteria | 24/30 | 27/30 | 27/30 |
| Correct next action | 24/30 | 28/30 | 27/30 |
| Policy followed (approvals + flags) | 30/30 | 29/30 | 27/30 |
| Human escalation correct | 30/30 | 30/30 | 30/30 |
| Evidence grounded in tool results | 30/30 | 30/30 | 30/30 |
| Public cases meeting minimum checks | 18/18 | 18/18 | 18/18 |
| Agent gathered all evidence itself | 27/30 | 26/30 | 0/30 |
| Guard corrections (LLM policy misses) | 0 | 0 | 0 |
| Avg latency (ms) | 5486 | 8774 | 23 |
| p95 latency (ms) | 13339 | 14596 | 32 |
| Avg LLM calls | 2.37 | 3.4 | 0 |
| Avg tool calls | 4.93 | 4.93 | 4 |

## Failures and guard corrections
- single L-01 (REQ-1001): action=REUSE_EXISTING; criteria not met
- single L-10 (REQ-1010): action=REUSE_EXISTING; criteria not met
- single L-01 (REQ-1001): action=REUSE_EXISTING; criteria not met
- single L-10 (REQ-1010): action=REUSE_EXISTING; criteria not met
- single L-01 (REQ-1001): action=REUSE_EXISTING; criteria not met
- single L-10 (REQ-1010): action=REUSE_EXISTING; criteria not met
- staged L-04 (REQ-1004): action=REVIEW; criteria not met
- staged L-01 (REQ-1001): action=REUSE_EXISTING; criteria not met
- staged L-10 (REQ-1010): action=REUSE_EXISTING; criteria not met
- rules L-08 (REQ-1008): action=PROCEED; criteria not met
- rules L-08 (REQ-1008): action=PROCEED; criteria not met
- rules L-08 (REQ-1008): action=PROCEED; criteria not met
