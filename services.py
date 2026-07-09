"""4개 AI 서비스의 호출 구조(데이터) + 비용 산출 함수.

데이터(딕셔너리)만 고치면 단계/횟수/토큰/모델이 변경된다 — 계산 로직과 분리.
출처: WORK_INSTRUCTION.md 3절(코드 기반 고정값).
단가·입력토큰은 추정 기본값이므로 UI에서 반드시 수정(공식 페이지 확인).

과금 방식 3종(WI 4절):
  token = (in_tok×in단가 + out_tok×out단가) / 1,000,000
  image = 장수 × 장당단가
  video = 초×초당단가  (또는 개당단가)
"""

TOKEN, IMAGE, VIDEO, REQUEST, PAGE = "token", "image", "video", "request", "page"

# ---- 비디오: Veo 3.1 티어(lite/fast/pro) × 해상도(720p/1080p). 기본 lite/720p. ----
# 단가(USD/초) = Google Gemini API 공식 가격(2026-06-30 확인).
#   https://ai.google.dev/gemini-api/docs/pricing  (Veo 3.1)
#   lite = veo-3.1-lite-generate-preview, fast = veo-3.1-fast-generate-preview,
#   pro  = Veo 3.1 Standard (veo-3.1-generate-preview)
VIDEO_MODELS = {"lite": "Veo 3.1 Lite", "fast": "Veo 3.1 Fast", "pro": "Veo 3.1 Standard"}
VIDEO_RESOLUTIONS = ["720p", "1080p"]


def video_key(model, res):
    return f"veo-{model}-{res}"


VIDEO_PRICES = {
    video_key("lite", "720p"):  {"per_second": 0.05},
    video_key("lite", "1080p"): {"per_second": 0.08},
    video_key("fast", "720p"):  {"per_second": 0.10},
    video_key("fast", "1080p"): {"per_second": 0.12},
    video_key("pro", "720p"):   {"per_second": 0.40},   # Standard: 720p/1080p 동일
    video_key("pro", "1080p"):  {"per_second": 0.40},
}

# ---- 단가 기본값(USD). 조사일(2026-07) 참고값. 공식 페이지에서 확인·수정. ----
DEFAULT_PRICES = {
    # USD / 1M tokens
    "gpt-4o":                 {"in": 2.50, "out": 10.00},
    "gpt-4o-mini":            {"in": 0.15, "out": 0.60},
    "gemini-2.5-flash":       {"in": 0.30, "out": 2.50},
    # USD / image — Gemini 3.1 Flash Image(Nano Banana 2) 1K(1024x1024) 장당.
    # 공식: https://ai.google.dev/gemini-api/docs/pricing ($0.067/1K장, 2026-06-30)
    "gemini-3.1-flash-image": {"per_image": 0.067},
    # USD / second — 비디오 모델×해상도 매트릭스
    **VIDEO_PRICES,
    # USD / video  (vod Luma 폴백, 개당)
    "luma-dream-machine":     {"per_video": 0.40},
    # USD / request & page — Exa 외부 검색 API (https://exa.ai/pricing)
    "exa-search":             {"per_request": 0.007},   # $7/1k 검색 요청(결과 ≤10)
    "exa-contents":           {"per_page": 0.001},      # $1/1k 본문 페이지
}

DEFAULT_FX = 1380.0  # KRW/USD — 실시간 환율 조회 실패 시 폴백(기본값은 app.py에서 실시간 조회)


def _tok(sid, name, model, in_tok, out_tok, count=1):
    return {"id": sid, "name": name, "model": model, "billing": TOKEN,
            "count": count, "in_tok": in_tok, "out_tok": out_tok}


def _img(sid, name, model, per_call=1, count=1):
    return {"id": sid, "name": name, "model": model, "billing": IMAGE,
            "count": count, "per_call": per_call}


def _vid(sid, name, model, per_call=5.0, count=1):
    return {"id": sid, "name": name, "model": model, "billing": VIDEO,
            "count": count, "per_call": per_call}


def _req(sid, name, model, count):
    return {"id": sid, "name": name, "model": model, "billing": REQUEST, "count": count}


def _page(sid, name, model, count):
    return {"id": sid, "name": name, "model": model, "billing": PAGE, "count": count}


# ---- 서비스 단계 레시피(데이터) ----
# coordi: 모드별 레시피. mode3_tryon 의 착장 이미지 per_call 은 런타임에 N으로 치환.
# review: 모델 런타임 선택. search: 큐레이션 단계 캐시 적중률로 count 감소.
# vod: 분석 단계 count=평균 이미지 수, Veo 초=런타임, Luma 폴백=확률 추가.
SERVICES = {
    "coordi": {
        "name": "상품 코디 추천", "unit": "코디 1개", "provider": "Google",
        "modes": {
            "mode1": "모드1 (추천+좌표+트렌드+화보)",
            "mode2": "모드2 (좌표+트렌드+화보)",
            "mode3_rec": "모드3-추천만 (추천+트렌드)",
            "mode3_tryon": "모드3-착장포함 (추천+트렌드+착장N)",
        },
        "recipes": {
            "mode1": [
                _tok("coordi_rec", "코디 추천", "gemini-2.5-flash", 1200, 1000),
                _tok("coordi_det", "좌표 검출", "gemini-2.5-flash", 800, 400),
                _tok("coordi_tr", "트렌드 평가", "gemini-2.5-flash", 600, 300),
                _img("coordi_flat", "화보 이미지 생성", "gemini-3.1-flash-image", 1),
            ],
            "mode2": [
                _tok("coordi_det", "좌표 검출", "gemini-2.5-flash", 800, 400),
                _tok("coordi_tr", "트렌드 평가", "gemini-2.5-flash", 600, 300),
                _img("coordi_flat", "화보 이미지 생성", "gemini-3.1-flash-image", 1),
            ],
            "mode3_rec": [
                _tok("coordi_rec", "코디 추천", "gemini-2.5-flash", 1200, 1000),
                _tok("coordi_tr", "트렌드 평가", "gemini-2.5-flash", 600, 300),
            ],
            "mode3_tryon": [
                _tok("coordi_rec", "코디 추천", "gemini-2.5-flash", 1200, 1000),
                _tok("coordi_tr", "트렌드 평가", "gemini-2.5-flash", 600, 300),
                _img("coordi_tryon", "착장 이미지 생성", "gemini-3.1-flash-image", 1),
            ],
        },
    },
    "review": {
        "name": "상품평 초안 자동작성·검증", "unit": "상품평 1개", "provider": "OpenAI",
        "selectable_model": ["gpt-4o", "gpt-4o-mini"],
        "steps": [
            _tok("rev_val", "이미지 검증", "gpt-4o-mini", 1000, 250),   # max_tokens 250, 런타임 모델 치환
            _tok("rev_gen", "리뷰 작성", "gpt-4o-mini", 1200, 1000),    # max_tokens 1000
        ],
    },
    "search": {
        "name": "검색 키워드 추천상품", "unit": "추천 1번", "provider": "OpenAI/LiteLLM",
        "steps": [
            _tok("srch_guide", "가이드 생성", "gpt-4o-mini", 800, 700),  # max_tokens 700
            _tok("srch_cur", "AI 큐레이션", "gpt-4o-mini", 1500, 800),   # max_tokens 800, ttl=600 캐시
        ],
    },
    "vod": {
        "name": "모델 워킹/턴 영상", "unit": "동영상 1개", "provider": "OpenAI+Google",
        "steps": [
            _tok("vod_analyze", "이미지 분석/크롭", "gpt-4o-mini", 500, 300),  # count=평균 이미지 수(1~14)
            _vid("vod_veo", "비디오 생성(Veo)", video_key("lite", "720p"), 10.0),
        ],
    },
}

# ---- 서비스별 기본 옵션(UI 초기값) ----
DEFAULT_OPTIONS = {
    "coordi": {"mode": "mode1", "try_on_n": 3, "retry_pct": 0.0},
    "review": {"model": "gpt-4o-mini", "val_retries": 0.0},
    "search": {"cache_hit_pct": 0.0, "exa_enabled": True, "exa_calls": 2, "exa_results": 5},
    "vod": {"avg_images": 14, "video_sec": 10.0, "luma_prob": 0.0, "video_model": "lite", "video_res": "720p"},
}


def concrete_steps(svc_key, opts):
    """옵션을 적용해 실제 단계 리스트(횟수/단위 확정) 반환. 유일한 분기 지점."""
    s = SERVICES[svc_key]
    out = []
    if svc_key == "coordi":
        for st in s["recipes"][opts["mode"]]:
            st = dict(st)
            if st["id"] == "coordi_tryon":
                st["per_call"] = opts["try_on_n"]
            out.append(st)
        rw = 1 + opts["retry_pct"] / 100          # 재시도 가중률 → 기댓값 호출 수
        for st in out:
            st["count"] *= rw
    elif svc_key == "review":
        for st in s["steps"]:
            st = dict(st)
            st["model"] = opts["model"]
            out.append(st)
        out[0]["count"] = 1 + opts["val_retries"]  # 검증 재시도 평균 추가 호출
    elif svc_key == "search":
        for st in s["steps"]:
            out.append(dict(st))
        out[1]["count"] = 1 - opts["cache_hit_pct"] / 100  # 캐시 적중 시 큐레이션 미호출
        if opts.get("exa_enabled"):                          # Exa 트렌드 수집(선택)
            out.append(_req("srch_exa", "Exa 트렌드 검색", "exa-search", opts["exa_calls"]))
            out.append(_page("srch_exa_body", "Exa 본문(contents)", "exa-contents",
                             opts["exa_calls"] * opts["exa_results"]))
    elif svc_key == "vod":
        a = dict(s["steps"][0]); a["count"] = opts["avg_images"]; out.append(a)
        v = dict(s["steps"][1])
        v["model"] = video_key(opts["video_model"], opts["video_res"])
        v["per_call"] = opts["video_sec"]
        out.append(v)
        if opts["luma_prob"] > 0:
            out.append({"id": "vod_luma", "name": "Luma 폴백",
                        "model": "luma-dream-machine", "billing": VIDEO,
                        "count": opts["luma_prob"] / 100, "per_call": 1})
    return out


def step_usd(step, prices, tok=None):
    """단일 단계 비용(USD). tok={"in","out"} 로 토큰 오버라이드 가능."""
    p = prices[step["model"]]
    if step["billing"] == TOKEN:
        it = tok["in"] if tok else step["in_tok"]
        ot = tok["out"] if tok else step["out_tok"]
        return (it * p["in"] + ot * p["out"]) / 1_000_000 * step["count"]
    if step["billing"] == IMAGE:
        return step["count"] * step["per_call"] * p["per_image"]
    if step["billing"] == REQUEST:
        return step["count"] * p["per_request"]
    if step["billing"] == PAGE:
        return step["count"] * p["per_page"]
    # VIDEO
    if "per_video" in p:
        return step["count"] * p["per_video"]
    return step["count"] * step["per_call"] * p["per_second"]


def cost_unit(svc_key, opts, prices, tok_overrides=None, fx=DEFAULT_FX):
    """기준 산출물 1건당 비용. → (usd, krw, breakdown[list])."""
    tok_overrides = tok_overrides or {}
    total = 0.0
    bd = []
    for st in concrete_steps(svc_key, opts):
        u = step_usd(st, prices, tok_overrides.get(st["id"]))
        total += u
        bd.append({"단계": st["name"], "모델": st["model"],
                   "과금": {"token": "토큰", "image": "이미지", "video": "비디오",
                           "request": "검색요청", "page": "본문"}[st["billing"]],
                   "USD": u, "KRW": u * fx})
    return total, total * fx, bd


def demo():
    """self-check: 4개 서비스 1건당 비용 출력."""
    fx = DEFAULT_FX
    for k in SERVICES:
        usd, krw, bd = cost_unit(k, DEFAULT_OPTIONS[k], DEFAULT_PRICES, fx=fx)
        print(f"{SERVICES[k]['name']:>20} | 1건당 ${usd:.6f} / ₩{krw:,.2f}  ({len(bd)}단계)")


if __name__ == "__main__":
    demo()
