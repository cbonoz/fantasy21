import pytest

from nfl_teams import normalize_player_name
import sleeper
from sleeper import build_projection_map


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
