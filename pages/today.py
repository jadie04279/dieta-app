"""
Today page: daily targets + natural-language meal/exercise logging with parse confirmation UI.
"""
import streamlit as st
import json
from datetime import date

from db.repo import (
    get_profile, get_daily_log, upsert_daily_log,
    get_latest_weekly_plan, get_latest_goal_schedule,
    get_all_foods, get_all_met_values, get_recent_logs,
)
from core.energy import plan_targets
from core.safety import apply_guardrails
from core.schedule import daily_target as goal_daily_target
from core.adaptive import ewma_trend_weights
from core.log_parser import (
    parse_meals, compute_intake_totals,
    parse_exercises, compute_exercise_totals,
)
from core.planner import calc_item_nutrition

profile   = get_profile()
today_str = str(date.today())
log       = get_daily_log(today_str) or {}

# ── Compute targets ──────────────────────────────────────────────────────────
plan      = get_latest_weekly_plan()
goal_line = get_latest_goal_schedule()

if plan:
    target_intake   = plan["target_intake_kcal"]
    target_exercise = plan["target_exercise_kcal"] / 7
    est_tdee        = plan["est_tdee"]
else:
    recent    = get_recent_logs(14)
    enriched  = ewma_trend_weights(recent) if recent else []
    trend_pts = [l for l in enriched if l.get("trend_weight")]
    current_w = trend_pts[-1]["trend_weight"] if trend_pts else (
        log.get("weight_kg") or profile.get("goal_weight_kg") or 65.0
    )
    raw = plan_targets(profile, current_w)
    adjusted, _ = apply_guardrails(profile, current_w, raw)
    target_intake   = adjusted["target_intake_kcal"]
    target_exercise = adjusted["target_exercise_kcal"] / 7
    est_tdee        = adjusted["tdee"]

if goal_line:
    gt = goal_daily_target(goal_line, date.today())
    target_weight_today = gt["target_weight"]
else:
    target_weight_today = None

actual_weight   = log.get("weight_kg")
actual_intake   = log.get("intake_kcal")  or 0.0
actual_exercise = log.get("exercise_kcal") or 0.0

# ── Header + summary metrics ──────────────────────────────────────────────────
DOW = ["월","화","수","목","금","토","일"]
st.markdown(
    f"## 오늘 &nbsp;<span style='font-size:14px;color:var(--text-secondary)'>"
    f"{date.today().strftime('%Y년 %m월 %d일')} ({DOW[date.today().weekday()]})</span>",
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns(3)
with c1:
    delta = f"{actual_weight - target_weight_today:+.1f}kg" if (actual_weight and target_weight_today) else None
    st.metric("체중", f"{actual_weight:.1f} kg" if actual_weight else "—",
              delta=delta, delta_color="inverse")
    if target_weight_today:
        st.caption(f"오늘 목표 {target_weight_today:.1f} kg")
with c2:
    remaining = max(0.0, target_intake - actual_intake)
    st.metric("섭취", f"{actual_intake:.0f} kcal",
              delta=f"목표 {target_intake:.0f} kcal", delta_color="off")
    if actual_intake > 0:
        st.progress(min(1.0, actual_intake / target_intake),
                    text=f"여유 {remaining:.0f} kcal")
with c3:
    st.metric("운동 소모", f"{actual_exercise:.0f} kcal",
              delta=f"목표 {target_exercise:.0f} kcal/일", delta_color="off")
    if target_exercise > 0:
        st.progress(min(1.0, actual_exercise / target_exercise))

st.divider()

# ── Tabs ─────────────────────────────────────────────────────────────────────
tab_w, tab_m, tab_e = st.tabs(["⚖️ 체중", "🍽️ 식사", "🏃 운동"])

# ── Weight tab ────────────────────────────────────────────────────────────────
with tab_w:
    new_weight = st.number_input("오늘 아침 공복 체중 (kg)",
                                 min_value=0.0, max_value=300.0,
                                 value=float(actual_weight) if actual_weight else 0.0,
                                 step=0.1, key="wt_input")
    if st.button("체중 저장", type="primary", key="save_weight"):
        if new_weight > 0:
            upsert_daily_log({"date": today_str, "weight_kg": new_weight})
            st.success(f"{new_weight:.1f} kg 저장됨")
            st.rerun()
        else:
            st.warning("체중을 입력해 주세요.")

# ── Meal tab ──────────────────────────────────────────────────────────────────
with tab_m:
    # ── Input area ────────────────────────────────────────────────────────────
    intake_raw = st.text_area(
        "오늘 식사 (자유롭게 적어주세요)",
        value=log.get("intake_raw", ""),
        placeholder="예) 아침에 현미밥 한 공기, 계란 2개. 점심은 김치찌개랑 공깃밥. 저녁은 닭가슴살 샐러드",
        height=100, key="intake_raw_area",
    )

    col_parse, col_manual = st.columns([1, 1])
    with col_parse:
        parse_btn = st.button("🔍 자동 분석", use_container_width=True, type="primary",
                              help="AI가 식사 텍스트를 분석합니다.")
    with col_manual:
        manual_kcal = st.number_input("직접 입력 (kcal)", min_value=0.0, max_value=10000.0,
                                      value=float(actual_intake), step=10.0, key="manual_intake")

    # ── Parse result state ─────────────────────────────────────────────────────
    if "parsed_meals" not in st.session_state:
        st.session_state.parsed_meals = []
    if "meal_confirmed" not in st.session_state:
        st.session_state.meal_confirmed = False

    if parse_btn and intake_raw.strip():
        with st.spinner("식사 분석 중..."):
            foods = get_all_foods()
            current_w = actual_weight or 65.0
            parsed = parse_meals(intake_raw, foods, current_w)
        st.session_state.parsed_meals  = parsed
        st.session_state.meal_confirmed = False
        st.rerun()

    # ── Confirmation UI ────────────────────────────────────────────────────────
    if st.session_state.parsed_meals:
        st.markdown("#### 분석 결과 — 확인 후 저장")
        st.caption("그램수를 수정하면 칼로리가 즉시 재계산됩니다.")

        all_foods_db = {f["id"]: f for f in get_all_foods()}
        updated_items: list[dict] = []

        for i, item in enumerate(st.session_state.parsed_meals):
            conf   = item.get("confidence", 1.0)
            source = item.get("source", "llm")
            badge  = "🔴 확인 필요" if conf < 0.6 else ("🟡" if conf < 0.85 else "🟢")

            with st.container():
                ci1, ci2, ci3, ci4 = st.columns([3, 1.5, 1.5, 0.7])
                with ci1:
                    st.markdown(f"**{item['name']}** {badge}")
                    st.caption(f"{item.get('meal','')}"
                               + (f" · {source}" if source == "fallback" else ""))
                with ci2:
                    new_g = st.number_input(
                        "g", min_value=5.0, max_value=2000.0,
                        value=float(item["grams"]), step=5.0,
                        key=f"meal_g_{i}", label_visibility="collapsed"
                    )
                with ci3:
                    food = all_foods_db.get(item["food_id"])
                    if food and new_g != item["grams"]:
                        recalc = calc_item_nutrition(food, new_g)
                    else:
                        recalc = item
                    st.markdown(f"**{recalc['kcal']:.0f} kcal**")
                with ci4:
                    keep = st.checkbox("", value=True, key=f"meal_keep_{i}")

                if keep:
                    updated_item = dict(recalc)
                    updated_item["grams"] = new_g
                    updated_item["meal"]  = item.get("meal", "breakfast")
                    updated_item["confidence"] = conf
                    updated_items.append(updated_item)

        if updated_items:
            totals = compute_intake_totals(updated_items)
            st.markdown(
                f"**합계: {totals['kcal']:.0f} kcal** "
                f"(탄 {totals['carb_g']:.0f}g · 단 {totals['protein_g']:.0f}g · 지 {totals['fat_g']:.0f}g)"
            )
            if st.button("✅ 확정 및 저장", type="primary", use_container_width=True,
                         key="confirm_meals"):
                upsert_daily_log({
                    "date": today_str,
                    "intake_raw":        intake_raw,
                    "intake_kcal":       totals["kcal"],
                    "intake_items_json": updated_items,
                })
                st.session_state.parsed_meals  = []
                st.session_state.meal_confirmed = True
                st.success(f"식사 기록 저장됨 — {totals['kcal']:.0f} kcal")
                st.rerun()

    # ── Manual save fallback ───────────────────────────────────────────────────
    if not st.session_state.parsed_meals:
        if st.button("저장 (직접 입력값)", key="save_manual_meal"):
            upsert_daily_log({
                "date": today_str,
                "intake_raw":  intake_raw,
                "intake_kcal": manual_kcal if manual_kcal > 0 else None,
            })
            st.success("식사 기록 저장됨")
            st.rerun()

# ── Exercise tab ──────────────────────────────────────────────────────────────
with tab_e:
    exercise_raw = st.text_area(
        "오늘 운동 (자유롭게 적어주세요)",
        value=log.get("exercise_raw", ""),
        placeholder="예) 빠르게 걷기 40분, 헬스장에서 근력운동 20분",
        height=80, key="exercise_raw_area",
    )

    col_ep, col_em = st.columns([1, 1])
    with col_ep:
        ex_parse_btn = st.button("🔍 자동 분석", use_container_width=True, type="primary",
                                 key="ex_parse_btn")
    with col_em:
        manual_ex_kcal = st.number_input("직접 입력 (kcal)", min_value=0.0, max_value=5000.0,
                                         value=float(actual_exercise), step=10.0,
                                         key="manual_exercise")

    if "parsed_exercises" not in st.session_state:
        st.session_state.parsed_exercises = []

    if ex_parse_btn and exercise_raw.strip():
        with st.spinner("운동 분석 중..."):
            met_values = get_all_met_values()
            current_w  = actual_weight or log.get("weight_kg") or 65.0
            parsed_ex  = parse_exercises(exercise_raw, met_values, float(current_w))
        st.session_state.parsed_exercises = parsed_ex
        st.rerun()

    # ── Exercise confirmation UI ───────────────────────────────────────────────
    if st.session_state.parsed_exercises:
        st.markdown("#### 운동 분석 결과")
        st.caption("소모 칼로리 = MET × 체중 × 시간 (과대추정 주의 — 참고용)")

        updated_ex: list[dict] = []
        met_db = {m["activity_key"]: m for m in get_all_met_values()}
        current_w = float(actual_weight or 65.0)

        for i, item in enumerate(st.session_state.parsed_exercises):
            badge = "🟡" if item.get("source") == "fallback" else "🟢"
            ei1, ei2, ei3, ei4 = st.columns([3, 1.5, 1.5, 0.7])
            with ei1:
                st.markdown(f"**{item['name']}** {badge}")
                st.caption(f"MET {item['met']:.1f}")
            with ei2:
                new_min = st.number_input("분", min_value=5, max_value=300,
                                          value=int(item["minutes"]), step=5,
                                          key=f"ex_min_{i}", label_visibility="collapsed")
            with ei3:
                new_kcal = round(item["met"] * current_w * (new_min / 60.0), 1)
                st.markdown(f"**{new_kcal:.0f} kcal**")
            with ei4:
                keep_ex = st.checkbox("", value=True, key=f"ex_keep_{i}")
            if keep_ex:
                updated_ex.append({**item, "minutes": new_min, "kcal": new_kcal})

        if updated_ex:
            ex_totals = compute_exercise_totals(updated_ex)
            st.info(f"⚠️ 운동 소모 {ex_totals['kcal']:.0f} kcal (총 {ex_totals['minutes']}분) — "
                    "소모분은 추가 섭취 명분이 아닌 적자 보조로만 활용합니다.")
            if st.button("✅ 확정 및 저장", type="primary", use_container_width=True,
                         key="confirm_exercise"):
                upsert_daily_log({
                    "date": today_str,
                    "exercise_raw":        exercise_raw,
                    "exercise_kcal":       ex_totals["kcal"],
                    "exercise_items_json": updated_ex,
                })
                st.session_state.parsed_exercises = []
                st.success(f"운동 기록 저장됨 — {ex_totals['kcal']:.0f} kcal")
                st.rerun()

    if not st.session_state.parsed_exercises:
        if st.button("저장 (직접 입력값)", key="save_manual_ex"):
            upsert_daily_log({
                "date": today_str,
                "exercise_raw":  exercise_raw,
                "exercise_kcal": manual_ex_kcal if manual_ex_kcal > 0 else None,
            })
            st.success("운동 기록 저장됨")
            st.rerun()

# ── Target detail expander ────────────────────────────────────────────────────
with st.expander("오늘의 타깃 상세"):
    st.markdown(f"""
| 항목 | 값 |
|------|-----|
| 추정 TDEE | {est_tdee:.0f} kcal/일 |
| 목표 섭취 | {target_intake:.0f} kcal/일 |
| 목표 운동 소모 | {target_exercise:.0f} kcal/일 |
| 일일 적자 | {est_tdee - target_intake:.0f} kcal |
""")
