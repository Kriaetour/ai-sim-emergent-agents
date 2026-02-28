"""
test_parse_logs.py — pytest suite for parse_logs.py
====================================================
Covers: extract_run_id, parse_line, parse_file, and the main() end-to-end path.
"""

import csv
import sys
from pathlib import Path

import pytest

import parse_logs
from parse_logs import extract_run_id, parse_line, parse_file, main


# ─────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────

EXPECTED_1234 = {"tick": 1234, "pop": 456, "factions": 7, "tension": 183.4}


# ─────────────────────────────────────────────────────
# extract_run_id
# ─────────────────────────────────────────────────────

class TestExtractRunId:
    def test_date_stamped_filename(self):
        assert extract_run_id("run_20260227_054559.txt") == "20260227_054559"

    def test_short_numeric_id(self):
        assert extract_run_id("run_042.txt") == "042"

    def test_unrecognised_filename_no_numeric_content(self):
        # Filename has no 'run_' prefix — full stem is returned
        assert extract_run_id("chronicle.txt") == "chronicle"


# ─────────────────────────────────────────────────────
# parse_line
# ─────────────────────────────────────────────────────

class TestParseLine:
    def test_variant_1_bracket_tick(self):
        line = "[Tick 1234] Pop: 456 | Factions: 7 | Tension: 183.4"
        assert parse_line(line) == EXPECTED_1234

    def test_variant_2_equals_delimited(self):
        line = "Tick=1234, Pop=456, Factions=7, Tension=183.4"
        assert parse_line(line) == EXPECTED_1234

    def test_variant_3_bracket_t_colon(self):
        line = "[T:1234] population=456 factions=7 tension=183.4"
        assert parse_line(line) == EXPECTED_1234

    def test_variant_4_pipe_delimited(self):
        line = "Tick 1234 | Pop 456 | Factions 7 | Tension 183.4"
        assert parse_line(line) == EXPECTED_1234

    def test_narrative_line_returns_none(self):
        assert parse_line("Era shift: The Dark Season begins.") is None

    def test_empty_string_returns_none(self):
        assert parse_line("") is None

    def test_float_tension_precision(self):
        result = parse_line("[Tick 1] Pop: 2 | Factions: 3 | Tension: 99.99")
        assert result is not None
        assert result["tension"] == pytest.approx(99.99)

    def test_case_insensitive_field_names(self):
        # All-caps variant 1
        line = "[TICK 1234] POP: 456 | FACTIONS: 7 | TENSION: 183.4"
        assert parse_line(line) == EXPECTED_1234

    def test_case_insensitive_population_keyword(self):
        # Variant 3 uses 'Population' capitalised
        line = "[T:1234] Population=456 Factions=7 Tension=183.4"
        assert parse_line(line) == EXPECTED_1234


# ─────────────────────────────────────────────────────
# parse_file
# ─────────────────────────────────────────────────────

_ALL_VARIANTS = (
    "[Tick 1] Pop: 10 | Factions: 2 | Tension: 1.0\n"
    "Tick=2, Pop=20, Factions=3, Tension=2.0\n"
    "[T:3] population=30 factions=4 tension=3.0\n"
    "Tick 4 | Pop 40 | Factions 5 | Tension 4.0\n"
)


class TestParseFile:
    def test_all_four_variants_parsed(self, tmp_path):
        f = tmp_path / "run_test.txt"
        f.write_text(_ALL_VARIANTS, encoding="utf-8")
        rows = parse_file(f)
        assert len(rows) == 4
        # Exact dict equality (no run_id field)
        assert rows[0] == {"tick": 1, "pop": 10, "factions": 2, "tension": pytest.approx(1.0)}
        assert rows[3] == {"tick": 4, "pop": 40, "factions": 5, "tension": pytest.approx(4.0)}

    def test_warning_emitted_for_data_like_unmatched_line(self, tmp_path, capsys):
        f = tmp_path / "run_warn.txt"
        # Contains 'tick' and 'tension' keywords but matches no pattern
        f.write_text("tick and tension data but no numbers\n", encoding="utf-8")
        parse_file(f)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "1" in captured.out

    def test_empty_result_for_non_data_lines_only(self, tmp_path):
        f = tmp_path / "run_empty.txt"
        f.write_text(
            "Era shift: The Dark Season begins.\n"
            "Chronicle: The First Kings united the northern reaches.\n",
            encoding="utf-8",
        )
        rows = parse_file(f)
        assert rows == []


# ─────────────────────────────────────────────────────
# End-to-end: main()
# ─────────────────────────────────────────────────────

_LOG_A = (
    "[Tick 1] Pop: 100 | Factions: 2 | Tension: 10.0\n"
    "[Tick 2] Pop: 110 | Factions: 2 | Tension: 11.0\n"
)
_LOG_B = (
    "Tick=1, Pop=200, Factions=3, Tension=20.0\n"
    "Tick=2, Pop=210, Factions=3, Tension=21.0\n"
)


def _write_logs(tmp_path):
    (tmp_path / "run_20260101_000001.txt").write_text(_LOG_A, encoding="utf-8")
    (tmp_path / "run_20260101_000002.txt").write_text(_LOG_B, encoding="utf-8")


class TestMain:
    def test_generates_valid_csv(self, tmp_path, monkeypatch):
        _write_logs(tmp_path)
        output = tmp_path / "results.csv"
        monkeypatch.setattr(sys, "argv", [
            "parse_logs.py", "--log-dir", str(tmp_path), "--output", str(output),
        ])
        main()
        assert output.exists()
        with open(output, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 4
        assert set(rows[0].keys()) == {"run_id", "tick", "pop", "factions", "tension"}

    def test_correct_run_ids_in_output(self, tmp_path, monkeypatch):
        _write_logs(tmp_path)
        output = tmp_path / "results.csv"
        monkeypatch.setattr(sys, "argv", [
            "parse_logs.py", "--log-dir", str(tmp_path), "--output", str(output),
        ])
        main()
        with open(output, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        run_ids = {r["run_id"] for r in rows}
        assert "20260101_000001" in run_ids
        assert "20260101_000002" in run_ids

    def test_rows_sorted_by_run_id_then_tick(self, tmp_path, monkeypatch):
        # Write files in reverse order to confirm sort is applied by content, not discovery
        (tmp_path / "run_20260101_000002.txt").write_text(_LOG_A, encoding="utf-8")
        (tmp_path / "run_20260101_000001.txt").write_text(_LOG_B, encoding="utf-8")
        output = tmp_path / "results.csv"
        monkeypatch.setattr(sys, "argv", [
            "parse_logs.py", "--log-dir", str(tmp_path), "--output", str(output),
        ])
        main()
        with open(output, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        run_ids = [r["run_id"] for r in rows]
        assert run_ids == sorted(run_ids)
        for rid in set(run_ids):
            ticks = [int(r["tick"]) for r in rows if r["run_id"] == rid]
            assert ticks == sorted(ticks)

    def test_exits_nonzero_on_empty_log_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "parse_logs.py", "--log-dir", str(tmp_path),
        ])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0
