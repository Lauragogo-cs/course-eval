#!/usr/bin/env python3
"""
Daily pipeline: pull current-term section/meeting data (CRN, days, time,
instructors) for each course code in course-eval-8_23.csv from GT Scheduler's
public crawler feed, and write it to gt_scheduler_sections.csv.

Data source: GT Scheduler (built by Georgia Tech's Bits of Good student
organization) runs its own crawler against Oscar (GT's registration system)
every 30 minutes and publishes the result as static JSON on GitHub Pages —
no authentication, no rate limits, no undocumented private API:

    Index of available terms: https://gt-scheduler.github.io/crawler-v2/index.json
    Per-term data:            https://gt-scheduler.github.io/crawler-v2/{term}.json

Schema reference (from the crawler's consumer, gt-scheduler/website):
    https://github.com/gt-scheduler/website/blob/main/src/data/beans/Oscar.ts
    https://github.com/gt-scheduler/website/blob/main/src/data/beans/Section.ts

Top-level term JSON shape:
    {
      "courses": {"<COURSE CODE>": [courseName, {<sectionId>: [crn, meetings, credits, ...]}], ...},
      "caches": {"periods": [...], "locations": [...], ...},
      "updatedAt": "<ISO date>",
      "version": <int>
    }
Each meeting tuple is [periodIndex, days, where, locationIndex, instructors, dateRangeIndex, ...].

Note: this feed does NOT include live seat-availability counts (those require
a separate per-CRN call to GT Scheduler's own backend, which isn't intended
for bulk polling) — this script only pulls section/meeting/instructor data.

Designed to run unattended in CI (see .github/workflows/update-gt-scheduler.yml).

Usage:
    python scripts/scrape_gt_scheduler.py
"""
import csv
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COURSE_CSV = ROOT / "course-eval-8_23.csv"
OUTPUT_CSV = ROOT / "gt_scheduler_sections.csv"

INDEX_URL = "https://gt-scheduler.github.io/crawler-v2/index.json"
TERM_URL_TMPL = "https://gt-scheduler.github.io/crawler-v2/{term}.json"
USER_AGENT = "python:omscs-course-eval-bot:v1.0 (course dataset aggregator)"
TIMEOUT_SECONDS = 30

FIELDNAMES = [
    "course_code",
    "course_name",
    "section",
    "crn",
    "credits",
    "days",
    "time",
    "instructors",
    "term",
    "term_updated_at",
    "fetched_at",
]


def load_course_codes(path: Path) -> list[str]:
    codes = set()
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for row in reader:
            if row and row[0].strip():
                codes.add(row[0].strip())
    return sorted(codes)


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_latest_term(index_payload: dict) -> str | None:
    terms = [t["term"] for t in index_payload.get("terms", []) if t.get("term")]
    return max(terms) if terms else None  # "YYYYMM" strings sort correctly lexicographically


def clean_instructor(name: str) -> str:
    return name.replace(" (P)", "").strip()


def main() -> int:
    if not COURSE_CSV.exists():
        print(f"course list not found: {COURSE_CSV}", file=sys.stderr)
        return 1

    course_codes = load_course_codes(COURSE_CSV)
    print(f"loaded {len(course_codes)} course codes")

    try:
        index_payload = fetch_json(INDEX_URL)
    except Exception as e:  # noqa: BLE001 - CI script, log and fail clearly
        print(f"[error] failed to fetch index.json: {e}", file=sys.stderr)
        return 1

    term = pick_latest_term(index_payload)
    if not term:
        print("[error] no terms found in index.json", file=sys.stderr)
        return 1
    print(f"using latest term: {term}")

    try:
        term_payload = fetch_json(TERM_URL_TMPL.format(term=term))
    except Exception as e:  # noqa: BLE001
        print(f"[error] failed to fetch {term}.json: {e}", file=sys.stderr)
        return 1

    courses = term_payload.get("courses", {})
    caches = term_payload.get("caches", {})
    periods = caches.get("periods", [])
    updated_at = term_payload.get("updatedAt", "")
    fetched_at = datetime.now(timezone.utc).isoformat()

    rows = []
    matched = 0
    for code in course_codes:
        entry = courses.get(code)
        if entry is None or len(entry) < 2:
            continue
        matched += 1
        course_name, sections = entry[0], entry[1]
        if not isinstance(sections, dict):
            continue

        for section_id, sec_data in sections.items():
            if not isinstance(sec_data, list) or len(sec_data) < 3:
                continue
            crn, meetings, credits = sec_data[0], sec_data[1], sec_data[2]

            days_set: list[str] = []
            times_set: list[str] = []
            instructors_set: list[str] = []
            for m in meetings if isinstance(meetings, list) else []:
                if not isinstance(m, list) or len(m) < 5:
                    continue
                period_idx, days, _where, _loc_idx, instructors = m[0], m[1], m[2], m[3], m[4]
                if days and days not in days_set:
                    days_set.append(days)
                if isinstance(period_idx, int) and 0 <= period_idx < len(periods):
                    period_str = periods[period_idx] or "TBA"
                else:
                    period_str = "TBA"
                if period_str not in times_set:
                    times_set.append(period_str)
                for instr in instructors if isinstance(instructors, list) else []:
                    cleaned = clean_instructor(instr)
                    if cleaned and cleaned not in instructors_set:
                        instructors_set.append(cleaned)

            rows.append(
                {
                    "course_code": code,
                    "course_name": course_name,
                    "section": section_id,
                    "crn": crn,
                    "credits": credits,
                    "days": "; ".join(days_set),
                    "time": "; ".join(times_set),
                    "instructors": "; ".join(instructors_set),
                    "term": term,
                    "term_updated_at": updated_at,
                    "fetched_at": fetched_at,
                }
            )

    print(f"matched {matched}/{len(course_codes)} course codes in term {term}")
    rows.sort(key=lambda r: (r["course_code"], str(r["section"])))

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} section rows to {OUTPUT_CSV}")
    if matched == 0:
        print("[error] no course codes matched any course in the term feed — schema may have changed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
