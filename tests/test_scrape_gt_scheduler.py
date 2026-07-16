"""
Unit tests for scripts/scrape_gt_scheduler.py.

These tests never touch the network: fetch_json is monkeypatched everywhere,
so they run the same in CI as they do locally. Run with:

    pytest tests/
"""
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "scrape_gt_scheduler.py"


def load_module():
    spec = importlib.util.spec_from_file_location("scrape_gt_scheduler", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """A fresh import of the script with COURSE_CSV/OUTPUT_CSV redirected into tmp_path."""
    m = load_module()
    monkeypatch.setattr(m, "COURSE_CSV", tmp_path / "course-eval-8_23.csv")
    monkeypatch.setattr(m, "OUTPUT_CSV", tmp_path / "gt_scheduler_sections.csv")
    return m


def write_course_csv(path: Path, codes: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["CODE", "AKA", "Foundational?"])  # header, only col 0 matters
        for code in codes:
            writer.writerow([code, "", ""])


# ---------------------------------------------------------------------------
# load_course_codes
# ---------------------------------------------------------------------------


def test_load_course_codes_dedupes_and_sorts(mod, tmp_path):
    write_course_csv(mod.COURSE_CSV, ["CS 6515", "CS 6035", "CS 6515", "  ", "CS 6300"])
    codes = mod.load_course_codes(mod.COURSE_CSV)
    assert codes == ["CS 6035", "CS 6300", "CS 6515"]


def test_load_course_codes_skips_blank_rows(mod):
    write_course_csv(mod.COURSE_CSV, ["CS 6035", "", "   "])
    codes = mod.load_course_codes(mod.COURSE_CSV)
    assert codes == ["CS 6035"]


# ---------------------------------------------------------------------------
# pick_latest_term
# ---------------------------------------------------------------------------


def test_pick_latest_term_picks_max(mod):
    payload = {"terms": [{"term": "202502"}, {"term": "202608"}, {"term": "202605"}]}
    assert mod.pick_latest_term(payload) == "202608"


def test_pick_latest_term_empty_returns_none(mod):
    assert mod.pick_latest_term({"terms": []}) is None
    assert mod.pick_latest_term({}) is None


# ---------------------------------------------------------------------------
# clean_instructor
# ---------------------------------------------------------------------------


def test_clean_instructor_strips_primary_marker(mod):
    assert mod.clean_instructor("Jane Thayer (P)") == "Jane Thayer"
    assert mod.clean_instructor("Bob Helper") == "Bob Helper"
    assert mod.clean_instructor("  Spacey Name (P)  ") == "Spacey Name"


# ---------------------------------------------------------------------------
# main() end-to-end, with fetch_json mocked
# ---------------------------------------------------------------------------

FAKE_INDEX = {"terms": [{"term": "202502"}, {"term": "202608"}, {"term": "202605"}]}

FAKE_TERM = {
    "courses": {
        "CS 6035": [
            "Introduction to Information Security",
            {
                "A": [
                    "84113",
                    [[0, "MW", "Some Building 103", 0, ["Jane Thayer (P)", "Bob Helper"], 0, -1, -1]],
                    3,
                    0,
                    0,
                    [0],
                    -1,
                ],
                "B": [
                    "92340",
                    [[1, "TR", "Some Building 104", 0, ["John Doe (P)"], 0, -1, -1]],
                    3,
                    0,
                    0,
                    [],
                    -1,
                ],
            },
        ],
    },
    "caches": {
        "periods": ["8:00 am - 9:15 am", "9:30 am - 10:45 am"],
        "locations": [],
    },
    "updatedAt": "2026-07-11T00:00:00.000Z",
    "version": 3,
}


def fake_fetch_json_factory(mod, term_payload=FAKE_TERM, index_payload=FAKE_INDEX):
    def fake_fetch_json(url):
        if url == mod.INDEX_URL:
            return index_payload
        if url == mod.TERM_URL_TMPL.format(term="202608"):
            return term_payload
        raise AssertionError(f"unexpected url in test: {url}")

    return fake_fetch_json


def test_main_happy_path_writes_matching_rows(mod, monkeypatch):
    write_course_csv(mod.COURSE_CSV, ["CS 6035", "CS 9999"])  # CS 9999 has no data on file
    monkeypatch.setattr(mod, "fetch_json", fake_fetch_json_factory(mod))

    rc = mod.main()
    assert rc == 0

    with mod.OUTPUT_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2  # section A and section B of CS 6035; CS 9999 skipped
    by_section = {r["section"]: r for r in rows}

    row_a = by_section["A"]
    assert row_a["course_code"] == "CS 6035"
    assert row_a["crn"] == "84113"
    assert row_a["days"] == "MW"
    assert row_a["time"] == "8:00 am - 9:15 am"
    assert row_a["instructors"] == "Jane Thayer; Bob Helper"
    assert row_a["term"] == "202608"

    row_b = by_section["B"]
    assert row_b["crn"] == "92340"
    assert row_b["days"] == "TR"
    assert row_b["instructors"] == "John Doe"


def test_main_no_matches_returns_error(mod, monkeypatch):
    write_course_csv(mod.COURSE_CSV, ["CS 0001", "CS 0002"])  # neither exists in FAKE_TERM
    monkeypatch.setattr(mod, "fetch_json", fake_fetch_json_factory(mod))

    rc = mod.main()
    assert rc == 1

    # the file should still be written, just with a header and no data rows
    with mod.OUTPUT_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == []


def test_main_index_fetch_failure_returns_error(mod, monkeypatch):
    write_course_csv(mod.COURSE_CSV, ["CS 6035"])

    def broken_fetch_json(url):
        raise RuntimeError("network is down")

    monkeypatch.setattr(mod, "fetch_json", broken_fetch_json)
    rc = mod.main()
    assert rc == 1


def test_main_missing_course_csv_returns_error(mod):
    # COURSE_CSV points into tmp_path but was never written
    rc = mod.main()
    assert rc == 1


def test_main_malformed_section_data_is_skipped_not_crashed(mod, monkeypatch):
    write_course_csv(mod.COURSE_CSV, ["CS 6035"])
    broken_term = {
        "courses": {
            "CS 6035": [
                "Introduction to Information Security",
                {
                    "A": ["84113"],  # too short, should be skipped safely
                    "B": [
                        "92340",
                        [[1, "TR", "Some Building 104", 0, ["John Doe (P)"], 0, -1, -1]],
                        3,
                        0,
                        0,
                        [],
                        -1,
                    ],
                },
            ]
        },
        "caches": {"periods": ["8:00 am - 9:15 am", "9:30 am - 10:45 am"], "locations": []},
        "updatedAt": "2026-07-11T00:00:00.000Z",
        "version": 3,
    }
    monkeypatch.setattr(mod, "fetch_json", fake_fetch_json_factory(mod, term_payload=broken_term))

    rc = mod.main()
    assert rc == 0

    with mod.OUTPUT_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["section"] == "B"
