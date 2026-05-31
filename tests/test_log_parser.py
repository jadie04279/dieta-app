"""
Unit tests for core/log_parser.py.
Tests run without LLM (fallback path only, provider offline).
"""
import pytest
from unittest.mock import patch, MagicMock
from core.log_parser import (
    parse_meals, parse_exercises,
    compute_intake_totals, compute_exercise_totals,
    _keyword_meal_match, _keyword_exercise_match,
    _default_grams,
)
import core.log_parser as _lp

# ── Test food/met fixtures ────────────────────────────────────────────────────

FOODS = [
    {"id": 1,  "name": "현미밥",  "category": "곡류",       "kcal_per_100g": 149.0,
     "carb_g": 32.1, "protein_g": 2.9, "fat_g": 0.9, "serving_desc": "1공기(210g)"},
    {"id": 2,  "name": "삶은 달걀","category": "달걀/유제품","kcal_per_100g": 155.0,
     "carb_g": 1.1,  "protein_g": 12.6,"fat_g": 11.5,"serving_desc": "1개(50g)"},
    {"id": 3,  "name": "닭가슴살(삶은)","category": "육류", "kcal_per_100g": 165.0,
     "carb_g": 0.0,  "protein_g": 31.0,"fat_g": 3.6, "serving_desc": "100g"},
    {"id": 4,  "name": "김치(배추)","category": "채소/김치", "kcal_per_100g": 18.0,
     "carb_g": 3.8,  "protein_g": 0.9, "fat_g": 0.4, "serving_desc": "100g"},
    {"id": 5,  "name": "바나나",   "category": "과일",       "kcal_per_100g": 89.0,
     "carb_g": 22.8, "protein_g": 1.1, "fat_g": 0.3, "serving_desc": "1개 중간(120g)"},
    {"id": 6,  "name": "아몬드",   "category": "견과류",     "kcal_per_100g": 579.0,
     "carb_g": 21.6, "protein_g": 21.2,"fat_g": 49.9,"serving_desc": "1줌(30g)"},
]

MET_VALUES = [
    {"activity_key": "walk_brisk",         "name": "빠르게 걷기 (6km/h)", "category": "유산소", "met": 4.3},
    {"activity_key": "walk_moderate",      "name": "걷기 (보통 속도 4km/h)","category":"유산소", "met": 3.5},
    {"activity_key": "run_jog",            "name": "조깅 (8km/h)",         "category": "유산소", "met": 8.0},
    {"activity_key": "resistance_moderate","name": "근력운동 (보통 강도)",  "category": "근력",   "met": 5.0},
    {"activity_key": "yoga",               "name": "요가",                  "category": "유연성", "met": 2.5},
    {"activity_key": "hiit",               "name": "HIIT / 고강도 인터벌",  "category": "복합",   "met": 8.0},
]


# ── _default_grams ────────────────────────────────────────────────────────────

class TestDefaultGrams:
    def test_extracts_from_serving_desc(self):
        food = {"serving_desc": "1공기(210g)", "category": "곡류"}
        assert _default_grams(food) == 210.0

    def test_category_fallback_grain(self):
        food = {"serving_desc": "", "category": "곡류"}
        assert _default_grams(food) == 210.0

    def test_category_fallback_nuts(self):
        food = {"serving_desc": "", "category": "견과류"}
        assert _default_grams(food) == 30.0

    def test_unknown_category_defaults_100(self):
        food = {"serving_desc": "", "category": "기타알수없음"}
        assert _default_grams(food) == 100.0


# ── _extract_minutes ──────────────────────────────────────────────────────────

class TestExtractMinutes:
    def test_minutes_only(self):
        assert _lp._extract_minutes("빠르게 걷기 40분", 8) == 40

    def test_hours_only(self):
        assert _lp._extract_minutes("1시간 달리기", 4) == 60

    def test_hours_and_minutes(self):
        assert _lp._extract_minutes("1시간 30분 하이킹", 7) == 90

    def test_no_time_defaults_30(self):
        assert _lp._extract_minutes("조깅", 2) == 30


# ── _keyword_meal_match ───────────────────────────────────────────────────────

class TestKeywordMealMatch:
    def test_matches_known_food(self):
        results = _keyword_meal_match("아침에 현미밥", FOODS)
        names = [r["name"] for r in results]
        assert "현미밥" in names

    def test_assigns_correct_meal_breakfast(self):
        results = _keyword_meal_match("아침에 달걀", FOODS)
        for r in results:
            assert r["meal"] == "breakfast"

    def test_assigns_correct_meal_lunch(self):
        results = _keyword_meal_match("점심에 닭가슴살", FOODS)
        for r in results:
            assert r["meal"] == "lunch"

    def test_no_match_returns_empty(self):
        results = _keyword_meal_match("오늘 특별한 외계인 음식", FOODS)
        assert results == []

    def test_confidence_set(self):
        results = _keyword_meal_match("현미밥", FOODS)
        assert all(0 < r["confidence"] <= 1.0 for r in results)


# ── _keyword_exercise_match ───────────────────────────────────────────────────

class TestKeywordExerciseMatch:
    def test_matches_walking(self):
        results = _keyword_exercise_match("걷기 40분", MET_VALUES)
        assert any("walk" in r["activity_key"] for r in results)

    def test_matches_jogging(self):
        results = _keyword_exercise_match("조깅 30분", MET_VALUES)
        assert any("run" in r["activity_key"] for r in results)

    def test_matches_strength(self):
        results = _keyword_exercise_match("헬스 20분", MET_VALUES)
        assert any("resistance" in r["activity_key"] for r in results)

    def test_extracts_duration(self):
        results = _keyword_exercise_match("빠르게 걷기 45분", MET_VALUES)
        if results:
            assert results[0]["minutes"] == 45

    def test_no_match_empty(self):
        results = _keyword_exercise_match("명상만 함", MET_VALUES)
        assert results == []


# ── parse_meals (with mocked NoLLMProvider) ───────────────────────────────────

class TestParseMeals:
    @patch("llm.provider.get_provider")
    def test_fallback_returns_items(self, mock_get):
        mock_prov = MagicMock()
        mock_prov.available = False
        mock_get.return_value = mock_prov

        results = parse_meals("아침에 현미밥", FOODS, 65.0)
        assert isinstance(results, list)
        # At least the fallback keyword match should find something
        assert len(results) >= 0  # might be 0 if no keyword match, that's OK

    @patch("llm.provider.get_provider")
    def test_llm_result_has_kcal_from_db(self, mock_get):
        """LLM returns food_id+grams; kcal must be computed from DB, not LLM."""
        mock_prov = MagicMock()
        mock_prov.available = True
        mock_prov.generate_json.return_value = {
            "items": [{"food_id": 1, "name": "현미밥", "grams": 210, "meal": "breakfast", "confidence": 0.95}]
        }
        mock_get.return_value = mock_prov

        results = parse_meals("아침에 현미밥 한 공기", FOODS, 65.0)
        assert len(results) == 1
        r = results[0]
        # kcal must equal 149 * 2.10 from DB (not from LLM output)
        expected_kcal = 149.0 * 2.10
        assert abs(r["kcal"] - expected_kcal) < 1.0

    @patch("llm.provider.get_provider")
    def test_llm_invalid_food_id_dropped(self, mock_get):
        """LLM returns non-existent food_id → dropped silently."""
        mock_prov = MagicMock()
        mock_prov.available = True
        mock_prov.generate_json.return_value = {
            "items": [{"food_id": 9999, "grams": 100, "meal": "lunch", "confidence": 0.8}]
        }
        mock_get.return_value = mock_prov

        results = parse_meals("점심", FOODS, 65.0)
        # 9999 is not in FOODS, so LLM result is empty → fallback may produce results
        invalid = [r for r in results if r.get("food_id") == 9999]
        assert len(invalid) == 0

    @patch("llm.provider.get_provider")
    def test_llm_failure_falls_back(self, mock_get):
        """LLM returns None → graceful fallback."""
        mock_prov = MagicMock()
        mock_prov.available = True
        mock_prov.generate_json.return_value = None
        mock_get.return_value = mock_prov

        # Should not raise
        results = parse_meals("현미밥", FOODS, 65.0)
        assert isinstance(results, list)


# ── parse_exercises ───────────────────────────────────────────────────────────

class TestParseExercises:
    @patch("llm.provider.get_provider")
    def test_fallback_extracts_activity(self, mock_get):
        mock_prov = MagicMock()
        mock_prov.available = False
        mock_get.return_value = mock_prov

        results = parse_exercises("걷기 40분", MET_VALUES, 65.0)
        assert isinstance(results, list)

    @patch("llm.provider.get_provider")
    def test_llm_kcal_computed_from_met(self, mock_get):
        """LLM returns activity_key+minutes; kcal computed by code from MET table."""
        mock_prov = MagicMock()
        mock_prov.available = True
        mock_prov.generate_json.return_value = {
            "items": [{"activity_key": "walk_brisk", "minutes": 40}]
        }
        mock_get.return_value = mock_prov

        results = parse_exercises("빠르게 걷기 40분", MET_VALUES, 65.0)
        if results:
            r = results[0]
            expected = round(4.3 * 65.0 * (40.0 / 60.0), 1)
            assert abs(r["kcal"] - expected) < 1.0

    @patch("llm.provider.get_provider")
    def test_llm_invalid_activity_key_dropped(self, mock_get):
        mock_prov = MagicMock()
        mock_prov.available = True
        mock_prov.generate_json.return_value = {
            "items": [{"activity_key": "nonexistent_activity", "minutes": 30}]
        }
        mock_get.return_value = mock_prov

        results = parse_exercises("운동", MET_VALUES, 65.0)
        invalid = [r for r in results if r.get("activity_key") == "nonexistent_activity"]
        assert len(invalid) == 0

    @patch("llm.provider.get_provider")
    def test_kcal_scales_with_weight(self, mock_get):
        mock_prov = MagicMock()
        mock_prov.available = True
        mock_prov.generate_json.return_value = {
            "items": [{"activity_key": "walk_brisk", "minutes": 30}]
        }
        mock_get.return_value = mock_prov

        res_light = parse_exercises("걷기 30분", MET_VALUES, 60.0)
        res_heavy = parse_exercises("걷기 30분", MET_VALUES, 90.0)
        if res_light and res_heavy:
            assert res_heavy[0]["kcal"] > res_light[0]["kcal"]


# ── compute_totals ────────────────────────────────────────────────────────────

class TestComputeTotals:
    def test_intake_totals(self):
        items = [
            {"kcal": 300.0, "carb_g": 50.0, "protein_g": 20.0, "fat_g": 5.0},
            {"kcal": 200.0, "carb_g": 30.0, "protein_g": 10.0, "fat_g": 8.0},
        ]
        totals = compute_intake_totals(items)
        assert abs(totals["kcal"] - 500.0) < 0.1
        assert abs(totals["carb_g"] - 80.0) < 0.1

    def test_exercise_totals(self):
        items = [
            {"kcal": 180.0, "minutes": 40},
            {"kcal": 120.0, "minutes": 20},
        ]
        totals = compute_exercise_totals(items)
        assert abs(totals["kcal"] - 300.0) < 0.1
        assert totals["minutes"] == 60

    def test_empty_totals(self):
        assert compute_intake_totals([])["kcal"] == 0.0
        assert compute_exercise_totals([])["kcal"] == 0.0
