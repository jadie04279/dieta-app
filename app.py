"""
Diet Loop — Adaptive TDEE closed-loop diet coach
Main Streamlit entry point: handles routing and shared setup.
"""
import os
import streamlit as st
from pathlib import Path

# Inject Streamlit secrets into os.environ so non-Streamlit modules (db, llm)
# can read DATABASE_URL, GEMINI_API_KEY, ANTHROPIC_API_KEY via os.environ.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:
    pass

from db.repo import init_db, get_profile

# ── Page config (must be first Streamlit call) ─────────────────────────────
st.set_page_config(
    page_title="Diet Loop",
    page_icon="🔄",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── Inject custom CSS ───────────────────────────────────────────────────────
def _inject_css() -> None:
    css_path = Path(__file__).parent / "assets" / "theme.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

_inject_css()

# 사이드바 완전히 숨김
st.markdown("""
<style>
[data-testid="stSidebarNav"] {display: none !important;}
section[data-testid="stSidebar"] {display: none !important;}
</style>
""", unsafe_allow_html=True)

# ── DB init (idempotent) ────────────────────────────────────────────────────
init_db()

# ── Routing ─────────────────────────────────────────────────────────────────
profile = get_profile()

if not profile:
    # 온보딩
    pg = st.navigation(
        [st.Page("pages/onboarding.py", title="프로필 설정")],
        position="hidden",
    )
else:
    NAV = [
        ("오늘",      "pages/today.py"),
        ("추세",      "pages/trends.py"),
        ("주간 계획",  "pages/weekly.py"),
        ("리포트",    "pages/report.py"),
        ("설정",      "pages/settings.py"),
    ]
    all_pages = [st.Page(path, title=label) for label, path in NAV]
    pg = st.navigation(all_pages, position="hidden")

    # ── 하단 탭 내비게이션 ──────────────────────────────────────────────────
    cols = st.columns(len(NAV))
    for i, (label, path) in enumerate(NAV):
        with cols[i]:
            is_active = (pg.title == label)
            if st.button(
                label,
                key=f"nav_{i}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.switch_page(path)

    st.divider()

pg.run()

# ── Disclaimer ─────────────────────────────────────────────────────────────
st.markdown(
    '<div class="disclaimer">본 앱은 참고용이며 전문의 상담을 대체하지 않습니다.</div>',
    unsafe_allow_html=True,
)
