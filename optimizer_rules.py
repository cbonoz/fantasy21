"""Helpers for composing lineup optimizer constraints."""

from draftfast.settings import CustomRule


def split_active_locks(locked_names, active_names):
    """Keep in-slate locks and return stale names separately, preserving order."""
    active_names = set(active_names)
    unique_names = list(dict.fromkeys(locked_names))
    active_locks = [name for name in unique_names if name in active_names]
    stale_locks = [name for name in unique_names if name not in active_names]
    return active_locks, stale_locks


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


def is_player_override(player_name, allowed_players):
    """Match a configured base player name, including its MVP variant."""
    base_name = player_name.replace(' (MVP)', '')
    return base_name in set(allowed_players)


def should_block_backup_qb(player_name, is_backup, starter_injured,
                           allowed_players):
    """Keep healthy backups out unless manually included in the allow list."""
    return (
        is_backup
        and not starter_injured
        and not is_player_override(player_name, allowed_players)
    )
