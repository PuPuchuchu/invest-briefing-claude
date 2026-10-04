"""
Step 10I -- FRED multi-vintage response structure validation (GitHub-hosted
runner only).

PURPOSE: answer exactly the question Step 10H's OPEN SEMANTIC ISSUE #1
raised -- does a real `fred/series/observations?output_type=2` response
actually let us recover MULTIPLE vintages for the SAME observation_date,
and can that be turned into something src/macro/pit.py's vintage-selection
design can consume? This script answers that from a REAL response, not
from documentation. It also calls `fred/series/vintagedates` and
`fred/series` (metadata) once each, and records exactly what each
endpoint actually returns.

THIS IS NOT A PROVIDER. It is intentionally NOT imported by anything
under src/macro/ or src/, writes no CSV, and makes no production
PIT/state/regime decision. The one deliberate exception, explained below,
is that Layer 7 of this script IMPORTS src/macro/pit.py (read-only --
calls its existing public functions, never modifies them) to check real
FRED data against the actual frozen PIT selection logic, rather than
against a hand-rolled reimplementation of it that could silently drift
from pit.py's real behavior. This is still validation-only infrastructure
for Step 10I: kept outside src/, workflow_dispatch-only trigger, no
CSV/state/regime output of any kind.

CORRECTION HISTORY (2026-10-04, same day, v2 of this script):
The v1 version of this script assumed `output_type=2` would return one
JSON object per (observation_date, vintage) pair, each carrying its own
`realtime_start`/`realtime_end`/`value` fields -- i.e. the same per-row
shape as the DEFAULT `output_type=1` response. That assumption was WRONG.
A real run against GitHub Actions (performed directly by the project
owner, independently of this script's own commit history) showed the
actual shape: `output_type=2` ("Observations by Vintage Date, All
