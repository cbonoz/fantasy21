import pandas as pd
import pytest

import nflverse
from nflverse import compute_fpa_map, compute_upside_map


def _df():
    cols = ['week', 'team', 'opponent_team', 'position', 'player_display_name',
            'receiving_receptions', 'receiving_yards', 'receiving_tds',
            'rushing_yards', 'rushing_tds', 'receiving_fumbles_lost',
            'rushing_fumbles_lost', 'fumbles_lost_total', 'fumbles_lost']
    rows = [
        # Week 1: opponent TE1 only, 30 yds
        (1, 'B', 'A', 'TE', 'T1', 3, 30, 0, 0, 0, 0, 0, 0, 0),
        # Week 2: opponent TE1 (100 yds, 1 TD) + 2 backup TEs at 0 -> sums to 16
        (2, 'C', 'A', 'TE', 'T2', 5, 100, 1, 0, 0, 0, 0, 0, 0),
        (2, 'C', 'A', 'TE', 'T3', 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (2, 'C', 'A', 'TE', 'T4', 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ]
    return pd.DataFrame(rows, columns=cols)


class TestComputeFpaMap:
    def test_sums_position_points_per_game(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: _df())
        monkeypatch.setattr(nflverse.config, 'CACHE_FOLDER', str(tmp_path))

        result = compute_fpa_map(3)

        # Week 1: 3.0 pts allowed. Week 2: 100*0.1 + 1*6 = 16.0 pts allowed.
        # Per-game average = (3.0 + 16.0) / 2 = 9.5. Backup TEs must not dilute it.
        assert result['A']['TE'] == pytest.approx(9.5)


class TestComputeUpsideMap:
    def test_small_sample_does_not_collapse_to_max(self, monkeypatch):
        # 100/200/400 rushing yards -> 10/20/40 FD points. The interpolated
        # 75th percentile is 30, not the 40-point maximum.
        df = pd.DataFrame([
            {'week': 1, 'position': 'QB', 'player_display_name': 'Test QB', 'rushing_yards': 100},
            {'week': 2, 'position': 'QB', 'player_display_name': 'Test QB', 'rushing_yards': 200},
            {'week': 3, 'position': 'QB', 'player_display_name': 'Test QB', 'rushing_yards': 400},
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_upside_map(4)

        assert result['test qb'] == pytest.approx(30.0)