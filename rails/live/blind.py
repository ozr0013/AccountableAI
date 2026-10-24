"""Separate offline system process: refuse private truth and answer-module access."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("system")
    parser.add_argument("name")
    parser.add_argument("--no-counterparts", action="store_true")
    args = parser.parse_args()
    from workflow.sealed.blind_run import _attempts, install_guard
    repo = Path(__file__).resolve().parents[2]
    install_guard([str(args.path / "truth.json"), str(repo / "world/scenario_spec.json"),
                   str(repo / "evals/answer_key.json")])
    from rails.serialization import canonical, digest

    from .sim import attack_control, simulate
    public = json.loads((args.path / "input.json").read_text())
    output = simulate(public, args.system, regression_dir=args.path / "regressions" / args.system,
                      counterparts=not args.no_counterparts)
    control = attack_control(public, output, args.system, counterparts=not args.no_counterparts)
    output.update(evidence_isolation="separate_process_guard", blocked_answer_accesses=list(_attempts))
    (args.path / f"{args.name}.json").write_text(canonical(output)+"\n")
    (args.path / f"{args.name}.sha256").write_text(digest(output)+"\n")
    (args.path / f"{args.name}-control.json").write_text(canonical(control)+"\n")


if __name__ == "__main__":
    main()
