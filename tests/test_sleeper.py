import pytest

import sleeper
from sleeper import build_projection_map


def _projections():
    return [
        {'team': 'KC', 'player': {'first_name': 'Travis', 'last_name': 'Kelce', 'position': 'TE'},
         'stats': {'pts_std': 6.98}},
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