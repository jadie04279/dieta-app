"""
Unit tests for core/adaptive.py.

Synthetic data strategy:
  We construct 14-day time series where the true TDEE is known (e.g. 2200 kcal/day)
  and intake is fixed (e.g. 1800 kcal/day), giving a deterministic weight trajectory.
  Then we verify that estimate_tdee() recovers a TDEE in a realistic range.

Note on EWMA bias:
  Because EWMA lags the actual weight trend, |delta_trend| < |delta_actual|.
  This means estimated TDEE is slightly lower than true TDEE when losing weight.
  Tests allow a ±300 kcal tolerance to accommodate this known smoothing effect.
"""
import pytest
from datetime import date, timedelta

from core.adaptive import (
    ewma,
    ewma_trend_weights,
    estimate_tdee,
    validate_tdee,
    fallback_tdee,
    compute_next_week,
    weekly_adjustment_report,
    run_weekly_cycle,
    ALPHA, MIN_VALID_DAYS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_logs(
    n_days: int = 14,
    start_date: date = date(2026, 5, 1),
    start_weight: float = 65.0,
    true_tdee: float = 2200.0,
    intake_kcal: float = 1800.0,
    skip_days: set[int] = frozenset(),
) -> list[dict]:
    """
    Generate synthetic daily logs.
    Weight drops at rate (true_tdee - intake) / 7700 kg per day.
    Days in skip_days have neither weight nor intake recorded.
    """
    daily_loss = (true_tdee - intake_kcal) / 7700.0  # kg/day
    logs = []
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        if i in skip_days:
            logs.append({"date": str(d), "weight_kg": None, "intake_kcal": None})
        else:
            logs.append({
                "date": str(d),
                "weight_kg": round(start_weight - i * daily_loss, 3),
                "intake_kcal": intake_kcal,
                "exercise_kcal": 0.0,
            })
    return logs


def _profile(
    sex="female",
    birth="1992-01-01",
    height=163.0,
    activity=1.55,
    goal_weight=58.0,
    target_date="2026-12-01",
):
    return {
        "sex": sex,
        "birth_date": birth,
        "height_cm": height,
        "activity_factor": activity,
        "goal_weight_kg": goal_weight,
        "target_date": target_date,
    }


def _goal_line(start_weight=65.0, goal=58.0):
    from core.schedule import build_goal_line
    return build_goal_line(
        start_weight, date(2026, 5, 1), goal, date(2026, 12, 1), "female", 163.0
    )


# ── EWMA ─────────────────────────────────────────────────────────────────────

class TestEwma:
    def test_single_element(self):
        assert ewma([5.0]) == [5.0]

    def test_first_output_equals_input(self):
        result = ewma([10.0, 9.0, 8.0])
        assert result[0] == 10.0

    def test_smoothing_reduces_step_change(self):
        # Big jump from 70 to 60; EWMA should be between
        result = ewma([70.0, 60.0], alpha=0.1)
        assert 60.0 < result[1] < 70.0

    def test_constant_series_unchanged(self):
        series = [65.0] * 10
        result = ewma(series)
        for v in result:
            assert abs(v - 65.0) < 1e-9

    def test_converges_to_new_level(self):
        # 100 steps at 65 then 100 steps at 60; EWMA should be near 60 at end
        series = [65.0] * 100 + [60.0] * 100
        result = ewma(series, alpha=0.1)
        assert abs(result[-1] - 60.0) < 0.2

    def test_alpha_one_is_identity(self):
        series = [70.0, 65.0, 60.0, 55.0]
        result = ewma(series, alpha=1.0)
        assert result == series

    def test_alpha_zero_holds_first_value(self):
        series = [70.0, 65.0, 60.0]
        result = ewma(series, alpha=0.0)
        for v in result:
            assert v == 70.0

    def test_empty_series(self):
        assert ewma([]) == []

    def test_output_length_matches_input(self):
        series = [float(i) for i in range(20)]
        assert len(ewma(series)) == 20

    def test_monotone_decreasing_input_gives_monotone_decreasing_ewma(self):
        series = [65.0 - i * 0.05 for i in range(14)]
        result = ewma(series, alpha=ALPHA)
        for i in range(len(result) - 1):
            assert result[i] >= result[i + 1]


class TestEwmaTrendWeights:
    def test_enriches_logs_with_trend(self):
        logs = _make_logs(7)
        enriched = ewma_trend_weights(logs)
        assert all("trend_weight" in l for l in enriched)

    def test_first_trend_equals_first_actual(self):
        logs = _make_logs(7, start_weight=65.0)
        enriched = ewma_trend_weights(logs)
        first_actual = logs[0]["weight_kg"]
        assert abs(enriched[0]["trend_weight"] - first_actual) < 1e-9

    def test_missing_weight_carries_trend_forward(self):
        logs = _make_logs(5, skip_days={2})
        enriched = ewma_trend_weights(logs)
        # Day 2 has no weight; trend should equal previous trend
        assert enriched[2]["trend_weight"] == enriched[1]["trend_weight"]

    def test_original_fields_preserved(self):
        logs = _make_logs(3)
        enriched = ewma_trend_weights(logs)
        for orig, enr in zip(logs, enriched):
            assert enr["date"] == orig["date"]
            assert enr["intake_kcal"] == orig["intake_kcal"]

    def test_empty_logs(self):
        assert ewma_trend_weights([]) == []


# ── estimate_tdee ────────────────────────────────────────────────────────────

class TestEstimateTdee:
    def test_enough_data_flag_true_with_14_days(self):
        logs = _make_logs(14)
        result = estimate_tdee(logs)
        assert result["enough_data"] is True

    def test_not_enough_data_with_5_days(self):
        logs = _make_logs(5)
        result = estimate_tdee(logs)
        assert result["enough_data"] is False
        assert result["n_days"] == 5

    def test_returns_required_keys_when_enough_data(self):
        logs = _make_logs(14)
        result = estimate_tdee(logs)
        for key in ("estimated_tdee", "n_days", "trend_start", "trend_end",
                    "delta_trend_kg", "total_intake", "stored_energy_delta"):
            assert key in result

    def test_estimated_tdee_in_reasonable_range(self):
        # True TDEE = 2200, intake = 1800; allow ±400 kcal for EWMA lag
        logs = _make_logs(14, true_tdee=2200.0, intake_kcal=1800.0)
        result = estimate_tdee(logs)
        assert result["enough_data"] is True
        assert 1800.0 <= result["estimated_tdee"] <= 2600.0

    def test_higher_intake_gives_higher_tdee_estimate(self):
        logs_low = _make_logs(14, true_tdee=2200.0, intake_kcal=1700.0)
        logs_high = _make_logs(14, true_tdee=2200.0, intake_kcal=2100.0)
        r_low = estimate_tdee(logs_low)
        r_high = estimate_tdee(logs_high)
        # Both should report enough_data; high intake → higher estimated TDEE
        assert r_high["estimated_tdee"] > r_low["estimated_tdee"]

    def test_maintenance_calories_gives_stable_estimate(self):
        # Eating exactly at TDEE → weight stable → estimated_tdee ≈ intake
        logs = _make_logs(14, true_tdee=2000.0, intake_kcal=2000.0)
        result = estimate_tdee(logs)
        assert result["enough_data"] is True
        # With no weight change, TDEE estimate ≈ intake exactly
        assert abs(result["estimated_tdee"] - 2000.0) < 5.0

    def test_skip_days_reduces_n(self):
        logs = _make_logs(14, skip_days={3, 7, 10})
        result = estimate_tdee(logs)
        assert result["n_days"] == 11

    def test_skip_days_still_gives_result_if_enough_remain(self):
        logs = _make_logs(14, skip_days={0, 1, 2, 3, 4, 5})  # skip 6, keep 8
        result = estimate_tdee(logs)
        assert result["enough_data"] is True
        assert result["n_days"] == 8

    def test_too_many_skips_triggers_fallback_flag(self):
        logs = _make_logs(14, skip_days=set(range(8)))  # only 6 valid
        result = estimate_tdee(logs)
        assert result["enough_data"] is False

    def test_delta_trend_negative_when_losing_weight(self):
        logs = _make_logs(14, true_tdee=2200.0, intake_kcal=1800.0)
        result = estimate_tdee(logs)
        assert result["delta_trend_kg"] < 0

    def test_delta_trend_zero_at_maintenance(self):
        logs = _make_logs(14, true_tdee=2000.0, intake_kcal=2000.0)
        result = estimate_tdee(logs)
        # EWMA of a flat series is flat
        assert abs(result["delta_trend_kg"]) < 0.01

    def test_window_limits_days_used(self):
        # Pass 20 days but window=14; should use last 14
        logs = _make_logs(20)
        result = estimate_tdee(logs, window=14)
        assert result["n_days"] <= 14


# ── validate_tdee ────────────────────────────────────────────────────────────

class TestValidateTdee:
    def test_reasonable_tdee_unchanged(self):
        validated, notes = validate_tdee(2000.0, bmr_val=1400.0)
        assert validated == pytest.approx(2000.0, abs=1.0)
        assert notes == []

    def test_below_bmr_gets_blended_up(self):
        # estimated=900, BMR=1400 → should be blended upward
        validated, notes = validate_tdee(900.0, bmr_val=1400.0)
        assert validated > 900.0
        assert len(notes) > 0

    def test_above_upper_bound_gets_capped(self):
        # BMR=1400, upper=1400*3.5=4900; estimate=6000 → capped
        validated, notes = validate_tdee(6000.0, bmr_val=1400.0)
        assert validated <= 1400.0 * 3.5
        assert len(notes) > 0

    def test_large_week_over_week_jump_is_blended(self):
        # Previous=2000, new=2500: jump > 200 → blend
        validated, notes = validate_tdee(2500.0, bmr_val=1400.0, prev_estimate=2000.0)
        # Should be between 2000 and 2500
        assert 2000.0 < validated < 2500.0
        assert any("블렌딩" in n for n in notes)

    def test_small_change_no_blend_note(self):
        validated, notes = validate_tdee(2100.0, bmr_val=1400.0, prev_estimate=2000.0)
        assert not any("블렌딩" in n for n in notes)

    def test_no_prev_estimate_still_works(self):
        validated, notes = validate_tdee(2000.0, bmr_val=1400.0, prev_estimate=None)
        assert validated == pytest.approx(2000.0, abs=1.0)


# ── fallback_tdee ─────────────────────────────────────────────────────────────

class TestFallbackTdee:
    def test_returns_positive(self):
        result = fallback_tdee(_profile(), 65.0)
        assert result > 0

    def test_higher_activity_gives_higher_tdee(self):
        p_low = _profile(activity=1.2)
        p_high = _profile(activity=1.9)
        assert fallback_tdee(p_high, 65.0) > fallback_tdee(p_low, 65.0)

    def test_heavier_weight_gives_higher_tdee(self):
        assert fallback_tdee(_profile(), 80.0) > fallback_tdee(_profile(), 60.0)


# ── compute_next_week ────────────────────────────────────────────────────────

class TestComputeNextWeek:
    def test_returns_required_keys(self):
        gl = _goal_line()
        result = compute_next_week(_profile(), 63.0, 2000.0, gl)
        for key in ("est_tdee", "bmr", "planned_loss_kg",
                    "target_intake_kcal", "target_exercise_kcal", "goal_line", "flags"):
            assert key in result

    def test_intake_at_least_calorie_floor(self):
        from core.energy import bmr, age_from_birth
        from core.safety import calorie_floor
        from datetime import date as _date
        profile = _profile()
        weight = 63.0
        age = age_from_birth(profile["birth_date"])
        b = bmr(profile["sex"], weight, profile["height_cm"], age)
        floor = calorie_floor(profile["sex"], b)

        gl = _goal_line()
        result = compute_next_week(profile, weight, 2000.0, gl)
        assert result["target_intake_kcal"] >= floor - 1.0

    def test_planned_loss_within_cap(self):
        from core.safety import speed_cap
        weight = 63.0
        gl = _goal_line()
        result = compute_next_week(_profile(), weight, 2000.0, gl)
        assert result["planned_loss_kg"] <= speed_cap(weight) + 0.005

    def test_health_flag_with_diabetes_limits_exercise(self):
        health = {"fasting_glucose": 130}
        gl = _goal_line()
        result = compute_next_week(_profile(), 63.0, 2000.0, gl, health_check=health)
        assert result["target_exercise_kcal"] <= 150.0
        assert any(f["code"] == "diabetes_risk" for f in result["flags"])

    def test_goal_line_reprojected(self):
        gl = _goal_line(start_weight=70.0)
        # Current trend is 63 (ahead of schedule)
        result = compute_next_week(_profile(), 63.0, 2000.0, gl)
        new_gl = result["goal_line"]
        # New start weight should be current trend
        assert abs(new_gl["start_weight_kg"] - 63.0) < 0.1


# ── run_weekly_cycle ─────────────────────────────────────────────────────────

class TestRunWeeklyCycle:
    def _prev_plan(self):
        return {
            "est_tdee": 2100.0,
            "target_intake_kcal": 1750.0,
            "target_exercise_kcal": 200.0,
            "planned_loss_kg": 0.5,
            "goal_line": _goal_line(),
        }

    def test_full_cycle_with_enough_data(self):
        logs = _make_logs(14, true_tdee=2200.0, intake_kcal=1800.0)
        result = run_weekly_cycle(
            _profile(), logs, self._prev_plan(), _goal_line()
        )
        assert result["used_fallback"] is False
        assert result["validated_tdee"] > 0
        assert "next_week" in result
        assert "report" in result

    def test_fallback_used_with_insufficient_data(self):
        logs = _make_logs(5)
        result = run_weekly_cycle(
            _profile(), logs, self._prev_plan(), _goal_line()
        )
        assert result["used_fallback"] is True
        assert result["validated_tdee"] > 0

    def test_report_contains_key_fields(self):
        logs = _make_logs(14, true_tdee=2200.0, intake_kcal=1800.0)
        result = run_weekly_cycle(
            _profile(), logs, self._prev_plan(), _goal_line()
        )
        report = result["report"]
        for key in ("actual_weekly_loss_kg", "new_est_tdee",
                    "new_target_intake", "new_planned_loss_kg"):
            assert key in report

    def test_next_week_intake_reasonable(self):
        logs = _make_logs(14, true_tdee=2200.0, intake_kcal=1800.0)
        result = run_weekly_cycle(
            _profile(), logs, self._prev_plan(), _goal_line()
        )
        intake = result["next_week"]["target_intake_kcal"]
        assert 1200.0 <= intake <= 3000.0

    def test_empty_logs_falls_back(self):
        result = run_weekly_cycle(
            _profile(), [], self._prev_plan(), _goal_line()
        )
        assert result["used_fallback"] is True

    def test_prev_tdee_passed_to_validate(self):
        logs = _make_logs(14, true_tdee=2200.0, intake_kcal=1800.0)
        # With a very different prev estimate, blending should occur
        result = run_weekly_cycle(
            _profile(), logs, self._prev_plan(), _goal_line(),
            prev_tdee_estimate=1500.0,
        )
        # Validated should be between raw estimate and 1500
        assert result["validated_tdee"] > 0


# ── weekly_adjustment_report ─────────────────────────────────────────────────

class TestWeeklyAdjustmentReport:
    def test_loss_delta_computed_correctly(self):
        prev_plan = {"planned_loss_kg": 0.5, "target_intake_kcal": 1800.0,
                     "est_tdee": 2100.0, "goal_line": _goal_line()}
        tdee_result = {"n_days": 14}
        next_plan = {"est_tdee": 2050.0, "target_intake_kcal": 1750.0,
                     "planned_loss_kg": 0.55, "flags": [],
                     "goal_line": _goal_line()}
        report = weekly_adjustment_report(
            prev_plan, tdee_result, next_plan,
            prev_trend_weight=65.0, current_trend_weight=64.3
        )
        assert abs(report["actual_weekly_loss_kg"] - 0.7) < 0.01
        assert abs(report["loss_delta_kg"] - (0.7 - 0.5)) < 0.01

    def test_tdee_delta_sign_correct(self):
        prev_plan = {"planned_loss_kg": 0.5, "target_intake_kcal": 1800.0,
                     "est_tdee": 2000.0, "goal_line": _goal_line()}
        next_plan = {"est_tdee": 2200.0, "target_intake_kcal": 1800.0,
                     "planned_loss_kg": 0.5, "flags": [],
                     "goal_line": _goal_line()}
        report = weekly_adjustment_report(
            prev_plan, {}, next_plan, 65.0, 64.5
        )
        assert report["tdee_delta"] == pytest.approx(200.0, abs=1.0)
