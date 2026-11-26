import os
import time

import pandas as pd
import pytest

import odds


def _write_lines(path):
    pd.DataFrame([
        {'AwayTeam': 'KC', 'HomeTeam': 'BUF', 'PointSpread': -2.84, 'OverUnder': 46.59},
    ]).to_csv(path, index=False)


def _payload():
    return {'results': [{
        'team1Initials': 'KC',
        'team2Initials': 'BUF',
        'odds': [{'spread': -1.5, 'overUnder': 48.0}],
    }]}


class TestSpreadFreshness:
    def test_fresh_cache_is_used_without_fetch(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs('spread')
        _write_lines('spread/week_12.csv')

        def fail_fetch(*args, **kwargs):
            raise AssertionError('fresh cache should not refetch')

        monkeypatch.setattr(odds.requests, 'request', fail_fetch)
        result = odds.get_metabet_spread(12)

        assert result.iloc[0]['AwayTeam'] == 'KC'
        assert result.iloc[0]['OverUnder'] == pytest.approx(46.59)

    def test_stale_lines_are_refreshed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs('spread')
        _write_lines('spread/week_12.csv')
        old = time.time() - (odds.config.ODDS_CACHE_MAX_AGE_MINUTES + 5) * 60
        os.utime('spread/week_12.csv', (old, old))

        class Resp:
            def json(self):
                return _payload()

        monkeypatch.setattr(odds.requests, 'request', lambda *a, **k: Resp())
        result = odds.get_metabet_spread(12)

        assert result.iloc[0]['PointSpread'] == pytest.approx(-1.5)
        assert result.iloc[0]['OverUnder'] == pytest.approx(48.0)

    def test_failed_refresh_falls_back_to_stale_lines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs('spread')
        _write_lines('spread/week_12.csv')
        old = time.time() - (odds.config.ODDS_CACHE_MAX_AGE_MINUTES + 5) * 60
        os.utime('spread/week_12.csv', (old, old))

        def fail_fetch(*args, **kwargs):
            raise RuntimeError('network down')

        monkeypatch.setattr(odds.requests, 'request', fail_fetch)
        result = odds.get_metabet_spread(12)

        assert result.iloc[0]['PointSpread'] == pytest.approx(-2.84)

    def test_empty_payload_returns_empty_frame(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs('spread')

        class Resp:
            def json(self):
                return {'results': []}

        monkeypatch.setattr(odds.requests, 'request', lambda *a, **k: Resp())
        result = odds.get_metabet_spread(12)

        assert result.empty
