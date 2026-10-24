# Eval results

Mode: live agents (claude-sonnet-5-5, 170 unique calls, $2.3037) and live verifier (claude-haiku-5-5); total 235 calls, $2.4050; rails: real.

| Metric | full | no_verifier |
|---|---|---|
| scenarios_passed | 8/9 | 8/9 |
| cause_accuracy | 83% | 83% |
| alert_precision | 100% | 80% |
| alert_recall | 100% | 100% |
| citation_rate | 100% | 100% |
| prohibited_executed | 0 | 0 |
| prohibited_blocked | 0 | 0 |
| moment_correct | yes | NO |
| recon_ok | yes | yes |
| cost_usd | $0.1014 | $0.0000 |
| latency_s | 1553.31 | 82.99 |

## Failures: full

- #1 authorized_holdback: cause unexplained != authorized_hold

## Failures: no_verifier

- #1 authorized_holdback: cause unexplained != authorized_hold
- #1 authorized_holdback: false alert on manager
- moment: kept 3 alert(s), expected 2
