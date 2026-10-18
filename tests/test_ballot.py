import json
from fractions import Fraction
from pathlib import Path

from ballot.cli import EXIT_INVALID, EXIT_OK, EXIT_TIE, main
from ballot.core import compare, count, parse

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "board.ballots"

IRV_TRANSFERS = """\
candidate A
candidate B
candidate C
candidate D
4 x A > B > C > D
3 x B > A > C > D
2 x C > A > B > D
1 x D > C > A > B
"""


def _election(text):
    election = parse(text)
    assert election.errors == []
    return election


def test_parse_declared_candidates_and_weights():
    election = _election("candidate Alice\ncandidate Bob\n3 x Alice > Bob\nBob > Alice\n")
    assert election.candidates == ["Alice", "Bob"]
    assert [(b.line, b.weight) for b in election.ballots] == [(3, 3), (4, 1)]
    assert election.ballots[0].ranks == (("Alice",), ("Bob",))
    assert election.total_weight == 4


def test_comments_and_blank_lines_are_ignored():
    election = _election("# header\n\ncandidate A   # first\ncandidate B\n\nA > B  # ballot\n")
    assert len(election.ballots) == 1
    assert election.ballots[0].ranks == (("A",), ("B",))


def test_equal_sign_puts_candidates_on_one_rank():
    election = _election("candidate A\ncandidate B\ncandidate C\nB = C > A\n")
    assert election.ballots[0].ranks == (("B", "C"), ("A",))


def test_names_resolve_to_declared_spelling_case_insensitively():
    election = _election("candidate Alice\ncandidate Bob\nalice > BOB\n")
    assert election.ballots[0].ranks == (("Alice",), ("Bob",))


def test_every_error_is_reported_with_its_line_number():
    text = (
        "candidate A\n"
        "candidate B\n"
        "A > Z\n"       # line 3: unknown candidate
        "A > A\n"       # line 4: ranked twice
        "2 x B >\n"     # line 5: empty slot
        "0 x A\n"       # line 6: count below 1
        "B > A\n"
    )
    election = parse(text)
    assert sorted(line for line, _ in election.errors) == [3, 4, 5, 6]
    assert "unknown candidate 'Z'" in dict(election.errors)[3]
    assert len(election.ballots) == 1


def test_duplicate_declaration_is_an_error():
    election = parse("candidate A\ncandidate a\nA > A\n")
    assert election.errors[0][0] == 2
    assert "declared more than once" in election.errors[0][1]


def test_empty_file_is_an_error():
    election = parse("candidate A\ncandidate B\n")
    assert (None, "no valid ballots found") in election.errors


def test_undeclared_candidates_are_inferred_in_first_seen_order():
    election = _election("A > B\nB > C\n")
    assert election.candidates == ["A", "B", "C"]


def test_warnings_flag_duplicate_ballots_and_unranked_candidates():
    election = parse("candidate A\ncandidate B\ncandidate C\ncandidate D\nA > B\nA > B\n")
    assert election.errors == []
    messages = [message for _, message in election.warnings]
    assert any("same ranking as line 5" in message for message in messages)
    assert any("'C' is never ranked" in message for message in messages)
    assert any("'D' is never ranked" in message for message in messages)


def test_plurality_splits_a_tied_top_rank_exactly():
    election = _election("candidate A\ncandidate B\n1 x A = B\n2 x B > A\n")
    result = count(election, "plurality")
    assert result["scores"]["A"] == Fraction(1, 2)
    assert result["scores"]["B"] == Fraction(5, 2)
    assert result["winners"] == ["B"]


def test_irv_on_example_eliminates_weakest_and_transfers_votes():
    election = _election(EXAMPLE.read_text(encoding="utf-8"))
    result = count(election, "irv")
    assert result["winners"] == ["Carol"]
    assert [round_["eliminated"] for round_ in result["rounds"]] == ["Dan", "Bob", None]
    assert result["rounds"][0]["tallies"]["Carol"] == Fraction(7, 2)
    assert result["rounds"][2]["tallies"] == {"Alice": Fraction(4), "Carol": Fraction(9)}


def test_irv_majority_on_first_round_stops_early():
    election = _election("candidate A\ncandidate B\n3 x A > B\n1 x B > A\n")
    result = count(election, "irv")
    assert result["winners"] == ["A"]
    assert len(result["rounds"]) == 1


def test_irv_dead_heat_is_reported_as_a_tie():
    election = _election("candidate A\ncandidate B\n1 x A > B\n1 x B > A\n")
    result = count(election, "irv")
    assert result["tied"] is True
    assert sorted(result["winners"]) == ["A", "B"]


def test_irv_last_place_tie_is_broken_by_earlier_round():
    election = _election(IRV_TRANSFERS)
    result = count(election, "irv")
    assert result["winners"] == ["A"]
    assert len(result["rounds"]) == 3
    assert result["tiebreaks"] == [{
        "round": 2,
        "candidates": ["B", "C"],
        "eliminated": "C",
        "method": "earlier round tally",
    }]


def test_irv_unresolvable_tie_uses_seeded_draw_repeatably():
    election = _election("candidate A\ncandidate B\ncandidate C\ncandidate D\n"
                         "4 x A > D\n4 x D > A\n2 x B > C\n2 x C > B\n")
    first = count(election, "irv", seed=7)
    second = count(election, "irv", seed=7)
    assert first["tiebreaks"][0]["method"] == "seeded random draw"
    assert first["tiebreaks"] == second["tiebreaks"]
    assert first["winners"] == second["winners"]


def test_borda_averages_points_across_tied_ranks():
    election = _election("candidate A\ncandidate B\ncandidate C\n1 x A = B > C\n1 x C > A\n")
    result = count(election, "borda")
    assert result["scores"] == {"A": Fraction(5, 2), "B": Fraction(3, 2), "C": Fraction(2)}
    assert result["winners"] == ["A"]


def test_borda_on_example_scores_unranked_as_zero_points():
    election = _election(EXAMPLE.read_text(encoding="utf-8"))
    result = count(election, "borda")
    assert result["winners"] == ["Bob"]
    assert result["scores"]["Bob"] == 26
    assert result["scores"]["Carol"] == Fraction(51, 2)
    assert result["scores"]["Alice"] == 15
    assert result["scores"]["Dan"] == Fraction(23, 2)


def test_condorcet_winner_beats_every_rival_on_example():
    election = _election(EXAMPLE.read_text(encoding="utf-8"))
    result = count(election, "condorcet")
    assert result["winners"] == ["Bob"]
    assert result["condorcet_winner"] is True
    assert result["matrix"]["Alice"]["Bob"] == 4
    assert result["matrix"]["Bob"]["Alice"] == 9


def test_condorcet_cycle_has_no_winner_and_ties_on_copeland():
    election = _election("candidate A\ncandidate B\ncandidate C\n"
                         "1 x A > B > C\n1 x B > C > A\n1 x C > A > B\n")
    result = count(election, "condorcet")
    assert result["condorcet_winner"] is False
    assert sorted(result["winners"]) == ["A", "B", "C"]
    assert result["tied"] is True


def test_compare_runs_every_method_on_the_same_ballots():
    election = _election(EXAMPLE.read_text(encoding="utf-8"))
    results = compare(election)
    assert set(results) == {"plurality", "irv", "borda", "condorcet"}
    assert results["plurality"]["winners"] == ["Alice"]
    assert results["irv"]["winners"] == ["Carol"]


def test_check_passes_on_example_and_reports_its_tie_warning(tmp_path, capsys):
    assert main(["check", str(EXAMPLE)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "OK: 5 ballot(s), total weight 13, 4 candidate(s), 1 warning(s)" in out
    assert f"{EXAMPLE}:11: warning: tie at the top rank" in out


def test_check_strict_fails_on_warnings(tmp_path):
    path = tmp_path / "warn.ballots"
    path.write_text("candidate A\ncandidate B\ncandidate C\nA > B\n", encoding="utf-8")
    assert main(["check", str(path)]) == EXIT_OK
    assert main(["check", "--strict", str(path)]) == EXIT_INVALID


def test_count_refuses_invalid_files_with_line_numbers(tmp_path, capsys):
    path = tmp_path / "bad.ballots"
    path.write_text("candidate A\ncandidate B\nA > Z\n", encoding="utf-8")
    assert main(["count", str(path)]) == EXIT_INVALID
    assert f"{path}:3: error: unknown candidate 'Z'" in capsys.readouterr().err


def test_count_returns_tie_exit_code_and_refuses_no_winner(tmp_path):
    path = tmp_path / "tie.ballots"
    path.write_text("candidate A\ncandidate B\n1 x A > B\n1 x B > A\n", encoding="utf-8")
    assert main(["count", "--method", "irv", str(path)]) == EXIT_TIE


def test_count_json_output_is_exact_strings(tmp_path, capsys):
    assert main(["count", str(EXAMPLE), "--method", "borda", "--format", "json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    result = data["results"]["borda"]
    assert result["winners"] == ["Bob"]
    assert result["scores"]["Carol"] == "51/2"
    assert data["total_weight"] == 13


def test_compare_text_summary_flags_disagreement(capsys):
    assert main(["compare", str(EXAMPLE)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Methods disagree" in out
    assert "Instant-runoff (IRV)" in out
