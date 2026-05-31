# Diet Loop — 적응형 TDEE 다이어트 폐루프 코치

매일 체중·식사를 기록하면 내 진짜 대사량을 역산해 다음 주 계획을 자동 재조정하는 개인용 다이어트 코치.

## 빠른 시작

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

```bash
cp .env.example .env
# .env 파일을 열어 API 키 입력
```

LLM 공급자(Phase 5에서 필요):
- **Gemini 2.5 Flash** (기본): [Google AI Studio](https://aistudio.google.com/)에서 `GEMINI_API_KEY` 발급
- **Claude** (선택): [Anthropic Console](https://console.anthropic.com/)에서 `ANTHROPIC_API_KEY` 발급

### 3. 영양 DB 확장 (선택)

`data/foods_seed.csv`에 기본 음식 데이터가 포함되어 있습니다.  
더 많은 데이터를 원하면 [공공데이터포털](https://www.data.go.kr/)에서 **식품영양성분 DB**를 내려받아 `data/` 폴더에 추가 후 `db/repo.py`의 `_seed_foods()`를 확장하세요.

### 4. 실행

```bash
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 접속.

---

## 구현 현황

| Phase | 내용 | 상태 |
|-------|------|------|
| 1 | 스캐폴딩, DB, CRUD, 기본 UI | ✅ 완료 |
| 2 | 에너지 모델, 안전 가드레일, 목표선 | 🔜 다음 |
| 3 | 적응형 TDEE 재조정 | 예정 |
| 4 | 추세 화면 (목표선 그래프) | 예정 |
| 5 | LLM 식단 생성, 자연어 기록 파서 | 예정 |
| 6 | 재조정 리포트, 안전 플래그 UI | 예정 |

## 주의 사항

본 앱은 참고용이며 전문의 상담을 대체하지 않습니다.
