from types import SimpleNamespace

from optimizer_rules import (
    forbid_opposing_positions_with_defense,
    is_player_override,
    split_active_locks,
    should_block_backup_qb,
    upload_slots,
)


def test_opposing_position_rule_is_conditional_on_the_defense():
    rule = forbid_opposing_positions_with_defense(
        'KC', 'BUF', ('QB',), roster_size=9
    )

    # Without Kansas City's DST, Buffalo offense may fill the lineup.
    assert rule.comparison(sum, [0], [1] * 8)
    # With that DST selected, an opposing QB is excluded.
    assert rule.comparison(sum, [1], [])
    assert not rule.comparison(sum, [1], [1])


def test_opposing_skill_players_can_be_stacked_with_defense_except_qb():
    rule = forbid_opposing_positions_with_defense(
        'KC', 'BUF', ('QB',), roster_size=9
    )
    for position in ('WR', 'RB', 'TE', 'K'):
        player = SimpleNamespace(pos=position, team='BUF')
        assert not rule.group_b(player)
    assert rule.comparison(sum, [1], [])


def test_manual_backup_qb_override_bypasses_healthy_backup_filter():
    assert not should_block_backup_qb(
        'Mason Rudolph', True, False, ['Mason Rudolph']
    )
    assert should_block_backup_qb(
        'Mason Rudolph', True, False, []
    )
    assert not should_block_backup_qb(
        'Mason Rudolph', True, True, []
    )


def test_general_player_override_matches_mvp_variant():
    assert is_player_override('Mason Rudolph (MVP)', ['Mason Rudolph'])
    assert not is_player_override('Mason Rudolph', [])


def test_split_active_locks_preserves_order_and_ignores_stale_names():
    active, stale = split_active_locks(
        ['Travis Kelce', 'Mason Rudolph', 'Travis Kelce'],
        {'Travis Kelce', 'Emmett Johnson'},
    )
    assert active == ['Travis Kelce']
    assert stale == ['Mason Rudolph']



def test_classic_upload_flex_accepts_a_second_te():
    slots = upload_slots(single_game=False)
    assert [name for name, _ in slots] == [
        'QB', 'RB', 'RB', 'WR', 'WR', 'WR', 'TE', 'FLEX', 'DEF'
    ]
    flex = dict(slots)['FLEX']
    assert 'TE' in flex and 'RB' in flex and 'WR' in flex


def test_single_game_upload_starts_with_mvp_and_has_six_slots():
    slots = upload_slots(single_game=True)
    assert [name for name, _ in slots] == ['MVP'] + ['FLEX'] * 5
    assert 'MVP' in dict(slots)['MVP']
    assert {'QB', 'RB', 'WR', 'TE', 'K', 'D'} <= dict(slots)['FLEX']
