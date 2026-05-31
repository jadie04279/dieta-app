"""
Natural language log parser: Korean meal/exercise text → structured records.

LLM contract:
  Meal:     input text → [{food_id, grams, meal, confidence}]  (NO kcal)
  Exercise: input text → [{activity_key, minutes}]             (NO kcal)
  Code computes all kcal values from DB/MET tables.

When LLM is unavailable or fails, keyword fallback is used.
All results are marked with `source`: "llm" | "fallback".
"""
from __future__ import annotations

import re
from typing import Optional

# Korean portion-size hints (for LLM prompt and fallback)
PORTION_HINTS = """한국어 분량 표현 참고:
- 한 공기 / 1공기 = 밥류 210g
- 한 그릇 = 국물류 800g, 면류 600g
- 한 개 / 1개 = 달걀 50g, 바나나 120g, 사과 200g
- 한 조각 = 빵 30g
- 한 줌 = 견과류 30g
- 1인분 = 한식메뉴 기본 제공량 (음식별 serving_desc 참고)
- 2인분 = 1인분 × 2
- 3인분 = 1인분 × 3
- 곱배기 = 기본 1인분 × 1.7 (면류/국물류)
- 대자 / 큰 것 = 기본 × 1.5
- 소자 / 작은 것 = 기본 × 0.7
- 반 / 절반 = 기본 × 0.5
- 한 캔 = 참치캔 100g
- 한 팩 = 요거트 100g
- 적당히 / 조금 = 50g 기본값
- 한식 메뉴 1인분 기준: 짬뽕 800g, 짜장면 600g, 물회 700g, 비빔밥 600g, 냉면 600g, 김밥 1줄 250g, 떡볶이 300g, 돈까스 200g"""

MEAL_KEYWORDS = {
    "breakfast": ["아침", "조식", "아침밥", "아침식사"],
    "lunch":     ["점심", "중식", "점심밥", "점심식사"],
    "dinner":    ["저녁", "석식", "저녁밥", "저녁식사"],
    "snack":     ["간식", "야식", "디저트", "후식"],
}


# ── Meal parsing ─────────────────────────────────────────────────────────────

def _build_meal_parse_prompt(user_text: str) -> str:
    """AI가 음식명·분량·끼니·총칼로리를 한번에 반환하는 프롬프트."""
    return f"""당신은 한국 식품 영양 전문가입니다. 사용자의 식사 기록에서 음식을 추출하고
실제 섭취한 양에 대한 총 칼로리를 추정하세요.

규칙:
- name: 원래 음식 이름 그대로.
- grams: 실제 섭취량 (그램). 곱배기·2인분·대자 등 수식어를 grams에 반드시 반영.
- meal: breakfast / lunch / dinner / snack 중 하나.
- kcal: grams에 해당하는 총 칼로리 (한국 식품영양소 기준).
- carb_g / protein_g / fat_g: grams에 해당하는 총 탄·단·지 (g).
- 칼로리 추정 시 조리 방법(튀김·구이·찜 등)과 재료를 고려하세요.
- 순수 JSON만 반환하세요. 마크다운 없이.

{PORTION_HINTS}

사용자 식사 기록:
{user_text}

반환 형식 예시:
{{"items": [
  {{"name": "짬뽕", "grams": 1360, "meal": "breakfast", "kcal": 938, "carb_g": 115, "protein_g": 68, "fat_g": 24}},
  {{"name": "물회", "grams": 1400, "meal": "dinner", "kcal": 812, "carb_g": 98, "protein_g": 91, "fat_g": 11}}
]}}"""


def _keyword_meal_match(user_text: str, all_foods: list[dict]) -> list[dict]:
    """Keyword fallback: search foods by name tokens in user text."""
    results: list[dict] = []
    seen_ids: set[int] = set()

    # Tokenize user text to prevent partial matches (e.g. "배" inside "곱배기")
    text_tokens = set(re.split(r"[,，、\s\d]+", user_text))

    # Detect meal per-sentence (아침→breakfast, 저녁→dinner, etc.)
    def _detect_meal(segment: str) -> str:
        for m, keywords in MEAL_KEYWORDS.items():
            if any(k in segment for k in keywords):
                return m
        return "breakfast"

    # Split text into meal segments by sentence delimiters
    segments = re.split(r"[,，.。]+", user_text)

    # Build meal map: token → meal
    token_meal: dict[str, str] = {}
    for seg in segments:
        meal = _detect_meal(seg)
        for tok in re.split(r"[\s\d]+", seg):
            if tok:
                token_meal[tok] = meal

    # Try to match food names as whole tokens only
    for food in all_foods:
        if food["id"] in seen_ids:
            continue
        name = food["name"]
        clean = re.sub(r"[\(（].*?[\)）]", "", name).strip()
        if clean and clean in text_tokens:
            grams = _default_grams(food)
            # Apply portion multipliers from user text
            if any(w in user_text for w in ["곱배기", "곱빼기"]):
                grams = round(grams * 1.7, 0)
            elif "2인분" in user_text:
                grams = round(grams * 2.0, 0)
            elif "3인분" in user_text:
                grams = round(grams * 3.0, 0)
            elif any(w in user_text for w in ["대자", "큰 것", "큰것"]):
                grams = round(grams * 1.5, 0)
            # Detect meal from surrounding sentence
            meal = token_meal.get(clean, "breakfast")
            results.append({
                "food_id":    food["id"],
                "name":       food["name"],
                "grams":      grams,
                "meal":       meal,
                "confidence": 0.7,
                "source":     "fallback",
            })
            seen_ids.add(food["id"])
            if len(results) >= 8:
                break

    return results


def _default_grams(food: dict) -> float:
    """Estimate standard serving grams from serving_desc or category defaults."""
    desc = food.get("serving_desc", "")
    # Extract grams from desc like "1공기(210g)"
    m = re.search(r"\((\d+(?:\.\d+)?)g\)", desc)
    if m:
        return float(m.group(1))
    # Category defaults
    cat = food.get("category", "")
    defaults = {
        "곡류": 210.0, "빵/면": 100.0, "달걀/유제품": 100.0,
        "육류": 150.0, "어패류": 100.0, "채소": 100.0,
        "채소/김치": 70.0, "과일": 150.0, "견과류": 30.0,
        "한식 메뉴": 300.0, "국/찌개": 200.0, "음료": 200.0,
    }
    return defaults.get(cat, 100.0)


def parse_meals(
    user_text: str,
    all_foods: list[dict],
    current_weight: float = 65.0,
) -> list[dict]:
    """
    Parse Korean meal text → list of food items with kcal.

    우선순위:
    1. AI 분석: 음식명·분량·영양을 AI가 직접 추정
       → DB에 같은 이름이 있으면 DB 값으로 덮어씀 (정확도 보장)
       → DB에 없으면 AI 추정값 사용 (source='ai_estimate')
    2. 규칙 기반 fallback: AI 실패 시 키워드 매칭
    """
    from llm.provider import get_provider
    from core.planner import calc_item_nutrition

    # DB 음식명 → food 매핑 (괄호 제거 버전도 포함)
    food_by_name: dict[str, dict] = {}
    for f in all_foods:
        food_by_name[f["name"]] = f
        clean = re.sub(r"[\(（].*?[\)）]", "", f["name"]).strip()
        if clean:
            food_by_name[clean] = f

    provider = get_provider()
    raw_items: list[dict] = []

    # ── Step 1: AI 분석 ───────────────────────────────────────────────────────
    if provider.available:
        prompt = _build_meal_parse_prompt(user_text)
        raw = provider.generate_json(prompt, retries=2)
        if raw and "items" in raw:
            for item in raw["items"]:
                name  = (item.get("name") or "").strip()
                grams = float(item.get("grams") or 0)
                meal  = item.get("meal", "breakfast")
                if not name or grams <= 0:
                    continue

                ai_kcal = float(item.get("kcal") or 0)
                ai_carb = float(item.get("carb_g") or 0)
                ai_prot = float(item.get("protein_g") or 0)
                ai_fat  = float(item.get("fat_g") or 0)

                db_food = food_by_name.get(name)
                if db_food:
                    # DB에 있으면 DB 영양값 사용 (정확)
                    nutr = calc_item_nutrition(db_food, grams)
                    nutr["meal"]       = meal
                    nutr["confidence"] = 0.95
                    nutr["source"]     = "db"
                else:
                    # DB에 없으면 AI가 추정한 총 칼로리 사용
                    if ai_kcal <= 0:
                        continue
                    # 100g당 값 역산 (그램 수정 시 재계산용)
                    kcal_p100 = ai_kcal / grams * 100 if grams > 0 else 0
                    carb_p100 = ai_carb / grams * 100 if grams > 0 else 0
                    prot_p100 = ai_prot / grams * 100 if grams > 0 else 0
                    fat_p100  = ai_fat  / grams * 100 if grams > 0 else 0
                    nutr = {
                        "food_id":   None,
                        "name":      name,
                        "grams":     grams,
                        "kcal":      round(ai_kcal, 1),
                        "carb_g":    round(ai_carb, 1),
                        "protein_g": round(ai_prot, 1),
                        "fat_g":     round(ai_fat,  1),
                        "_kcal_per_100g":    kcal_p100,
                        "_carb_per_100g":    carb_p100,
                        "_protein_per_100g": prot_p100,
                        "_fat_per_100g":     fat_p100,
                        "meal":       meal,
                        "confidence": 0.75,
                        "source":     "ai_estimate",
                    }
                raw_items.append(nutr)

    # ── Step 2: 규칙 기반 fallback ────────────────────────────────────────────
    if not raw_items:
        food_db = {f["id"]: f for f in all_foods}
        for item in _keyword_meal_match(user_text, all_foods):
            fid   = item["food_id"]
            grams = item["grams"]
            if fid in food_db:
                nutr = calc_item_nutrition(food_db[fid], grams)
                nutr["meal"]       = item["meal"]
                nutr["confidence"] = item["confidence"]
                nutr["source"]     = "fallback"
                raw_items.append(nutr)

    return raw_items


def compute_intake_totals(items: list[dict]) -> dict:
    """Sum nutrition across all parsed meal items."""
    from core.planner import sum_nutrition
    return sum_nutrition(items)


# ── Exercise parsing ──────────────────────────────────────────────────────────

def _build_exercise_parse_prompt(user_text: str, met_values: list[dict]) -> str:
    acts_txt = "\n".join(
        f"{m['activity_key']}|{m['name']}|{m['category']}|MET={m['met']}"
        for m in met_values
    )
    return f"""당신은 한국어 운동 기록 파서 겸 운동생리학 전문가입니다.
사용자의 운동 텍스트를 분석해 운동별 정보를 추출하세요.

규칙:
- name: 운동 이름 그대로.
- minutes: 운동 시간을 분으로 변환 ("1시간" = 60, "30분" = 30, 미기재 시 30).
- met: MET 값 (아래 DB 참고; DB에 없으면 직접 추정).
- 강도가 모호하면 보수적(낮은 MET) 값 사용.
- 순수 JSON만 반환하세요. 마크다운 없이.

참고 MET DB (activity_key|이름|MET):
{acts_txt}

사용자 운동 기록:
{user_text}

반환 형식:
{{"items": [{{"name": "빠르게 걷기", "minutes": 40, "met": 4.3}}]}}"""


# Simple keyword → activity_key mapping for fallback
EXERCISE_KEYWORDS: list[tuple[list[str], str]] = [
    (["걷기", "산책", "워킹"],              "walk_moderate"),
    (["빠르게 걷기", "빠른 걷기"],           "walk_brisk"),
    (["달리기", "러닝", "조깅", "뛰기"],     "run_jog"),
    (["자전거", "사이클"],                   "cycling_moderate"),
    (["수영"],                               "swimming_moderate"),
    (["근력", "헬스", "웨이트", "덤벨"],     "resistance_moderate"),
    (["HIIT", "인터벌"],                     "hiit"),
    (["요가", "필라테스"],                   "yoga"),
    (["등산", "하이킹"],                     "hiking"),
    (["계단"],                               "stair_climbing"),
    (["줄넘기"],                             "jump_rope"),
    (["스트레칭"],                           "stretching"),
]


def _extract_minutes(text: str, start: int) -> int:
    """Extract duration in minutes from text near position `start`."""
    local = text[max(0, start - 30):start + 30]
    h = re.search(r"(\d+)\s*시간", local)
    m = re.search(r"(\d+)\s*분",   local)
    total = 0
    if h:
        total += int(h.group(1)) * 60
    if m:
        total += int(m.group(1))
    return total if total > 0 else 30  # default 30 min


def _keyword_exercise_match(user_text: str, met_values: list[dict]) -> list[dict]:
    met_db = {m["activity_key"]: m for m in met_values}
    results: list[dict] = []

    for keywords, key in EXERCISE_KEYWORDS:
        for kw in keywords:
            idx = user_text.find(kw)
            if idx >= 0:
                mins = _extract_minutes(user_text, idx)
                if key in met_db:
                    results.append({
                        "activity_key": key,
                        "name":         met_db[key]["name"],
                        "minutes":      mins,
                        "source":       "fallback",
                    })
                break

    return results


def parse_exercises(
    user_text: str,
    met_values: list[dict],
    current_weight: float = 65.0,
) -> list[dict]:
    """
    Parse Korean exercise text → list of activity items with kcal.

    우선순위:
    1. AI 분석: 운동명·시간·MET를 AI가 직접 추정
       → DB에 같은 이름이 있으면 DB MET 사용 (정확)
       → DB에 없으면 AI 추정 MET 사용 (source='ai_estimate')
    2. 규칙 기반 fallback: AI 실패 시 키워드 매칭
    """
    from llm.provider import get_provider

    # MET DB: 이름 → met_record 매핑
    met_by_name: dict[str, dict] = {}
    for m in met_values:
        met_by_name[m["name"]] = m

    provider = get_provider()
    raw_items: list[dict] = []

    if provider.available:
        prompt = _build_exercise_parse_prompt(user_text, met_values)
        raw = provider.generate_json(prompt, retries=2)
        if raw and "items" in raw:
            for item in raw["items"]:
                name = (item.get("name") or "").strip()
                mins = float(item.get("minutes") or 0)
                ai_met = float(item.get("met") or 0)
                if not name or mins <= 0 or ai_met <= 0:
                    continue

                db_rec = met_by_name.get(name)
                met_val = db_rec["met"] if db_rec else ai_met
                source  = "db" if db_rec else "ai_estimate"
                kcal = round(met_val * current_weight * (mins / 60.0), 1)
                raw_items.append({
                    "activity_key": db_rec["activity_key"] if db_rec else None,
                    "name":         name,
                    "minutes":      round(mins),
                    "met":          met_val,
                    "kcal":         kcal,
                    "source":       source,
                })

    if not raw_items:
        met_db = {m["activity_key"]: m for m in met_values}
        for item in _keyword_exercise_match(user_text, met_values):
            key  = item["activity_key"]
            mins = item["minutes"]
            if key in met_db:
                met_val = met_db[key]["met"]
                kcal = round(met_val * current_weight * (mins / 60.0), 1)
                raw_items.append({
                    "activity_key": key,
                    "name":         met_db[key]["name"],
                    "minutes":      mins,
                    "met":          met_val,
                    "kcal":         kcal,
                    "source":       "fallback",
                })

    return raw_items


def compute_exercise_totals(items: list[dict]) -> dict:
    total_kcal = sum(i["kcal"] for i in items)
    total_mins = sum(i["minutes"] for i in items)
    return {
        "kcal": round(total_kcal, 1),
        "minutes": total_mins,
    }
