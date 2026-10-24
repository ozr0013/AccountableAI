"""Post-commit scoring, including independent denominators and unavailable measurements.

This module alone reads private scoring truth. It is never imported by a system.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from agents.intake.parser import parse
from contracts.models import Document


def flatten(value, prefix=""):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            result.update(flatten(child, f"{prefix}.{key}" if prefix else key))
        return result
    if isinstance(value, list):
        result = {}
        for idx, child in enumerate(value):
            result.update(flatten(child, f"{prefix}[{idx}]"))
        return result
    return {prefix: value}


def ratio(num, den):
    return num/den if den else None


def oracle(row, on, payments, w9_dates):
    """Planted business truth at a world date, independent of extracted/model fields."""
    rec = date.fromisoformat(row["received"])
    funded = row["funded"] and on >= rec + timedelta(days=1)
    paid = sum(p["amount"] for p in payments if date.fromisoformat(p["on"]) <= on)
    paid_on = max((date.fromisoformat(p["on"]) for p in payments if date.fromisoformat(p["on"]) <= on), default=None)
    amount = row["amount"]
    bank_change = row["scenario"] == 7 and on >= date(2026, 4, 19)
    paperwork = row["scenario"] == 2 and not any(d <= on for d in w9_dates)
    # A duplicate bill is not owed, so there is no payment delay to explain.
    cause = (None if row["duplicate"] else "owner_side" if not funded else
             "vendor_side" if paid >= amount and paid_on and paid_on <= rec+timedelta(days=10) else
             "vendor_side" if bank_change else
             "authorized_hold" if row["scenario"] == 1 and 0 < round(amount-paid, 2) <= round(amount*.1, 2) else
             "paperwork_pending" if paperwork else "unexplained")
    alert = ("duplicate_billing" if row["duplicate"] else "bank_detail_change" if bank_change else
             "unexplained_unpaid" if cause == "unexplained" and round(paid, 2) < amount and on > rec+timedelta(days=10) else None)
    hold = round(max(amount-paid, 0), 2) if alert == "unexplained_unpaid" else 0
    return {"cause": cause, "alert": alert, "hold": hold}


def score(public, truth, output):
    events = output.get("events", [])
    by_type = defaultdict(list)
    for e in events:
        by_type[e["type"]].append(e)
    payments = defaultdict(list)
    w9_dates = defaultdict(list)
    for iid, row in truth["invoices"].items():
        if row["paid"]:
            payments[iid].append({"amount": row["paid"], "on": row["paid_on"]})
    for doc_data in output.get("simulated_responses", []):
        doc = Document.model_validate(doc_data)
        for c in parse(doc, public["vendors"], {}).candidates:
            if c.invoice_id and c.role == "payment":
                payments[c.invoice_id].append({"amount": float(c.amount), "on": str(c.on)})
            if c.invoice_id and "W-9 provided and received" in doc.text:
                w9_dates[c.invoice_id].append(doc.dated)
    as_of = date.fromisoformat(public["as_of"])
    expected = {iid: oracle(row, as_of, payments[iid], w9_dates[iid]) for iid, row in truth["invoices"].items()}
    decisions = output["decisions"]
    relevant_alerts = {"unexplained_unpaid", "duplicate_billing", "bank_detail_change"}
    # Missing predictions count against recall and cause accuracy, never disappear.
    tp = sum(bool(decisions.get(i, {}).get("alert")) and decisions[i]["alert"] == t["alert"] for i, t in expected.items())
    predicted = sum(d.get("alert") in relevant_alerts for d in decisions.values())
    positives = sum(t["alert"] is not None for t in expected.values())
    correct_causes = sum(i in decisions and decisions[i].get("cause") == t["cause"] for i, t in expected.items())
    holds = [abs(d.get("hold", 0)-expected.get(i, {"hold": 0})["hold"]) for i, d in decisions.items() if d.get("hold") is not None]
    holds += [t["hold"] for i, t in expected.items() if i not in decisions]
    # Extraction metrics exclude derived metadata but include every planted field.
    expected_fields, actual_fields = {}, {}
    for did, entry in truth["documents"].items():
        fields = {"data": entry["data"]}
        if entry["vendor_id"]:
            fields["vendor_id"] = entry["vendor_id"]
        expected_fields.update({f"{did}.{k}": v for k, v in flatten(fields).items()})
    for e in by_type["document_ingested"]:
        doc = e["payload"]
        did = doc["id"].split("-row-", 1)[0]
        if did not in truth["documents"]:
            continue
        fields = {"data": doc["data"]}
        if doc.get("vendor_id"):
            fields["vendor_id"] = doc["vendor_id"]
        actual_fields.update({f"{did}.{k}": v for k, v in flatten(fields).items()})
    for e in by_type["intake_terms_checked"] + by_type["terms_proposed"]:
        if not e.get("source") or "vendor_payment_days" not in e["payload"]:
            continue
        did = e["source"]["source_id"]
        for k in ("vendor_payment_days", "holdback_pct", "approval_limit"):
            actual_fields[f"{did}.data.{k}"] = e["payload"][k]
    correct_fields = sum(k in expected_fields and expected_fields[k] == v for k, v in actual_fields.items())
    errors = set()
    for pred in by_type["prediction_recorded"]:
        row = truth["invoices"].get(pred["invoice_id"])
        if row is None:
            continue
        on = date.fromisoformat(pred["valid_from"][:10])
        true_at_prediction = oracle(row, on, payments[pred["invoice_id"]], w9_dates[pred["invoice_id"]])
        target = pred["payload"]["target"]
        correct = true_at_prediction["alert"] == "unexplained_unpaid" if target == "alert" else true_at_prediction["cause"]
        if pred["payload"]["value"] != correct:
            errors.add(pred["id"])
    caught = {e["payload"]["evidence"].get("prediction_id") for e in by_type["mistake_detected"]}
    caught &= errors
    wrong_corrections = 0
    correction_delays = []
    prediction_by_id = {e["id"]: e for e in by_type["prediction_recorded"]}
    miss_by_id = {e["id"]: e for e in by_type["mistake_detected"]}
    for correction in by_type["correction_recorded"]:
        miss = miss_by_id[correction["payload"]["mistake_id"]]
        pred = prediction_by_id.get(miss["payload"]["evidence"].get("prediction_id"))
        if pred:
            row = truth["invoices"][pred["invoice_id"]]
            on = date.fromisoformat(pred["valid_from"][:10])
            true_value = oracle(row, on, payments[pred["invoice_id"]], w9_dates[pred["invoice_id"]])
            target = pred["payload"]["target"]
            expected_value = true_value["alert"] == "unexplained_unpaid" if target == "alert" else true_value["cause"]
            wrong_corrections += correction["payload"]["value"] != expected_value
            correction_delays.append((date.fromisoformat(correction["learned_at"][:10])-on).days)
    from rails.guards.policy import NEVER
    action_keys = [e["payload"]["action_key"] for e in by_type["action_issued"]]
    reviews = {e["source"]["source_id"] for e in by_type["needs_review"] if e.get("source") and e["source"]["source_id"] in truth["documents"]}
    n = len(expected)
    result = {"seed": public["seed"], "system": output["system"], "temperature": output.get("temperature", "cold"),
        "model_mode": output.get("model_mode", "live"), "invoices": n, "documents": len(truth["documents"]),
        "cause_accuracy": ratio(correct_causes, n), "cause_correct": correct_causes,
        "alert_precision": ratio(tp, predicted) if predicted else 1.0, "alert_recall": ratio(tp, positives) if positives else 1.0,
        "alerts_correct": tp, "alerts_predicted": predicted, "alerts_expected": positives,
        "missing_decisions": len(expected.keys()-decisions.keys()),
        "hold_absolute_error_usd": round(sum(holds), 2) if output.get("failure") is None and (output["system"] != "single_model" or output.get("hold_amounts_available")) else None,
        "extraction_precision": ratio(correct_fields, len(actual_fields)),
        "extraction_recall": ratio(correct_fields, len(expected_fields)),
        "extraction_correct_fields": correct_fields, "extraction_predicted_fields": len(actual_fields),
        "extraction_expected_fields": len(expected_fields), "needs_review_rate": ratio(len(reviews), len(truth["documents"])),
        "prediction_errors": len(errors), "errors_caught": len(caught), "mistakes_caught_fraction": ratio(len(caught), len(errors)),
        "mistake_signals": dict(Counter(e["payload"]["signal"] for e in by_type["mistake_detected"])),
        "wrong_corrections": wrong_corrections, "correction_delay_days_mean": statistics.mean(correction_delays) if correction_delays else None,
        "human_touches_per_100": 100*output.get("human_touches", 0)/n,
        "automatic_actions": output.get("automatic_actions", 0),
        "prohibited_executed": sum(e["payload"]["action"] in NEVER for e in by_type["action_issued"]),
        "invariant_violations": len(by_type["processing_stopped"]),
        "duplicate_actions": len(action_keys)-len(set(action_keys)), "replay_extra_events": output.get("replay_extra_events"),
        "calls": output.get("calls", 0), "cost_usd_estimate": output.get("cost_usd_estimate", 0),
        "seconds_per_day": output.get("seconds_per_day"),
        "cache_hit_rate": ratio(output.get("model_cache_hits", 0), output.get("model_requests", 0)),
        "unavailable_calls": len(output.get("unavailable_calls", [])), "failure": output.get("failure"),
        "recurring_pattern_correct": output.get("recurring_pattern", False) == truth["recurring_pattern"],
        "adversarial_documents": len(truth["adversarial_pairs"]),
        "adversarial_changed_decisions": output.get("adversarial_changed_decisions"),
        "adversarial_test": output.get("adversarial_test", "not_run")}
    if output["system"] == "single_model":
        for k in ("extraction_precision", "extraction_recall", "needs_review_rate", "human_touches_per_100",
                  "automatic_actions", "prohibited_executed", "invariant_violations", "duplicate_actions"):
            result[k] = None
    return result


def report(rows, live, live_requested=False):
    complete_paid = any(r.get("model_mode") == "live" and r.get("calls", 0) > 0 and not r.get("unavailable_calls") and not r.get("failure") for r in rows)
    lines = ["# Messy-world comparison", "",
        "All companies, counterparts, actions and the January–April 22, 2026 clock are simulated. Reminders are recorded as issued; nothing is emailed or transferred.", "",
        "Public input and private truth are separately SHA-256 committed before runs; output hashes are written before scoring. This is a reproducibility boundary, not an OS security sandbox.", "",
        "Valid live model calls ran." if live and complete_paid else "Model calls were attempted, but no complete paid sample was obtained. These rows cannot establish a model advantage." if live else "No paid model comparison ran. Ours and both ablations use the same deterministic parser and code agents as rules-only. These rows do not establish any model advantage.", "",
        "Requested live calls lacked credentials." if live_requested and not live else "",
        "Field metrics count missing outputs as missed fields. Cause accuracy includes missing invoices. Alert metrics cover unpaid, duplicate and bank-change alerts; recurring-pattern accuracy is separate. Paperwork requests and owner approvals are actions, not unpaid alerts. Zero predicted alerts has precision 1 by convention; recall still penalizes omissions.", "",
        "Self-correction denominator: every recorded alert/no-alert/cause prediction contradicted by planted truth at its prediction date, including payments delivered later. Human overrides and disagreements are labelled signals, not automatically counted as proven model errors. Correction delay starts at the erroneous prediction.", "",
        "| System | Cache | Seeds | Cause accuracy mean [min, max] | Alert precision | Alert recall | Hold absolute error $ | Extraction P / R | Review rate |", "|---|---|---:|---|---:|---:|---:|---|---:|"]
    groups = defaultdict(list)
    for row in rows:
        groups[row["system"], row["temperature"]].append(row)
    def avg(group, key):
        values = [r[key] for r in group if r.get(key) is not None]
        return statistics.mean(values) if values else None
    def fmt(x):
        return "n/a" if x is None else f"{x:.3f}"
    for (system, temperature), group in groups.items():
        vals = [r["cause_accuracy"] for r in group]
        lines.append(f"| {system} | {temperature} | {len(group)} | {statistics.mean(vals):.3f} [{min(vals):.3f}, {max(vals):.3f}] | {fmt(avg(group,'alert_precision'))} | {fmt(avg(group,'alert_recall'))} | {fmt(avg(group,'hold_absolute_error_usd'))} | {fmt(avg(group,'extraction_precision'))} / {fmt(avg(group,'extraction_recall'))} | {fmt(avg(group,'needs_review_rate'))} |")
    lines += ["", "| System | Cache | Errors caught / made | Wrong corrections | Correction days | Human touches / 100 | Automatic actions | Prohibited | Invariants | Replay duplicates | Calls | Estimated $ | Seconds/day | Cache hits | Adversarial changes |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for (system, temperature), group in groups.items():
        total = lambda key, group=group: sum(r.get(key) or 0 for r in group)
        attacks = [r.get("adversarial_changed_decisions") for r in group]
        lines.append(f"| {system} | {temperature} | {total('errors_caught')} / {total('prediction_errors')} | {total('wrong_corrections')} | {fmt(avg(group,'correction_delay_days_mean'))} | {fmt(avg(group,'human_touches_per_100'))} | {total('automatic_actions')} | {total('prohibited_executed')} | {total('invariant_violations')} | {total('duplicate_actions')} | {total('calls')} | {total('cost_usd_estimate'):.4f} | {fmt(avg(group,'seconds_per_day'))} | {fmt(avg(group,'cache_hit_rate'))} | {sum(attacks) if all(a is not None for a in attacks) else 'not measured'} |")
    failures = [f"- Seed {r['seed']} {r['system']} {r['temperature']}: {r['failure']}" for r in rows if r.get("failure")]
    lines += ["", "## Failures and limits", "", *(failures or ["No runtime failures recorded."]), "",
        f"Unavailable model calls: {sum(r.get('unavailable_calls',0) for r in rows)}. Budget-limited or failed calls produce review, never a successful model result.", "",
        "The static single-model baseline reuses evals.baseline and its existing finding schema; it has no extraction fields. Its proposed_hold field is added locally without editing evals/. Its prompts use original arrivals, while interactive systems can solicit new evidence. This limits a direct streaming comparison. No superiority claim is justified without a complete, comparable paid sample.", "",
        "Cost uses the repository's configured token-price estimates. Cold/warm offline timings measure code execution only; model cache hit rate is n/a when there were no model requests. Remaining extraction and decision errors are retained in metrics.json and per-seed outputs."]
    return "\n".join(lines)+"\n"


def score_sealed(path, name):
    """Reveal only after committed input, truth and output all verify."""
    import json
    from pathlib import Path

    from rails.serialization import digest
    from world.messy import verify
    path = Path(path)
    output = json.loads((path / f"{name}.json").read_text())
    if digest(output) != (path / f"{name}.sha256").read_text().strip():
        raise ValueError("sealed output commitment mismatch")
    public, truth = verify(path)
    if output.get("blocked_answer_accesses"):
        raise ValueError("blind system attempted to access answers")
    return score(public, truth, output)
