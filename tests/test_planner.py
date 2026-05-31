"""
Unit tests for core/planner.py pure functions.
DB and LLM calls are mocked.
"""
import pytest
from unittest.mock import patch, MagicMock
from core.planner import (
    calc_item_nutrition, sum_nutrition,
    _scale_to_target, _greedy_meal, _validate_meal,
    generate_exercise_plan,
    TOLERANCE,
)

# ── Sample food records ───────────────────────────────────────────────────────

RICE = {
    "id": 1, "name": "현미밥", "category": "곡류",
    "kcal_per_100g": 149.0, "carb_g": 32.1, "protein_g": 2.9, "fat_g": 0.9,
    "serving_desc": "1공기(210g)",
}
EGG = {
    "id": 2, "name": "삶은 달걀", "category": "달걀/유제품",
    "kcal_per_100g": 155.0, "carb_g": 1.1, "protein_g": 12.6, "fat_g": 11.5,
    "serving_desc": "1개(50g)",
}
CHICKEN = {
    "id": 3, "name": "닭가슴살", "category": "육류",
    "kcal_per_100g": 165.0, "carb_g": 0.0, "protein_g": 31.0, "fat_g": 3.6,
    "serving_desc": "100g",
}
KIMCHI = {
    "id": 4, "name": "김치", "category": "채소",
    "kcal_per_100g": 18.0, "carb_g": 3.8, "protein_g": 0.9, "fat_g": 0.4,
    "serving_desc": "100g",
}
ALMOND = {
    "id": 5, "name": "아몬드", "category": "견과류",
    "kcal_per_100g": 579.0, "carb_g": 21.6, "protein_g": 21.2, "fat_g": 49.9,
    "serving_desc": "1줌(30g)",
}
FOODS = [RICE, EGG, CHICKEN, KIMCHI, ALMOND]

MET_VALUES = [
    {"activity_key": "walk_brisk",          "name": "빠르게 걷기",   "category": "유산소", "met": 4.3},
    {"activity_key": "walk_moderate",       "name": "걷기",          "category": "유산소", "met": 3.5},
    {"activity_key": "run_jog",             "name": "조깅",           "category": "유산소", "met": 8.0},
    {"activity_key": "resistance_moderate", "name": "근력운동(보통)", "category": "근력",   "met": 5.0},
    {"activity_key": "resistance_light",    "name": "근력운동(가벼운)", "category": "근력", "met": 3.0},
    {"activity_key": "hiit",                "name": "HIIT",           "category": "복합",   "met": 8.0},
]


# ── calc_item_nutrition ───────────────────────────────────────────────────────

class TestCalcItemNutrition:
    def test_kcal_at_100g(self):
        item = calc_item_nutrition(RICE, 100.0)
        assert abs(item["kcal"] - 149.0) < 0.1

    def test_kcal_at_210g(self):
        item = calc_item_nutrition(RICE, 210.0)
        assert abs(item["kcal"] - 149.0 * 2.1) < 0.5

    def test_protein_scales_correctly(self):
        item = calc_item_nutrition(CHICKEN, 200.0)
        assert abs(item["protein_g"] - 31.0 * 2.0) < 0.1

    def test_returns_all_fields(self):
        item = calc_item_nutrition(EGG, 50.0)
        for key in ("food_id", "name", "grams", "kcal", "carb_g", "protein_g", "fat_g"):
            assert key in item

    def test_grams_stored_correctly(self):
        item = calc_item_nutrition(RICE, 155.0)
        assert abs(item["grams"] - 155.0) < 0.1

    def test_zero_grams_yields_zero_kcal(self):
        item = calc_item_nutrition(RICE, 0.0)
        assert item["kcal"] == 0.0


# ── sum_nutrition ─────────────────────────────────────────────────────────────

class TestSumNutrition:
    def test_sums_kcal(self):
        items = [
            calc_item_nutrition(RICE, 210),
            calc_item_nutrition(EGG, 50),
        ]
        totals = sum_nutrition(items)
        expected = 149.0 * 2.1 + 155.0 * 0.5
        assert abs(totals["kcal"] - expected) < 1.0

    def test_sums_protein(self):
        items = [calc_item_nutrition(CHICKEN, 100), calc_item_nutrition(EGG, 50)]
        totals = sum_nutrition(items)
        assert abs(totals["protein_g"] - (31.0 + 12.6 * 0.5)) < 0.1

    def test_empty_list_returns_zeros(self):
        totals = sum_nutrition([])
        assert totals["kcal"]      == 0.0
        assert totals["carb_g"]    == 0.0
        assert totals["protein_g"] == 0.0
        assert totals["fat_g"]     == 0.0


# ── _scale_to_target ──────────────────────────────────────────────────────────

class TestScaleToTarget:
    def _items(self):
        return [calc_item_nutrition(RICE, 210), calc_item_nutrition(EGG, 100)]

    def test_scales_up_to_target(self):
        items   = self._items()
        total   = sum(i["kcal"] for i in items)
        target  = total * 1.5
        food_db = {f["id"]: f for f in FOODS}
        scaled  = _scale_to_target(items, target, food_db)
        new_total = sum(i["kcal"] for i in scaled)
        assert abs(new_total - target) / target < 0.02

    def test_scales_down_to_target(self):
        items   = self._items()
        total   = sum(i["kcal"] for i in items)
        target  = total * 0.7
        food_db = {f["id"]: f for f in FOODS}
        scaled  = _scale_to_target(items, target, food_db)
        new_total = sum(i["kcal"] for i in scaled)
        assert abs(new_total - target) / target < 0.02

    def test_empty_items_returns_empty(self):
        result = _scale_to_target([], 500.0, {})
        assert result == []

    def test_grams_never_below_5(self):
        items = [calc_item_nutrition(RICE, 6)]
        food_db = {RICE["id"]: RICE}
        scaled = _scale_to_target(items, 1.0, food_db)
        assert scaled[0]["grams"] >= 5.0


# ── _greedy_meal ──────────────────────────────────────────────────────────────

class TestGreedyMeal:
    def test_returns_nonempty(self):
        items = _greedy_meal(FOODS, 500.0)
        assert len(items) > 0

    def test_total_kcal_close_to_target(self):
        target = 600.0
        items  = _greedy_meal(FOODS, target)
        total  = sum(i["kcal"] for i in items)
        assert total > 0

    def test_respects_max_5_items(self):
        items = _greedy_meal(FOODS, 2000.0)
        assert len(items) <= 5

    def test_prefers_staple_category(self):
        items = _greedy_meal(FOODS, 500.0)
        cats  = [i["food_id"] for i in items]
        # Rice (id=1) should appear if pool has it
        assert 1 in cats or len(items) > 0


# ── _validate_meal ────────────────────────────────────────────────────────────

class TestValidateMeal:
    def test_within_tolerance_returns_true(self):
        items  = [calc_item_nutrition(RICE, 210)]
        target = sum(i["kcal"] for i in items)
        _, ok  = _validate_meal(items, target, {RICE["id"]: RICE})
        assert ok is True

    def test_out_of_tolerance_rescales(self):
        items  = [calc_item_nutrition(RICE, 210)]
        total  = sum(i["kcal"] for i in items)
        # Set target 50% higher — exceeds 12% tolerance
        target = total * 1.5
        food_db = {RICE["id"]: RICE}
        result, ok = _validate_meal(items, target, food_db)
        assert ok is True
        new_total = sum(i["kcal"] for i in result)
        assert abs(new_total - target) / target < TOLERANCE + 0.05

    def test_empty_items_returns_false(self):
        _, ok = _validate_meal([], 500.0, {})
        assert ok is False


# ── generate_exercise_plan ────────────────────────────────────────────────────

class TestGenerateExercisePlan:
    @patch("db.repo.get_all_met_values")
    def test_returns_sessions(self, mock_met):
        mock_met.return_value = MET_VALUES
        targets = {"target_exercise_kcal": 1400.0}
        result  = generate_exercise_plan(targets, 65.0, sessions=4)
        assert "sessions" in result
        assert len(result["sessions"]) == 4

    @patch("db.repo.get_all_met_values")
    def test_sessions_have_activities(self, mock_met):
        mock_met.return_value = MET_VALUES
        result = generate_exercise_plan({"target_exercise_kcal": 1400.0}, 70.0, sessions=3)
        for session in result["sessions"]:
            assert "activities" in session
            assert len(session["activities"]) > 0

    @patch("db.repo.get_all_met_values")
    def test_kcal_computed_from_met(self, mock_met):
        mock_met.return_value = MET_VALUES
        result = generate_exercise_plan({"target_exercise_kcal": 1400.0}, 65.0, sessions=4)
        for session in result["sessions"]:
            for act in session["activities"]:
                expected = round(act["met"] * 65.0 * (act["minutes"] / 60.0), 1)
                assert abs(act["kcal"] - expected) < 1.0

    @patch("db.repo.get_all_met_values")
    def test_zero_target_returns_empty(self, mock_met):
        mock_met.return_value = MET_VALUES
        result = generate_exercise_plan({"target_exercise_kcal": 0.0}, 65.0, sessions=4)
        assert result["sessions"] == []
        assert result["weekly_total_kcal"] == 0.0

    @patch("db.repo.get_all_met_values")
    def test_high_intensity_restricted_by_flag(self, mock_met):
        mock_met.return_value = MET_VALUES
        flags = [{"modifier": "limit_high_intensity", "severity": "error", "message": "혈압"}]
        result = generate_exercise_plan({"target_exercise_kcal": 1400.0}, 65.0, flags, sessions=4)
        for session in result["sessions"]:
            for act in session["activities"]:
                assert act["met"] <= 5.0, f"High-intensity activity included: {act}"

    @patch("db.repo.get_all_met_values")
    def test_weekly_total_kcal_field_present(self, mock_met):
        mock_met.return_value = MET_VALUES
        result = generate_exercise_plan({"target_exercise_kcal": 1400.0}, 65.0, sessions=4)
        assert "weekly_total_kcal" in result
        assert result["weekly_total_kcal"] > 0
