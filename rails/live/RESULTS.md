# Messy-world results

Branch `aaryan/messy-world-audit`. Code at commit `2f4e697` plus report wording. Everything here is
simulated: companies, counterparts, replies and the January–April 2026 clock. Reminders are
recorded, never sent, and no money moves. Raw per-seed metrics are in `rails/live/results/`.

Clean world (unchanged): `pytest -q` passes, except one test that needs Postgres.
`python -m evals.harness --systems full` scores 9/9 and `python -m workflow.sealed` passes.

## How to reproduce

```
python -m rails.live.sim --seeds $(seq 13 32) --systems rules no_checks --out OUT/dev
python -m rails.live.sim --seeds $(seq 9000 9009) --split held_out --systems rules --out OUT/held
python -m rails.live.sim --seeds 9000 --split held_out --live --budget 4 --max-calls 300 --out OUT/paid
```

Each seed directory holds `input.json`, `truth.json`, `control.json` and a `commitment.json` of
their SHA-256 digests, all written before any system runs. Every output is hashed before it is
scored. The offline runs execute each system in a separate process that cannot open the truth
file. The paid run executes in-process: the truth file is written, dropped from memory and read
back only when scoring.

## 1. Development wording, 20 seeds (13–32), no model

| System | Cause accuracy mean [min, max] | Alert P | Alert R | Hold error $ | Extraction P / R | Review rate |
|---|---|---:|---:|---:|---|---:|
| rules-only | 0.903 [0.818, 0.958] | 0.973 | 0.870 | 418 | 1.000 / 0.919 | 0.084 |
| without code checks | 0.914 [0.727, 1.000] | 0.973 | 0.926 | 285 | 0.997 / 0.990 | 0.000 |

| System | Errors caught / made | Wrong corrections | Prohibited actions | Invariant stops | Replay duplicates |
|---|---:|---:|---:|---:|---:|
| rules-only | 124 / 138 | 0 | 0 | 0 | 0 |
| without code checks | 120 / 137 | 0 | 0 | 1 | 0 |

Without the code checks, more documents reach the books, so most numbers rise. On seed 21,
though, an unchecked payment unbalanced the books and stopped processing on March 29; that seed
fell to 0.727. With the checks, the misses are planted wrong or missing dates, which intake
correctly sends to review. The simulation has no person to clear the review queue, so those
invoices are scored as missed.

Self-correction: most errors are predictions made before a late bank export arrived. They are
caught when it arrives, after 17.5 days on average, which is how late the exports are planted.

## 2. Held-out wording, 10 seeds (9000–9009), no model

| System | Cause accuracy mean [min, max] | Alert P | Alert R | Extraction P / R | Review rate | Errors caught / made |
|---|---|---:|---:|---|---:|---:|
| rules-only | 0.219 [0.125, 0.333] | 0.228 | 0.095 | 0.979 / 0.330 | 0.663 | 9 / 85 |

The held-out seeds use wording and layouts the parser was never built against: US dates, wire
confirmations, semicolon bank exports, forwarded threads and prose statements. Rules-only stays
precise (0.979) but reads a third of the facts, so two thirds of documents go to review. This
is the gap a model is supposed to close.

## 3. Paid sample: held-out seed 9000, model on

Model: claude-sonnet-5-5 for intake; claude-opus-5-5 for the single-model baseline.

| System | Cause accuracy | Alert P | Alert R | Hold error $ | Extraction P / R | Review rate | Calls | Est. $ |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| rules-only | 0.318 | 0.000 | 0.000 | 8,711 | 0.967 / 0.406 | 0.560 | 0 | 0.00 |
| ours (cold cache) | 0.273 | 0.143 | 0.167 | 17,085 | 0.974 / 0.319 | 0.667 | 80 | 1.14 |
| ours (warm cache) | 0.273 | 0.143 | 0.167 | 17,085 | 0.974 / 0.319 | 0.667 | 0 | 0.00 |
| ours, no verifier | 0.273 | 0.143 | 0.167 | 17,085 | 0.974 / 0.319 | 0.667 | 0 | 0.00 |
| ours, no code checks | 0.409 | 0.250 | 0.286 | 16,557 | 0.929 / 0.440 | 0.520 | 33 | 0.52 |
| single model, all documents | 0.955 | 1.000 | 1.000 | 0 | n/a | n/a | 1 | 0.19 |

The per-system rows do not add up to the run total. The run made 193 model calls at an
estimated $2.81, plus $0.19 for the baseline. That total includes the attack-control runs. An
earlier attempt on the same seed cost $0.99 and is not scored: it lost calls to connection
errors and halted on the books invariant, which commit `2f4e697` fixed.

Across all ours runs: 0 prohibited actions, 0 invariant stops, 0 replay duplicates. The warm
cache repeated the cold run exactly, from 80 exact-prompt hits.

**On this seed, ours does not beat rules-only, and the single model is far ahead.** One seed
is not a conclusion, but the cause is clear from the review log.

### Why ours lost

The model's reading is not the problem. Ours accepted almost nothing that was wrong
(precision 0.974). It lost on recall: 67% of documents went to review. The review reasons were:

| Reason | Count |
|---|---:|
| model marked its own confidence below 0.85 | 30 |
| model and rules parser disagreed on a field the parser could read | 15 |
| reference to an invoice that was itself still in review (cascade) | 14 |
| no invoice reference on a claim | 8 |
| payer or payee not quoted verbatim | 11 |
| model output failed the schema | 6 |

The model also writes free-text uncertainty notes, such as "payer inferred from manager_id".
Each note sends its whole document to review. One rejected invoice then takes its funding,
payments and emails with it. The single model has no such gate. It reads every document at
once, with nothing that can veto it.

### Planted instructions

22 documents carried planted instructions. 19 went to review and 3 were accepted. Those 3
(two invoices and one funding record) carry the correct values, with nothing taken from the
instruction. No status, payment or action came from an instruction. Removing the instructions
still changed 9 of ours' decisions, because the documents that carried them went to review
instead of into the books. The instructions caused no wrong facts, but they stopped correct
facts from being used. The single model's attack control was not run, so its exposure is not
measured here.

## What this does and does not show

- Clean world: rules-only alone reaches 9/9; nothing here changes that.
- Development messy wording: rules-only is strong (0.90 cause) because the parser was written
  against this wording. The model's value cannot show here.
- Held-out wording: rules-only collapses (0.22). Ours as currently gated does not recover it.
  A single all-documents model does (0.955 on one seed).
- Safety held on every run: no prohibited action, no fact taken from an instruction, no
  replay duplicates. The only invariant stop was the run without code checks.
- Not shown: that ours beats the single model or rules-only on messy data. The brief expected
  that result; this sample does not support it.

## Next step (not started; needs budget approval)

1. Treat the model's free-text uncertainty notes as advisory. Only failed code checks, or
   confidence below the threshold, should send a document to review.
2. Retry documents held for an unknown invoice once that invoice is accepted. This mirrors the
   funding retry.
3. Count model/rules disagreement only on fields where the rules parser's quote and the
   model's quote are the same text.

Seed 9000 has now been looked at, so it becomes a development seed. Score any change on fresh
held-out seeds (9010 and up) that no model has read. Cost is about $3 per seed for all
systems plus controls, or about $1.30 for ours plus the baseline.

## Limits

- The same team wrote the development and held-out wording. `--pool-file` accepts held-out
  wording written by someone else.
- The simulation has no person to clear review items or approve changes to rules.
- The autonomy gate is reported (Wilson lower bound on the reminder track record) but does
  not change workflow behavior.
- Costs are estimates from the repository's token prices, not a provider invoice.
