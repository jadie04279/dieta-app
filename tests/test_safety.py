"""Unit tests for core/safety.py."""
import pytest
from core.safety import (
    speed_cap, calorie_floor, bmi, min_weight_for_bmi,
    check_bmi_gate, check_actual_speed, apply_guardrails,
)


class TestSpeedCap:
    def test_light_person_uses_percentage(self):
        assert abs(speed_cap(80.0) - 0.80) < 1e-9

    def test_heavy_person_capped_at_1kg(self):
        assert speed_cap(120.0) == pytest.approx(1.0)

    def test_exactly_100kg_is_1kg(self):
        assert speed_cap(100.0) == pytest.approx(1.0)

    def test_99kg_uses_percentage(self):
        assert speed_cap(99.0) == pytest.approx(0.99)

    def test_monotone_increase(self):
        caps = [speed_cap(w) for w in range(40, 200, 10)]
        for i in range(len(caps) - 1):
            assert caps[i] <= caps[i + 1]


class TestCalorieFloor:
    def test_female_absolute_min(self):
        # BMR 900 → floor = max(1200, 900*1.1=990) = 1200
        assert calorie_floor("female", 900.0) == pytest.approx(1200.0)

    def test_male_absolute_min(self):
        # BMR 1200 → floor = max(1500, 1200*1.1=1320) = 1500
        assert calorie_floor("male", 1200.0) == pytest.approx(1500.0)

    def test_bmr_multiplier_dominates_when_large(self):
        # BMR 2000 female → floor = max(1200, 2200) = 2200
        assert calorie_floor("female", 2000.0) == pytest.approx(2200.0)

    def test_male_bmr_multiplier_dominates(self):
        # BMR 1800 male → floor = max(1500, 1980) = 1980
        assert calorie_floor("male", 1800.0) == pytest.approx(1980.0)


class TestBmi:
    def test_normal_bmi(self):
        # 65 kg, 165 cm → 65 / 1.65² ≈ 23.88
        result = bmi(65.0, 165.0)
        assert abs(result - 23.88) < 0.1

    def test_underweight(self):
        assert bmi(45.0, 165.0) < 18.5

    def test_obese(self):
        assert bmi(100.0, 165.0) > 30.0


class TestMinWeightForBmi:
    def test_height_165(self):
        # BMI 18.5 at 165 cm → 18.5 × 1.65² = 50.33 kg
        result = min_weight_for_bmi(165.0)
        assert abs(result - 50.33) < 0.1

    def test_custom_threshold(self):
        result = min_weight_for_bmi(170.0, bmi_threshold=22.0)
        assert abs(result - 22.0 * 1.70 * 1.70) < 0.01


class TestCheckBmiGate:
    def test_normal_weight_no_flags(self):
        result = check_bmi_gate(65.0, 165.0, 58.0)
        assert result["loss_mode_active"] is True
        assert result["flags"] == []
        assert result["clamped_goal_weight"] == 58.0

    def test_underweight_disables_loss_mode(self):
        result = check_bmi_gate(45.0, 165.0, 40.0)
        assert result["loss_mode_active"] is False
        assert any(f["code"] == "bmi_underweight" for f in result["flags"])

    def test_goal_below_bmi_threshold_is_clamped(self):
        # 165 cm: min weight at BMI 18.5 ≈ 50.3 kg; request 45 kg goal
        result = check_bmi_gate(75.0, 165.0, goal_weight_kg=45.0)
        assert result["clamped_goal_weight"] > 45.0
        assert result["clamped_goal_weight"] >= 50.0
        assert any(f["code"] == "goal_bmi_clamp" for f in result["flags"])
        assert result["loss_mode_active"] is True  # current weight is fine

    def test_goal_at_bmi_boundary_not_clamped(self):
        min_w = min_weight_for_bmi(165.0)
        result = check_bmi_gate(70.0, 165.0, goal_weight_kg=min_w + 0.1)
        assert result["clamped_goal_weight"] == pytest.approx(min_w + 0.1, abs=0.01)


class TestCheckActualSpeed:
    def test_normal_speed_no_alert(self):
        # Lose 0.5 kg over 7 days; weight ~70 kg → cap = 0.70 → fine
        result = check_actual_speed(70.0, 69.5, 7)
        assert result["alert"] is False

    def test_excessive_speed_triggers_alert(self):
        # Lose 1.5 kg over 7 days; weight ~70 kg → cap = 0.70 → alert
        result = check_actual_speed(70.5, 69.0, 7)
        assert result["alert"] is True
        assert result["actual_weekly_loss"] == pytest.approx(1.5, abs=0.01)

    def test_zero_days_no_alert(self):
        result = check_actual_speed(70.0, 70.0, 0)
        assert result["alert"] is False


class TestApplyGuardrails:
    def _profile(self, sex="female"):
        return {
            "sex": sex,
            "birth_date": "1992-01-01",
            "height_cm": 163.0,
            "activity_factor": 1.55,
            "goal_weight_kg": 58.0,
        }

    def _raw(self, tdee_val=2200.0, bmr_val=1500.0, planned_loss=0.5):
        # Defaults chosen so no guardrail fires for a 65 kg female:
        #   speed_cap(65)=0.65 → 0.5 OK; floor=max(1200,1650)=1650; intake=2200-550=1650 OK
        deficit = planned_loss * 7700 / 7
        return {
            "bmr": bmr_val,
            "tdee": tdee_val,
            "planned_loss_kg": planned_loss,
            "daily_deficit": round(deficit, 1),
            "target_intake_kcal": round(tdee_val - deficit, 1),
            "target_exercise_kcal": 0.0,
        }

    def test_normal_no_changes(self):
        targets, flags = apply_guardrails(self._profile(), 65.0, self._raw())
        assert not any(f["code"] == "calorie_floor_applied" for f in flags)
        assert not any(f["code"] == "speed_cap_applied" for f in flags)

    def test_underweight_zeroes_deficit(self):
        profile = self._profile()
        profile["height_cm"] = 163.0
        # Make person underweight: 45 kg
        targets, flags = apply_guardrails(profile, 45.0, self._raw())
        assert targets["planned_loss_kg"] == 0.0
        assert targets["target_intake_kcal"] == targets["tdee"]
        assert any(f["code"] == "bmi_underweight" for f in flags)

    def test_calorie_floor_enforced(self):
        # planned_loss=0.5 is within speed_cap(65)=0.65, so speed-cap won't re-compute intake.
        # Manually set intake to 800 kcal which is well below floor=max(1200, 1650).
        raw = self._raw(tdee_val=2200.0, bmr_val=1500.0, planned_loss=0.5)
        raw["target_intake_kcal"] = 800.0  # artificially low
        targets, flags = apply_guardrails(self._profile(), 65.0, raw)
        assert targets["target_intake_kcal"] >= 1200.0
        assert any(f["code"] == "calorie_floor_applied" for f in flags)

    def test_health_flag_conservative_exercise(self):
        health = {"fasting_glucose": 135, "sbp": 0, "dbp": 0}
        raw = self._raw()
        raw["target_exercise_kcal"] = 500.0
        targets, flags = apply_guardrails(self._profile(), 65.0, raw, health_check=health)
        assert targets["target_exercise_kcal"] <= 150.0
        assert any(f["code"] == "diabetes_risk" for f in flags)
