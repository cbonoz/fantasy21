import pytest
import pandas as pd

from weather import (
    _action_network_weather_records,
    _merge_weather_forecasts,
    _optional_round,
    matchup_coverage,
    parse_weather_data,
)


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


class TestWeatherParsing:
    def test_week_container_does_not_mix_weather_from_other_game_cards(self):
        html = '''
        <div class="container-game-box">
          <div class="game-box">Washington @ Giants 14% 48 °F Rain 7 mph south_west</div>
          <div class="game-box">Patriots @ Lions 8% 52 °F Clear 2 mph north</div>
        </div>
        '''

        weather = parse_weather_data(html)

        was_nyg = weather[weather['away_team'].eq('Washington')].iloc[0]
        assert was_nyg['condition'] == 'Rain'
        assert was_nyg['temperature'] == 48
        assert was_nyg['wind_speed'] == 7
        assert was_nyg['precipitation_chance'] == 14

    def test_action_network_uses_home_away_ids_and_nearest_kickoff_forecast(self):
        data = {'games': [{
            'away_team_id': 145,
            'home_team_id': 144,
            'start_time': '2026-11-13T01:15:00+00:00',
            'teams': [
                {'id': 144, 'abbr': 'NYG'},
                {'id': 145, 'abbr': 'WAS'},
            ],
            'weather_forecast': [
                {'date': '2026-11-13T01:00:00+00:00', 'temperature': 49,
                 'wind_speed': 10, 'precipitation': 0.60, 'description': 'Rain'},
                {'date': '2026-11-13T03:00:00+00:00', 'temperature': 47,
                 'wind_speed': 11, 'precipitation': 0.35, 'description': 'Drizzle'},
            ],
        }]}

        weather = _action_network_weather_records(data)

        assert weather.iloc[0]['away_team'] == 'WAS'
        assert weather.iloc[0]['home_team'] == 'NYG'
        assert weather.iloc[0]['precipitation_chance'] == 60
        assert weather.iloc[0]['wind_speed'] == 10

    def test_hourly_forecast_overrides_matching_row_without_changing_venue(self):
        primary = pd.DataFrame([{
            'away_team': 'Washington', 'home_team': 'Giants',
            'temperature': 48, 'condition': 'Clear', 'wind_speed': 7,
            'precipitation_chance': 14, 'matchup': 'Washington@Giants',
        }])
        hourly = pd.DataFrame([{
            'away_team': 'WAS', 'home_team': 'NYG',
            'temperature': 49, 'condition': 'Rain', 'wind_speed': 10,
            'precipitation_chance': 60, 'matchup': 'WAS@NYG',
        }])

        merged = _merge_weather_forecasts(primary, hourly).iloc[0]

        assert merged['away_team'] == 'Washington'
        assert merged['home_team'] == 'Giants'
        assert merged['condition'] == 'Rain'
        assert merged['precipitation_chance'] == 60
