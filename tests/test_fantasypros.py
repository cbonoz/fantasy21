import json

import pytest

import fantasypros
from fantasypros import get_projections


class TestGetProjectionsFallback:
    def _write_cache(self, week, entries, cache_dir):
        path = f"{cache_dir}/fantasypros_week_{week}.json"
        if entries is None:
            return path
        with open(path, 'w') as f:
            json.dump(entries, f)
        return path

    def test_empty_cache_falls_back_to_previous_week(self, tmp_path, monkeypatch, capsys):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        prev = [{'position': 'QB', 'name': 'jalen hurts', 'team': 'PHI', 'stats': {}, 'fpts': 20.7}]
        self._write_cache(1, prev, tmp_path)
        empty = self._write_cache(2, [], tmp_path)

        # Fetch returns empty HTML so the current week parses to nothing.
        class Resp:
            status_code = 200
            text = '<html><body>blocked</body></html>'

        monkeypatch.setattr(fantasypros.requests, 'get', lambda *a, **k: Resp())
        result = get_projections(2)

        assert result == prev
        assert __import__('os').path.isfile(empty), "fallback should be cached"
        with open(empty) as f:
            assert json.load(f) == prev
        assert 'cached week 1 projections as fallback' in capsys.readouterr().out

    def test_populated_cache_is_used_without_fetch(self, tmp_path, monkeypatch, capsys):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)
        cached = [{'position': 'RB', 'name': 'bijan robinson', 'team': 'ATL', 'stats': {}, 'fpts': 21.0}]
        self._write_cache(3, cached, tmp_path)

        def fail_fetch(*a, **k):
            raise AssertionError('should not hit the network')

        monkeypatch.setattr(fantasypros.requests, 'get', fail_fetch)
        assert get_projections(3) == cached
        assert 'return cached data' in capsys.readouterr().out

    def test_no_fallback_returns_empty(self, tmp_path, monkeypatch, capsys):
        fantasypros.config.CACHE_FOLDER = str(tmp_path)

        class Resp:
            status_code = 200
            text = '<html></html>'

        monkeypatch.setattr(fantasypros.requests, 'get', lambda *a, **k: Resp())
        assert get_projections(1) == []
        assert 'no fallback available' in capsys.readouterr().out

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
        assert __import__('os').path.isfile(f"{tmp_path}/fantasypros_week_2.json")
        assert 'Saved FantasyPros projections' in capsys.readouterr().out