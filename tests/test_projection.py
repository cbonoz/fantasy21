import pandas as pd
import pytest

from projection import (
    blend_projections,
    calculate_precipitation_factor,
    calculate_temperature_factor,
    calculate_wind_factor,
    cap_projection,
    blend_available_projections,
    compute_role_trend_bonus,
    compute_role_competition_penalty,
    combined_weather_factor,
    compute_recent_form_adjustment,
    compute_qb_health_drop,
    compute_qb_pass_catcher_penalty,
    allocate_vacated_role_bonus,
    adjust_ceiling_for_role_change,
    adjust_ceiling_for_weather,
    compute_kicker_total_bonus,
    compute_same_team_rb_overlap_penalty,
    compute_same_team_wr_overlap_penalty,
    compute_same_team_wr_te_overlap_penalty,
    center_empirical_samples,
    compute_defense_fd_points,
    compute_fd_points,
    compute_fp_projection,
    compute_team_totals,
    compute_wr_injury_bonus,
    qb_quality_multiplier,
    scale_source_weight,
)


class TestWindFactor:
    def test_neutral_without_wind(self):
        assert calculate_wind_factor(None, 'QB') == 1.0
        assert calculate_wind_factor(1, 'QB') == 1.0

    def test_nan_is_neutral(self):
        assert calculate_wind_factor(float('nan'), 'WR') == 1.0

    def test_rb_boosted_by_wind(self):
        assert calculate_wind_factor(18, 'RB') > 1.0

    def test_qb_penalized_by_wind(self):
        assert calculate_wind_factor(25, 'QB') < 1.0

    def test_wr_penalized_more_than_qb_at_extreme(self):
        assert calculate_wind_factor(25, 'WR') < calculate_wind_factor(25, 'QB')

    def test_unknown_position_neutral(self):
        assert calculate_wind_factor(15, 'NOT_A_POS') == 1.0


class TestTemperatureFactor:
    def test_neutral_without_temp(self):
        assert calculate_temperature_factor(None, 'QB') == 1.0

    def test_zero_degrees_is_a_valid_cold_temperature(self):
        assert calculate_temperature_factor(0, 'QB') == 0.96

    def test_nan_is_neutral(self):
        assert calculate_temperature_factor(float('nan'), 'WR') == 1.0

    def test_cold_penalizes_qb_wr(self):
        assert calculate_temperature_factor(10, 'QB') < 1.0
        assert calculate_temperature_factor(10, 'WR') < 1.0

    def test_warm_boosts_qb(self):
        assert calculate_temperature_factor(95, 'QB') > 1.0

    def test_extreme_temp_neutral(self):
        assert calculate_temperature_factor(200, 'QB') == 1.0


class TestPrecipitationFactor:
    def test_neutral_without_precip(self):
        assert calculate_precipitation_factor(None, 'QB') == 1.0

    def test_nan_is_neutral(self):
        assert calculate_precipitation_factor(float('nan'), 'TE') == 1.0

    def test_rain_penalizes_qb_wr(self):
        assert calculate_precipitation_factor(80, 'QB') < 1.0
        assert calculate_precipitation_factor(80, 'WR') < 1.0

    def test_rain_boosts_rb(self):
        assert calculate_precipitation_factor(80, 'RB') > 1.0

    def test_moderate_chance_alone_causes_only_small_passing_penalty(self):
        assert calculate_precipitation_factor(43, 'WR') == pytest.approx(0.98)

    def test_kicker_and_defense_precip_stay_modest(self):
        # A forecast chance is not rain intensity; the combined cap handles the
        # correlated wind/precip product.
        assert calculate_precipitation_factor(80, 'K') == pytest.approx(0.94)
        assert calculate_precipitation_factor(80, 'D') == pytest.approx(1.02)
        assert calculate_precipitation_factor(43, 'K') == 1.0
        assert calculate_precipitation_factor(43, 'D') == 1.0


class TestCombinedWeatherFactor:
    def test_multiplies_the_three_inputs(self):
        expected = (calculate_wind_factor(21, 'K')
                    * calculate_temperature_factor(35, 'K')
                    * calculate_precipitation_factor(84, 'K'))
        assert combined_weather_factor(21, 35, 84, 'K') == pytest.approx(expected)

    def test_cap_bounds_a_compounded_penalty(self):
        uncapped = combined_weather_factor(21, 35, 84, 'K')
        capped = combined_weather_factor(21, 35, 84, 'K', max_adjustment=0.18)
        assert uncapped < capped
        assert capped == pytest.approx(0.82)

    def test_cap_bounds_a_compounded_boost(self):
        uncapped = combined_weather_factor(21, 35, 84, 'D')
        capped = combined_weather_factor(21, 35, 84, 'D', max_adjustment=0.06)
        assert uncapped > capped
        assert capped == pytest.approx(1.06)

    def test_missing_or_invalid_cap_leaves_factor_unchanged(self):
        assert combined_weather_factor(5, 70, 0, 'D') == 1.0
        assert combined_weather_factor(5, 70, 0, 'D', max_adjustment=None) == 1.0
        assert (combined_weather_factor(21, 35, 84, 'K', max_adjustment=-1)
                == pytest.approx(calculate_wind_factor(21, 'K')
                                 * calculate_temperature_factor(35, 'K')
                                 * calculate_precipitation_factor(84, 'K')))


class TestComputeTeamTotals:
    def test_home_and_away_totals(self):
        df = pd.DataFrame([
            {'HomeTeam': 'A', 'AwayTeam': 'B', 'OverUnder': 50.0, 'PointSpread': -3.0},
        ])
        totals, avg = compute_team_totals(df)
        assert totals['A'] == pytest.approx((50.0 - (-3.0)) / 2)
        assert totals['B'] == pytest.approx((50.0 + (-3.0)) / 2)
        assert avg == pytest.approx(25.0)

    def test_aliases(self):
        df = pd.DataFrame([
            {'HomeTeam': 'JAX', 'AwayTeam': 'LVS', 'OverUnder': 44.0, 'PointSpread': 1.0},
        ])
        totals, _ = compute_team_totals(df)
        assert totals['JAC'] == totals['JAX']
        assert totals['LV'] == totals['LVS']

    def test_empty_df(self):
        df = pd.DataFrame(columns=['HomeTeam', 'AwayTeam', 'OverUnder', 'PointSpread'])
        totals, avg = compute_team_totals(df)
        assert totals == {}
        assert avg == 0


class TestCapProjection:
    def test_max_score_cap(self):
        assert cap_projection(40.0, 20.0, 'QB', max_score=27.0) == 27.0

    def test_min_proj_floor(self):
        assert cap_projection(1.0, 20.0, 'QB', min_proj_multiplier=0.5) == 10.0

    def test_def_max_multiplier(self):
        assert cap_projection(25.0, 8.0, 'D', max_def_multiplier=2.0) == 16.0

    def test_non_def_ignores_max_multiplier(self):
        assert cap_projection(25.0, 8.0, 'QB', max_def_multiplier=2.0, max_proj_multiplier=10.0) == 25.0

    def test_max_proj_ceiling(self):
        assert cap_projection(25.0, 8.0, 'QB') == 16.0
        assert cap_projection(25.0, 8.0, 'QB', max_proj_multiplier=3.0) == 24.0

    def test_within_bounds_unchanged(self):
        assert cap_projection(21.0, 20.0, 'QB') == 21.0


class TestRoleAdjustments:
    def test_vacated_bonus_follows_recent_workload_weights(self):
        result = allocate_vacated_role_bonus(2.0, {'starter': 3, 'backup': 1})
        assert result == {'starter': pytest.approx(1.5), 'backup': pytest.approx(0.5)}

    def test_vacated_bonus_ignores_invalid_weights(self):
        assert allocate_vacated_role_bonus(2.0, {'zero': 0, 'missing': float('nan')}) == {}

    def test_role_signal_increases_ceiling_but_is_capped(self):
        assert adjust_ceiling_for_role_change(8.0, 0.75) == pytest.approx(8.75)
        assert adjust_ceiling_for_role_change(8.0, 5.0, max_bonus=1.5) == pytest.approx(9.5)

    def test_negative_role_signal_does_not_reduce_historical_ceiling(self):
        assert adjust_ceiling_for_role_change(8.0, -1.0) == pytest.approx(8.0)

    def test_kicker_implied_total_bonus_is_small_and_bounded(self):
        assert compute_kicker_total_bonus(24.0, 20.0) == pytest.approx(0.4)
        assert compute_kicker_total_bonus(40.0, 20.0) == pytest.approx(0.75)
        assert compute_kicker_total_bonus(None, 20.0) == 0

    def test_weather_adjusts_empirical_ceiling_with_neutral_missing_fallback(self):
        assert adjust_ceiling_for_weather(10.0, 0.9) == pytest.approx(9.0)
        assert adjust_ceiling_for_weather(10.0, None) == pytest.approx(10.0)
        assert adjust_ceiling_for_weather(10.0, 2.0) == pytest.approx(14.0)

    def test_same_team_wr_penalty_only_applies_to_actual_lineup_overlap(self):
        assert compute_same_team_wr_overlap_penalty({'PHI': 1, 'WAS': 1}) == 0
        assert compute_same_team_wr_overlap_penalty({'PHI': 2, 'WAS': 1}) == pytest.approx(2)
        assert compute_same_team_wr_overlap_penalty({'PHI': 3}) == pytest.approx(4)

    def test_same_team_rb_penalty_matches_the_overlap_rule(self):
        assert compute_same_team_rb_overlap_penalty({'PIT': 1}) == 0
        assert compute_same_team_rb_overlap_penalty({'PIT': 2, 'DEN': 1}) == pytest.approx(2)
        assert compute_same_team_rb_overlap_penalty({'PIT': 3}, 1.5) == pytest.approx(3)
        assert compute_same_team_rb_overlap_penalty({'PIT': 2}, 0) == 0

    def test_wr_te_penalty_is_small_bounded_and_requires_both_usage_shares(self):
        assert compute_same_team_wr_te_overlap_penalty(
            0.25, 0.20, points_per_share=4.0, max_penalty=1.25
        ) == pytest.approx(0.8)
        assert compute_same_team_wr_te_overlap_penalty(
            0.50, 0.40, points_per_share=4.0, max_penalty=1.25
        ) == pytest.approx(1.25)
        assert compute_same_team_wr_te_overlap_penalty(None, 0.20) == 0.0


class TestComputeFdPoints:
    def test_qb(self):
        stats = {'passing_yards': 300, 'passing_tds': 3, 'rushing_yards': 20,
                 'rushing_tds': 0, 'passing_interceptions': 1, 'fumbles_lost_total': 0}
        assert compute_fd_points(stats, 'QB') == pytest.approx(25.0)

    def test_skill_position(self):
        stats = {'rushing_yards': 100, 'rushing_tds': 1, 'receiving_yards': 30,
                 'receiving_tds': 1, 'fumbles_lost_total': 0}
        assert compute_fd_points(stats, 'RB') == pytest.approx(25.0)

    def test_receiving_receptions_score_half_a_point_each(self):
        assert compute_fd_points({'receptions': 4}, 'WR') == pytest.approx(2.0)

    def test_two_point_conversions_and_return_tds_score(self):
        qb = {'passing_2pt_conversions': 1, 'rushing_2pt_conversions': 1,
              'special_teams_tds': 1}
        receiver = {'receiving_2pt_conversions': 1, 'rushing_2pt_conversions': 1,
                    'special_teams_tds': 1}
        assert compute_fd_points(qb, 'QB') == pytest.approx(10.0)
        assert compute_fd_points(receiver, 'WR') == pytest.approx(10.0)

    def test_kicker_distance_buckets(self):
        stats = {'fg_made_20_29': 1, 'fg_made_40_49': 1, 'fg_made_50_59': 1, 'pat_made': 2}
        assert compute_fd_points(stats, 'K') == pytest.approx(3 + 4 + 5 + 2)

    def test_missing_fields_default_zero(self):
        assert compute_fd_points({}, 'QB') == 0.0

    def test_lost_fumble_is_two_points_for_skill_players(self):
        assert compute_fd_points({'fumbles_lost_total': 1}, 'WR') == -2.0

    def test_lost_fumble_is_two_points_for_qbs(self):
        assert compute_fd_points({'fumbles_lost_total': 1}, 'QB') == -2.0

    def test_missing_nan_stats_are_zero(self):
        assert compute_fd_points({'receiving_yards': float('nan')}, 'TE') == 0.0


class TestComputeDefenseFdPoints:
    def test_big_play_scoring(self):
        stats = {'def_tds': 1, 'def_interceptions': 2, 'def_fumbles_forced': 1,
                 'fumble_recovery_opp': 1, 'def_sacks': 3, 'def_safeties': 0,
                 'fumble_recovery_tds': 0}
        assert compute_defense_fd_points(stats) == pytest.approx(15.0)

    def test_forced_fumble_is_not_a_recovery_and_blocks_score(self):
        stats = {'def_fumbles_forced': 3, 'def_punt_blocks': 1,
                 'def_fg_blocks': 1, 'def_pat_blocks': 1}
        assert compute_defense_fd_points(stats) == pytest.approx(6.0)

    def test_nan_defense_stats_are_zero(self):
        assert compute_defense_fd_points({'def_sacks': float('nan')}) == 0.0

    def test_empty(self):
        assert compute_defense_fd_points({}) == 0.0


class TestBlendProjections:
    def test_weighted_blend(self):
        assert blend_projections(20.0, 10.0, 0.3) == pytest.approx(17.0)

    def test_missing_secondary_returns_primary(self):
        assert blend_projections(20.0, None) == 20.0
        assert blend_projections(20.0, 0.0) == 20.0

    def test_missing_primary_returns_secondary(self):
        assert blend_projections(None, 10.0) == 10.0

    def test_non_finite_inputs_are_treated_as_missing(self):
        assert blend_projections(float('nan'), 10.0) == 10.0
        assert blend_projections(10.0, float('nan')) == 10.0


class TestBlendAvailableProjections:
    def test_uses_normalized_source_weights(self):
        projections = {
            'fd_fppg': 16.9,
            'nflverse_history': 11.061,
            'sleeper': 11.02,
            'fantasypros': 10.54,
        }
        weights = {
            'fd_fppg': 0.20,
            'nflverse_history': 0.10,
            'sleeper': 0.35,
            'fantasypros': 0.35,
        }
        expected = (16.9 * 0.2 + 11.061 * 0.1 + 11.02 * 0.35 + 10.54 * 0.35)
        assert blend_available_projections(projections, weights) == pytest.approx(expected)

    def test_renormalizes_when_sources_are_missing_or_invalid(self):
        result = blend_available_projections(
            {'fd_fppg': 10, 'nflverse_history': float('nan'), 'sleeper': 20},
            {'fd_fppg': 0.20, 'nflverse_history': 0.10, 'sleeper': 0.35},
        )
        assert result == pytest.approx((10 * 0.20 + 20 * 0.35) / 0.55)

    def test_returns_zero_when_no_positive_source_is_available(self):
        assert blend_available_projections(
            {'fd_fppg': 0, 'sleeper': None},
            {'fd_fppg': 0.20, 'sleeper': 0.35},
        ) == 0.0


class TestRoleTrendBonus:
    def test_rewards_target_share_growth_with_a_cap(self):
        assert compute_role_trend_bonus(0.25, 0.10) == pytest.approx(0.9)
        assert compute_role_trend_bonus(0.40, 0.10) == pytest.approx(1.0)

    def test_no_growth_or_invalid_inputs_get_no_bonus(self):
        assert compute_role_trend_bonus(0.10, 0.20) == 0.0
        assert compute_role_trend_bonus(None, 0.10) == 0.0


class TestRoleCompetitionPenalty:
    def test_penalizes_share_loss_after_a_meaningful_teammate_return(self):
        assert compute_role_competition_penalty(
            0.35, 0.75, 0.30,
        ) == pytest.approx(2.0)

    def test_caps_penalty_and_ignores_small_or_unsupported_changes(self):
        assert compute_role_competition_penalty(
            0.20, 0.90, 0.30,
        ) == pytest.approx(2.5)
        assert compute_role_competition_penalty(
            0.65, 0.70, 0.30,
        ) == 0.0
        assert compute_role_competition_penalty(
            0.35, 0.75, 0.08,
        ) == 0.0
        assert compute_role_competition_penalty(
            None, 0.75, 0.30,
        ) == 0.0


class TestRecentFormAdjustment:
    def test_recent_median_form_is_shrunk_and_bounded(self):
        season = [5, 9, 16, 18, 19, 20, 29, 30]
        recent = [29, 18, 9, 5]
        result = compute_recent_form_adjustment(recent, season)
        assert result == pytest.approx(-1.1666667)

    def test_requires_recent_and_prior_game_samples(self):
        assert compute_recent_form_adjustment([5, 20, 30], [5, 20, 30]) == 0.0
        assert compute_recent_form_adjustment([5, 20], [5, 20, 30, 40]) == 0.0

    def test_missing_and_non_finite_scores_are_ignored(self):
        result = compute_recent_form_adjustment(
            [None, 20, 22, 24], [5, 10, 18, 20, 22, 24]
        )
        assert result > 0


class TestCenterEmpiricalSamples:
    def test_recent_game_shape_is_preserved_around_current_projection(self):
        result = center_empirical_samples([2, 6, 10], target_mean=12)
        assert result == pytest.approx([8, 12, 16])
        assert sum(result) / len(result) == pytest.approx(12)

    def test_invalid_samples_are_dropped(self):
        result = center_empirical_samples([1, float('nan'), 3], target_mean=10)
        assert result == pytest.approx([9, 11])


class TestQbQualityMultiplier:
    def test_average_qb_is_neutral(self):
        assert qb_quality_multiplier(18.0, 18.0) == pytest.approx(1.0)

    def test_above_average_qb_modestly_increases_injury_credit(self):
        assert qb_quality_multiplier(22.0, 18.0) == pytest.approx(1.0 + 0.5 * (4 / 18))

    def test_adjustment_is_bounded(self):
        assert qb_quality_multiplier(40.0, 10.0) == pytest.approx(1.15)
        assert qb_quality_multiplier(2.0, 20.0) == pytest.approx(0.85)

    def test_missing_qb_quality_is_neutral(self):
        assert qb_quality_multiplier(None, 18.0) == pytest.approx(1.0)


class TestQbHealthAdjustments:
    def test_expected_qb_drop_accounts_for_primary_and_backup_availability(self):
        assert compute_qb_health_drop(20, 0.5, [(12, 0)]) == pytest.approx(4.0)
        assert compute_qb_health_drop(20, 0.5, [(12, 0.5), (8, 0)]) == pytest.approx(5.0)

    def test_missing_backup_or_healthy_primary_has_no_drop(self):
        assert compute_qb_health_drop(20, 0.5, []) == 0.0
        assert compute_qb_health_drop(20, 0.0, [(12, 0)]) == 0.0

    def test_pass_catcher_penalty_scales_with_projection_and_is_capped(self):
        assert compute_qb_pass_catcher_penalty(4, 20) == pytest.approx(0.8)
        assert compute_qb_pass_catcher_penalty(30, 30) == pytest.approx(1.25)
        assert compute_qb_pass_catcher_penalty(None, 20) == 0.0


class TestWrInjuryBonus:
    def test_caps_typical_production_then_splits_and_scales_by_qb(self):
        assert compute_wr_injury_bonus(2.4, 0.404, 1.098, 2.0, 1.0) == pytest.approx(0.887184)

    def test_invalid_or_negative_inputs_do_not_create_bonus(self):
        assert compute_wr_injury_bonus(-1, 0.5) == 0.0
        assert compute_wr_injury_bonus(2, float('nan')) == 0.0


class TestComputeFpProjection:
    def test_qb(self):
        stats = {'pass_yds': 260, 'pass_tds': 2, 'rush_yds': 20, 'rush_tds': 0,
                 'pass_ints': 1, 'fl': 0}
        assert compute_fp_projection('QB', stats) == pytest.approx(10.4 + 8 + 2 - 1)

    def test_skill_uses_fanduel_half_ppr(self):
        stats = {'rush_yds': 90, 'rush_tds': 1, 'rec_rec': 5, 'rec_yds': 40,
                 'rec_tds': 0, 'fl': 0}
        assert compute_fp_projection('RB', stats) == pytest.approx(9 + 6 + 4 + 2.5)

    def test_te(self):
        stats = {'rec_rec': 4, 'rec_yds': 50, 'rec_tds': 1, 'fl': 0}
        assert compute_fp_projection('TE', stats) == pytest.approx(5 + 6 + 2)

    def test_kicker(self):
        assert compute_fp_projection('K', {'fg': 2, 'xpt': 3}) == pytest.approx(9)

    def test_missing_fields(self):
        assert compute_fp_projection('QB', {}) == 0.0

    def test_lost_fumble_is_two_points(self):
        assert compute_fp_projection('WR', {'fl': 1}) == -2.0
        assert compute_fp_projection('TE', {'fl': 1}) == -2.0


class TestScaleSourceWeight:
    def test_partial_coverage_scales_the_weight(self):
        assert scale_source_weight(0.35, 0.25) == pytest.approx(0.0875)

    def test_full_coverage_keeps_the_weight(self):
        assert scale_source_weight(0.35, 1.0) == pytest.approx(0.35)
        assert scale_source_weight(0.35, 2.0) == pytest.approx(0.35)

    def test_missing_coverage_keeps_the_weight(self):
        assert scale_source_weight(0.35, None) == pytest.approx(0.35)

    def test_invalid_inputs_are_safe(self):
        assert scale_source_weight(None, 0.5) == 0.0
        assert scale_source_weight(0, 0.5) == 0.0
        assert scale_source_weight(0.35, -1) == 0.0
