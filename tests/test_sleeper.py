import pytest

from nfl_teams import normalize_player_name
import sleeper
from sleeper import build_projection_map
import json
import os
import time


def _projections():
    return [
        {'team': 'KC', 'player': {'first_name': 'Travis', 'last_name': 'Kelce', 'position': 'TE'},
         'stats': {'pts_std': 6.98}},
        {'team': 'PHI', 'player': {'first_name': 'Marquise', 'last_name': 'Brown', 'position': 'WR'},
         'stats': {'pts_std': 4.21}},
        {'team': 'LAC', 'player': {'first_name': 'Tre', 'last_name': 'Harris', 'position': 'WR'},
         'stats': {'pts_std': 4.1}},
        {'team': 'ARI', 'player': {'first_name': 'Zonovan', 'last_name': 'Knight', 'position': 'RB'},
         'stats': {'pts_std': 5.78}},
        {'team': 'CHI', 'player': {'first_name': 'Luther', 'last_name': 'Burden', 'position': 'WR'},
         'stats': {'pts_std': 7.41}},
        {'team': 'LV', 'player': {'position': 'DEF'}, 'stats': {'pts_std': 5.22}},
        {'team': 'SF', 'player': {'position': 'DEF'}, 'stats': {'pts_std': 9.51}},
    ]


class TestBuildProjectionMap:
    def test_defenses_keyed_by_team(self, monkeypatch):
        monkeypatch.setattr(sleeper, 'get_projections', lambda week, season=None: _projections())
        result = build_projection_map(3)

        # DEF entries have no player full name; must be keyed by (team, 'DEF', '').
        assert result[('LV', 'DEF', '')] == 5.22
        assert result[('SF', 'DEF', '')] == 9.51

    def test_skill_players_still_keyed_by_name(self, monkeypatch):
        monkeypatch.setattr(sleeper, 'get_projections', lambda week, season=None: _projections())
        result = build_projection_map(3)

        assert result[('KC', 'TE', 'travis kelce')] == 6.98

    def test_projection_matches_roster_name_with_suffix(self, monkeypatch):
        monkeypatch.setattr(sleeper, 'get_projections', lambda week, season=None: _projections())
        result = build_projection_map(3)

        roster_key = normalize_player_name('Luther Burden III')
        assert result[('CHI', 'WR', roster_key)] == 7.41

    def test_nickname_and_apostrophe_aliases_match_provider_names(self, monkeypatch):
        monkeypatch.setattr(sleeper, 'get_projections', lambda week, season=None: _projections())
        result = build_projection_map(8)

        hollywood_key = normalize_player_name('Hollywood Brown')
        tre_key = normalize_player_name("Tre' Harris")
        assert result[('PHI', 'WR', hollywood_key)] == 4.21
        assert result[('LAC', 'WR', tre_key)] == 4.1
        bam_key = normalize_player_name('Bam Knight')
        assert result[('ARI', 'RB', bam_key)] == 5.78

    def test_uses_sleeper_half_ppr_projection_when_available(self, monkeypatch):
        projections = [{
            'team': 'CHI',
            'player': {'first_name': 'Luther', 'last_name': 'Burden', 'position': 'WR'},
            'stats': {'pts_std': 8.0, 'pts_half_ppr': 10.5, 'rec': 5.0},
        }]
        monkeypatch.setattr(sleeper, 'get_projections', lambda week, season=None: projections)

        result = build_projection_map(9)

        assert result[('CHI', 'WR', 'luther burden')] == pytest.approx(10.5)

    def test_converts_standard_projection_when_half_ppr_is_missing(self, monkeypatch):
        projections = [{
            'team': 'CHI',
            'player': {'first_name': 'Luther', 'last_name': 'Burden', 'position': 'WR'},
            'stats': {'pts_std': 8.0, 'rec': 5.0},
        }]
        monkeypatch.setattr(sleeper, 'get_projections', lambda week, season=None: projections)

        result = build_projection_map(9)

        assert result[('CHI', 'WR', 'luther burden')] == pytest.approx(10.5)


class TestProjectionFreshness:
    def test_fresh_cache_is_returned_without_fetch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sleeper.config, 'CACHE_FOLDER', str(tmp_path))
        payload = _projections()
        with open(f"{tmp_path}/sleeper_week_12.json", 'w') as f:
            json.dump(payload, f)

        def fail_fetch(*args, **kwargs):
            raise AssertionError('fresh cache should not refetch')

        monkeypatch.setattr(sleeper.requests, 'get', fail_fetch)
        assert sleeper.get_projections(12) == payload

    def test_stale_cache_survives_a_failed_refresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sleeper.config, 'CACHE_FOLDER', str(tmp_path))
        payload = _projections()
        path = f"{tmp_path}/sleeper_week_12.json"
        with open(path, 'w') as f:
            json.dump(payload, f)
        old = time.time() - 12 * 60 * 60
        os.utime(path, (old, old))

        def fail_fetch(*args, **kwargs):
            raise RuntimeError('network down')

        monkeypatch.setattr(sleeper.requests, 'get', fail_fetch)
        assert sleeper.get_projections(12) == payload

    def test_cache_older_than_ttl_triggers_a_fetch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sleeper.config, 'CACHE_FOLDER', str(tmp_path))
        path = f"{tmp_path}/sleeper_week_12.json"
        with open(path, 'w') as f:
            json.dump([], f)
        old = time.time() - (sleeper.config.SLEEPER_CACHE_MAX_AGE_HOURS + 1) * 3600
        os.utime(path, (old, old))

        calls = []

        class Resp:
            def json(self):
                return _projections()

        monkeypatch.setattr(
            sleeper.requests, 'get', lambda *a, **k: calls.append(1) or Resp()
        )
        assert sleeper.get_projections(12) == _projections()
        assert calls
