import json
import os
import time

import pytest

import fantasypros
from fantasypros import build_projection_map, get_projections


@pytest.fixture(autouse=True)
def _isolate_api_key(monkeypatch):
    """A developer's .env must not leak into the test suite."""
    monkeypatch.delenv('FANTASYPROS_API_KEY', raising=False)
    monkeypatch.delenv('FANTASYPROS_FORCE_REFRESH', raising=False)
    monkeypatch.setattr(fantasypros.config, 'FANTASYPROS_USE_API', False)
    # Keep tests fast; pacing is asserted explicitly where it matters.
    monkeypatch.setattr(
        fantasypros.config, 'FANTASYPROS_API_REQUEST_DELAY_SECONDS', 0
    )
    monkeypatch.setattr(fantasypros.time, 'sleep', lambda *_: None)


class TestGetProjectionsFallback:
    def _write_cache(self, week, entries, cache_dir):
        path = f"{cache_dir}/fantasypros_week_{week}.json"
        if entries is None:
            return path
        with open(path, 'w') as f:
            json.dump(entries, f)
        return path

    def test_previous_week_is_not_used_and_failure_is_negative_cached(self, tmp_path, monkeypatch, capsys):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        prev = [{'position': 'QB', 'name': 'jalen hurts', 'team': 'PHI', 'stats': {}, 'fpts': 20.7}]
        self._write_cache(1, prev, tmp_path)
        empty = f"{tmp_path}/fantasypros_week_2.json"

        # Fetch returns empty HTML so the current week parses to nothing.
        class Resp:
            status_code = 200
            text = '<html><body>blocked</body></html>'

        calls = []
        monkeypatch.setattr(fantasypros.requests, 'get', lambda *a, **k: calls.append(k) or Resp())
        result = get_projections(2)

        assert result == []
        with open(empty) as f:
            assert json.load(f) == []
        status = fantasypros.get_projection_source_status()
        assert status['status'] == 'unavailable'
        assert status['requested_week'] == 2
        assert status['source_week'] is None
        assert 'week 1 cache exists but is not used' in status['detail']
        assert get_projections(2) == []
        assert len(calls) == len(fantasypros.POSITIONS), 'negative cache should prevent repeated fetches'
        assert 'cached unavailable data' in capsys.readouterr().out

    def test_populated_cache_is_used_without_fetch(self, tmp_path, monkeypatch, capsys):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        cached = [{'position': 'RB', 'name': 'bijan robinson', 'team': 'ATL', 'stats': {}, 'fpts': 21.0}]
        self._write_cache(3, cached, tmp_path)

        def fail_fetch(*a, **k):
            raise AssertionError('should not hit the network')

        monkeypatch.setattr(fantasypros.requests, 'get', fail_fetch)
        assert get_projections(3) == cached
        assert fantasypros.get_projection_source_status()['status'] == 'ok'
        assert 'return cached data' in capsys.readouterr().out

    def test_no_fallback_returns_empty(self, tmp_path, monkeypatch, capsys):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)

        class Resp:
            status_code = 200
            text = '<html></html>'

        monkeypatch.setattr(fantasypros.requests, 'get', lambda *a, **k: Resp())
        assert get_projections(1) == []
        assert fantasypros.get_projection_source_status()['status'] == 'unavailable'
        assert 'current-week scrape unavailable' in capsys.readouterr().out

    def test_fetch_success_writes_cache(self, tmp_path, monkeypatch, capsys):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)

        class Resp:
            status_code = 200
            text = '<table>' \
                   '<tr class="mpb-player-0"><td><a>Jalen Hurts</a>PHI</td>' \
                   '<td>20</td><td>1</td><td>2</td><td>3</td><td>0</td>' \
                   '<td>1</td><td>2</td><td>3</td><td>0</td><td>20.7</td></tr>' \
                   '</table>'

        monkeypatch.setattr(fantasypros.requests, 'get', lambda *a, **k: Resp())
        result = get_projections(2)

        assert any(e['position'] == 'QB' and e['name'] == 'jalen hurts' for e in result)
        assert fantasypros.get_projection_source_status()['status'] == 'ok'
        assert fantasypros.get_projection_source_status()['source_week'] == 2
        assert __import__('os').path.isfile(f"{tmp_path}/fantasypros_week_2.json")
        assert 'Saved FantasyPros projections' in capsys.readouterr().out

    def test_parses_embedded_report_config_rows(self):
        payload = {'table': {'rows': [{
            'player': {'name': 'Jalen Hurts', 'team': 'PHI'},
            'pass_yds': 240.5, 'pass_tds': 2, 'pass_ints': 1,
            'rush_yds': 24, 'rush_tds': 1, 'fumbles': 0.2, 'fpts': 23.0,
        }]}}
        html = '<script>window.FP.reportConfig = ' + json.dumps(payload) + ';</script>'
        diagnostics = []

        entries = fantasypros._parse_position('QB', html, diagnostics)

        assert len(entries) == 1
        assert entries[0]['name'] == 'jalen hurts'
        assert entries[0]['team'] == 'PHI'
        assert entries[0]['stats']['pass_yds'] == pytest.approx(240.5)
        assert entries[0]['stats']['fl'] == pytest.approx(0.2)
        assert 'full-table pagination not exposed' in diagnostics[0]

    def test_partial_embedded_rows_are_cached_and_reused(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        monkeypatch.delenv('FANTASYPROS_API_KEY', raising=False)
        monkeypatch.delenv('FANTASYPROS_FORCE_REFRESH', raising=False)
        payload = {'table': {'rows': [{
            'player': {'name': 'Test Player', 'team': 'KC'},
            'pass_yds': 100, 'pass_tds': 1, 'pass_ints': 0,
            'rush_yds': 10, 'rush_tds': 0, 'rec_att': 3,
            'rec_yds': 40, 'rec_tds': 0, 'fumbles': 0,
            'fg': 1, 'fga': 1, 'xpt': 2, 'fpts': 12,
            'def_sack': 2, 'def_int': 1, 'def_fr': 0, 'def_ff': 1,
            'def_td': 0, 'def_safety': 0, 'def_pa': 20,
            'def_tyda': 300,
        }]}}
        html = '<script>window.FP.reportConfig = ' + json.dumps(payload) + ';</script>'

        class Resp:
            status_code = 200
            text = html
            headers = {'Content-Type': 'text/html'}
            url = 'https://www.fantasypros.com/nfl/projections/test.php'

        calls = []
        monkeypatch.setattr(
            fantasypros.requests, 'get', lambda *args, **kwargs: calls.append(args[0]) or Resp()
        )
        entries = get_projections(9)

        assert len(entries) == len(fantasypros.POSITIONS)
        assert fantasypros.get_projection_source_status()['status'] == 'degraded'
        cache_file = f"{tmp_path}/fantasypros_week_9.json"
        assert os.path.isfile(cache_file)
        assert get_projections(9) == entries
        assert len(calls) == len(fantasypros.POSITIONS)

    def test_degraded_provenance_survives_failure_ttl_while_cache_is_fresh(
            self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        monkeypatch.delenv('FANTASYPROS_FORCE_REFRESH', raising=False)
        cached = [{'position': 'TE', 'name': 'test player', 'team': 'KC', 'stats': {}, 'fpts': 9.0}]
        self._write_cache(12, cached, tmp_path)
        now = __import__('time').time()
        # The scrape partial-succeeded 3 hours ago (past the 2h failure TTL)
        # but the data itself is still inside the 6h cache TTL.
        with open(f"{tmp_path}/fantasypros_week_12.status.json", 'w') as f:
            json.dump({
                'status': 'degraded', 'requested_week': 12, 'source_week': 12,
                'detail': 'partial current-week fetch', 'method': 'html',
                'updated_at': now - 3 * 60 * 60,
            }, f)

        def fail_fetch(*args, **kwargs):
            raise AssertionError('fresh partial cache should not refetch')

        monkeypatch.setattr(fantasypros.requests, 'get', fail_fetch)

        assert get_projections(12) == cached
        status = fantasypros.get_projection_source_status()
        assert status['status'] == 'degraded'
        assert status['source_week'] == 12

    def test_expired_degraded_cache_is_refetched(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        monkeypatch.delenv('FANTASYPROS_FORCE_REFRESH', raising=False)
        cached = [{'position': 'TE', 'name': 'test player', 'team': 'KC', 'stats': {}, 'fpts': 9.0}]
        self._write_cache(12, cached, tmp_path)
        old = __import__('time').time() - 7 * 60 * 60
        __import__('os').utime(f"{tmp_path}/fantasypros_week_12.json", (old, old))
        with open(f"{tmp_path}/fantasypros_week_12.status.json", 'w') as f:
            json.dump({
                'status': 'degraded', 'requested_week': 12, 'source_week': 12,
                'detail': 'partial current-week fetch', 'method': 'html',
                'updated_at': old,
            }, f)

        class Resp:
            status_code = 503
            text = ''
            url = 'https://www.fantasypros.com/nfl/projections/test.php'
            headers = {'Content-Type': 'text/html'}

        calls = []
        monkeypatch.setattr(
            fantasypros.requests, 'get', lambda *a, **k: calls.append(1) or Resp()
        )
        get_projections(12)
        assert calls, 'expired partial cache should trigger a refresh attempt'

    def test_configured_api_key_uses_official_json_projections(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        monkeypatch.setattr(fantasypros.config, 'FANTASYPROS_USE_API', True)
        monkeypatch.setenv('FANTASYPROS_API_KEY', 'test-key')

        class Resp:
            status_code = 200
            text = ''

            def __init__(self, position):
                self.position = position

            def json(self):
                return {'players': [{
                    'name': f'Test {self.position}',
                    'team_id': 'KC',
                    'position_id': self.position,
                    'stats': [{'points': 12.5}],
                }]}

        requests_seen = []

        def fake_get(url, headers, params, timeout):
            requests_seen.append((url, headers, params))
            return Resp(params['position'])

        monkeypatch.setattr(fantasypros.requests, 'get', fake_get)
        result = build_projection_map(8)

        assert result[('KC', 'QB', 'test qb')] == 12.5
        assert result[('KC', 'D', '')] == 12.5
        assert len(requests_seen) == len(fantasypros.POSITIONS)
        assert all(item[1]['x-api-key'] == 'test-key' for item in requests_seen)
        status = fantasypros.get_projection_source_status()
        assert status['status'] == 'ok'
        assert status['method'] == 'api'
        assert status['source_week'] == 8


class TestApiFallback:
    def test_failed_api_key_falls_back_to_html(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        monkeypatch.setattr(fantasypros.config, 'FANTASYPROS_USE_API', True)
        monkeypatch.setenv('FANTASYPROS_API_KEY', 'bad-key')
        monkeypatch.setattr(
            fantasypros, '_fetch_api_projections',
            lambda week, key: ([], ['QB: API HTTP 403; {"message":"Forbidden"}']),
        )
        payload = {'table': {'rows': [{
            'player': {'name': 'Test Player', 'team': 'KC'},
            'pass_yds': 100, 'pass_tds': 1, 'pass_ints': 0,
            'rush_yds': 10, 'rush_tds': 0, 'rec_att': 3,
            'rec_yds': 40, 'rec_tds': 0, 'fumbles': 0,
            'fg': 1, 'fga': 1, 'xpt': 2, 'fpts': 12,
            'def_sack': 2, 'def_int': 1, 'def_fr': 0, 'def_ff': 1,
            'def_td': 0, 'def_safety': 0, 'def_pa': 20, 'def_tyda': 300,
        }]}}
        html = '<script>window.FP.reportConfig = ' + json.dumps(payload) + ';</script>'

        class Resp:
            status_code = 200
            text = html
            headers = {'Content-Type': 'text/html'}
            url = 'https://www.fantasypros.com/nfl/projections/test.php'

        calls = []
        monkeypatch.setattr(
            fantasypros.requests, 'get', lambda *a, **k: calls.append(a[0]) or Resp()
        )

        entries = get_projections(9)

        assert entries, 'an unusable key must not disable the source'
        assert len(calls) == len(fantasypros.POSITIONS)
        status = fantasypros.get_projection_source_status()
        assert status['status'] == 'degraded'
        assert status['method'] == 'html'
        assert status.get('api_fallback') is True

        calls_after_first = len(calls)
        assert get_projections(9) == entries
        assert len(calls) == calls_after_first, 'the fallback cache should be reused'

    def test_no_key_uses_html_without_calling_the_api(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)

        def api_must_not_run(*args, **kwargs):
            raise AssertionError('API should not be called without a key')

        monkeypatch.setattr(fantasypros, '_fetch_api_projections', api_must_not_run)

        class Resp:
            status_code = 503
            text = ''
            headers = {'Content-Type': 'text/html'}
            url = 'https://www.fantasypros.com/nfl/projections/test.php'

        monkeypatch.setattr(fantasypros.requests, 'get', lambda *a, **k: Resp())
        get_projections(9)

        assert fantasypros.get_projection_source_status()['method'] == 'html'


class TestApiRetry:
    def test_auth_failure_stops_after_one_position(self, monkeypatch):
        calls = []

        class Resp:
            status_code = 403
            text = '{"message":"Forbidden"}'
            headers = {}

        monkeypatch.setattr(
            fantasypros.requests, 'get',
            lambda *a, **k: calls.append(k.get('params')) or Resp(),
        )

        entries, diagnostics = fantasypros._fetch_api_projections(12, 'bad-key')

        assert entries == []
        assert len(calls) == 1, '403 must not be retried for every position'
        assert 'stopping API attempt' in diagnostics[0]

    def test_transient_failure_is_retried(self, monkeypatch):
        state = {'calls': 0}

        class Err:
            status_code = 503
            text = 'unavailable'
            headers = {}

        class Ok:
            status_code = 200
            text = ''
            headers = {}

            def json(self):
                return {'players': [{
                    'name': 'Test Player', 'team_id': 'KC',
                    'position_id': 'QB', 'stats': {'points': 12.5},
                }]}

        def fake_get(*args, **kwargs):
            state['calls'] += 1
            return Err() if state['calls'] == 1 else Ok()

        monkeypatch.setattr(fantasypros.requests, 'get', fake_get)
        monkeypatch.setattr(fantasypros.time, 'sleep', lambda *_: None)

        entries, diagnostics = fantasypros._fetch_api_projections(12, 'key')

        assert entries, 'the retry should recover the first position'
        assert state['calls'] == 7, 'one retry for the first call, then five positions'

    def test_retries_are_bounded(self, monkeypatch):
        calls = {'n': 0}

        class Err:
            status_code = 500
            text = 'server error'
            headers = {}

        def fake_get(*args, **kwargs):
            calls['n'] += 1
            return Err()

        monkeypatch.setattr(fantasypros.requests, 'get', fake_get)
        monkeypatch.setattr(fantasypros.time, 'sleep', lambda *_: None)

        entries, diagnostics = fantasypros._fetch_api_projections(12, 'key')

        assert entries == []
        assert calls['n'] == 2 * len(fantasypros.POSITIONS)


class TestApiPacing:
    def test_positions_are_spaced(self, monkeypatch):
        monkeypatch.setattr(
            fantasypros.config, 'FANTASYPROS_API_REQUEST_DELAY_SECONDS', 1.5
        )
        sleeps = []
        monkeypatch.setattr(fantasypros.time, 'sleep', lambda s: sleeps.append(s))

        class Ok:
            status_code = 200
            text = ''
            headers = {}

            def json(self):
                return {'players': [{
                    'name': 'Test Player', 'team_id': 'KC',
                    'position_id': 'QB', 'stats': {'points': 12.5},
                }]}

        monkeypatch.setattr(fantasypros.requests, 'get', lambda *a, **k: Ok())
        entries, diagnostics = fantasypros._fetch_api_projections(12, 'key')

        assert entries
        assert sleeps == [1.5] * (len(fantasypros.POSITIONS) - 1)

    def test_rate_limit_stops_after_the_retry(self, monkeypatch):
        calls = {'n': 0}

        class TooMany:
            status_code = 429
            text = 'rate limited'
            headers = {}

        def fake_get(*args, **kwargs):
            calls['n'] += 1
            return TooMany()

        monkeypatch.setattr(fantasypros.requests, 'get', fake_get)
        monkeypatch.setattr(fantasypros.time, 'sleep', lambda *_: None)

        entries, diagnostics = fantasypros._fetch_api_projections(12, 'key')

        assert entries == []
        # One attempt + one retry, then stop instead of trying the other positions.
        assert calls['n'] == 2
        assert 'stopping API attempt' in diagnostics[0]


class TestCurrentWeekCacheReuse:
    def _entries(self, count):
        return [{
            'position': 'QB', 'name': f'player {i}', 'team': 'KC',
            'stats': {}, 'fpts': 1.0,
        } for i in range(count)]

    def test_substantial_expired_cache_is_reused(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        entries = self._entries(40)
        path = f"{tmp_path}/fantasypros_week_12.json"
        with open(path, 'w') as f:
            json.dump(entries, f)
        old = time.time() - 48 * 60 * 60
        os.utime(path, (old, old))

        def fail_fetch(*args, **kwargs):
            raise AssertionError('a substantial current-week cache should be reused')

        monkeypatch.setattr(fantasypros.requests, 'get', fail_fetch)

        assert get_projections(12) == entries
        status = fantasypros.get_projection_source_status()
        assert 'reused current-week cache' in status['detail']

    def test_thin_expired_cache_is_refreshed(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        entries = self._entries(5)
        path = f"{tmp_path}/fantasypros_week_12.json"
        with open(path, 'w') as f:
            json.dump(entries, f)
        old = time.time() - 48 * 60 * 60
        os.utime(path, (old, old))

        class Resp:
            status_code = 503
            text = ''
            headers = {'Content-Type': 'text/html'}
            url = 'https://www.fantasypros.com/nfl/projections/test.php'

        calls = []
        monkeypatch.setattr(
            fantasypros.requests, 'get', lambda *a, **k: calls.append(1) or Resp()
        )

        get_projections(12)

        assert calls, 'a thin cache should trigger a refresh'

    def test_force_refresh_bypasses_reuse(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        entries = self._entries(40)
        path = f"{tmp_path}/fantasypros_week_12.json"
        with open(path, 'w') as f:
            json.dump(entries, f)
        old = time.time() - 48 * 60 * 60
        os.utime(path, (old, old))
        monkeypatch.setenv('FANTASYPROS_FORCE_REFRESH', '1')

        class Resp:
            status_code = 503
            text = ''
            headers = {'Content-Type': 'text/html'}
            url = 'https://www.fantasypros.com/nfl/projections/test.php'

        calls = []
        monkeypatch.setattr(
            fantasypros.requests, 'get', lambda *a, **k: calls.append(1) or Resp()
        )

        get_projections(12)

        assert calls


class TestApiDisabledByDefault:
    def test_key_is_ignored_when_the_api_is_off(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        monkeypatch.setenv('FANTASYPROS_API_KEY', 'configured-key')
        monkeypatch.setattr(fantasypros.config, 'FANTASYPROS_USE_API', False)

        def api_must_not_run(*args, **kwargs):
            raise AssertionError('the API should be disabled by default')

        monkeypatch.setattr(fantasypros, '_fetch_api_projections', api_must_not_run)

        class Resp:
            status_code = 503
            text = ''
            headers = {'Content-Type': 'text/html'}
            url = 'https://www.fantasypros.com/nfl/projections/test.php'

        monkeypatch.setattr(fantasypros.requests, 'get', lambda *a, **k: Resp())

        get_projections(9)

        assert fantasypros.get_projection_source_status()['method'] == 'html'
