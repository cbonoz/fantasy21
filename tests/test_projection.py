import pandas as pd
import pytest

from projection import (
    calculate_precipitation_factor,
    calculate_temperature_factor,
    calculate_wind_factor,
    cap_projection,
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