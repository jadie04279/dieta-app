"""
Weekly plan page: targets, safety flags, goal trajectory, meal plan suggestion, exercise plan.
"""
import streamlit as st
import plotly.graph_objects as go
from datetime import date, timedelta
import json

from db.repo import (
    get_profile, get_latest_weekly_plan, get_latest_goal_schedule,
    get_recent_logs, get_latest_health_check, upsert_weekly_plan,
)
from core.energy import plan_targets
from core.safety import apply_guardrails, bmi as calc_bmi
from core.adaptive import ewma_trend_weights
from core.health_flags import check_health_flags

profile = get_profile()
if not profile:
    st.warning("프로필을 먼저 설정해 주세요.")
    st.stop()

plan      = get_latest_weekly_plan()
goal_line = get_latest_goal_schedule()
health    = get_latest_health_check()

recent   = get_recent_logs(14)
enriched = ewma_trend_weights(recent) if recent else []
trend_pts = [l for l in enriched if l.get("trend_weight")]
current_w = float(trend_pts[-1]["trend_weight"]) if trend_pts else (
    profile.get("goal_weight_kg") or 65.0
)

st.markdown("## 주간 계획")

tab_goal, tab_meal, tab_ex = st.tabs(["🎯 목표 & 알림", "🍽️ 식단 제안", "🏃 운동 계획"])

# ── Tab 1: Goals & alerts ─────────────────────────────────────────────────────
with tab_goal:
    if plan:
        st.markdown("### 이번 주 목표")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("일일 목표 섭취",  f"{plan['target_intake_kcal']:.0f} kcal")
        c2.metric("주간 운동 소모",  f"{plan['target_exercise_kcal']:.0f} kcal")
        c3.metric("주간 목표 감량",  f"{plan['planned_loss_kg']:.2f} kg")
        c4.metric("추정 TDEE",       f"{plan['est_tdee']:.0f} kcal/일")

        # Safety flags from current plan
        flags = plan.get("flags_json") or []
        if isinstance(flags, list) and flags:
            for f in flags:
                sev = f.get("severity", "info")
                if sev == "error":
                    st.error(f["message"])
                elif sev == "warning":
                    st.warning(f["message"])
                else:
                    st.info(f["message"])
    else:
        st.info("아직 주간 계획이 없습니다. '추세' 탭에서 재조정을 실행하세요.")
        if recent:
            raw      = plan_targets(profile, current_w)
            adjusted, flags = apply_guardrails(profile, current_w, raw)
            st.markdown("#### 현재 기준 임시 타깃")
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("일일 목표 섭취", f"{adjusted['target_intake_kcal']:.0f} kcal")
            cc2.metric("추정 TDEE",      f"{adjusted['tdee']:.0f} kcal/일")
            cc3.metric("주간 목표 감량", f"{adjusted['planned_loss_kg']:.2f} kg")

    # Health flags
    hflags = check_health_flags(health) if health else []
    if hflags:
        st.markdown("#### 건강 알림")
        for f in hflags:
            sev = f.get("severity", "info")
            if sev == "error":
                st.error(f"🔴 {f['message']}")
            else:
                st.warning(f"🟡 {f['message']}")

    st.divider()

    # Goal trajectory table
    if goal_line and goal_line.get("weekly_targets_json"):
        st.markdown("### 목표선 주차별 계획")
        weekly   = goal_line["weekly_targets_json"]
        today_str = str(date.today())
        rows = []
        for i, entry in enumerate(weekly[:16]):
            ws = entry.get("week_start", "")
            if not ws:
                continue
            we = str(date.fromisoformat(ws) + timedelta(weeks=1))
            is_current = ws <= today_str < we
            rows.append({
                "주차":      f"{'▶ ' if is_current else ''}{i+1}주",
                "주 시작":   ws,
                "목표 체중": f"{entry['target_weight_kg']:.2f} kg",
                "목표 감량": f"{entry['planned_loss_kg']:.2f} kg",
                "안전":      "🔴 초과" if entry.get("exceeds_safe_cap") else "✅",
            })
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if goal_line["feasible"] == 0:
            st.warning(
                f"현재 모드에서 목표 기간 내 안전 달성이 어렵습니다. "
                f"안전 속도 기준 예상 완료일: **{goal_line['projected_date']}**"
            )
        else:
            st.success(f"목표 달성 예상일: **{goal_line['projected_date']}** (설정 목표일: {goal_line['target_date']})")
    elif goal_line:
        st.info("목표선 데이터가 없습니다.")
    else:
        st.info("온보딩을 완료하면 목표선이 생성됩니다.")

    st.divider()

    # Macro donut
    st.markdown("### 매크로 목표")
    carb_pct = profile.get("macro_carb_pct", 50)
    prot_pct = profile.get("macro_prot_pct", 30)
    fat_pct  = profile.get("macro_fat_pct",  20)
    intake   = plan["target_intake_kcal"] if plan else 1800.0

    carb_g = intake * carb_pct / 100 / 4
    prot_g = intake * prot_pct / 100 / 4
    fat_g  = intake * fat_pct  / 100 / 9

    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("탄수화물", f"{carb_g:.0f} g", delta=f"{carb_pct}%", delta_color="off")
    mc2.metric("단백질",   f"{prot_g:.0f} g", delta=f"{prot_pct}%", delta_color="off")
    mc3.metric("지방",     f"{fat_g:.0f} g",  delta=f"{fat_pct}%",  delta_color="off")

    fig = go.Figure(go.Pie(
        labels=["탄수화물", "단백질", "지방"],
        values=[carb_pct, prot_pct, fat_pct],
        hole=0.6,
        marker_colors=["#84C5F4", "#6FD4C9", "#F4B84A"],
        textfont_color="#EAF0F6",
    ))
    fig.update_layout(
        paper_bgcolor="#10151B",
        font=dict(color="#EAF0F6"),
        showlegend=True,
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=0, r=0, t=0, b=0),
        height=200,
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Tab 2: Meal plan suggestion ───────────────────────────────────────────────
with tab_meal:
    if "weekly_meal_plan" not in st.session_state:
        st.session_state.weekly_meal_plan = None

    targets = {
        "target_intake_kcal": plan["target_intake_kcal"] if plan else 1800.0,
        "target_exercise_kcal": plan["target_exercise_kcal"] if plan else 0.0,
    }

    col_gen, col_info = st.columns([1, 2])
    with col_gen:
        gen_btn = st.button("🍽️ 오늘 식단 생성", type="primary", use_container_width=True)
    with col_info:
        st.caption(
            f"일일 목표 {targets['target_intake_kcal']:.0f} kcal 기준으로 AI가 식단을 구성합니다. "
            "LLM이 없으면 규칙 기반 식단이 제안됩니다."
        )

    if gen_btn:
        with st.spinner("식단 구성 중..."):
            from core.planner import generate_meal_plan
            mp = generate_meal_plan(profile, targets, current_w)
        st.session_state.weekly_meal_plan = mp
        st.rerun()

    mp = st.session_state.weekly_meal_plan
    if mp:
        src_badge = "🤖 AI 생성" if mp.get("used_llm") else "📋 규칙 기반"
        st.caption(src_badge)

        MEAL_KO = {"breakfast": "아침", "lunch": "점심", "dinner": "저녁", "snack": "간식"}
        for meal_key, label in MEAL_KO.items():
            items = mp.get(meal_key, [])
            if not items:
                continue
            meal_kcal = sum(i["kcal"] for i in items)
            with st.expander(f"**{label}** — {meal_kcal:.0f} kcal", expanded=True):
                for item in items:
                    cols = st.columns([3, 1, 1, 1, 1])
                    cols[0].markdown(f"**{item['name']}**")
                    cols[1].markdown(f"{item['grams']:.0f} g")
                    cols[2].markdown(f"{item['kcal']:.0f} kcal")
                    cols[3].markdown(f"탄 {item['carb_g']:.0f}g")
                    cols[4].markdown(f"단 {item['protein_g']:.0f}g")

        totals = mp.get("totals", {})
        if totals:
            st.markdown(
                f"**합계: {totals['kcal']:.0f} kcal** "
                f"(탄 {totals['carb_g']:.0f}g · 단 {totals['protein_g']:.0f}g · 지 {totals['fat_g']:.0f}g)"
            )

        if st.button("📥 이 식단을 주간 계획에 저장", key="save_meal_plan"):
            week_start = str(date.today())
            base = plan or {}
            upsert_weekly_plan({
                "week_start":            week_start,
                "est_tdee":              base.get("est_tdee", 2000.0),
                "target_intake_kcal":    targets["target_intake_kcal"],
                "target_exercise_kcal":  targets["target_exercise_kcal"],
                "planned_loss_kg":       base.get("planned_loss_kg", 0.5),
                "diet_json":             mp,
                "flags_json":            base.get("flags_json", []),
            })
            st.success("식단이 저장되었습니다.")
    else:
        st.info("'오늘 식단 생성' 버튼을 눌러 AI 식단 제안을 받으세요.")


# ── Tab 3: Exercise plan ──────────────────────────────────────────────────────
with tab_ex:
    if "weekly_exercise_plan" not in st.session_state:
        st.session_state.weekly_exercise_plan = None

    ex_targets = {
        "target_exercise_kcal": plan["target_exercise_kcal"] if plan else 1400.0,
    }

    hflags_for_ex = check_health_flags(health) if health else []

    ex_col1, ex_col2 = st.columns([1, 2])
    with ex_col1:
        ex_gen_btn = st.button("🏃 운동 계획 생성", type="primary", use_container_width=True)
    with ex_col2:
        sessions = st.number_input("주당 운동 횟수", min_value=2, max_value=7, value=4, step=1, key="ex_sessions")

    if ex_gen_btn:
        with st.spinner("운동 계획 생성 중..."):
            from core.planner import generate_exercise_plan
            ep = generate_exercise_plan(ex_targets, current_w, hflags_for_ex, int(sessions))
        st.session_state.weekly_exercise_plan = ep
        st.rerun()

    ep = st.session_state.weekly_exercise_plan
    if ep:
        DAY_KO = {
            "monday": "월", "tuesday": "화", "wednesday": "수",
            "thursday": "목", "friday": "금", "saturday": "토", "sunday": "일",
        }
        for session in ep.get("sessions", []):
            day_label = DAY_KO.get(session["day"], session["day"])
            acts      = session["activities"]
            sess_kcal = sum(a["kcal"] for a in acts)
            with st.expander(f"**{day_label}요일** — {sess_kcal:.0f} kcal", expanded=True):
                for act in acts:
                    ac1, ac2, ac3, ac4 = st.columns([3, 1, 1, 1])
                    ac1.markdown(f"**{act['name']}**")
                    ac2.markdown(f"{act['minutes']}분")
                    ac3.markdown(f"MET {act['met']:.1f}")
                    ac4.markdown(f"{act['kcal']:.0f} kcal")

        total_kcal = ep.get("weekly_total_kcal", 0)
        st.markdown(f"**주간 합계: {total_kcal:.0f} kcal** (목표 {ex_targets['target_exercise_kcal']:.0f} kcal)")

        if hflags_for_ex:
            with st.expander("적용된 건강 제약"):
                for f in hflags_for_ex:
                    if f.get("modifier"):
                        st.caption(f"• {f['message']}")

        if st.button("📥 이 운동 계획을 주간 계획에 저장", key="save_ex_plan"):
            week_start = str(date.today())
            base = plan or {}
            upsert_weekly_plan({
                "week_start":            week_start,
                "est_tdee":              base.get("est_tdee", 2000.0),
                "target_intake_kcal":    base.get("target_intake_kcal", 1800.0),
                "target_exercise_kcal":  ex_targets["target_exercise_kcal"],
                "planned_loss_kg":       base.get("planned_loss_kg", 0.5),
                "exercise_json":         ep,
                "flags_json":            base.get("flags_json", []),
            })
            st.success("운동 계획이 저장되었습니다.")
    else:
        st.info("'운동 계획 생성' 버튼을 눌러 주간 운동 플랜을 만드세요.")
        st.caption("운동 강도 제한: 건강 검사에서 고혈압·당뇨 지표가 있으면 자동 조정됩니다.")
