"""Parse plain-text ranked ballots and count them.

Ballot file format (``#`` starts a comment, blank lines are ignored)::

    candidate Alice          # optional: declare the candidates
    candidate Bob
    candidate Carol

    3 x Alice > Bob > Carol  # three voters: Alice first, then Bob, then Carol
    Bob = Carol > Alice      # "=" puts candidates on the same rank
    Carol                    # a ballot may rank only some candidates

Counting uses ``fractions.Fraction`` so every number is exact, and every tie
is reported in the result instead of being silently resolved. The only
randomness is a seeded draw used when an IRV elimination cannot be decided
from earlier rounds; every such draw is recorded in ``tiebreaks``.
"""

import random
import re
from dataclasses import dataclass, field
from fractions import Fraction

COMMENT_RE = re.compile(r"#.*$")
DECLARE_RE = re.compile(r"^candidate\s+(.+)$", re.IGNORECASE)
COUNT_RE = re.compile(r"^(\d+)\s*x\s+(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class Ballot:
    line: int
    weight: int
    ranks: tuple  # tuple of tuples, best rank first; tied candidates share a tuple


@dataclass
class Election:
    candidates: list
    ballots: list
    errors: list = field(default_factory=list)    # (line or None, message)
    warnings: list = field(default_factory=list)  # (line or None, message)

    @property
    def total_weight(self):
        return sum(ballot.weight for ballot in self.ballots)


def parse(text):
    """Parse ballot text into an Election, collecting every error and warning."""
    declared = []
    lookup = {}  # casefolded name -> canonical name
    errors, warnings = [], []
    pending = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        content = COMMENT_RE.sub("", raw).strip()
        if not content:
            continue
        match = DECLARE_RE.match(content)
        if not match:
            pending.append((lineno, content))
            continue
        name = match.group(1).strip()
        if name.casefold() in lookup:
            errors.append((lineno, f"candidate {name!r} is declared more than once"))
        else:
            lookup[name.casefold()] = name
            declared.append(name)

    strict = bool(declared)
    ballots = []
    for lineno, content in pending:
        ballot = _parse_ballot(lineno, content, lookup, strict, errors)
        if ballot is not None:
            ballots.append(ballot)

    candidates = list(declared)
    for ballot in ballots:
        for group in ballot.ranks:
            for name in group:
                if name not in candidates:
                    candidates.append(name)

    if not ballots:
        errors.append((None, "no valid ballots found"))
    if len(candidates) < 2:
        errors.append((None, "need at least two candidates"))

    ranked = {name for ballot in ballots for group in ballot.ranks for name in group}
    for name in candidates:
        if name not in ranked:
            warnings.append((None, f"candidate {name!r} is never ranked"))
    first_seen = {}
    for ballot in ballots:
        if len(ballot.ranks[0]) > 1:
            warnings.append((ballot.line, "tie at the top rank; its weight is split between those candidates"))
        if ballot.ranks in first_seen:
            warnings.append((ballot.line, f"same ranking as line {first_seen[ballot.ranks]}"))
        else:
            first_seen[ballot.ranks] = ballot.line

    errors.sort(key=lambda item: (item[0] is None, item[0] or 0))
    warnings.sort(key=lambda item: (item[0] is None, item[0] or 0))
    return Election(candidates, ballots, errors, warnings)


def _parse_ballot(lineno, content, lookup, strict, errors):
    """Return a Ballot, or None after appending its errors."""
    weight = 1
    body = content
    match = COUNT_RE.match(content)
    if match:
        weight = int(match.group(1))
        body = match.group(2)
        if weight < 1:
            errors.append((lineno, "ballot count must be at least 1"))
            return None

    ranks = []
    seen = set()
    valid = True
    for group_text in body.split(">"):
        group = []
        for part in group_text.split("="):
            name = part.strip()
            if not name:
                errors.append((lineno, "empty slot in ranking (check for a stray '>' or '=')"))
                valid = False
                continue
            key = name.casefold()
            if key not in lookup:
                if strict:
                    errors.append((lineno, f"unknown candidate {name!r}"))
                    valid = False
                    continue
                lookup[key] = name
            canonical = lookup[key]
            if canonical in seen:
                errors.append((lineno, f"{canonical!r} is ranked more than once"))
                valid = False
                continue
            seen.add(canonical)
            group.append(canonical)
        if group:
            ranks.append(tuple(group))
    if not valid:
        return None
    return Ballot(lineno, weight, tuple(ranks))


def _top_group(ranks, live):
    """Best-ranked candidates on a ballot that are still in the running."""
    for group in ranks:
        choices = [name for name in group if name in live]
        if choices:
            return choices
    return []


def _round_tallies(election, remaining):
    """Weight each remaining candidate receives; a tied top rank splits its share."""
    live = set(remaining)
    tallies = {name: Fraction(0) for name in remaining}
    exhausted = 0
    for ballot in election.ballots:
        top = _top_group(ballot.ranks, live)
        if not top:
            exhausted += ballot.weight
            continue
        share = Fraction(ballot.weight, len(top))
        for name in top:
            tallies[name] += share
    return tallies, exhausted


def _result(method, winners, **extra):
    return {"method": method, "winners": list(winners), "tied": len(winners) != 1, **extra}


def _by_score(method, scores, **extra):
    best = max(scores.values())
    winners = [name for name in scores if scores[name] == best]
    ranking = sorted(scores, key=lambda name: (-scores[name], name))
    return _result(method, winners, scores=scores, ranking=ranking, **extra)


def plurality(election, seed=0):
    """First-choice count only. Ties on the top rank split the ballot's weight."""
    tallies, _ = _round_tallies(election, election.candidates)
    return _by_score("plurality", tallies)


def _break_last_place(tied, rounds, rng):
    """Pick one loser from candidates tied for last place.

    Walks back through earlier rounds and drops the candidates that were
    weakest the last time they differed. If that never separates them, a
    seeded draw decides.
    """
    candidates = list(tied)
    for past in reversed(rounds[:-1]):
        values = {name: past["tallies"][name] for name in candidates}
        low = min(values.values())
        if low == max(values.values()):
            continue
        candidates = [name for name in candidates if values[name] == low]
        if len(candidates) == 1:
            return candidates[0], "earlier round tally"
    return rng.choice(candidates), "seeded random draw"


def irv(election, seed=0):
    """Instant-runoff: eliminate the weakest candidate until someone has a majority."""
    rng = random.Random(seed)
    remaining = list(election.candidates)
    rounds = []
    tiebreaks = []
    while True:
        tallies, exhausted = _round_tallies(election, remaining)
        total = sum(tallies.values(), Fraction(0))
        rounds.append({"tallies": tallies, "exhausted": exhausted, "eliminated": None})

        majority = [name for name in remaining if 2 * tallies[name] > total]
        if majority:
            return _result("irv", majority[:1], rounds=rounds, tiebreaks=tiebreaks)
        low, high = min(tallies.values()), max(tallies.values())
        if low == high:
            # Covers a single survivor and a dead heat between everyone left.
            return _result("irv", remaining, rounds=rounds, tiebreaks=tiebreaks)

        lowest = [name for name in remaining if tallies[name] == low]
        if len(lowest) == 1:
            loser, how = lowest[0], "lowest tally"
        else:
            loser, how = _break_last_place(lowest, rounds, rng)
            tiebreaks.append({
                "round": len(rounds),
                "candidates": lowest,
                "eliminated": loser,
                "method": how,
            })
        rounds[-1]["eliminated"] = loser
        remaining.remove(loser)


def borda(election, seed=0):
    """Borda count: the top of an n-candidate ballot scores n-1, unranked score 0.

    Candidates sharing a rank split the points for the positions they occupy.
    """
    count = len(election.candidates)
    scores = {name: Fraction(0) for name in election.candidates}
    for ballot in election.ballots:
        position = 0
        for group in ballot.ranks:
            points = Fraction(sum(count - 1 - index for index in range(position, position + len(group))), len(group))
            for name in group:
                scores[name] += points * ballot.weight
            position += len(group)
    return _by_score("borda", scores)


def pairwise(election):
    """Matrix where matrix[a][b] is the weight of ballots ranking a strictly above b.

    A ranked candidate is preferred to an unranked one; two unranked
    candidates, or two on the same rank, are not preferred either way.
    """
    candidates = election.candidates
    matrix = {a: {b: 0 for b in candidates} for a in candidates}
    for ballot in election.ballots:
        position = {}
        for index, group in enumerate(ballot.ranks):
            for name in group:
                position[name] = index
        for a in candidates:
            for b in candidates:
                if a == b or a not in position:
                    continue
                if b not in position or position[a] < position[b]:
                    matrix[a][b] += ballot.weight
    return matrix


def condorcet(election, seed=0):
    """Condorcet winner if one exists; otherwise the Copeland leaders."""
    candidates = election.candidates
    matrix = pairwise(election)
    copeland = {}
    for a in candidates:
        score = Fraction(0)
        for b in candidates:
            if a == b:
                continue
            if matrix[a][b] > matrix[b][a]:
                score += 1
            elif matrix[a][b] == matrix[b][a]:
                score += Fraction(1, 2)
        copeland[a] = score

    beats_all = [a for a in candidates
                 if all(matrix[a][b] > matrix[b][a] for b in candidates if b != a)]
    if beats_all:
        return _result("condorcet", beats_all, scores=copeland, matrix=matrix,
                       condorcet_winner=True, ranking=sorted(candidates, key=lambda n: (-copeland[n], n)))
    best = max(copeland.values())
    leaders = [name for name in candidates if copeland[name] == best]
    return _result("condorcet", leaders, scores=copeland, matrix=matrix,
                   condorcet_winner=False, ranking=sorted(candidates, key=lambda n: (-copeland[n], n)))


COUNTERS = {
    "plurality": plurality,
    "irv": irv,
    "borda": borda,
    "condorcet": condorcet,
}
METHODS = tuple(COUNTERS)
METHOD_NAMES = {
    "plurality": "Plurality (first past the post)",
    "irv": "Instant-runoff (IRV)",
    "borda": "Borda count",
    "condorcet": "Condorcet (pairwise)",
}


def count(election, method, seed=0):
    if method not in COUNTERS:
        raise ValueError(f"unknown method {method!r}")
    return COUNTERS[method](election, seed)


def compare(election, seed=0):
    return {method: count(election, method, seed) for method in METHODS}
