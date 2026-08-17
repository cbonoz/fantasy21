## Fantasy (Fanduel)

Weekly FanDuel NFL lineup optimizer. `draft.py` ingests the newest salary CSV from `data26/`, pulls Vegas spreads / over-unders / weather, weights player projections, and uses `draftfast` to produce an optimal lineup under the salary cap.

### To use:

1. Drop the most recent FanDuel salary CSV into `data26/`.
2. `poetry install` (creates a `.venv` with all pinned deps).
3. Run it headless:
   ```
   poetry run python draft.py
   ```
   Optionally in Jupyter: `poetry run jupyter lab` and open `draft.py`.
4. The final lineup is printed as a table and written to `upload/upload.csv` in FanDuel's template column order.

### Dependency management

This project uses [Poetry](https://python-poetry.org/) (`pyproject.toml` + `poetry.lock`). Install deps with `poetry install` and add new ones with `poetry add <package>`.

Note: if you need to point at a fork (see below), use `poetry add draftfast@git+https://github.com/<you>/draftfast.git@master`.

### Why we fork `draftfast`

`draftfast` (last release 3.12.5) is effectively unmaintained. Its `setup.py` pins stale versions — `numpy==1.26.2`, `ortools==9.8.3296`, `terminaltables==3.1.0` — that conflict with the modern stack this project runs on (`numpy` 2.x, `ortools` 9.10+). pip tolerated the mismatch by letting those pins get upgraded afterward; poetry's strict resolver fails instead.

The fix is a **minimal fork**: keep the runtime code untouched, relax the pins in `setup.py` from `==` to `>=`, and depend on the fork directly:

```toml
[tool.poetry.dependencies]
draftfast = { git = "https://github.com/<you>/draftfast.git", branch = "master" }
```

This preserves the modern numpy/ortools and makes `poetry install` resolve. Only re-sync if upstream ever releases (unlikely).
