from types import SimpleNamespace

from optimizer_rules import forbid_opposing_positions_with_defense


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
