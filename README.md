# Ballot

**Count ranked-choice votes from one plain-text file, offline, with every round and tie on the record.**

Ballot keeps an election in a readable text file: one line per ballot, with `>` for "preferred to" and `=` for a tie on the same rank. It checks the file line by line, then counts it with plurality, instant-runoff (IRV), Borda, or Condorcet, and shows the round-by-round working. Nothing is hidden: exact fractions, not floats, and every tie-break is reported.

## Features

- **Plain-text ballots.** One ballot per line, `3 x Alice > Bob > Carol` means three identical ballots. Plain `#` comments and blank lines are allowed.
- **Equal rankings.** `Bob = Carol > Alice` puts two candidates on the same rank, and partial ballots that skip candidates are fine.
- **Declared or inferred candidates.** Declare them with `candidate Name` to catch typos, or leave them out and the names are taken from the ballots.
- **Case-insensitive names.** `alice` and `Alice` count as the same candidate, and the declared spelling is used in output.
- **Line-numbered errors.** `check` and `count` report every problem at once as `FILE:LINE: error: ...`: unknown names, repeats, empty slots, bad counts, and duplicate declarations.
- **Lint warnings.** Flags split top ranks, ballots that repeat an earlier line, and candidates nobody ranked. `check --strict` turns warnings into a failing exit code.
- **Plurality.** First-choice count, with tied top ranks split exactly.
- **Instant-runoff (IRV).** Round-by-round tallies, exhausted-ballot counts, early majority detection, and elimination of the weakest candidate.
- **Borda count.** Points from the ranking, with tied ranks sharing their points and unranked candidates scoring zero.
- **Condorcet analysis.** Full pairwise matrix, Condorcet winner detection, Copeland scores, and a clear "the votes form a cycle" verdict when there is no winner.
- **Exact arithmetic.** All tallies use `fractions.Fraction`, so `7/2` is `7/2`, not a rounded float.
- **Honest ties.** A dead heat is reported as a tie with no invented winner. Tie-breaks in IRV are explained: first by earlier-round tallies, then by a seeded draw (`--seed`) that is always listed.
- **Side-by-side comparison.** `compare` runs all four methods and says whether they agree.
- **Machine-readable output.** `--format json` emits every round, score and matrix as exact strings.
- **CI-friendly exit codes.** `0` clear result, `1` invalid input or failed strict check, `3` no single winner.

## Quick start

Save this as `board.ballots`, or use the copy in `examples/board.ballots`:

```text
# Board election, 2026: choose one chair.
candidate Alice
candidate Bob
candidate Carol
candidate Dan

4 x Alice > Bob > Carol > Dan
3 x Bob > Carol > Alice > Dan
3 x Carol > Bob > Dan > Alice
2 x Dan > Carol > Bob > Alice
1 x Carol = Dan > Bob > Alice    # equal rankings share a rank
```

Then run:

```console
$ python -m ballot count examples/board.ballots
Ballots: 5 (total weight 13); candidates: Alice, Bob, Carol, Dan
Method: Instant-runoff (IRV)
Round 1: Alice 4 | Bob 3 | Carol 7/2 (3.500) | Dan 5/2 (2.500) | exhausted 0
  eliminated: Dan
Round 2: Alice 4 | Bob 3 | Carol 6 | exhausted 0
  eliminated: Bob
Round 3: Alice 4 | Carol 9 | exhausted 0
Winner: Carol
```

Compare every method:

```console
$ python -m ballot compare examples/board.ballots
Method                           Result
Plurality (first past the post)  Alice
Instant-runoff (IRV)             Carol
Borda count                      Bob
Condorcet (pairwise)             Bob
Methods disagree; run `count --method <name>` for the round-by-round detail.
```

Lint a file before counting it:

```console
$ python -m ballot check examples/board.ballots
examples/board.ballots:11: warning: tie at the top rank; its weight is split between those candidates
OK: 5 ballot(s), total weight 13, 4 candidate(s), 1 warning(s)
```

A typo is caught with its line number and the count is refused. Given this `broken.ballots`:

```text
candidate Alice
candidate Bob
Alice > Bob
Bob > Zed
```

```console
$ python -m ballot count broken.ballots
broken.ballots:4: error: unknown candidate 'Zed'
ballot: refusing to count broken.ballots until its errors are fixed
```

## Ballot file format

| Line | Meaning |
| --- | --- |
| `candidate Alice` | Declare a candidate (optional, but unknown names are then errors). |
| `Alice > Bob > Carol` | One ballot, best first. |
| `3 x Alice > Bob` | Three identical ballots. |
| `Bob = Carol > Alice` | Bob and Carol share a rank. |
| `Alice > Bob  # note` | Text after `#` is ignored. |

## How to run

Requires Python 3.9 or newer. There are no runtime dependencies and no network access.

```console
python -m ballot check FILE [--strict]
python -m ballot count FILE [--method {plurality,irv,borda,condorcet}] [--seed N] [--format {text,json}]
python -m ballot compare FILE [--seed N] [--format {text,json}]
```

Use `-` in place of `FILE` to read from standard input. The default method is `irv`.

## How to test

The tests use pytest. Install it once, then run the suite from the project root:

```console
python -m pip install pytest
python -m pytest
```

The suite covers parsing, error line numbers, warnings, each counting method (including exact ties, IRV transfers, backward and seeded tie-breaks, and Condorcet cycles), and the CLI exit codes and JSON output.

## Project layout

```text
ballot/
  core.py       parser and counting methods
  cli.py        check / count / compare commands
  __main__.py   python -m ballot entry point
examples/
  board.ballots sample election
tests/
  test_ballot.py
```
