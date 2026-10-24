"""Sealed (commit-reveal) evaluation: prove the agents never saw the answers, and that the
answers weren't changed after the results were in.

    python -m workflow.sealed                 # seal -> blind run (separate process) -> reveal
    python -m workflow.sealed --dir out/ --n-clean 100

1. seal    builds the world, writes the system's only input (events.jsonl) and a commitment:
           SHA-256 of every answer file (evals/answer_key.json, world/scenario_spec.json and the
           world's planted truth) plus the inputs.
2. run     a separate process gets events.jsonl and nothing else. An audit hook refuses to open
           any sealed file and an import blocker refuses evals/ and world/. It writes
           outputs.json and its SHA-256.
3. reveal  re-hashes answers, inputs and outputs against the commitment; only if all match does
           it score the outputs with Omar's scorer and answer key (read-only).

Hashing doesn't hide anything. Process isolation is what hides the answers; the hashes prove
nobody edited them (or the outputs) between the run and the grading.
"""

from .seal import seal

__all__ = ["seal"]
