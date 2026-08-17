## Fantasy (Fanduel)

Weekly FanDuel NFL lineup optimizer. `draft.py` ingests the newest salary CSV from `data26/`, pulls Vegas spreads / over-unders / weather, weights player projections, and uses `draftfast` to produce an optimal lineup under the salary cap.

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

Weekly tuning lives in `config.py`, not `draft.py`. The core signal is the **Vegas implied team total** (`O/U` ± spread): a player's projection scales by how far their team's total deviates from the slate average (`OFFENSE_TOTAL_WEIGHT`), and defenses by how low the *opponent's* implied total is (`DEFENSE_TOTAL_WEIGHT`). Fantasy-points-allowed data (`FPA_WEIGHT`), injury context, and weather add smaller adjustments on top. Everything is clamped by safety caps (`MAX_SCORE`, `MIN_PROJ_MULTIPLIER`, `MAX_DEF_MULTIPLIER`).

The pure projection math lives in `projection.py` so it can be unit-tested:
```
uv run pytest
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
