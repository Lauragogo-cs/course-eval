# Course Evaluation Dataset

A snapshot clone of a community-maintained course selection/review spreadsheet. It aggregates course metadata (course code, specialization requirements, seats), workload/rating statistics, and student review comments/links.

Identifying references to the source program and institution have been anonymized/generalized in this copy's data content. Note that the automated data source described below (GT Scheduler's public crawler feed) is inherently tied to a specific institution at the code/URL level, since it mirrors that institution's own registration system.

## Files

- `course-eval-8_23.xlsx` — spreadsheet version
- `course-eval-8_23.csv` — plain-text CSV version of the same data (1,008 course rows)
- `gt_scheduler_sections.csv` — current-term section data (CRN, meeting days/times, instructors) per course code, refreshed once per semester (see below)
- `scripts/scrape_gt_scheduler.py` — the script that builds `gt_scheduler_sections.csv`
- `.github/workflows/update-gt-scheduler.yml` — the semester automation
- `tests/test_scrape_gt_scheduler.py` — unit tests for the scraper (mocked, no network access needed)
- `.github/workflows/tests.yml` — runs the test suite on every push/PR to `main`

## Automated updates

A GitHub Actions workflow runs `scripts/scrape_gt_scheduler.py` once per semester (06:00 UTC on Jan 1 / May 1 / Aug 1, matching Spring/Summer/Fall term starts) and on manual trigger. Section/CRN/instructor data barely changes within a semester once it's posted, so polling daily would just be unnecessary load with no real freshness benefit — the schedule is set to match how often the underlying data actually changes, not an arbitrary "run it every day" default. The script:

1. Reads every unique course code from `course-eval-8_23.csv`.
2. Fetches the list of available terms and the current term's data from GT Scheduler's public crawler feed (`gt-scheduler.github.io/crawler-v2/*.json`) — static JSON published by Georgia Tech's Bits of Good student organization, refreshed from Oscar (GT's registration system) every 30 minutes. No authentication, no API key, no rate limits: it's just static files on GitHub Pages.
3. For each matching course, writes one row per section with its CRN, meeting days, meeting time, and instructor(s).
4. Commits and pushes the updated CSV back to the repo if anything changed.

This does **not** include live seat-availability counts — those require a separate real-time lookup per CRN against GT Scheduler's own backend, which isn't designed for bulk polling, so it's intentionally left out.

No API keys or account registration are required.

To run it locally:

```bash
python scripts/scrape_gt_scheduler.py
```

To trigger a run manually on GitHub: **Actions → Update GT Scheduler Section Data → Run workflow**. The workflow needs the repo's Actions setting **Workflow permissions → Read and write permissions** enabled (Settings → Actions → General) so it can push its own commits.

## Tests

`tests/test_scrape_gt_scheduler.py` covers the parsing/matching logic with mocked API responses (happy path, no-match courses, malformed section data, network failures) — it never makes a real request, so it runs identically in CI and locally. The `Tests` workflow runs it on every push and pull request against `main`.

To run locally:

```bash
pip install pytest
pytest tests/ -v
```

## Notes

The base course dataset (`course-eval-8_23.*`) is a point-in-time snapshot — seat counts, ratings, and comments in it may have changed since that clone was made. `gt_scheduler_sections.csv` is the file in this repo that stays current, refreshed automatically each semester from the latest term GT Scheduler has published.

Two earlier data-source attempts were tried and abandoned for this pipeline, in case anyone picks this back up:
- **Reddit search API**: unauthenticated requests are blocked (HTTP 403) from datacenter IPs, and Reddit now gates new API app registration behind manual review.
- **Course Critique's undocumented API** (`c4citk6s9k.execute-api.us-east-1.amazonaws.com/test/...`): returned HTTP 500 for every request after Course Critique's April 2026 relaunch — the endpoint appears to have been retired.
