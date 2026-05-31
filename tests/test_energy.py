"""Unit tests for core/energy.py."""
import pytest
from unittest.mock import patch
from datetime import date

from core.energy import bmr, tdee, age_from_birth, plan_targets


class TestBmr:
    def test_male_known_value(self):
        # 30-year-old male, 80 kg, 175 cm
        # BMR = 10*80 + 6.25*175 - 5*30 + 5 = 800 + 1093.75 - 150 + 5 = 1748.75
        result = bmr("male", 80.0, 175.0, 30)
        assert abs(result - 1748.75) < 0.01

    def test_female_known_value(self):
        # 30-year-old female, 60 kg, 163 cm
        # BMR = 10*60 + 6.25*163 - 5*30 - 161 = 600 + 1018.75 - 150 - 161 = 1307.75
        result = bmr("female", 60.0, 163.0, 30)
        assert abs(result - 1307.75) < 0.01

    def test_male_is_higher_than_female_same_stats(self):
        b_m = bmr("male", 70.0, 170.0, 25)
        b_f = bmr("female", 70.0, 170.0, 25)
        assert b_m > b_f
        assert abs(b_m - b_f) == pytest.approx(166.0, abs=0.01)  # male offset: 5 + 161 = 166

    def test_heavier_weight_raises_bmr(self):
        b_light = bmr("male", 60.0, 170.0, 30)
        b_heavy = bmr("male", 90.0, 170.0, 30)
        assert b_heavy > b_light

    def test_older_age_lowers_bmr(self):
        b_young = bmr("female", 65.0, 160.0, 25)
        b_old = bmr("female", 65.0, 160.0, 55)
        assert b_old < b_young


class TestTdee:
    def test_sedentary(self):
        b = bmr("female", 60.0, 163.0, 30)
        result = tdee(b, 1.2)
        assert abs(result - b * 1.2) < 0.01

    def test_very_active(self):
        b = bmr("male", 80.0, 175.0, 28)
        result = tdee(b, 1.9)
        assert abs(result - b * 1.9) < 0.01


class TestAgeFromBirth:
    def test_exact_birthday_today(self):
        today = date.today()
        birth = date(today.year - 30, today.month, today.day)
        assert age_from_birth(birth) == 30

    def test_birthday_not_yet_this_year(self):
        today = date.today()
        # Birthday is tomorrow: haven't turned the age yet
        next_day = today + __import__("datetime").timedelta(days=1)
        birth = date(today.year - 30, next_day.month, next_day.day)
        assert age_from_birth(birth) == 29

    def test_string_input(self):
        with patch("core.energy.date") as mock_date:
            mock_date.today.return_value = date(2026, 6, 1)
            mock_date.fromisoformat = date.fromisoformat
            assert age_from_birth("1996-01-15") == 30


class TestPlanTargets:
    def _make_profile(self, sex="female", birth="1992-01-01",
                      height=163.0, activity=1.55, goal=58.0):
        return {
            "sex": sex,
            "birth_date": birth,
            "height_cm": height,
            "activity_factor": activity,
            "goal_weight_kg": goal,
        }

    def test_returns_required_keys(self):
        profile = self._make_profile()
        result = plan_targets(profile, 65.0)
        assert "bmr" in result
        assert "tdee" in result
        assert "planned_loss_kg" in result
        assert "target_intake_kcal" in result
        assert "target_exercise_kcal" in result
        assert "daily_deficit" in result

    def test_bmr_less_than_tdee(self):
        profile = self._make_profile()
        result = plan_targets(profile, 65.0)
        assert result["bmr"] < result["tdee"]

    def test_intake_at_least_floor(self):
        # Force a heavy person where deficit would exceed floor
        profile = self._make_profile(sex="female", activity=1.2)
        result = plan_targets(profile, 40.0)  # low weight → low TDEE
        # Floor for female: max(1200, BMR*1.1)
        from core.energy import bmr as _bmr
        from core.safety import calorie_floor
        import datetime
        age = (date.today() - date.fromisoformat(profile["birth_date"])).days // 365
        b = _bmr("female", 40.0, 163.0, age)
        floor = calorie_floor("female", b)
        assert result["target_intake_kcal"] >= floor - 1e-3

    def test_planned_loss_within_cap(self):
        profile = self._make_profile()
        for weight in [50.0, 80.0, 110.0, 150.0]:
            result = plan_targets(profile, weight)
            from core.safety import speed_cap
            cap = speed_cap(weight)
            assert result["planned_loss_kg"] <= cap + 1e-6

    def test_exercise_kcal_non_negative(self):
        profile = self._make_profile()
        result = plan_targets(profile, 65.0)
        assert result["target_exercise_kcal"] >= 0
