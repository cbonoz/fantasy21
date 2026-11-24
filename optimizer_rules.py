"""Helpers for composing lineup optimizer constraints."""

from draftfast.settings import CustomRule


def forbid_opposing_positions_with_defense(
        defense_team, opponent, restricted_positions, roster_size):
    """Forbid selected opposing positions with a DST, conditionally."""
    restricted_positions = frozenset(restricted_positions)
    return CustomRule(
        group_a=lambda p, team=defense_team: p.pos == 'D' and p.team == team,
        group_b=lambda p, team=opponent, positions=restricted_positions: (
            p.team == team and p.pos in positions
        ),
        # If the defense is absent, do not constrain opposing players. If it
        # is selected, require the restricted opposing group to be empty.
        comparison=lambda sum, a, b: (
            roster_size * sum(a) + sum(b) <= roster_size
        ),
    )
