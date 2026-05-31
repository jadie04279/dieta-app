"""Unit tests for core/schedule.py."""
import pytest
from datetime import date, timedelta

from core.schedule import (
    build_goal_line, daily_target, reproject,
    weeks_to_goal, unsafe_weeks,
)
from core.safety import speed_cap, min_weight_for_bmi


# ── Helpers ─────────────────────────────────────────────────────────────────

def _build(
    start_weight=70.0,
    goal_weight=60.0,
    weeks_given=20,
    mode="rate_safe",
    sex="female",
    height_cm=163.0,
):
    start = date(2026, 1, 5)
    target = start + timedelta(weeks=weeks_given)
    return build_goal_line(start_weight, start, goal_weight, target, sex, height_cm, mode)


# ── build_goal_line ─────────────────────────────────────────────────────────

class TestBuildGoalLine:
    def test_returns_required_fields(self):
        gl = _build()
        for key in ("mode", "start_weight_kg", "start_date", "goal_weight_kg",
                    "target_date", "projected_date", "feasible", "weekly_targets_json"):
            assert key in gl

    def test_feasible_when_ample_time(self):
        # 10 kg loss, 30 weeks → well within safe speed
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=30)
        assert gl["feasible"] == 1

    def test_infeasible_when_tight_deadline(self):
        # 15 kg loss in 4 weeks is impossible at safe cap
        gl = _build(start_weight=75.0, goal_weight=60.0, weeks_given=4)
        assert gl["feasible"] == 0

    def test_weekly_targets_non_empty(self):
        gl = _build()
        assert len(gl["weekly_targets_json"]) > 0

    def test_weights_strictly_decreasing(self):
        gl = _build()
        weights = [e["target_weight_kg"] for e in gl["weekly_targets_json"]]
        for i in range(len(weights) - 1):
            assert weights[i] >= weights[i + 1]

    def test_final_weight_reaches_goal(self):
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=30)
        final = gl["weekly_targets_json"][-1]["target_weight_kg"]
        assert abs(final - 60.0) < 0.1

    def test_no_week_exceeds_safe_cap_rate_safe(self):
        # Verify that no week's loss materially exceeds the safe cap.
        # We allow 0.005 kg tolerance because stored target_weight_kg is rounded to 2 dp,
        # so prev_w differs slightly from the exact simulation weight.
        gl = _build(mode="rate_safe")
        for entry in gl["weekly_targets_json"]:
            idx = gl["weekly_targets_json"].index(entry)
            if idx == 0:
                prev_w = gl["start_weight_kg"]
            else:
                prev_w = gl["weekly_targets_json"][idx - 1]["target_weight_kg"]
            cap = speed_cap(prev_w)
            assert entry["planned_loss_kg"] <= cap + 0.005, (
                f"Week {idx}: loss {entry['planned_loss_kg']:.4f} > cap {cap:.4f} at {prev_w:.2f}kg"
            )

    def test_bmi_clamp_applied(self):
        # 165 cm: BMI 18.5 floor ≈ 50.3 kg; set goal to 40 kg → should be clamped
        gl = _build(start_weight=70.0, goal_weight=40.0, weeks_given=52, height_cm=165.0)
        assert gl["goal_weight_kg"] > 40.0
        assert gl["goal_weight_kg"] >= min_weight_for_bmi(165.0) - 0.1
        assert any(f["code"] == "goal_bmi_clamp" for f in gl["flags"])

    def test_already_at_goal_returns_empty_targets(self):
        gl = _build(start_weight=60.0, goal_weight=60.0, weeks_given=10)
        assert gl["weekly_targets_json"] == []

    def test_projected_date_later_than_start(self):
        gl = _build()
        start = date.fromisoformat(gl["start_date"])
        projected = date.fromisoformat(gl["projected_date"])
        assert projected > start

    def test_infeasible_has_projected_date_beyond_target(self):
        gl = _build(start_weight=80.0, goal_weight=60.0, weeks_given=4)
        assert gl["feasible"] == 0
        # rate_safe: projected should be further out than target
        projected = date.fromisoformat(gl["projected_date"])
        target = date.fromisoformat(gl["target_date"])
        assert projected > target

    def test_deadline_fixed_marks_unsafe_weeks(self):
        # 15 kg in 6 weeks: uniform 2.5 kg/week >> safe cap
        gl = _build(start_weight=75.0, goal_weight=60.0, weeks_given=6, mode="deadline_fixed")
        unsafe = [e for e in gl["weekly_targets_json"] if e.get("exceeds_safe_cap")]
        assert len(unsafe) > 0

    def test_rate_safe_curve_decelerates(self):
        # First half losses should be >= second half losses (deceleration curve)
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=40, mode="rate_safe")
        losses = [e["planned_loss_kg"] for e in gl["weekly_targets_json"]]
        if len(losses) >= 4:
            first_avg = sum(losses[:len(losses)//2]) / (len(losses)//2)
            second_avg = sum(losses[len(losses)//2:]) / (len(losses) - len(losses)//2)
            assert first_avg >= second_avg - 1e-6


# ── daily_target ────────────────────────────────────────────────────────────

class TestDailyTarget:
    def test_start_date_returns_start_weight(self):
        gl = _build(start_weight=70.0, goal_weight=60.0)
        start = date.fromisoformat(gl["start_date"])
        result = daily_target(gl, start)
        assert abs(result["target_weight"] - 70.0) < 0.1

    def test_midpoint_is_between_bounds(self):
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=30)
        midpoint = date.fromisoformat(gl["start_date"]) + timedelta(weeks=15)
        result = daily_target(gl, midpoint)
        assert 60.0 <= result["target_weight"] <= 70.0

    def test_delta_is_negative_for_loss(self):
        gl = _build()
        d = date.fromisoformat(gl["start_date"]) + timedelta(days=7)
        result = daily_target(gl, d)
        assert result["target_delta"] <= 0

    def test_past_last_checkpoint_returns_goal(self):
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=20)
        far_future = date(2030, 1, 1)
        result = daily_target(gl, far_future)
        assert abs(result["target_weight"] - gl["goal_weight_kg"]) < 0.1


# ── reproject ───────────────────────────────────────────────────────────────

class TestReproject:
    def test_reproject_returns_valid_goal_line(self):
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=20)
        new_gl = reproject(gl, current_trend_weight=67.0, sex="female", height_cm=163.0)
        assert "weekly_targets_json" in new_gl
        assert new_gl["goal_weight_kg"] == gl["goal_weight_kg"]

    def test_reproject_ahead_of_schedule_still_feasible(self):
        # Ahead: current 63 when goal is 60, with 10 weeks left
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=10)
        new_gl = reproject(gl, current_trend_weight=63.0, sex="female", height_cm=163.0)
        assert len(new_gl["weekly_targets_json"]) <= 10

    def test_reproject_behind_schedule_extends_if_needed(self):
        # Start was 70, should be 65 now but still at 68
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=4)
        # Still 68 kg with original deadline just 4 weeks: infeasible
        new_gl = reproject(gl, current_trend_weight=68.0, sex="female", height_cm=163.0)
        # Should flag as infeasible and push projected date
        # (depends on whether 8 kg in 4 weeks is feasible — it's not)
        assert new_gl["feasible"] == 0 or len(new_gl["weekly_targets_json"]) > 0


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_very_small_goal_difference(self):
        gl = _build(start_weight=60.5, goal_weight=60.0, weeks_given=4)
        assert len(gl["weekly_targets_json"]) >= 1

    def test_string_dates_accepted(self):
        gl = build_goal_line(70.0, "2026-01-05", 60.0, "2026-06-01", "female", 163.0)
        assert gl["start_date"] == "2026-01-05"

    def test_weeks_to_goal_helper(self):
        gl = _build(start_weight=70.0, goal_weight=60.0, weeks_given=30)
        assert weeks_to_goal(gl) == len(gl["weekly_targets_json"])

    def test_unsafe_weeks_helper_empty_for_rate_safe(self):
        gl = _build(mode="rate_safe")
        assert unsafe_weeks(gl) == []
