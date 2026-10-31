import pandas as pd
import pytest

import nflverse
from nflverse import (
    compute_fpa_map,
    compute_defense_upside_map,
    compute_recent_target_share_map,
    compute_target_share_trend_map,
    compute_upside_map,
)


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
        # 85th percentile is 34, not the 40-point maximum.
        df = pd.DataFrame([
            {'week': 1, 'position': 'QB', 'player_display_name': 'Test QB', 'rushing_yards': 100},
            {'week': 2, 'position': 'QB', 'player_display_name': 'Test QB', 'rushing_yards': 200},
            {'week': 3, 'position': 'QB', 'player_display_name': 'Test QB', 'rushing_yards': 400},
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_upside_map(4)

        assert result['test qb'] == pytest.approx(34.0)

    def test_recent_ceiling_rises_only_when_target_share_confirms_role_growth(self, monkeypatch):
        rows = []
        for week in range(1, 8):
            rows.append({
                'week': week,
                'position': 'WR',
                'player_display_name': 'Role Breakout',
                'rushing_yards': 0 if week <= 4 else 100 * (week - 4),
                'target_share': 0.05 if week <= 4 else 0.12,
            })
        df = pd.DataFrame(rows)
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_upside_map(8)

        # Recent four-game P85 (25.5) replaces the stale full-season P85 (21)
        # because target share rose by more than the configured threshold.
        assert result['role breakout'] == pytest.approx(25.5)

    def test_recent_scoring_spike_without_role_growth_does_not_raise_ceiling(self, monkeypatch):
        rows = []
        for week in range(1, 8):
            rows.append({
                'week': week,
                'position': 'WR',
                'player_display_name': 'No Role Change',
                'rushing_yards': 0 if week <= 4 else 100 * (week - 4),
                'target_share': 0.05,
            })
        df = pd.DataFrame(rows)
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_upside_map(8)

        assert result['no role change'] == pytest.approx(21.0)


class TestRecentTargetShareMap:
    def test_averages_recent_pass_catcher_target_share_by_team_and_name(self, monkeypatch):
        df = pd.DataFrame([
            {'week': 3, 'position': 'WR', 'team': 'PIT', 'player_display_name': 'Germie Bernard', 'target_share': 0.10},
            {'week': 4, 'position': 'WR', 'team': 'PIT', 'player_display_name': 'Germie Bernard', 'target_share': 0.20},
            {'week': 5, 'position': 'WR', 'team': 'PIT', 'player_display_name': 'Germie Bernard', 'target_share': 0.30},
            {'week': 6, 'position': 'WR', 'team': 'PIT', 'player_display_name': 'Germie Bernard', 'target_share': 0.40},
            {'week': 7, 'position': 'TE', 'team': 'PIT', 'player_display_name': 'Pat Freiermuth', 'target_share': 0.50},
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_recent_target_share_map(8, recent_weeks=4)

        assert result[('PIT', 'germie bernard')] == pytest.approx(0.3)
        assert result[('PIT', 'pat freiermuth')] == pytest.approx(0.5)

    def test_returns_recent_and_prior_share_for_rising_role(self, monkeypatch):
        df = pd.DataFrame([
            {'week': week, 'position': 'WR', 'team': 'PHI',
             'player_display_name': 'Makai Lemon',
             'target_share': 0.10 if week <= 3 else 0.20}
            for week in range(1, 8)
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_target_share_trend_map(8, recent_weeks=4)

        trend = result[('PHI', 'makai lemon')]
        assert trend['earlier'] == pytest.approx(0.10)
        assert trend['recent'] == pytest.approx(0.20)
        assert trend['delta'] == pytest.approx(0.10)


class TestComputeDefenseUpsideMap:
    def test_returns_only_big_play_tail_uplift(self, monkeypatch):
        monkeypatch.setattr(
            nflverse, 'compute_score_distributions',
            lambda week, season=None: ({}, {'det': [2, 6, 10]}),
        )

        result = compute_defense_upside_map(8)

        # P85 is 8.8 and the mean is 6.0; points-allowed baseline is not
        # reconstructed from incomplete player-level defensive stats.
        assert result['det'] == pytest.approx(2.8)
