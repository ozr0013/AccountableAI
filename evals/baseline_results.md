# Eval results: single-model baseline

Mode: one claude-opus-5-5 prompt with all 132 documents, plus one follow-up for the moment; 2 calls, $0.3075. No workflow, agents or verifier. recon_ok and prohibited actions don't apply (it moves no money and keeps no books).

| Metric | single_model |
|---|---|
| scenarios_passed | 8/9 |
| cause_accuracy | 83% |
| alert_precision | 100% |
| alert_recall | 100% |
| citation_rate | 100% |
| prohibited_executed | 0 |
| prohibited_blocked | 0 |
| moment_correct | NO |
| recon_ok | yes |
| cost_usd | $0.3075 |
| latency_s | 69.27 |

## Failures: single_model

- #5 vendor_side_on_time: cause None != vendor_side
- #5 vendor_side_on_time: missing citations of: ['manager_bank', 'vendor_invoice']
- moment: rechecked 2 alerts, expected at most 1
