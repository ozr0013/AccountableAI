"""Export judge-visible prompts from source without importing or running any agents."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "dashboard/lib/agent-prompts.json"


def tree(source: str) -> ast.Module:
    return ast.parse((ROOT / source).read_text())


def assignment(source: str, name: str) -> ast.expr:
    for node in tree(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    raise ValueError(f"{source}: missing {name}")


def section(title: str, source: str, text: str, note: str = "") -> dict:
    return {"title": title, "source": source, "text": text, "note": note}


def agreement_fields(kind: str) -> str:
    fields = assignment("agreement_agent/fields.py", kind)
    if not isinstance(fields, ast.List):
        raise ValueError(f"Unexpected agreement field list: {kind}")
    lines = []
    for spec in fields.elts:
        if not isinstance(spec, ast.Call) or not isinstance(spec.func, ast.Name) or spec.func.id != "Spec":
            raise ValueError("Unexpected agreement field specification")
        name, label, hint = [ast.literal_eval(arg) for arg in spec.args]
        required = next((ast.literal_eval(k.value) for k in spec.keywords if k.arg == "required"), True)
        lines.append(f"- {name}: {label} ({hint})" + ("" if required else " [optional]"))
    return "\n".join(lines)


def chaser_template(source: str) -> str:
    function = next(n for n in tree(source).body if isinstance(n, ast.FunctionDef) and n.name == "system_prompt")
    returned = next(n.value for n in function.body if isinstance(n, ast.Return))
    if not isinstance(returned, ast.JoinedStr):
        raise ValueError("Chaser prompt format changed; update the prompt exporter")
    parts = []
    for value in returned.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue) and ast.unparse(value.value) == "_sending()":
            parts.append("{delivery_policy}")
        else:
            raise ValueError("Unknown dynamic value in chaser prompt")
    return "".join(parts)


def export() -> list[dict]:
    agreement = "agreement_agent/graph.py"
    chaser = "chaser_agent/llm_chaser.py"
    anomaly = "anomaly_agent/agent.py"
    verifier = "agents/verifier/verifier.py"
    agreement_system = ast.literal_eval(assignment(agreement, "SYSTEM"))
    chasing = "chaser_agent/skills/chasing/SKILL.md"
    payment_review = "chaser_agent/skills/payment_review/SKILL.md"
    kind_rules = ast.literal_eval(assignment(verifier, "KIND_RULES"))
    return [
        {"id": "agreement_agent", "name": "Agreement agent", "actors": ["agreement_agent", "agreement"],
         "role": "Reads agreements and invoices, extracts terms with verbatim citations, and asks the controller to resolve missing or unclear fields.",
         "sections": [section("System prompt · agreements", agreement, agreement_system.format(fields=agreement_fields("AGREEMENT"))),
                      section("System prompt · invoices", agreement, agreement_system.format(fields=agreement_fields("INVOICE")))]},
        {"id": "chaser", "name": "Chaser agent", "actors": ["chaser", "chaser_agent"],
         "role": "Follows each invoice’s approved reminder schedule, checks payment evidence, and hands payments to the Anomaly agent for review.",
         "sections": [section("System prompt template", chaser, chaser_template(chaser),
                              "At runtime, {delivery_policy} is replaced by the current reminder and escalation delivery settings."),
                      section("Chasing skill instructions", chasing, (ROOT / chasing).read_text())]},
        {"id": "anomaly", "name": "Anomaly agent", "actors": ["anomaly", "anomaly_agent", "anomaly_detector"],
         "role": "Checks a payment’s recipient, account, amount, timing, and statement; records a supported verdict for the controller.",
         "sections": [section("System prompt", anomaly, ast.literal_eval(assignment(anomaly, "SYSTEM"))),
                      section("Payment review skill instructions", payment_review, (ROOT / payment_review).read_text())]},
        {"id": "verifier", "name": "Verifier agent", "actors": ["verifier"],
         "role": "Tries to disprove each alert against its evidence. Checks citations and source trust, and blocks alerts when verification fails.",
         "sections": [section("System prompt", verifier, ast.literal_eval(assignment(verifier, "SYSTEM"))),
                      section("Alert-specific checks", verifier, "\n\n".join(
                          f"{kind}\nAssertion: {assertion}\nEvidence that would refute it: {refutation}"
                          for kind, (assertion, refutation) in kind_rules.items()))]},
    ]


if __name__ == "__main__":
    content = json.dumps(export(), indent=2, ensure_ascii=False) + "\n"
    if not OUTPUT.exists() or OUTPUT.read_text() != content:
        OUTPUT.write_text(content)
    print("Exported prompts for Agreement, Chaser, Anomaly, and Verifier.")
