import json
import os

import pytest

import fantasypros
from fantasypros import build_projection_map, get_projections


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

    def test_configured_api_key_uses_official_json_projections(self, tmp_path, monkeypatch):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
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
