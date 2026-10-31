## Fantasy (Fanduel)

Weekly FanDuel NFL lineup optimizer. `draft.py` ingests the newest salary CSV from `data26/`, pulls Vegas spreads / over-unders / weather, weights player projections, and uses `draftfast` to produce an optimal lineup under the salary cap.

### Goal

The target is **FanDuel tournament (GPP) upside**, especially the highest-scoring lineup in winner-take-all contests—not a cash-game lineup optimized for the safest median score. Classic lineups currently blend adjusted projections with an 85th-percentile ceiling at `GPP_UPSIDE_WEIGHT`; single-game lineups use a stronger ceiling tilt, and multi-entry portfolios also target winner-take-all coverage. These are heuristics, not guarantees of tournament equity, and should be evaluated with out-of-sample backtests.

### To use:

1. Drop the most recent FanDuel salary CSV into `data26/`.
2. `uv sync` (creates a `.venv` with all pinned deps).
3. Run it headless:
   ```
   uv run python draft.py
   ```
   Optionally in Jupyter: `uv run jupyter lab` and open `draft.py`.
4. The final lineup is printed as a table and written to `upload/upload.csv` in FanDuel's template column order.

### How projections are weighted

Weekly tuning lives in `config.py`, not `draft.py`. The starting projection is a normalized weighted blend of FanDuel salary-file FPPG (20%), recency-weighted nflverse history (10%), Sleeper's weekly projection (35%), and FantasyPros' weekly projection (35%). The source weights live in `PROJECTION_WEIGHTS`; unavailable or non-positive sources are omitted and the remaining weights are renormalized. The two weekly projection sources therefore carry 70% of the blend when both are available, while the two backward-looking sources carry 30%. A previous-week FantasyPros fallback is logged and excluded from current-week blending. FantasyPros' official API can be used by setting `FANTASYPROS_API_KEY` (request a key [here](https://secure.fantasypros.com/api-keys/request/)); without a key, the HTML scraper logs HTTP/parser details. Set `FANTASYPROS_FORCE_REFRESH=1` to bypass the short cache during diagnosis.

The run's `DATA SOURCES` section reports active-player match coverage and fallback use. If all projection inputs are missing/non-positive for a player, that player is blocked from the optimizer rather than treated as a zero-point bargain.

Matchup adjustments are additive fantasy points, not multipliers. The main signal is the **Vegas implied team total** (`O/U` and spread): offense gets `(team total - slate average) * OFFENSE_TOTAL_WEIGHT`, while defenses get a bonus when the *opponent's* implied total is below average (`DEFENSE_TOTAL_WEIGHT`). Fantasy-points-allowed data (`FPA_WEIGHT`), injury context, recent target-share growth, and outdoor weather add further adjustments. Injury vacancies are shared across pass catchers using recent target shares; role growth can modestly raise both projection and ceiling. Precipitation chance alone gets a deliberately modest adjustment because it does not describe rain intensity. Kickers and players at or below `LOW_SALARY_SKIP` only receive the source/history blend. Other adjusted projections are clamped by safety caps (`MAX_SCORE`, `MIN_PROJ_MULTIPLIER`, `MAX_PROJ_MULTIPLIER`, `MAX_DEF_MULTIPLIER`).

In the sorted player tables, `BlendBase` is the source/history blend before matchup, injury, and weather adjustments; it is not the raw FanDuel salary-file FPPG. The final `Proj` includes those adjustments (and, in GPP mode, may also include the upside tilt).

The pure projection math lives in `projection.py` so it can be unit-tested:
```
uv run python -m pytest
```

### Dependency management

This project uses [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`). Install deps with `uv sync` and add new ones with `uv add <package>`.

### Why we fork `draftfast`

`draftfast` (last release 3.12.5) is effectively unmaintained. Its `setup.py` pins stale versions — `numpy==1.26.2`, `ortools==9.8.3296`, `terminaltables==3.1.0` — that conflict with the modern stack this project runs on (`numpy` 2.x, `ortools` 9.10+). pip tolerated the mismatch by letting those pins get upgraded afterward; uv's strict resolver fails instead.

The fix is a **minimal fork**: keep the runtime code untouched, relax the pins in `setup.py` from `==` to `>=`, and depend on the fork directly:

```toml
[tool.uv.sources]
draftfast = { git = "https://github.com/<you>/draftfast.git", branch = "master" }
```

This preserves the modern numpy/ortools and makes `uv sync` resolve. Only re-sync if upstream ever releases (unlikely).
