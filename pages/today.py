"""
Today page: daily targets + natural-language meal/exercise logging with parse confirmation UI.
"""
import streamlit as st
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

# ── Compute targets ───────────────────────────────────────────────────────────
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
    target_weight_today = gt.get("target_weight") if gt else None
else:
    target_weight_today = None

actual_weight   = log.get("weight_kg")
actual_intake   = log.get("intake_kcal")  or 0.0
actual_exercise = log.get("exercise_kcal") or 0.0

intake_pct   = min(100, actual_intake   / target_intake   * 100) if target_intake   > 0 else 0
exercise_pct = min(100, actual_exercise / target_exercise * 100) if target_exercise > 0 else 0

# ── Header ────────────────────────────────────────────────────────────────────
DOW = ["월","화","수","목","금","토","일"]
st.markdown(
    f"## 오늘 &nbsp;<span style='font-size:14px;color:var(--text-secondary)'>"
    f"{date.today().strftime('%Y년 %m월 %d일')} ({DOW[date.today().weekday()]})</span>",
    unsafe_allow_html=True,
)

# ── 오늘의 목표 카드 ──────────────────────────────────────────────────────────
intake_color   = "var(--danger)"  if intake_pct >= 100 else ("var(--warning)" if intake_pct >= 80 else "var(--positive)")
exercise_color = "var(--positive)" if exercise_pct >= 100 else ("var(--warning)" if exercise_pct >= 50 else "var(--danger)")
intake_status  = f"+{actual_intake - target_intake:.0f} kcal 초과" if actual_intake > target_intake else f"여유 {target_intake - actual_intake:.0f} kcal"
exercise_status = "목표 달성! 🎉" if actual_exercise >= target_exercise else f"남은 {target_exercise - actual_exercise:.0f} kcal"

st.markdown(f"""
<div class="diet-card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
    <span style="font-size:15px;font-weight:600">오늘의 목표</span>
    <span style="font-size:12px;color:var(--text-secondary)">추정 TDEE {est_tdee:.0f} kcal</span>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
    <div>
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px">🍽️ 섭취 목표</div>
      <div style="font-size:28px;font-weight:700;color:var(--accent);line-height:1">{target_intake:.0f}<span style="font-size:13px;font-weight:400;margin-left:4px">kcal</span></div>
      <div style="margin-top:8px;font-size:13px">현재 <strong>{actual_intake:.0f} kcal</strong></div>
      <div style="margin-top:4px;background:var(--bg-elevated);border-radius:6px;height:8px;overflow:hidden">
        <div style="width:{intake_pct:.0f}%;background:{intake_color};height:100%;border-radius:6px;transition:width .3s"></div>
      </div>
      <div style="margin-top:4px;font-size:12px;color:{intake_color};font-weight:600">{intake_pct:.0f}% &nbsp;·&nbsp; {intake_status}</div>
    </div>
    <div>
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px">🏃 운동 목표</div>
      <div style="font-size:28px;font-weight:700;color:var(--positive);line-height:1">{target_exercise:.0f}<span style="font-size:13px;font-weight:400;margin-left:4px">kcal</span></div>
      <div style="margin-top:8px;font-size:13px">현재 <strong>{actual_exercise:.0f} kcal</strong></div>
      <div style="margin-top:4px;background:var(--bg-elevated);border-radius:6px;height:8px;overflow:hidden">
        <div style="width:{exercise_pct:.0f}%;background:{exercise_color};height:100%;border-radius:6px;transition:width .3s"></div>
      </div>
      <div style="margin-top:4px;font-size:12px;color:{exercise_color};font-weight:600">{exercise_pct:.0f}% &nbsp;·&nbsp; {exercise_status}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── 체중 입력 ─────────────────────────────────────────────────────────────────
with st.expander("⚖️ 체중 입력", expanded=not bool(actual_weight)):
    if actual_weight:
        delta = f"{actual_weight - target_weight_today:+.1f}kg" if target_weight_today else None
        st.metric("기록된 체중", f"{actual_weight:.1f} kg", delta=delta, delta_color="inverse")
        if target_weight_today:
            st.caption(f"오늘 목표 {target_weight_today:.1f} kg")
    new_weight = st.number_input("공복 체중 (kg)", min_value=0.0, max_value=300.0,
                                 value=float(actual_weight) if actual_weight else 0.0,
                                 step=0.1, key="wt_input")
    if st.button("체중 저장", type="primary", key="save_weight"):
        if new_weight > 0:
            upsert_daily_log({"date": today_str, "weight_kg": new_weight})
            st.success(f"{new_weight:.1f} kg 저장됨")
            st.rerun()
        else:
            st.warning("체중을 입력해 주세요.")

st.divider()

# ── 식사 기록 ─────────────────────────────────────────────────────────────────
st.markdown("### 🍽️ 식사 기록")

col_ip1, col_ip2 = st.columns([4, 1])
with col_ip1:
    st.progress(
        min(1.0, actual_intake / target_intake) if target_intake > 0 else 0.0,
        text=f"{actual_intake:.0f} / {target_intake:.0f} kcal — 목표 대비 {intake_pct:.0f}%",
    )
with col_ip2:
    if actual_intake > target_intake:
        st.markdown(f"<div style='color:var(--danger);font-size:12px;font-weight:600'>+{actual_intake-target_intake:.0f} kcal 초과</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='color:var(--positive);font-size:12px;font-weight:600'>여유 {target_intake-actual_intake:.0f} kcal</div>", unsafe_allow_html=True)

intake_raw = st.text_area(
    "식사 입력",
    value=log.get("intake_raw", ""),
    placeholder="예) 아침에 현미밥 한 공기, 계란 2개. 점심은 김치찌개랑 공깃밥. 저녁은 닭가슴살 샐러드",
    height=90, key="intake_raw_area", label_visibility="collapsed",
)

col_parse, col_manual = st.columns([1, 1])
with col_parse:
    parse_btn = st.button("🤖 AI 분석", use_container_width=True, type="primary", key="parse_meal_btn")
with col_manual:
    manual_kcal = st.number_input("직접 입력 (kcal)", min_value=0.0, max_value=10000.0,
                                  value=float(actual_intake), step=10.0, key="manual_intake")

if "parsed_meals" not in st.session_state:
    st.session_state.parsed_meals = []
if "meal_confirmed" not in st.session_state:
    st.session_state.meal_confirmed = False

if parse_btn and intake_raw.strip():
    with st.status("🤖 AI가 식사를 분석하고 있습니다...", expanded=True) as status:
        st.write("📚 식품 데이터베이스 로딩 중...")
        foods = get_all_foods()
        prog = st.progress(30, text="식품 DB 로드 완료")

        st.write("🧠 AI가 음식을 인식하고 칼로리를 계산합니다...")
        prog.progress(50, text="AI 분석 중 — 잠시 기다려 주세요")
        current_w = actual_weight or 65.0
        parsed = parse_meals(intake_raw, foods, current_w)

        prog.progress(90, text="결과 정리 중...")
        st.write(f"✅ {len(parsed)}개 항목 인식 완료")
        prog.progress(100)
        status.update(label="✅ AI 분석 완료!", state="complete", expanded=False)

    st.session_state.parsed_meals  = parsed
    st.session_state.meal_confirmed = False
    st.rerun()

# 분석 결과 확인 UI
if st.session_state.parsed_meals:
    st.markdown("#### 분석 결과 — 수정 후 저장")
    st.caption("그램수를 수정하면 칼로리가 즉시 재계산됩니다. 체크 해제하면 제외됩니다.")

    all_foods_db = {f["id"]: f for f in get_all_foods()}
    updated_items: list[dict] = []

    for i, item in enumerate(st.session_state.parsed_meals):
        conf   = item.get("confidence", 1.0)
        source = item.get("source", "llm")
        badge  = "🔴 확인 필요" if conf < 0.6 else ("🟡" if conf < 0.85 else "🟢")

        ci1, ci2, ci3, ci4 = st.columns([3, 1.5, 1.5, 0.7])
        with ci1:
            st.markdown(f"**{item['name']}** {badge}")
            st.caption(f"{item.get('meal','')}" + (" · 규칙 기반" if source == "fallback" else ""))
        with ci2:
            new_g = st.number_input("g", min_value=5.0, max_value=2000.0,
                                    value=float(item["grams"]), step=5.0,
                                    key=f"meal_g_{i}", label_visibility="collapsed")
        with ci3:
            food = all_foods_db.get(item["food_id"])
            recalc = calc_item_nutrition(food, new_g) if food and new_g != item["grams"] else item
            st.markdown(f"**{recalc['kcal']:.0f} kcal**")
        with ci4:
            keep = st.checkbox("", value=True, key=f"meal_keep_{i}")

        if keep:
            updated_item = dict(recalc)
            updated_item.update({"grams": new_g, "meal": item.get("meal","breakfast"), "confidence": conf})
            updated_items.append(updated_item)

    if updated_items:
        totals = compute_intake_totals(updated_items)
        preview_pct = min(100, totals["kcal"] / target_intake * 100) if target_intake > 0 else 0
        p_color = "var(--danger)" if preview_pct >= 100 else ("var(--warning)" if preview_pct >= 80 else "var(--positive)")
        st.markdown(f"""
<div class="diet-card" style="margin-top:8px">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <span><strong>{totals['kcal']:.0f} kcal</strong> &nbsp; 탄 {totals['carb_g']:.0f}g · 단 {totals['protein_g']:.0f}g · 지 {totals['fat_g']:.0f}g</span>
    <span style="color:{p_color};font-weight:700;font-size:18px">{preview_pct:.0f}%</span>
  </div>
  <div style="margin-top:6px;font-size:12px;color:var(--text-secondary)">저장 시 오늘 목표 대비 달성률</div>
</div>
""", unsafe_allow_html=True)

        if st.button("✅ 확정 및 저장", type="primary", use_container_width=True, key="confirm_meals"):
            upsert_daily_log({
                "date": today_str,
                "intake_raw":        intake_raw,
                "intake_kcal":       totals["kcal"],
                "intake_items_json": updated_items,
            })
            st.session_state.parsed_meals  = []
            st.session_state.meal_confirmed = True
            st.success(f"식사 기록 저장됨 — {totals['kcal']:.0f} kcal (목표 대비 {preview_pct:.0f}%)")
            st.rerun()

if not st.session_state.parsed_meals:
    if st.button("저장 (직접 입력값)", key="save_manual_meal"):
        upsert_daily_log({"date": today_str, "intake_raw": intake_raw,
                          "intake_kcal": manual_kcal if manual_kcal > 0 else None})
        st.success("식사 기록 저장됨")
        st.rerun()

st.divider()

# ── 운동 기록 ─────────────────────────────────────────────────────────────────
st.markdown("### 🏃 운동 기록")

col_ep1, col_ep2 = st.columns([4, 1])
with col_ep1:
    st.progress(
        min(1.0, actual_exercise / target_exercise) if target_exercise > 0 else 0.0,
        text=f"{actual_exercise:.0f} / {target_exercise:.0f} kcal — 목표 대비 {exercise_pct:.0f}%",
    )
with col_ep2:
    if actual_exercise >= target_exercise:
        st.markdown("<div style='color:var(--positive);font-size:12px;font-weight:600'>목표 달성! 🎉</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='color:var(--warning);font-size:12px;font-weight:600'>남은 {target_exercise-actual_exercise:.0f} kcal</div>", unsafe_allow_html=True)

exercise_raw = st.text_area(
    "운동 입력",
    value=log.get("exercise_raw", ""),
    placeholder="예) 빠르게 걷기 40분, 헬스장에서 근력운동 20분",
    height=80, key="exercise_raw_area", label_visibility="collapsed",
)

col_ep, col_em = st.columns([1, 1])
with col_ep:
    ex_parse_btn = st.button("🤖 AI 분석", use_container_width=True, type="primary", key="ex_parse_btn")
with col_em:
    manual_ex_kcal = st.number_input("직접 입력 (kcal)", min_value=0.0, max_value=5000.0,
                                     value=float(actual_exercise), step=10.0, key="manual_exercise")

if "parsed_exercises" not in st.session_state:
    st.session_state.parsed_exercises = []

if ex_parse_btn and exercise_raw.strip():
    with st.status("🤖 AI가 운동을 분석하고 있습니다...", expanded=True) as status:
        st.write("📚 운동 데이터베이스 로딩 중...")
        met_values = get_all_met_values()
        prog_ex = st.progress(30, text="운동 DB 로드 완료")

        st.write("🧠 AI가 운동 종류와 시간을 인식합니다...")
        prog_ex.progress(50, text="AI 분석 중 — 잠시 기다려 주세요")
        current_w = float(actual_weight or log.get("weight_kg") or 65.0)
        parsed_ex = parse_exercises(exercise_raw, met_values, current_w)

        prog_ex.progress(90, text="결과 정리 중...")
        st.write(f"✅ {len(parsed_ex)}개 운동 인식 완료")
        prog_ex.progress(100)
        status.update(label="✅ AI 분석 완료!", state="complete", expanded=False)

    st.session_state.parsed_exercises = parsed_ex
    st.rerun()

# 운동 분석 결과 확인 UI
if st.session_state.parsed_exercises:
    st.markdown("#### 운동 분석 결과")
    st.caption("분 수를 수정하면 소모 칼로리가 즉시 재계산됩니다.")

    updated_ex: list[dict] = []
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
        ex_pct = min(100, ex_totals["kcal"] / target_exercise * 100) if target_exercise > 0 else 0
        ex_color = "var(--positive)" if ex_pct >= 100 else ("var(--warning)" if ex_pct >= 50 else "var(--danger)")
        st.markdown(f"""
<div class="diet-card" style="margin-top:8px">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <span><strong>{ex_totals['kcal']:.0f} kcal</strong> 소모 &nbsp; (총 {ex_totals['minutes']}분)</span>
    <span style="color:{ex_color};font-weight:700;font-size:18px">{ex_pct:.0f}%</span>
  </div>
  <div style="margin-top:6px;font-size:12px;color:var(--text-secondary)">⚠️ 소모분은 추가 섭취 명분이 아닌 적자 보조로만 활용하세요.</div>
</div>
""", unsafe_allow_html=True)

        if st.button("✅ 확정 및 저장", type="primary", use_container_width=True, key="confirm_exercise"):
            upsert_daily_log({
                "date": today_str,
                "exercise_raw":        exercise_raw,
                "exercise_kcal":       ex_totals["kcal"],
                "exercise_items_json": updated_ex,
            })
            st.session_state.parsed_exercises = []
            st.success(f"운동 기록 저장됨 — {ex_totals['kcal']:.0f} kcal (목표 대비 {ex_pct:.0f}%)")
            st.rerun()

if not st.session_state.parsed_exercises:
    if st.button("저장 (직접 입력값)", key="save_manual_ex"):
        upsert_daily_log({"date": today_str, "exercise_raw": exercise_raw,
                          "exercise_kcal": manual_ex_kcal if manual_ex_kcal > 0 else None})
        st.success("운동 기록 저장됨")
        st.rerun()

# ── 타깃 상세 ─────────────────────────────────────────────────────────────────
with st.expander("📊 오늘의 타깃 상세"):
    st.markdown(f"""
| 항목 | 값 |
|------|-----|
| 추정 TDEE | {est_tdee:.0f} kcal/일 |
| 목표 섭취 | {target_intake:.0f} kcal/일 |
| 목표 운동 소모 | {target_exercise:.0f} kcal/일 |
| 일일 적자 | {est_tdee - target_intake:.0f} kcal |
""")
