import pandas as pd
import pytest

from projection import (
    blend_projections,
    calculate_precipitation_factor,
    calculate_temperature_factor,
    calculate_wind_factor,
    cap_projection,
    compute_defense_fd_points,
    compute_fd_points,
    compute_team_totals,
)


class TestWindFactor:
    def test_neutral_without_wind(self):
        assert calculate_wind_factor(None, 'QB') == 1.0
        assert calculate_wind_factor(1, 'QB') == 1.0

    def test_rb_boosted_by_wind(self):
        assert calculate_wind_factor(18, 'RB') > 1.0

    def test_qb_penalized_by_wind(self):
        assert calculate_wind_factor(25, 'QB') < 1.0

    def test_wr_penalized_more_than_qb_at_extreme(self):
        assert calculate_wind_factor(25, 'WR') < calculate_wind_factor(25, 'QB')

    def test_unknown_position_neutral(self):
        assert calculate_wind_factor(15, 'NOT_A_POS') == 1.0


class TestTemperatureFactor:
    def test_neutral_without_temp(self):
        assert calculate_temperature_factor(None, 'QB') == 1.0

    def test_cold_penalizes_qb_wr(self):
        assert calculate_temperature_factor(10, 'QB') < 1.0
        assert calculate_temperature_factor(10, 'WR') < 1.0

    def test_warm_boosts_qb(self):
        assert calculate_temperature_factor(95, 'QB') > 1.0

    def test_extreme_temp_neutral(self):
        assert calculate_temperature_factor(200, 'QB') == 1.0


class TestPrecipitationFactor:
    def test_neutral_without_precip(self):
        assert calculate_precipitation_factor(None, 'QB') == 1.0

    def test_rain_penalizes_qb_wr(self):
        assert calculate_precipitation_factor(80, 'QB') < 1.0
        assert calculate_precipitation_factor(80, 'WR') < 1.0

    def test_rain_boosts_rb(self):
        assert calculate_precipitation_factor(80, 'RB') > 1.0


class TestComputeTeamTotals:
    def test_home_and_away_totals(self):
        df = pd.DataFrame([
            {'HomeTeam': 'A', 'AwayTeam': 'B', 'OverUnder': 50.0, 'PointSpread': -3.0},
        ])
        totals, avg = compute_team_totals(df)
        assert totals['A'] == pytest.approx((50.0 - (-3.0)) / 2)
        assert totals['B'] == pytest.approx((50.0 + (-3.0)) / 2)
        assert avg == pytest.approx(25.0)

    def test_aliases(self):
        df = pd.DataFrame([
            {'HomeTeam': 'JAX', 'AwayTeam': 'LVS', 'OverUnder': 44.0, 'PointSpread': 1.0},
        ])
        totals, _ = compute_team_totals(df)
        assert totals['JAC'] == totals['JAX']
        assert totals['LV'] == totals['LVS']

    def test_empty_df(self):
        df = pd.DataFrame(columns=['HomeTeam', 'AwayTeam', 'OverUnder', 'PointSpread'])
        totals, avg = compute_team_totals(df)
        assert totals == {}
        assert avg == 0


class TestCapProjection:
    def test_max_score_cap(self):
        assert cap_projection(40.0, 20.0, 'QB', max_score=27.0) == 27.0

    def test_min_proj_floor(self):
        assert cap_projection(1.0, 20.0, 'QB', min_proj_multiplier=0.5) == 10.0

    def test_def_max_multiplier(self):
        assert cap_projection(25.0, 8.0, 'D', max_def_multiplier=2.0) == 16.0

    def test_non_def_ignores_max_multiplier(self):
        assert cap_projection(25.0, 8.0, 'QB', max_def_multiplier=2.0) == 25.0

    def test_within_bounds_unchanged(self):
        assert cap_projection(21.0, 20.0, 'QB') == 21.0


class TestComputeFdPoints:
    def test_qb(self):
        stats = {'passing_yards': 300, 'passing_tds': 3, 'rushing_yards': 20,
                 'rushing_tds': 0, 'passing_interceptions': 1, 'fumbles_lost_total': 0}
        assert compute_fd_points(stats, 'QB') == pytest.approx(25.0)

    def test_skill_position(self):
        stats = {'rushing_yards': 100, 'rushing_tds': 1, 'receiving_yards': 30,
                 'receiving_tds': 1, 'fumbles_lost_total': 0}
        assert compute_fd_points(stats, 'RB') == pytest.approx(25.0)

    def test_kicker_distance_buckets(self):
        stats = {'fg_made_20_29': 1, 'fg_made_40_49': 1, 'fg_made_50_59': 1, 'pat_made': 2}
        assert compute_fd_points(stats, 'K') == pytest.approx(3 + 4 + 5 + 2)

    def test_missing_fields_default_zero(self):
        assert compute_fd_points({}, 'QB') == 0.0


class TestComputeDefenseFdPoints:
    def test_big_play_scoring(self):
        stats = {'def_tds': 1, 'def_interceptions': 2, 'def_fumbles_forced': 1,
                 'def_sacks': 3, 'def_safeties': 0, 'fumble_recovery_tds': 0}
        assert compute_defense_fd_points(stats) == pytest.approx(15.0)

    def test_empty(self):
        assert compute_defense_fd_points({}) == 0.0


class TestBlendProjections:
    def test_weighted_blend(self):
        assert blend_projections(20.0, 10.0, 0.3) == pytest.approx(17.0)

    def test_missing_secondary_returns_primary(self):
        assert blend_projections(20.0, None) == 20.0
        assert blend_projections(20.0, 0.0) == 20.0

    def test_missing_primary_returns_secondary(self):
        assert blend_projections(None, 10.0) == 10.0