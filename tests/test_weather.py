import pytest
import pandas as pd

from weather import _optional_round, matchup_coverage


class TestOptionalWeatherValues:
    def test_preserves_missing_and_non_finite_values(self):
        assert _optional_round(None) is None
        assert _optional_round(float('nan')) is None
        assert _optional_round(float('inf')) is None

    def test_keeps_real_zero_and_scales_precipitation(self):
        assert _optional_round(0) == 0
        assert _optional_round(0.35, scale=100) == 35

    def test_invalid_values_are_missing(self):
        assert _optional_round('unknown') is None


class TestMatchupCoverage:
    def test_ignores_malformed_extras_but_detects_missing_slate_games(self):
        weather = pd.DataFrame([
            {'away_team': 'Titans', 'home_team': 'Bengals'},
            {'away_team': '19', 'home_team': '27'},
        ])
        covered, total, missing = matchup_coverage(
            ['TEN@CIN', 'PHI@WAS'], weather
        )

        assert covered == 1
        assert total == 2
        assert missing == [('PHI', 'WAS')]

    def test_team_aliases_match_schedule_abbreviations(self):
        weather = pd.DataFrame([{'away_team': 'JAX', 'home_team': 'LVS'}])
        covered, total, missing = matchup_coverage(['JAC@LV'], weather)

        assert (covered, total, missing) == (1, 1, [])
