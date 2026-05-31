"""
Trends page: weight chart (actual + EWMA + goal line), TDEE history,
cumulative loss, projected arrival date, weekly re-calibration trigger.
"""
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import date, timedelta

from db.repo import (
    get_profile, get_recent_logs, get_latest_goal_schedule,
    get_latest_weekly_plan, list_daily_logs,
)
from core.adaptive import ewma_trend_weights, estimate_tdee, run_weekly_cycle
from core.schedule import daily_target as goal_daily_target

# ── Colors ───────────────────────────────────────────────────────────────────
C = {
    "bg":       "#10151B",
    "card":     "#18202A",
    "actual":   "#84C5F4",   # ice blue — actual weight
    "trend":    "#6FD4C9",   # mint — EWMA trend
    "goal":     "#F4B84A",   # amber — goal line
    "tdee":     "#A78BFA",   # soft purple — TDEE
    "text":     "#EAF0F6",
    "subtext":  "#9AA7B4",
    "grid":     "#1E2835",
}

PLOTLY_LAYOUT = dict(
    paper_bgcolor=C["bg"],
    plot_bgcolor=C["card"],
    font=dict(color=C["text"], size=12),
    margin=dict(l=0, r=0, t=30, b=0),
    legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h",
                yanchor="bottom", y=1.01, xanchor="left", x=0),
    hoverlabel=dict(bgcolor=C["card"], font_color=C["text"]),
)

profile = get_profile()
if not profile:
    st.warning("프로필을 먼저 설정해 주세요.")
    st.stop()

st.markdown("## 추세")

# ── Data loading ─────────────────────────────────────────────────────────────
logs = get_recent_logs(90)
goal_line = get_latest_goal_schedule()
latest_plan = get_latest_weekly_plan()

if not logs:
    st.info("아직 기록이 없습니다. '오늘' 탭에서 체중과 식사를 기록해 주세요.")
    st.stop()

# Enrich with EWMA trend
enriched = ewma_trend_weights(logs)

# ── Top metrics ───────────────────────────────────────────────────────────────
with_weight = [l for l in enriched if (l.get("weight_kg") or 0) > 0]
with_trend  = [l for l in enriched if l.get("trend_weight")]

latest_actual = with_weight[-1]["weight_kg"]  if with_weight  else None
latest_trend  = with_trend[-1]["trend_weight"] if with_trend   else None
start_weight  = goal_line["start_weight_kg"]   if goal_line    else (with_weight[0]["weight_kg"] if with_weight else None)
goal_weight   = goal_line["goal_weight_kg"]    if goal_line    else profile.get("goal_weight_kg")
projected_date = goal_line["projected_date"]   if goal_line    else None

cumulative_loss = (start_weight - latest_trend) if (start_weight and latest_trend) else None
pct_done = (cumulative_loss / (start_weight - goal_weight) * 100) if (cumulative_loss and start_weight and goal_weight and start_weight != goal_weight) else None

m1, m2, m3, m4 = st.columns(4)
m1.metric("추세 체중",
          f"{latest_trend:.1f} kg" if latest_trend else "—",
          delta=f"{latest_actual - latest_trend:+.1f}kg 실측 차" if (latest_actual and latest_trend) else None,
          delta_color="off")
m2.metric("누적 감량",
          f"{cumulative_loss:.2f} kg" if cumulative_loss else "—",
          delta=f"{pct_done:.0f}% 달성" if pct_done else None,
          delta_color="off")
m3.metric("목표 체중", f"{goal_weight:.1f} kg" if goal_weight else "—")
m4.metric("도달 예상일",
          projected_date if projected_date else "—",
          delta=f"목표 {goal_line['target_date']}" if goal_line else None,
          delta_color="off")

st.divider()

# ── Chart 1: Weight trajectory ────────────────────────────────────────────────
st.markdown("### 체중 추이")

fig = go.Figure()

# Actual weight (dots)
actual_dates   = [l["date"]      for l in enriched if (l.get("weight_kg") or 0) > 0]
actual_weights = [l["weight_kg"] for l in enriched if (l.get("weight_kg") or 0) > 0]
if actual_dates:
    fig.add_trace(go.Scatter(
        x=actual_dates, y=actual_weights,
        mode="markers", name="실측 체중",
        marker=dict(color=C["actual"], size=7, opacity=0.7),
    ))

# EWMA trend (line)
trend_dates   = [l["date"]         for l in enriched if l.get("trend_weight")]
trend_weights = [l["trend_weight"] for l in enriched if l.get("trend_weight")]
if trend_dates:
    fig.add_trace(go.Scatter(
        x=trend_dates, y=trend_weights,
        mode="lines", name="추세 체중 (EWMA)",
        line=dict(color=C["trend"], width=2.5),
    ))

# Goal line
if goal_line and goal_line.get("weekly_targets_json"):
    gl_dates   = [goal_line["start_date"]]
    gl_weights = [goal_line["start_weight_kg"]]
    for entry in goal_line["weekly_targets_json"]:
        d_end = date.fromisoformat(entry["week_start"]) + timedelta(weeks=1)
        gl_dates.append(str(d_end))
        gl_weights.append(entry["target_weight_kg"])
    fig.add_trace(go.Scatter(
        x=gl_dates, y=gl_weights,
        mode="lines", name="목표선",
        line=dict(color=C["goal"], width=1.5, dash="dot"),
    ))

# Goal weight horizontal line
if goal_weight:
    fig.add_hline(
        y=goal_weight,
        line_dash="dash", line_color=C["goal"],
        annotation_text=f"목표 {goal_weight:.1f}kg",
        annotation_position="bottom right",
        annotation_font_color=C["goal"],
        opacity=0.5,
    )

fig.update_layout(
    **PLOTLY_LAYOUT,
    xaxis=dict(gridcolor=C["grid"], title="날짜", showgrid=True),
    yaxis=dict(gridcolor=C["grid"], title="체중 (kg)", showgrid=True),
    height=320,
)
st.plotly_chart(fig, use_container_width=True)

# ── Chart 2: Daily intake vs target ──────────────────────────────────────────
intake_dates  = [l["date"]        for l in logs if (l.get("intake_kcal") or 0) > 0]
intake_values = [l["intake_kcal"] for l in logs if (l.get("intake_kcal") or 0) > 0]

if intake_dates:
    st.markdown("### 일별 섭취 칼로리")
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=intake_dates, y=intake_values,
        name="실제 섭취",
        marker_color=C["actual"],
        opacity=0.75,
    ))
    target_line = latest_plan["target_intake_kcal"] if latest_plan else None
    if target_line:
        fig2.add_hline(
            y=target_line,
            line_dash="dash", line_color=C["goal"],
            annotation_text=f"목표 {target_line:.0f} kcal",
            annotation_position="top right",
            annotation_font_color=C["goal"],
        )
    fig2.update_layout(
        **PLOTLY_LAYOUT,
        xaxis=dict(gridcolor=C["grid"], title="날짜"),
        yaxis=dict(gridcolor=C["grid"], title="kcal"),
        height=240,
        bargap=0.3,
    )
    st.plotly_chart(fig2, use_container_width=True)

# ── TDEE estimate history ─────────────────────────────────────────────────────
st.markdown("### TDEE 추정 추이")
from db.repo import get_connection
conn = get_connection()
plan_rows = conn.execute(
    "SELECT week_start, est_tdee FROM weekly_plan ORDER BY week_start"
).fetchall()
conn.close()

if plan_rows:
    plan_dates = [r["week_start"] for r in plan_rows]
    plan_tdees = [r["est_tdee"]   for r in plan_rows]
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=plan_dates, y=plan_tdees,
        mode="lines+markers", name="추정 TDEE",
        line=dict(color=C["tdee"], width=2),
        marker=dict(size=7),
    ))
    fig3.update_layout(
        **PLOTLY_LAYOUT,
        xaxis=dict(gridcolor=C["grid"], title="주 시작일"),
        yaxis=dict(gridcolor=C["grid"], title="kcal/일"),
        height=220,
    )
    st.plotly_chart(fig3, use_container_width=True)
else:
    st.info("주간 계획이 생성되면 TDEE 추이가 여기에 표시됩니다.")

# ── TDEE estimation result (latest window) ────────────────────────────────────
st.markdown("### 이번 주 TDEE 역산 결과")
tdee_result = estimate_tdee(logs)

if tdee_result["enough_data"]:
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("추정 TDEE", f"{tdee_result['estimated_tdee']:.0f} kcal/일")
    t2.metric("분석 기간", f"{tdee_result['n_days']}일")
    t3.metric("추세 체중 변화", f"{tdee_result['delta_trend_kg']:+.2f} kg")
    t4.metric("총 섭취", f"{tdee_result['total_intake']:.0f} kcal")
else:
    st.info(f"TDEE 역산 데이터 부족 — {tdee_result['reason']}")

# ── Weekly recalibration trigger ─────────────────────────────────────────────
st.divider()
st.markdown("### 주간 재조정")

col_btn, col_info = st.columns([1, 3])
with col_btn:
    run_rebalance = st.button("지금 재조정 실행", type="primary", use_container_width=True)
with col_info:
    st.caption(
        "매주 실행하면 최근 체중·섭취 기록을 분석해 다음 주 목표를 자동 재설정합니다. "
        "보통 일요일 저녁에 실행하세요."
    )

if run_rebalance:
    if not goal_line:
        st.error("목표선이 없습니다. 온보딩을 완료해 주세요.")
    else:
        with st.spinner("재조정 중..."):
            prev_plan_data = {
                "est_tdee": latest_plan["est_tdee"] if latest_plan else 2000.0,
                "target_intake_kcal": latest_plan["target_intake_kcal"] if latest_plan else 1800.0,
                "target_exercise_kcal": latest_plan["target_exercise_kcal"] if latest_plan else 0.0,
                "planned_loss_kg": latest_plan["planned_loss_kg"] if latest_plan else 0.5,
                "goal_line": goal_line,
            }
            from db.repo import get_latest_health_check, upsert_weekly_plan, save_goal_schedule
            health = get_latest_health_check()
            prev_tdee = latest_plan["est_tdee"] if latest_plan else None

            cycle = run_weekly_cycle(
                profile, logs, prev_plan_data, goal_line,
                prev_tdee_estimate=prev_tdee,
                health_check=health,
            )

        report = cycle["report"]
        nw = cycle["next_week"]
        new_gl = nw["goal_line"]

        st.success("재조정 완료!")

        r1, r2, r3 = st.columns(3)
        r1.metric("추정 TDEE",
                  f"{report['new_est_tdee']:.0f} kcal",
                  delta=f"{report['tdee_delta']:+.0f} kcal" if report["tdee_delta"] else None)
        r2.metric("다음 주 목표 섭취",
                  f"{report['new_target_intake']:.0f} kcal/일",
                  delta=f"{report['intake_delta']:+.0f}" if report["intake_delta"] else None)
        r3.metric("실제 주간 감량",
                  f"{report['actual_weekly_loss_kg']:.2f} kg",
                  delta=f"목표 {report['prev_planned_loss_kg']:.2f}kg",
                  delta_color="off")

        if cycle["validation_notes"]:
            for note in cycle["validation_notes"]:
                st.info(note)

        # Save new weekly plan
        from datetime import date as _date
        week_start = str(_date.today())
        upsert_weekly_plan({
            "week_start": week_start,
            "est_tdee": nw["est_tdee"],
            "target_intake_kcal": nw["target_intake_kcal"],
            "target_exercise_kcal": nw["target_exercise_kcal"],
            "planned_loss_kg": nw["planned_loss_kg"],
            "flags_json": nw["flags"],
        })
        save_goal_schedule(new_gl)
        st.rerun()

# ── Recorded days summary ─────────────────────────────────────────────────────
st.divider()
recorded = len([l for l in logs if (l.get("weight_kg") or 0) > 0])
intake_recorded = len([l for l in logs if (l.get("intake_kcal") or 0) > 0])
st.caption(f"최근 90일 — 체중 {recorded}일 기록 / 식사 {intake_recorded}일 기록")
