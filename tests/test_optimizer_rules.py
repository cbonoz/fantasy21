from types import SimpleNamespace

from optimizer_rules import forbid_opposing_positions_with_defense


def test_opposing_position_rule_is_conditional_on_the_defense():
    rule = forbid_opposing_positions_with_defense(
        'KC', 'BUF', ('QB', 'RB', 'TE', 'K'), roster_size=9
    )

    # Without Kansas City's DST, Buffalo offense may fill the lineup.
    assert rule.comparison(sum, [0], [1] * 8)
    # With that DST selected, opposing non-WR skill players are excluded.
    assert rule.comparison(sum, [1], [])
    assert not rule.comparison(sum, [1], [1])


def test_opposing_receiver_can_be_stacked_with_defense():
    rule = forbid_opposing_positions_with_defense(
        'KC', 'BUF', ('QB', 'RB', 'TE', 'K'), roster_size=9
    )
    receiver = SimpleNamespace(pos='WR', team='BUF')

    assert not rule.group_b(receiver)
    assert rule.comparison(sum, [1], [])
