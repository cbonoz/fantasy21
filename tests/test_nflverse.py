import pandas as pd
import pytest

import nflverse
from nflverse import (
    compute_fpa_map,
    compute_defense_upside_map,
    compute_recent_target_share_map,
    compute_recent_form_map,
    compute_recent_opportunity_map,
    compute_returning_competition_map,
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

    def test_kicker_gets_empirical_ceiling_from_fanduel_scoring(self, monkeypatch):
        df = pd.DataFrame([
            {'week': 1, 'position': 'K', 'player_display_name': 'Test Kicker',
             'fg_made_20_29': 1, 'pat_made': 1},
            {'week': 2, 'position': 'K', 'player_display_name': 'Test Kicker',
             'fg_made_30_39': 1, 'pat_made': 1},
            {'week': 3, 'position': 'K', 'player_display_name': 'Test Kicker',
             'fg_made_50_59': 1, 'pat_made': 1},
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_upside_map(4)

        assert result['test kicker'] == pytest.approx(5.4)

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

    def test_recent_scoring_spike_without_role_growth_modestly_raises_ceiling(self, monkeypatch):
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

        # Season P85 is 21; recent P85 is 25.5 with 4/6 reliability and a 0.5
        # blend weight, so recent form lifts the ceiling modestly to 22.5.
        assert result['no role change'] == pytest.approx(22.5)


class TestRecentFormMap:
    def test_uses_recent_game_appearances_and_median_form(self, monkeypatch):
        scores = [
            (1, 5), (2, 10), (3, 20), (4, 22), (5, 16), (6, 35),
        ]
        df = pd.DataFrame([
            {
                'week': week, 'position': 'QB', 'player_display_name': 'Form QB',
                'passing_yards': score * 25,
            }
            for week, score in scores
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_recent_form_map(7, recent_games=4)

        player = result['form qb']
        assert player['recent_games'] == 4
        assert player['total_games'] == 6
        assert player['recent_median'] == pytest.approx(21)
        assert player['season_median'] == pytest.approx(18)
        assert player['adjustment'] == pytest.approx(0.7)

    def test_no_adjustment_when_recent_sample_is_too_small(self, monkeypatch):
        df = pd.DataFrame([
            {'week': week, 'position': 'QB', 'player_display_name': 'New QB',
             'passing_yards': 300}
            for week in (1, 2, 3, 4)
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: df)

        result = compute_recent_form_map(5, recent_games=4)

        assert result == {}


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


class TestRecentOpportunityMap:
    def test_tracks_rb_touches_and_recent_snap_share(self, monkeypatch):
        stats = pd.DataFrame([
            {'week': 6, 'team': 'NE', 'position': 'RB', 'player_display_name': 'Lead Back',
             'carries': 10, 'targets': 2},
            {'week': 6, 'team': 'NE', 'position': 'RB', 'player_display_name': 'Change Back',
             'carries': 5, 'targets': 0},
            {'week': 7, 'team': 'NE', 'position': 'RB', 'player_display_name': 'Lead Back',
             'carries': 6, 'targets': 3},
            {'week': 7, 'team': 'NE', 'position': 'RB', 'player_display_name': 'Change Back',
             'carries': 4, 'targets': 0},
        ])
        snaps = pd.DataFrame([
            {'week': 6, 'team': 'NE', 'position': 'RB', 'player': 'Lead Back', 'offense_pct': 0.60},
            {'week': 6, 'team': 'NE', 'position': 'RB', 'player': 'Change Back', 'offense_pct': 0.20},
            {'week': 7, 'team': 'NE', 'position': 'RB', 'player': 'Lead Back', 'offense_pct': 0.70},
            {'week': 7, 'team': 'NE', 'position': 'RB', 'player': 'Change Back', 'offense_pct': 0.10},
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: stats)
        monkeypatch.setattr(nflverse, 'get_snap_counts', lambda week, season=None: snaps)

        result = compute_recent_opportunity_map(8, recent_weeks=3)

        back = result[('NE', 'RB', 'change back')]
        assert back['opportunities'] == pytest.approx(9)
        assert back['opportunities_per_game'] == pytest.approx(4.5)
        assert back['opportunity_share'] == pytest.approx(9 / 30)
        assert back['snap_share'] == pytest.approx(0.15)

    def test_wr_and_te_opportunity_is_targets_not_carries(self, monkeypatch):
        stats = pd.DataFrame([
            {'week': 7, 'team': 'NE', 'position': 'TE', 'player_display_name': 'Receiving TE',
             'carries': 4, 'targets': 3},
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: stats)
        monkeypatch.setattr(nflverse, 'get_snap_counts', lambda week, season=None: pd.DataFrame())

        result = compute_recent_opportunity_map(8, recent_weeks=3)

        assert result[('NE', 'TE', 'receiving te')]['opportunities'] == pytest.approx(3)

    def test_snap_source_failure_preserves_weekly_opportunity_signal(self, monkeypatch):
        stats = pd.DataFrame([
            {'week': 7, 'team': 'NE', 'position': 'RB', 'player_display_name': 'Available Back',
             'carries': 8, 'targets': 2},
        ])
        monkeypatch.setattr(nflverse, 'prior_week_stats', lambda week, season=None: stats)

        def unavailable(*args, **kwargs):
            raise OSError('snap source unavailable')

        monkeypatch.setattr(nflverse, 'get_snap_counts', unavailable)
        result = compute_recent_opportunity_map(8, recent_weeks=3)

        role = result[('NE', 'RB', 'available back')]
        assert role['opportunities_per_game'] == pytest.approx(10)
        assert role['snap_share'] == 0

    def test_weekly_stats_failure_falls_back_to_snap_roles(self, monkeypatch):
        snaps = pd.DataFrame([
            {'week': 7, 'team': 'NE', 'position': 'RB', 'player': 'Available Back',
             'offense_pct': 0.25},
        ])

        def unavailable(*args, **kwargs):
            raise OSError('weekly stats unavailable')

        monkeypatch.setattr(nflverse, 'prior_week_stats', unavailable)
        monkeypatch.setattr(nflverse, 'get_snap_counts', lambda week, season=None: snaps)
        result = compute_recent_opportunity_map(8, recent_weeks=3)

        role = result[('NE', 'RB', 'available back')]
        assert role['opportunities_per_game'] == 0
        assert role['snap_share'] == pytest.approx(0.25)


class TestReturningCompetitionMap:
    def test_detects_role_loss_after_available_teammate_returns(self, monkeypatch):
        rows = []
        for week in (1, 2):
            rows.extend([
                {'week': week, 'team': 'NYJ', 'position': 'RB',
                 'player_display_name': 'Returning Back', 'carries': 8, 'targets': 2},
                {'week': week, 'team': 'NYJ', 'position': 'RB',
                 'player_display_name': 'Change Back', 'carries': 3, 'targets': 1},
            ])
        for week in range(4, 8):
            rows.append({
                'week': week, 'team': 'NYJ', 'position': 'RB',
                'player_display_name': 'Change Back',
                'carries': 16, 'targets': 4,
            })
        rows.extend([
            {'week': 8, 'team': 'NYJ', 'position': 'RB',
             'player_display_name': 'Returning Back', 'carries': 8, 'targets': 2},
            {'week': 8, 'team': 'NYJ', 'position': 'RB',
             'player_display_name': 'Change Back', 'carries': 8, 'targets': 2},
        ])
        monkeypatch.setattr(
            nflverse, 'prior_week_stats', lambda week, season=None: pd.DataFrame(rows)
        )

        result = compute_returning_competition_map(
            9,
            available_players={
                ('NYJ', 'RB', 'returning back'),
                ('NYJ', 'RB', 'change back'),
            },
        )

        role = result[('NYJ', 'RB', 'change back')]
        assert role['penalty'] == pytest.approx(2.5)
        assert role['recent_share'] == pytest.approx((2 / 7 + 2 / 7 + 0.5) / 3)
        assert role['earlier_share'] == pytest.approx(1.0)
        assert role['returning_teammate'] == 'returning back'

    def test_ignores_returner_not_in_current_available_pool(self, monkeypatch):
        rows = []
        for week in (1, 2):
            rows.append({
                'week': week, 'team': 'NYJ', 'position': 'RB',
                'player_display_name': 'Returning Back', 'carries': 8, 'targets': 2,
            })
        for week in range(4, 8):
            rows.append({
                'week': week, 'team': 'NYJ', 'position': 'RB',
                'player_display_name': 'Change Back', 'carries': 16, 'targets': 4,
            })
        rows.extend([
            {'week': 8, 'team': 'NYJ', 'position': 'RB',
             'player_display_name': 'Returning Back', 'carries': 8, 'targets': 2},
            {'week': 8, 'team': 'NYJ', 'position': 'RB',
             'player_display_name': 'Change Back', 'carries': 8, 'targets': 2},
        ])
        monkeypatch.setattr(
            nflverse, 'prior_week_stats', lambda week, season=None: pd.DataFrame(rows)
        )

        result = compute_returning_competition_map(9, available_players=set())

        assert result == {}

    def test_anticipates_available_teammate_return_from_shared_game_roles(self, monkeypatch):
        rows = []
        for week in (1, 2):
            rows.extend([
                {'week': week, 'team': 'NYJ', 'position': 'RB',
                 'player_display_name': 'Returning Back', 'carries': 8, 'targets': 2},
                {'week': week, 'team': 'NYJ', 'position': 'RB',
                 'player_display_name': 'Change Back', 'carries': 3, 'targets': 1},
            ])
        for week in range(4, 9):
            rows.append({
                'week': week, 'team': 'NYJ', 'position': 'RB',
                'player_display_name': 'Change Back', 'carries': 16, 'targets': 4,
            })
        monkeypatch.setattr(
            nflverse, 'prior_week_stats', lambda week, season=None: pd.DataFrame(rows)
        )

        result = compute_returning_competition_map(
            9,
            available_players={
                ('NYJ', 'RB', 'returning back'),
                ('NYJ', 'RB', 'change back'),
            },
        )

        role = result[('NYJ', 'RB', 'change back')]
        assert role['penalty'] == pytest.approx(2.5)
        assert role['prospective'] is True
        assert role['returning_teammate'] == 'returning back'


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
