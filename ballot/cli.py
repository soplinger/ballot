"""Command-line interface: ``python -m ballot check|count|compare FILE``."""

import argparse
import json
import sys
from fractions import Fraction

from .core import METHOD_NAMES, METHODS, compare, count, parse

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_TIE = 3


def fmt(value):
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator} ({float(value):.3f})"


def _jsonable(value):
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _where(name, line):
    return f"{name}:{line}" if line else name


def _report(name, election, stream):
    for line, message in election.errors:
        print(f"{_where(name, line)}: error: {message}", file=stream)
    for line, message in election.warnings:
        print(f"{_where(name, line)}: warning: {message}", file=stream)


def _matrix_lines(matrix):
    candidates = list(matrix)
    width = max(max(len(name) for name in candidates), 5) + 2
    lines = ["Pairwise votes (row ranked above column, by weight):",
             " " * width + "".join(f"{name:>{width}}" for name in candidates)]
    for a in candidates:
        cells = "".join(f"{'-' if a == b else matrix[a][b]:>{width}}" for b in candidates)
        lines.append(f"{a:<{width}}{cells}")
    return lines


def describe(result):
    """Human-readable lines for one method's result."""
    lines = [f"Method: {METHOD_NAMES[result['method']]}"]
    if "rounds" in result:
        for number, rnd in enumerate(result["rounds"], start=1):
            cells = " | ".join(f"{name} {fmt(tally)}" for name, tally in rnd["tallies"].items())
            lines.append(f"Round {number}: {cells} | exhausted {fmt(rnd['exhausted'])}")
            if rnd["eliminated"]:
                lines.append(f"  eliminated: {rnd['eliminated']}")
        for brk in result["tiebreaks"]:
            lines.append(
                f"Tie for last place in round {brk['round']}: {', '.join(brk['candidates'])}; "
                f"{brk['eliminated']} eliminated by {brk['method']}"
            )
    else:
        label = "Copeland score" if result["method"] == "condorcet" else "Score"
        lines.append(f"{label}:")
        for name in result["ranking"]:
            lines.append(f"  {name}: {fmt(result['scores'][name])}")
    if "matrix" in result:
        lines.extend(_matrix_lines(result["matrix"]))
        verdict = "yes" if result["condorcet_winner"] else "no (the votes form a cycle)"
        lines.append(f"Condorcet winner: {verdict}")
    if result["tied"]:
        lines.append(f"Tie between {', '.join(result['winners'])}: no single winner")
    else:
        lines.append(f"Winner: {result['winners'][0]}")
    return lines


def _summary(results):
    width = max(len(METHOD_NAMES[method]) for method in results) + 2
    lines = [f"{'Method':<{width}}Result"]
    for method, result in results.items():
        text = ", ".join(result["winners"]) + (" (tie)" if result["tied"] else "")
        lines.append(f"{METHOD_NAMES[method]:<{width}}{text}")
    agreed = len({tuple(result["winners"]) for result in results.values()}) == 1
    if agreed:
        lines.append(f"All methods agree on {', '.join(next(iter(results.values()))['winners'])}.")
    else:
        lines.append("Methods disagree; run `count --method <name>` for the round-by-round detail.")
    return lines


def _check(args, name, election):
    _report(name, election, sys.stdout)
    if election.errors or (args.strict and election.warnings):
        print(f"FAIL: {len(election.errors)} error(s), {len(election.warnings)} warning(s)")
        return EXIT_INVALID
    print(f"OK: {len(election.ballots)} ballot(s), total weight {election.total_weight}, "
          f"{len(election.candidates)} candidate(s), {len(election.warnings)} warning(s)")
    return EXIT_OK


def _run(args, name, election):
    _report(name, election, sys.stderr)
    if election.errors:
        print(f"ballot: refusing to count {name} until its errors are fixed", file=sys.stderr)
        return EXIT_INVALID

    if args.command == "count":
        results = {args.method: count(election, args.method, args.seed)}
    else:
        results = compare(election, args.seed)

    if args.format == "json":
        payload = {
            "ballots": len(election.ballots),
            "total_weight": election.total_weight,
            "candidates": election.candidates,
            "seed": args.seed,
            "results": results,
        }
        print(json.dumps(_jsonable(payload), indent=2))
    else:
        print(f"Ballots: {len(election.ballots)} (total weight {election.total_weight}); "
              f"candidates: {', '.join(election.candidates)}")
        if args.command == "count":
            for line in describe(results[args.method]):
                print(line)
        else:
            for line in _summary(results):
                print(line)

    if any(result["tied"] for result in results.values()):
        return EXIT_TIE
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ballot",
        description="Count ranked-choice ballots kept in one plain-text file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="lint a ballot file")
    check_p.add_argument("file", help="ballot file, or - for stdin")
    check_p.add_argument("--strict", action="store_true", help="treat warnings as failures")

    count_p = sub.add_parser("count", help="count with one method")
    count_p.add_argument("file", help="ballot file, or - for stdin")
    count_p.add_argument("--method", choices=METHODS, default="irv")
    count_p.add_argument("--seed", type=int, default=0, help="seed for random tie-breaks")
    count_p.add_argument("--format", choices=("text", "json"), default="text")

    compare_p = sub.add_parser("compare", help="count with every method side by side")
    compare_p.add_argument("file", help="ballot file, or - for stdin")
    compare_p.add_argument("--seed", type=int, default=0, help="seed for random tie-breaks")
    compare_p.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.file == "-":
        name, text = "<stdin>", sys.stdin.read()
    else:
        name = args.file
        try:
            with open(args.file, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print(f"ballot: {exc}", file=sys.stderr)
            return EXIT_INVALID

    election = parse(text)
    if args.command == "check":
        return _check(args, name, election)
    return _run(args, name, election)
