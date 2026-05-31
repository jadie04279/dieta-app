"""
Settings page: profile, macros, food preferences, LLM status, health check input.
"""
import streamlit as st
import json
from datetime import date, timedelta
from db.repo import get_profile, upsert_profile, add_health_check, get_latest_health_check

st.markdown("## 설정")

profile = get_profile()
if not profile:
    st.error("프로필이 없습니다. 앱을 재시작해 주세요.")
    st.stop()

# ── Profile & Macro form ──────────────────────────────────────────────────────
with st.form("settings_form"):
    st.markdown("### 신체 정보")
    col1, col2 = st.columns(2)
    with col1:
        sex = st.selectbox(
            "성별",
            ["female", "male"],
            index=0 if profile["sex"] == "female" else 1,
            format_func=lambda x: "여성" if x == "female" else "남성",
        )
        height_cm = st.number_input(
            "키 (cm)", min_value=100.0, max_value=250.0,
            value=float(profile["height_cm"]), step=0.5,
        )
    with col2:
        birth_date = st.date_input("생년월일",
            value=date.fromisoformat(profile["birth_date"]) if profile.get("birth_date") else date(1990, 1, 1))
        activity_labels = {
            1.2:   "거의 안 움직임",
            1.375: "가볍게 활동 (주 1~3회)",
            1.55:  "보통 활동 (주 3~5회)",
            1.725: "활발히 활동 (주 6~7회)",
            1.9:   "매우 활발",
        }
        activity_factor = st.selectbox(
            "활동 수준",
            list(activity_labels.keys()),
            index=list(activity_labels.keys()).index(profile["activity_factor"])
            if profile["activity_factor"] in activity_labels else 2,
            format_func=lambda x: activity_labels[x],
        )

    st.markdown("### 목표")
    col3, col4 = st.columns(2)
    with col3:
        goal_weight = st.number_input(
            "목표 체중 (kg)", min_value=30.0, max_value=300.0,
            value=float(profile.get("goal_weight_kg") or 60.0), step=0.5,
        )
    with col4:
        target_date_val = (
            date.fromisoformat(profile["target_date"])
            if profile.get("target_date")
            else date.today() + timedelta(weeks=12)
        )
        target_date = st.date_input("목표 날짜", value=target_date_val)

    st.markdown("### 매크로 비율")
    col5, col6, col7 = st.columns(3)
    with col5:
        carb_pct = st.number_input("탄수화물 (%)", min_value=20, max_value=70, value=int(profile.get("macro_carb_pct", 50)))
    with col6:
        prot_pct = st.number_input("단백질 (%)",   min_value=15, max_value=50, value=int(profile.get("macro_prot_pct", 30)))
    with col7:
        fat_pct  = st.number_input("지방 (%)",     min_value=10, max_value=40, value=int(profile.get("macro_fat_pct",  20)))

    macro_sum = carb_pct + prot_pct + fat_pct
    if macro_sum != 100:
        st.warning(f"매크로 합계가 {macro_sum}%입니다. 100%가 되어야 합니다.")

    st.markdown("### 음식 선호도")
    prefs = profile.get("food_prefs_json", "{}")
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except Exception:
            prefs = {}

    cuisine = st.selectbox(
        "음식 스타일",
        ["korean", "asian", "western"],
        index=["korean", "asian", "western"].index(prefs.get("cuisine", "korean"))
        if prefs.get("cuisine", "korean") in ["korean", "asian", "western"] else 0,
        format_func=lambda x: {"korean": "한식 위주", "asian": "아시아식", "western": "서양식"}[x],
    )
    dislikes_raw = st.text_area(
        "기피 음식 (줄 구분, 예: 오징어)",
        value="\n".join(prefs.get("dislikes", [])),
        height=80,
        help="AI 식단 생성 시 이 음식들을 제외합니다.",
    )

    submitted = st.form_submit_button("저장", use_container_width=True, type="primary")

if submitted:
    if carb_pct + prot_pct + fat_pct != 100:
        st.error("매크로 비율 합계가 100%가 되어야 합니다.")
    else:
        dislikes = [d.strip() for d in dislikes_raw.splitlines() if d.strip()]
        new_prefs = {"cuisine": cuisine, "dislikes": dislikes}
        upsert_profile({
            "sex":             sex,
            "birth_date":      str(birth_date),
            "height_cm":       height_cm,
            "activity_factor": activity_factor,
            "goal_weight_kg":  goal_weight,
            "target_date":     str(target_date),
            "macro_carb_pct":  carb_pct,
            "macro_prot_pct":  prot_pct,
            "macro_fat_pct":   fat_pct,
            "food_prefs_json": json.dumps(new_prefs, ensure_ascii=False),
        })
        st.success("설정이 저장되었습니다.")
        st.rerun()

st.divider()

# ── Health check input ────────────────────────────────────────────────────────
st.markdown("### 건강 검사 기록 입력")
st.caption("최근 건강검진 결과를 입력하면 안전 알림 및 운동 강도 조정에 활용됩니다.")

latest_hc = get_latest_health_check()
if latest_hc:
    st.info(f"가장 최근 기록: {latest_hc.get('date', '—')} (공복혈당 {latest_hc.get('fasting_glucose') or '—'} mg/dL)")

with st.form("health_check_form"):
    hc_date = st.date_input("검사 날짜", value=date.today())

    hc1, hc2, hc3 = st.columns(3)
    with hc1:
        glucose = st.number_input("공복혈당 (mg/dL)", min_value=0.0, max_value=500.0, value=0.0, step=1.0)
    with hc2:
        sbp = st.number_input("수축기 혈압 (mmHg)", min_value=0, max_value=300, value=0, step=1)
    with hc3:
        dbp = st.number_input("이완기 혈압 (mmHg)", min_value=0, max_value=200, value=0, step=1)

    hc4, hc5, hc6, hc7 = st.columns(4)
    with hc4:
        ldl    = st.number_input("LDL (mg/dL)",      min_value=0.0, max_value=500.0, value=0.0, step=1.0)
    with hc5:
        hdl    = st.number_input("HDL (mg/dL)",      min_value=0.0, max_value=200.0, value=0.0, step=1.0)
    with hc6:
        tg     = st.number_input("중성지방 (mg/dL)", min_value=0.0, max_value=1000.0, value=0.0, step=1.0)
    with hc7:
        tc     = st.number_input("총 콜레스테롤",    min_value=0.0, max_value=600.0,  value=0.0, step=1.0)

    notes = st.text_input("메모 (선택)", placeholder="예: 공복 8시간 후 채혈")
    hc_submitted = st.form_submit_button("건강 검사 저장", use_container_width=True)

if hc_submitted:
    add_health_check({
        "date":            str(hc_date),
        "fasting_glucose": glucose  or None,
        "sbp":             sbp      or None,
        "dbp":             dbp      or None,
        "ldl":             ldl      or None,
        "hdl":             hdl      or None,
        "triglyceride":    tg       or None,
        "total_chol":      tc       or None,
        "notes":           notes    or None,
    })
    st.success("건강 검사 기록이 저장되었습니다.")
    st.rerun()

st.divider()

# ── LLM provider status ───────────────────────────────────────────────────────
st.markdown("### AI 제공자 상태")

from llm.provider import provider_status
status = provider_status()

ps1, ps2, ps3 = st.columns(3)
ps1.metric("사용 가능", "✅ 예" if status["available"] else "❌ 아니오")
ps2.metric("제공자",    status["type"])
ps3.metric("API 키",
           ("Groq ✅" if status.get("groq_key_set") else "Groq ❌") + " / " +
           ("Gemini ✅" if status["gemini_key_set"] else "Gemini ❌") + " / " +
           ("Claude ✅" if status["claude_key_set"] else "Claude ❌"))

if not status["available"]:
    st.warning(
        "LLM이 설정되지 않았습니다. Streamlit Secrets에 `GROQ_API_KEY`를 설정하면 "
        "AI 식단 파싱 및 식단 제안 기능이 활성화됩니다. 현재는 규칙 기반 폴백으로 동작합니다."
    )
else:
    st.success(f"{status['type']}를 통해 AI 기능이 활성화되어 있습니다.")

st.divider()

# ── Medical disclaimer ────────────────────────────────────────────────────────
st.markdown("### 의학적 고지")
st.markdown(
    """
    <div style="
        background:rgba(244,184,74,0.07);
        border:1px solid rgba(244,184,74,0.25);
        border-radius:12px;
        padding:16px 20px;
        font-size:13px;
        color:#9AA7B4;
        line-height:1.7;
    ">
    ⚠️ 본 앱은 체중 관리를 위한 참고 정보를 제공하며 의료 진단·치료·처방을 대체하지 않습니다.<br>
    당뇨, 고혈압, 심혈관·신장 질환, 임신·수유 중인 경우 반드시 <strong>전문 의료인과 상담</strong> 후 계획을 조정하세요.<br>
    급격한 체중 변화, 극심한 피로, 어지러움, 심계항진이 나타나면 즉시 의료 도움을 받으세요.
    </div>
    """,
    unsafe_allow_html=True,
)
