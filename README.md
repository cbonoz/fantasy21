## Fantasy (Fanduel)

Weekly FanDuel NFL lineup optimizer. `draft.py` ingests the newest salary CSV from `data26/`, pulls Vegas spreads / over-unders / weather, weights player projections, and uses `draftfast` to produce an optimal lineup under the salary cap.

### Goal

The target is **FanDuel tournament (GPP) upside**, especially the highest-scoring lineup in winner-take-all contests—not a cash-game lineup optimized for the safest median score. Full-slate projections include capped, shrinkage-adjusted recent scoring form and a recency-aware ceiling, then blend adjusted projections with the ceiling at `GPP_UPSIDE_WEIGHT`; single-game lineups use a stronger ceiling tilt. On short classic slates, candidate lineups also receive a capped target-share overlap adjustment when a same-team WR and TE are both rostered. These are heuristics, not guarantees of tournament equity, and should be evaluated with out-of-sample backtests.

### To use:

1. Drop the most recent FanDuel salary CSV into `data26/`.
2. `uv sync` (creates a `.venv` with all pinned deps).
3. Run it headless:
   ```
   uv run python draft.py
   ```
   Optionally in Jupyter: `uv run jupyter lab` and open `draft.py`.
4. The final lineup is printed as a table and written to `upload/upload.csv` in FanDuel's template column order.

### Caching

Every external source is cached per week (`ranking/`, odds in `spread/`) and reused while the file is newer than its TTL:

| Source | TTL |
|---|---|
| Odds | 60 min |
| Weather | 30 min |
| Sleeper | 6 h |
| FantasyPros | 6 h, or the rest of the week once a current-week cache has real rows |
| nflverse stats, snaps, FPA, giveaways | 3 days |

A run with warm caches makes no HTTP calls. A failed odds or Sleeper refresh falls back to the last good file instead of dropping the source. The multi-MB nflverse season CSVs are parsed once per process and reused by every analysis step. FantasyPros' six-hour TTL is a floor, not a target: a current-week cache with at least `FANTASYPROS_MIN_CACHED_ENTRIES` rows is reused instead of re-scraped, because the ten-per-position consensus rarely changes intraday and re-scraping risks throttling. `FANTASYPROS_FORCE_REFRESH=1` overrides that.

### Correlation rules and locks

Single-game builds let the optimizer choose any defense/QB pairing: a defense can score on sacks and turnovers while the opposing passer still produces, so banning the pair removes real upside combinations. They also require `SINGLE_GAME_MIN_PER_TEAM` players (default 2) from each of the two teams, so a six-man roster cannot be 5-1 or 6-0. Two running backs from the same team are allowed but cost `SAME_TEAM_RB_OVERLAP_PENALTY` (2 points) at the lineup level, because their ceilings are not independent — a big game for one usually comes at the other's expense. Full slates keep the same-team stacking caps and the QB-vs-opposing-D exclusion. Those are preferences, not platform rules: when the configured locks (or a thin player pool) leave no legal lineup under them, the optimizer retries without the heuristics, prints a warning, and returns the best legal build instead of `No solution`. The per-team floor is a lineup-construction rule and is not relaxed.

### How projections are weighted

Weekly tuning lives in `config.py`, not `draft.py`. The starting projection is a normalized weighted blend of FanDuel salary-file FPPG (20%), recency-weighted nflverse history (10%), Sleeper's weekly projection (35%), and FantasyPros' weekly projection (35%). The source weights live in `PROJECTION_WEIGHTS`; unavailable or non-positive sources are omitted and the remaining weights are renormalized. The two weekly projection sources therefore carry 70% of the blend when both are available, while the two backward-looking sources carry 30%. A previous-week FantasyPros fallback is logged and excluded from current-week blending.

FantasyPros is scraped from the public HTML projections page by default. The official API is optional and **disabled** (`FANTASYPROS_USE_API = False`) because the free tier returns only ten rows per position and enforces a very small daily quota. To use a higher-tier key, copy `.env.example` to `.env`, set `FANTASYPROS_API_KEY`, and set `FANTASYPROS_USE_API = True` in `config.py` (request a key [here](https://secure.fantasypros.com/api-keys/request/)). A failed API fetch falls back to the HTML page, and the app runs fine without either. API requests are spaced by `FANTASYPROS_API_REQUEST_DELAY_SECONDS` (default 1s), transient failures retry once, and the attempt stops on the first auth/quota/rate-limit response. Set `FANTASYPROS_FORCE_REFRESH=1` to bypass the short cache during diagnosis.

The run's `DATA SOURCES` section reports active-player match coverage and fallback use. If all projection inputs are missing/non-positive for a player, that player is blocked from the optimizer rather than treated as a zero-point bargain.

Matchup adjustments are additive fantasy points, not multipliers. The main signal is the **Vegas implied team total** (`O/U` and spread): offense gets `(team total - slate average) * OFFENSE_TOTAL_WEIGHT`, while defenses get a bonus when the *opponent's* implied total is below average (`DEFENSE_TOTAL_WEIGHT`). Fantasy-points-allowed data (`FPA_WEIGHT`) is shrunk toward the positional league average by `FPA_SHRINKAGE_GAMES` so a single blow-up game cannot dominate a small sample. Injury context, recent target-share growth, and outdoor weather add further adjustments. Weather uses a per-position wind × temperature × precipitation factor, bounded by `WEATHER_MAX_ADJUSTMENT` so correlated inputs cannot compound into an implausible swing (kickers are capped at −25%, defenses at +6%). Injury vacancies are shared across pass catchers using recent target shares; role growth can modestly raise both projection and ceiling. Precipitation chance alone gets a deliberately modest adjustment because it does not describe rain intensity. Kickers and players at or below `LOW_SALARY_SKIP` only receive the source/history blend. Other adjusted projections are clamped by safety caps (`MAX_SCORE`, `MIN_PROJ_MULTIPLIER`, `MAX_PROJ_MULTIPLIER`, `MAX_DEF_MULTIPLIER`).

FantasyPros' free API and public HTML page both expose only the top ten players per position, so coverage can be partial even on a successful fetch. The source is still used, but its blend weight is scaled by the fraction of the pool it covers. A source that covers a quarter of the slate therefore contributes a quarter of its 35% weight, instead of giving the covered stars an extra market signal the rest of the pool never receives. Set `FANTASYPROS_ALLOW_DEGRADED = False` to drop partial data entirely, or `FANTASYPROS_SCALE_WEIGHT_BY_COVERAGE = False` to use it at full weight. A higher API tier is the only way to get full coverage.

In the sorted player tables, `BlendBase` is the source/history blend before matchup, injury, and weather adjustments; it is not the raw FanDuel salary-file FPPG. `Mean` is the adjusted projection (blend plus matchup, role, form, and weather) before the GPP upside tilt. `Ceil` is the empirical ceiling. `Proj` is the final value: `(1 - GPP_UPSIDE_WEIGHT) * Mean + GPP_UPSIDE_WEIGHT * Ceil` when the ceiling is higher, otherwise `Mean`. Single-game slates also print a cross-position FLEX table sorted by ceiling, because every non-MVP slot is AnyFLEX and a quarterback and a receiver compete for the same spot on points, ceiling, and salary rather than by position.

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
