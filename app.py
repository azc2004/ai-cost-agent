"""AI 서비스 비용 산출 대시보드 (Streamlit).
단가·토큰·환율은 모두 UI 입력값. 이 앱은 '산출 엔진'이며 단가 진위는 보증하지 않는다.
구조: 영역A(사이드바 전역 파라미터) / 영역B(서비스별 시나리오) / 영역C(통합 비교+차트).
차트 색상은 dataviz 스킬 검증 팔레트(라이트/다크) 사용. 비교표=대비필요 충족, 라인 종단 라벨=CVD floor 충족."""

import json
import urllib.request

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

import services as S

st.set_page_config(page_title="AI 비용 산출 대시보드", layout="wide", page_icon="💸")


@st.cache_data(ttl=3600)
def _live_fx():
    """실시간 USD→KRW 환율 (open.er-api.com, API 키 불필요). 실패 시 DEFAULT_FX 폴백. → (rate, ok)"""
    try:
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=5) as r:
            return float(json.load(r)["rates"]["KRW"]), True
    except Exception:
        return S.DEFAULT_FX, False

# ---- 테마(라이트/다크) 감지 → 팔레트/잉크 선택 ----
try:
    _base = st.get_option("theme.base")
except Exception:
    _base = None
DARK = _base == "dark"

SERV_ORDER = ["coordi", "review", "search", "vod"]
PALETTE = {
    "light": ["#2a78d6", "#1baf7a", "#eda100", "#008300"],
    "dark":  ["#3987e5", "#199e70", "#c98500", "#008300"],
}
INK = {
    "light": dict(primary="#0b0b0b", secondary="#52514e", muted="#898781",
                  grid="#e1e0d9", axis="#c3c2b7", surface="#fcfcfb"),
    "dark":  dict(primary="#ffffff", secondary="#c3c2b7", muted="#898781",
                  grid="#2c2c2a", axis="#383835", surface="#1a1a19"),
}
SERIES_COLOR = {k: PALETTE["dark" if DARK else "light"][i] for i, k in enumerate(SERV_ORDER)}
ink = INK["dark" if DARK else "light"]

# ---- 모델별 과금 종류 + 토큰 단계(데이터에서 추출, UI 편집용) ----
MODEL_KIND = {
    "gpt-4o": "token", "gpt-4o-mini": "token", "gemini-2.5-flash": "token",
    "gemini-3.1-flash-image": "image",
    **{k: "per_second" for k in S.VIDEO_PRICES},  # veo {lite/pro}-{720p/1080p} 매트릭스
    "luma-dream-machine": "per_video",
}
KIND_LABEL = {"token": "토큰/1M", "image": "이미지/장", "per_second": "비디오/초", "per_video": "비디오/개"}


def token_steps():
    """편집 가능한 토큰 단계 목록(coordi는 mode1의 토큰 단계로 대표)."""
    rows, seen = [], set()
    for st_ in S.SERVICES["coordi"]["recipes"]["mode1"] + S.SERVICES["review"]["steps"] \
            + S.SERVICES["search"]["steps"] + S.SERVICES["vod"]["steps"]:
        if st_["billing"] == S.TOKEN and st_["id"] not in seen:
            seen.add(st_["id"])
            owner = next(s for s, v in S.SERVICES.items()
                         if any(x["id"] == st_["id"] for x in
                                (v.get("steps") or [r for rec in v.get("recipes", {}).values() for r in rec])))
            rows.append((st_["id"], S.SERVICES[owner]["name"], st_["name"], st_["in_tok"], st_["out_tok"]))
    return rows


# ================= 영역 A — 사이드바(전역 파라미터) =================
st.sidebar.title("⚙️ 전역 파라미터")
_fx_live, _fx_ok = _live_fx()
fx = st.sidebar.slider("환율 (KRW/USD)", 1000, 2000,
                       max(1000, min(2000, int(round(_fx_live)))), step=10)
st.sidebar.caption(f"기본값 = {'실시간' if _fx_ok else '폴백'} ₩{int(round(_fx_live)):,}/USD · 출처 open.er-api.com")

st.sidebar.subheader("단가 테이블 (USD)")
st.sidebar.caption("⚠️ 조사일(2026-07) 참고값. 공식 가격 페이지에서 반드시 확인·수정.")
price_df = pd.DataFrame([
    {"model": m, "kind": KIND_LABEL[MODEL_KIND[m]],
     "in_price": S.DEFAULT_PRICES[m].get("in", 0.0),
     "out_price": S.DEFAULT_PRICES[m].get("out", 0.0),
     "per_image": S.DEFAULT_PRICES[m].get("per_image", 0.0),
     "per_second": S.DEFAULT_PRICES[m].get("per_second", 0.0),
     "per_video": S.DEFAULT_PRICES[m].get("per_video", 0.0)}
    for m in MODEL_KIND
])
edited_price = st.sidebar.data_editor(
    price_df,
    column_config={
        "model": st.column_config.TextColumn("모델", disabled=True),
        "kind": st.column_config.TextColumn("과금", disabled=True),
        "in_price": st.column_config.NumberColumn("input/1M", format="%.4f"),
        "out_price": st.column_config.NumberColumn("output/1M", format="%.4f"),
        "per_image": st.column_config.NumberColumn("장당", format="%.4f"),
        "per_second": st.column_config.NumberColumn("초당", format="%.4f"),
        "per_video": st.column_config.NumberColumn("개당", format="%.4f"),
    },
    num_rows="fixed", key="price_tbl", width="stretch",
)
prices = {}
for _, r in edited_price.iterrows():
    k = MODEL_KIND[r["model"]]
    if k == "token":
        prices[r["model"]] = {"in": r["in_price"], "out": r["out_price"]}
    elif k == "image":
        prices[r["model"]] = {"per_image": r["per_image"]}
    elif k == "per_second":
        prices[r["model"]] = {"per_second": r["per_second"]}
    else:
        prices[r["model"]] = {"per_video": r["per_video"]}

st.sidebar.subheader("입력 토큰 추정치")
st.sidebar.caption("코드에 명시된 값은 출력 max_tokens뿐. input은 추정(수정 가능).")
tok_df = pd.DataFrame(token_steps(), columns=["sid", "svc", "step", "in_tok", "out_tok"])
edited_tok = st.sidebar.data_editor(
    tok_df,
    column_config={
        "sid": st.column_config.TextColumn("ID", disabled=True),
        "svc": st.column_config.TextColumn("서비스", disabled=True),
        "step": st.column_config.TextColumn("단계", disabled=True),
        "in_tok": st.column_config.NumberColumn("input 토큰", step=100),
        "out_tok": st.column_config.NumberColumn("output 토큰", step=100),
    },
    num_rows="fixed", key="tok_tbl", width="stretch",
)
tok_overrides = {r["sid"]: {"in": r["in_tok"], "out": r["out_tok"]} for _, r in edited_tok.iterrows()}


def unit(svc_key, opts):
    usd, krw, _ = S.cost_unit(svc_key, opts, prices, tok_overrides, fx=fx)
    return usd, krw


# ================= 영역 B — 서비스별 시나리오 =================
st.title("💸 AI 서비스 비용 산출 대시보드")
st.caption("4개 서비스의 기준 산출물 1건당 비용을 환율·수량·단가 변동에 따라 시뮬레이션.")

opts = {}
qty = {}
unit_krw = {}
unit_usd = {}
tabs = st.tabs([f"{S.SERVICES[k]['name']} ({S.SERVICES[k]['unit']})" for k in SERV_ORDER])
for tab, k in zip(tabs, SERV_ORDER):
    svc = S.SERVICES[k]
    with tab:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.markdown(f"**제공자:** {svc['provider']}")
            q = st.slider("기준 수량 (건)", 1, 10000, 100, 10, key=f"qty_{k}")
            o = dict(S.DEFAULT_OPTIONS[k])
            if k == "coordi":
                o["mode"] = st.selectbox("모드", list(svc["modes"]), format_func=lambda m: svc["modes"][m], key=f"m_{k}")
                o["try_on_n"] = st.number_input("착장 횟수 N", 1, 50, S.DEFAULT_OPTIONS[k]["try_on_n"], key=f"n_{k}")
                o["retry_pct"] = st.slider("재시도 가중률 (%)", 0.0, 300.0, 0.0, 5.0, key=f"r_{k}")
            elif k == "review":
                o["model"] = st.selectbox("모델", svc["selectable_model"],
                                          index=svc["selectable_model"].index(S.DEFAULT_OPTIONS[k]["model"]),
                                          key=f"md_{k}")
                o["val_retries"] = st.slider("검증 재시도 평균 (회)", 0.0, 5.0, 0.0, 0.1, key=f"vr_{k}")
            elif k == "search":
                o["cache_hit_pct"] = st.slider("큐레이션 캐시 적중률 (%)", 0.0, 100.0, 0.0, 5.0, key=f"c_{k}")
            elif k == "vod":
                o["avg_images"] = st.slider("평균 분석 이미지 수", 1, 14, S.DEFAULT_OPTIONS[k]["avg_images"], key=f"ai_{k}")
                o["video_model"] = st.selectbox("비디오 모델", list(S.VIDEO_MODELS),
                                                format_func=lambda m: S.VIDEO_MODELS[m],
                                                index=list(S.VIDEO_MODELS).index(S.DEFAULT_OPTIONS[k]["video_model"]), key=f"vm_{k}")
                o["video_res"] = st.selectbox("해상도", S.VIDEO_RESOLUTIONS,
                                              index=S.VIDEO_RESOLUTIONS.index(S.DEFAULT_OPTIONS[k]["video_res"]), key=f"vr_{k}")
                o["video_sec"] = st.slider("비디오 길이 (초)", 1.0, 30.0, S.DEFAULT_OPTIONS[k]["video_sec"], 1.0, key=f"vs_{k}")
                o["luma_prob"] = st.slider("Luma 폴백 확률 (%)", 0.0, 100.0, 0.0, 5.0, key=f"lp_{k}")
        with c2:
            usd1, krw1 = unit(k, o)
            opts[k], qty[k], unit_usd[k], unit_krw[k] = o, q, usd1, krw1
            m1, m2, m3 = st.columns(3)
            m1.metric("1건당 (USD)", f"${usd1:.6f}")
            m2.metric("1건당 (KRW)", f"₩{krw1:,.2f}")
            m3.metric(f"총비용 ({q:,}건)", f"₩{krw1*q:,.0f}")
            _, _, bd = S.cost_unit(k, o, prices, tok_overrides, fx=fx)
            st.dataframe(pd.DataFrame(bd), width="stretch", hide_index=True)


# ================= 영역 C — 통합 비교 + 차트 + 환율 민감도 =================
st.divider()
st.header("📊 통합 비교")

cmp_df = pd.DataFrame({
    "서비스": [S.SERVICES[k]["name"] for k in SERV_ORDER],
    "기준단위": [S.SERVICES[k]["unit"] for k in SERV_ORDER],
    "1건당 USD": [unit_usd[k] for k in SERV_ORDER],
    "1건당 KRW": [unit_krw[k] for k in SERV_ORDER],
    "수량(건)": [qty[k] for k in SERV_ORDER],
    "총비용 KRW": [unit_krw[k] * qty[k] for k in SERV_ORDER],
})
st.dataframe(cmp_df.style.format({"1건당 USD": "${:.6f}", "1건당 KRW": "₩{:,.2f}",
                                  "총비용 KRW": "₩{:,.0f}"}),
             width="stretch", hide_index=True)
st.caption("ℹ️ aqua/yellow 계열은 라이트 배경에서 대비가 낮아 정확한 값은 이 표를 기준으로 확인.")

# ---- 건수별 비용 라인 차트 (4 series, 단일 축) ----
cc1, cc2 = st.columns([3, 1])
with cc2:
    sweep_max = st.slider("스윕 최대 건수", 10, 10000, 1000, 50, key="sweep")
    log_y = st.checkbox("Y축 로그 스케일 (vod↔review 격차 큼)", value=True, key="logy")

xs = sorted(set(max(1, round(sweep_max * i / 59)) for i in range(60)))
fig = go.Figure()
short = {"coordi": "코디", "review": "상품평", "search": "검색추천", "vod": "VOD영상"}
for k in SERV_ORDER:
    ys = [unit_krw[k] * x for x in xs]
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=S.SERVICES[k]["name"],
                             line=dict(color=SERIES_COLOR[k], width=2),
                             hovertemplate=f"{short[k]}<br>%{{x:,}}건 → ₩%{{y:,.0f}}<extra></extra>"))
# 종단 직접 라벨 (다크 CVD floor / 라이트 대비 relief)
y_last = {k: unit_krw[k] * xs[-1] for k in SERV_ORDER}
for k in SERV_ORDER:
    fig.add_annotation(x=xs[-1], y=y_last[k], text=short[k], showarrow=False,
                       xanchor="left", xshift=6, font=dict(color=SERIES_COLOR[k], size=11))
fig.update_layout(
    margin=dict(l=10, r=70, t=20, b=10), hovermode="x",
    paper_bgcolor=ink["surface"], plot_bgcolor=ink["surface"],
    font=dict(color=ink["primary"], family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
    legend=dict(orientation="h", y=1.08, font=dict(color=ink["secondary"])),
    height=360,
)
fig.update_xaxes(gridcolor=ink["grid"], zeroline=False, tickfont=dict(color=ink["muted"]),
                 title_text="기준 수량 (건)")
log_type = "log" if log_y else "linear"
fig.update_yaxes(gridcolor=ink["grid"], zeroline=False, tickfont=dict(color=ink["muted"]),
                 type=log_type, title_text="총비용 (KRW)")
with cc1:
    st.plotly_chart(fig, width="stretch")

# ---- 환율 민감도 표 ----
st.subheader("환율 민감도 (총비용 KRW)")
fx_vals = sorted(set([1300, 1350, 1380, 1400, 1450, 1500, int(fx)]))
fx_rows = []
for f in fx_vals:
    row = {"환율": f"{f:,}" + (" ←현재" if f == int(fx) else "")}
    for k in SERV_ORDER:
        row[short[k]] = unit_usd[k] * f * qty[k]
    fx_rows.append(row)
fx_df = pd.DataFrame(fx_rows).set_index("환율")
st.dataframe(fx_df.style.format({c: "₩{:,.0f}" for c in fx_df.columns}), width="stretch")

st.caption("산출 엔진: services.py · 단가/토큰/환율은 모두 추정 기본값이며 UI에서 수정 가능합니다.")
