"""
신당 숏폼 자동화 파이프라인 (웹앱에서 호출)
대본(Claude) → 캐릭터 목소리(ElevenLabs) → 말하는 캐릭터(fal.ai Kling Avatar) 또는 장면 영상(Veo) → 합성(FFmpeg) → 업로드
설정은 data/config.json, API 키는 data/secrets.json, 기록은 data/history.json
"""

import datetime as dt
import json
import os
import re
import subprocess
import threading
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
SECRETS_FILE = os.path.join(DATA_DIR, "secrets.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
FALLBACK_BG_DIR = os.path.join(BASE_DIR, "backgrounds")
YT_TOKEN = os.path.join(DATA_DIR, "youtube_token.json")
TT_TOKEN = os.path.join(DATA_DIR, "tiktok_token.json")
LICENSE_FILE = os.path.join(DATA_DIR, "license.json")
# 판매자 라이선스 공개키 (서명은 판매자만 가진 개인키로 함)
LICENSE_PUBLIC_KEY = "yHwZOj7QJDkHCHtZDlAQnE3gIPmdqd0JKqpYEhOTyyY="

CLAUDE_MODEL = "claude-sonnet-5-5"
VEO_NEGATIVE = ("text, letters, subtitles, watermark, logo, horror, blood, ghost, "
                "scary face, distorted hands, extra fingers, cartoon, low quality")

SECRET_KEYS = ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY",
               "IG_USER_ID", "IG_ACCESS_TOKEN", "X_USER_ACCESS_TOKEN",
               "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"]

PLATFORMS = ["instagram", "youtube", "tiktok", "x"]   # 인스타그램은 기본, 나머지는 옵션(라이선스)

# 플랫폼별 편집: 같은 캐릭터 장면으로 첫 화면 문구·자막 모양·길이·엔딩 문구를 다르게
VARIANT_STYLE = {
    # ASS 색은 &HAABBGGRR
    "instagram": {"sub": "FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=70",
                  "trim": False, "cta": "댓글에 '{keyword}' 남기면 DM 드려요"},
    "youtube":   {"sub": "FontSize=17,Bold=1,PrimaryColour=&H0000E5FF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,MarginV=90",
                  "trim": False, "cta": "고정 댓글에서 예약 링크 확인"},
    "tiktok":    {"sub": "FontSize=15,PrimaryColour=&H00FFFFFF,OutlineColour=&H99000000,BackColour=&H99000000,BorderStyle=3,Outline=6,Shadow=0,MarginV=100",
                  "trim": False, "cta": "프로필 링크에서 상담 예약"},
    "x":         {"sub": "FontSize=15,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=70",
                  "trim": True, "cta": "DM으로 상담 문의"},
}

# ───────────────────────── 진행 캐릭터 ─────────────────────────
# 기준 이미지는 data/characters/<id>.png. 정면 상반신이어야 입 모양이 잘 맞아요.
CHARACTER_IDS = ["dongja", "seonbi", "seonnyeo", "mudang"]
DEFAULT_CHARACTERS = {
    "dongja": {
        "name": "애기동자", "on": True, "voice_id": "",
        "role": "밝고 귀여운 아이 말투(존댓말)로 오늘 주제를 꺼내고 쉽게 설명해요.",
        "motion": "bouncing lightly with excitement, waving a small round fan, cheerful hand gestures",
        "prompt": ("Cute 3D animated character in a Pixar-like style: a Korean shrine child spirit (aegi-dongja) "
                   "with chubby rosy cheeks and a round topknot tied with a yellow-green ribbon, "
                   "wearing a rainbow-striped saekdong hanbok with a blue ribbon, holding a small round fan and a wooden staff. "
                   "Front-facing upper-body portrait looking straight at the camera, mouth gently closed, friendly expression. "
                   "Warm candlelight and a softly blurred traditional shrine altar behind. Vertical 9:16, no text."),
    },
    "seonbi": {
        "name": "선비", "on": True, "voice_id": "",
        "role": "점잖고 차분한 말투로 옛 풍습과 유래를 풀어줘요.",
        "motion": "calm nods, occasionally gesturing with an ink brush in his right hand",
        "prompt": ("Photorealistic cinematic portrait of a fictional Korean Joseon-era scholar (seonbi) in his 40s, "
                   "wearing a white dopo robe and a black horsehair gat hat, neatly trimmed beard, calm and wise expression. "
                   "Front-facing upper-body portrait looking straight at the camera, mouth gently closed. "
                   "Soft window light, a traditional hanok study with ink brushes and paper scrolls softly blurred behind. Vertical 9:16, no text."),
    },
    "seonnyeo": {
        "name": "선녀", "on": True, "voice_id": "",
        "role": "부드럽고 따뜻한 존댓말로 마무리하고 댓글·상담을 안내해요.",
        "motion": "graceful slow hand gestures, silk shawl gently flowing, warm smile",
        "prompt": ("Photorealistic cinematic portrait of a fictional graceful Korean celestial maiden (seonnyeo) in her 20s, "
                   "wearing a pink and sky-blue hanbok with gold embroidery and a flowing translucent silk sash, "
                   "pink flower ornaments in her traditional updo, gentle smile. "
                   "Front-facing upper-body portrait looking straight at the camera, mouth gently closed. "
                   "Cherry blossom branches and soft pastel clouds softly blurred behind. Vertical 9:16, no text."),
    },
    "mudang": {
        "name": "무당", "on": False, "voice_id": "",
        "role": "친근한 반말로 비밀 얘기해주듯, 띠·나이·달을 콕 집어 짧게 끊어 말해요. (예: 딱 하루 있어. 딱 세 가지만 짚어줄게.)",
        "motion": ("expressive hand gestures while explaining, palms open toward the camera, "
                   "occasionally lifting the brass ritual bells, leaning slightly forward as if sharing a secret"),
        "prompt": ("Photorealistic cinematic portrait of a fictional Korean female shaman (mudang) in her 40s, "
                   "wearing a red and royal-blue ritual robe with gold embroidery, a black jeollip ritual hat decorated with "
                   "a pink peony and red tassels, a long amber bead necklace, holding brass ritual bells. "
                   "Front-facing upper-body portrait looking straight at the camera, mouth gently closed, calm confident expression. "
                   "Dim shrine with many lit candles, painted deity scrolls and fruit offerings softly blurred behind. Vertical 9:16, no text."),
    },
}

DEFAULT_CONFIG = {
    "shindang": {
        "name": "대박신당",
        "features": "편안한 카페 분위기, 사업/재물운 전문, 전화/비대면 상담 가능",
        "keyword": "운세",
        "link": "https://open.kakao.com/o/example",
        "location": "서울 강남구",
    },
    "topic_fixed": "",
    "voice_ids": [],
    "veo_model": "veo-3.1-generate-preview",
    "schedule_enabled": False,
    "schedule_time": "08:37",
    "upload_mode": "review",          # review = 내가 확인 후 업로드, auto = 바로 업로드
    "platforms": {"instagram": True, "youtube": True, "tiktok": False, "x": True},
    "sub_font": "NanumGothic",
    "graph_version": "v23.0",
    "brand": {
        "accent": "#B3302A",          # 앱 버튼·도장 색
        "seal": "",                   # 도장 두 글자 (비우면 신당 이름 앞 두 글자)
        "cta": "카카오톡으로 상담 예약",  # 영상 마지막 화면 문구
        "end_card": True,             # 영상 끝 3초 엔딩 카드
    },
    "plan": {},                       # {"2026-10-21": "주제", ...} 날짜별 예정 주제
    "setup_done": False,
    "character_mode": True,           # 캐릭터가 말하는 영상 (False면 장면 영상 + 내레이션)
    "cast_mode": "ensemble",          # ensemble = 여럿이 번갈아, solo = 한 명이 혼자
    "solo_id": "mudang",
    "subtitle_mode": "chunk",         # chunk = 말에 맞춰 2~4단어씩 크게, sentence = 문장 단위
    "characters": DEFAULT_CHARACTERS,
    "avatar_model": "fal-ai/kling-video/ai-avatar/v2/standard",   # pro로 바꾸면 화질↑ 비용 2배
    "image_model": "gemini-nano-banana-2.1",
}
BRAND_DIR = os.path.join(DATA_DIR, "brand")
CHAR_DIR = os.path.join(DATA_DIR, "characters")
os.makedirs(CHAR_DIR, exist_ok=True)


def char_image(cid: str) -> str:
    return os.path.join(CHAR_DIR, f"{cid}.png")
LOGO_PATH = os.path.join(BRAND_DIR, "logo.png")
os.makedirs(BRAND_DIR, exist_ok=True)

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
_file_lock = threading.Lock()


# ───────────────────────── 로그 ─────────────────────────
_logger = print


def set_logger(fn):
    global _logger
    _logger = fn


def log(*args):
    _logger(" ".join(str(a) for a in args))


# ───────────────────────── 설정·키·기록 ─────────────────────────
def _read_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    saved = _read_json(CONFIG_FILE, {})
    for k, v in saved.items():
        if k == "characters" and isinstance(v, dict):
            for cid, cv in v.items():
                if cid in cfg["characters"] and isinstance(cv, dict):
                    cfg["characters"][cid].update(cv)
        elif isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def save_config(cfg: dict) -> None:
    with _file_lock:
        _write_json(CONFIG_FILE, cfg)


def load_secrets() -> dict:
    s = _read_json(SECRETS_FILE, {})
    for k in SECRET_KEYS:          # 환경변수가 있으면 그것도 사용
        if not s.get(k) and os.environ.get(k):
            s[k] = os.environ[k]
    return s


def save_secrets(new: dict) -> None:
    with _file_lock:
        s = _read_json(SECRETS_FILE, {})
        for k in SECRET_KEYS:
            if new.get(k):          # 빈칸은 기존 값 유지
                s[k] = new[k].strip()
        _write_json(SECRETS_FILE, s)
    try:
        os.chmod(SECRETS_FILE, 0o600)
    except OSError:
        pass


def load_history() -> list:
    return _read_json(HISTORY_FILE, [])


def _update_entry(entry: dict) -> None:
    with _file_lock:
        hist = _read_json(HISTORY_FILE, [])
        for i, h in enumerate(hist):
            if h["id"] == entry["id"]:
                hist[i] = entry
                break
        else:
            hist.append(entry)
        _write_json(HISTORY_FILE, hist[-365:])


def get_entry(run_id: str) -> dict | None:
    return next((h for h in load_history() if h["id"] == run_id), None)


# ───────────────────────── 라이선스 (플랫폼 옵션) ─────────────────────────
def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def license_info(path: str = LICENSE_FILE) -> dict:
    """서명된 라이선스를 확인. 없거나 틀리면 인스타그램만."""
    base = {"valid": False, "customer": "", "platforms": ["instagram"], "expires": "", "message": "라이선스 파일이 없어요"}
    if not os.path.exists(path):
        return base
    try:
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        lic = _read_json(path, {})
        payload, sig = lic["payload"], base64.b64decode(lic["sig"])
        Ed25519PublicKey.from_public_bytes(base64.b64decode(LICENSE_PUBLIC_KEY)).verify(sig, _canonical(payload))
    except Exception:
        return {**base, "message": "라이선스가 올바르지 않아요"}
    expires = str(payload.get("expires", ""))
    if expires and expires < dt.date.today().isoformat():
        return {**base, "customer": payload.get("customer", ""), "expires": expires, "message": f"라이선스가 {expires}에 끝났어요"}
    plats = ["instagram"] + [p for p in PLATFORMS[1:] if p in payload.get("platforms", [])]
    return {"valid": True, "customer": payload.get("customer", ""), "platforms": plats, "expires": expires, "message": ""}


def licensed_platforms() -> list:
    return license_info()["platforms"]


def save_license(data: bytes) -> dict:
    tmp = LICENSE_FILE + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    info = license_info(tmp)
    if not info["valid"]:
        os.remove(tmp)
        raise ValueError(info["message"])
    os.replace(tmp, LICENSE_FILE)
    return info


# ───────────────────────── 1. 대본 + 장면 프롬프트 ─────────────────────────
SCRIPT_PROMPT = """너는 무속·운세 숏폼 바이럴 마케터이자 AI 영상 프롬프트 전문가야.
오늘 날짜: {today}

[신당 정보]
이름: {name} / 특징: {features} / 지역: {location}
댓글 키워드: {keyword} / 예약 링크: {link}

[오늘 주제]
{topic_rule}

[최근 사용한 주제 - 겹치지 말 것]
{recent_topics}

[최근 사용한 장면 분위기 - 비슷하게 만들지 말 것]
{recent_scenes}

[작성 규칙]
- 결과를 장담하는 표현("반드시", "100%", "무조건") 금지. 전통 풍습·이야기로 표현.
- 건강·임신·질병 관련 주제면 캡션과 설명란에 "의학적 진단을 대신하지 않습니다" 포함.
- 시청자의 처지를 지목하는 문구("~해서 힘드시죠?") 금지.
- narration: TTS가 읽을 4~6문장, 전체 20~28초 분량. 이모지·기호 없이 말하듯.
- 마지막 narration 문장은 댓글에 '{keyword}'를 남기라는 안내.
- scenes: 영어 영상 프롬프트 정확히 3개. 각 8초짜리 세로 9:16 컷.
  1번: 훅 장면 (신당 소품·자연·상징의 클로즈업)
  2번: 인물 장면 — 한복 입은 사람의 손이나 뒷모습만. 얼굴은 절대 보이지 않게 "face not visible" 명시.
  3번: 마무리 장면 — 화면 아래쪽이 비어 있어 자막이 들어갈 공간이 있게.
  매일 계절({season}), 시간대, 색감, 소품, 카메라 움직임을 다르게 조합.
  공통: cinematic, photorealistic, slow camera movement, calm and trustworthy mood, Korean traditional aesthetic.
  텍스트·글자가 화면에 나오지 않게.

아래 JSON만 출력해. 다른 말 금지.
{{
  "topic": "오늘 주제",
  "narration": ["문장1", "..."],
  "scenes": ["english prompt 1", "english prompt 2", "english prompt 3"],
  "scene_summary": "오늘 장면 분위기를 한국어 한 줄로",
  "instagram": {{"caption": "", "hashtags": ["#... 10개"], "auto_dm": ""}},
  "x": {{"tweet": "140자 내외, 링크 없이", "auto_dm": ""}},
  "youtube": {{"title": "... #Shorts", "description": "", "pinned_comment": ""}},
  "tiktok": {{"caption": "150자 안팎 + 해시태그 5개, 링크 없이"}},
  "variants": {{
    "instagram": {{"hook": "첫 화면 큰 글씨 12자 이내", "cta": "엔딩 안내 18자 이내 (댓글 '{keyword}' → DM)"}},
    "youtube": {{"hook": "검색되는 말로 12자 이내", "cta": "고정 댓글 안내 18자 이내"}},
    "tiktok": {{"hook": "호기심 자극 12자 이내", "cta": "프로필 링크 안내 18자 이내"}},
    "x": {{"hook": "짧고 센 한마디 12자 이내", "cta": "DM 문의 안내 18자 이내"}}
  }}
}}"""


CHAR_SCRIPT_PROMPT = """너는 무속·운세 숏폼 바이럴 마케터이자 대본 작가야.
오늘 날짜: {today} ({season})

[신당 정보]
이름: {name} / 특징: {features} / 지역: {location}
댓글 키워드: {keyword} / 예약 링크: {link}

[오늘 주제]
{topic_rule}

[최근 사용한 주제 - 겹치지 말 것]
{recent_topics}

[진행 캐릭터 - 이 캐릭터들만 등장]
{cast}

[작성 규칙]
{format_rule}
- 각 줄은 한 캐릭터가 말하는 1~2문장, 40자 안팎. 이모지·기호 없이 말하듯.
- 첫 줄은 시청자가 멈춰서 보게 만드는 질문이나 한마디.
- 마지막 줄은 {closer}가 댓글에 '{keyword}'를 남기라고 안내.
- 캐릭터마다 말투를 지켜. 캐릭터가 신당 선생님 본인인 척하지 말 것.
- 결과를 장담하는 표현("반드시", "100%", "무조건") 금지. 전통 풍습·이야기로 표현.
- 건강·임신·질병 관련 주제면 캡션과 설명란에 "의학적 진단을 대신하지 않습니다" 포함.
- 시청자의 처지를 지목하는 문구("~해서 힘드시죠?") 금지.
- 캡션·설명란 끝에 "AI 캐릭터가 등장하는 영상입니다"를 넣어.

아래 JSON만 출력해. 다른 말 금지.
{{
  "topic": "오늘 주제",
  "lines": [{{"speaker": "캐릭터 id", "text": "대사"}}],
  "instagram": {{"caption": "", "hashtags": ["#... 10개"], "auto_dm": ""}},
  "x": {{"tweet": "140자 내외, 링크 없이", "auto_dm": ""}},
  "youtube": {{"title": "... #Shorts", "description": "", "pinned_comment": ""}},
  "tiktok": {{"caption": "150자 안팎 + 해시태그 5개, 링크 없이"}},
  "variants": {{
    "instagram": {{"hook": "첫 화면 큰 글씨 12자 이내", "cta": "엔딩 안내 18자 이내 (댓글 '{keyword}' → DM)"}},
    "youtube": {{"hook": "검색되는 말로 12자 이내", "cta": "고정 댓글 안내 18자 이내"}},
    "tiktok": {{"hook": "호기심 자극 12자 이내", "cta": "프로필 링크 안내 18자 이내"}},
    "x": {{"hook": "짧고 센 한마디 12자 이내", "cta": "DM 문의 안내 18자 이내"}}
  }}
}}"""


def active_characters(cfg) -> list:
    """켜져 있고 기준 이미지가 있는 캐릭터만."""
    if not cfg.get("character_mode"):
        return []
    if cfg.get("cast_mode") == "solo":
        c = cfg.get("solo_id", "mudang")
        return [c] if c in CHARACTER_IDS and os.path.exists(char_image(c)) else []
    return [c for c in CHARACTER_IDS
            if cfg["characters"].get(c, {}).get("on") and os.path.exists(char_image(c))]


def _season(d: dt.date) -> str:
    return {12: "겨울", 1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄",
            6: "여름", 7: "여름", 8: "여름"}.get(d.month, "가을")


def generate_content(cfg, secrets, history, topic_override="") -> dict:
    from anthropic import Anthropic
    log("🤖 [1/5] 오늘의 주제·대본 쓰는 중...")
    recent = [h for h in history if h.get("topic")][-30:]
    topic = topic_override or cfg.get("topic_fixed", "")
    topic_rule = (f"반드시 이 주제로: {topic}" if topic
                  else "운세·꿈해몽·띠별운·풍습·생활 풍수 중 오늘 날짜와 계절에 맞는 새 주제를 직접 정해.")
    cast = active_characters(cfg)
    if cast:
        chars = cfg["characters"]
        closer = "seonnyeo" if "seonnyeo" in cast else cast[-1]
        prompt = CHAR_SCRIPT_PROMPT.format(
            today=dt.date.today().isoformat(), season=_season(dt.date.today()), **cfg["shindang"],
            topic_rule=topic_rule, closer=f"{chars[closer]['name']}({closer})",
            format_rule=("- 한 캐릭터가 시청자에게 직접 말하는 20~28초짜리 세로 숏폼 대본. lines는 5~7개, 모두 같은 speaker.\n"
                         "- 짧게 끊어 말하는 리듬으로. 예: '딱 하루 있어.' '딱 세 가지만 짚어줄게.' '첫째,'"
                         if len(cast) == 1 else
                         "- 캐릭터들이 번갈아 말하는 20~28초짜리 세로 숏폼 대본. lines는 4~6개."),
            recent_topics="\n".join(f"- {h['topic']}" for h in recent) or "- 없음",
            cast="\n".join(f"- id: {c} / 이름: {chars[c]['name']} / 역할·말투: {chars[c]['role']}" for c in cast))
        client = Anthropic(api_key=secrets.get("ANTHROPIC_API_KEY"))
        response = client.messages.create(model=CLAUDE_MODEL, max_tokens=4000,
                                          messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in response.content if b.type == "text")
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"대본 JSON을 찾지 못했어요: {text[:300]}")
        data = json.loads(match.group(0))
        lines = [l for l in data.get("lines", []) if l.get("speaker") in cast and str(l.get("text", "")).strip()]
        if len(lines) < 2:
            raise ValueError("캐릭터 대사가 너무 적어요. 다시 만들어 주세요.")
        data["lines"] = lines
        data["narration"] = [l["text"] for l in lines]
        data["scene_summary"] = "캐릭터 진행: " + ", ".join(chars[c]["name"] for c in dict.fromkeys(l["speaker"] for l in lines))
        log(f"   주제: {data['topic']} ({len(lines)}줄)")
        return data

    prompt = SCRIPT_PROMPT.format(
        today=dt.date.today().isoformat(), season=_season(dt.date.today()), **cfg["shindang"],
        topic_rule=topic_rule,
        recent_topics="\n".join(f"- {h['topic']}" for h in recent) or "- 없음",
        recent_scenes="\n".join(f"- {h.get('scene_summary', '')}" for h in recent[-7:]) or "- 없음",
    )
    client = Anthropic(api_key=secrets.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(model=CLAUDE_MODEL, max_tokens=4000,
                                      messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in response.content if b.type == "text")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"대본 JSON을 찾지 못했어요: {text[:300]}")
    data = json.loads(match.group(0))
    if len(data.get("scenes", [])) != 3:
        raise ValueError("장면 프롬프트가 3개가 아니에요.")
    log(f"   주제: {data['topic']}")
    log(f"   장면: {data.get('scene_summary', '')}")
    return data


# ───────────────────────── 2. 장면 영상 (Veo) ─────────────────────────
def _generate_clip(gclient, types, model, prompt, path):
    operation = gclient.models.generate_videos(
        model=model, prompt=prompt,
        config=types.GenerateVideosConfig(aspect_ratio="9:16", duration_seconds=8,
                                          negative_prompt=VEO_NEGATIVE,
                                          person_generation="allow_all"))
    deadline = time.time() + 15 * 60
    while not operation.done:
        if time.time() > deadline:
            raise TimeoutError("Veo 생성이 15분 안에 끝나지 않았어요.")
        time.sleep(10)
        operation = gclient.operations.get(operation)
    videos = getattr(operation.response, "generated_videos", None) if operation.response else None
    if not videos:
        raise RuntimeError("Veo가 영상을 만들지 않았어요 (안전 필터 가능성)")
    gclient.files.download(file=videos[0].video)
    videos[0].video.save(path)


def concat_clips(clips, out_path):
    inputs, filters = [], []
    for i, c in enumerate(clips):
        inputs += ["-i", c]
        filters.append(f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                       f"crop=1080:1920,fps=30,setsar=1[v{i}]")
    chain = "".join(f"[v{i}]" for i in range(len(clips)))
    fc = ";".join(filters) + f";{chain}concat=n={len(clips)}:v=1:a=0[out]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc,
                    "-map", "[out]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                    out_path], check=True)


def _fallback_background():
    if os.path.isdir(FALLBACK_BG_DIR):
        files = sorted(f for f in os.listdir(FALLBACK_BG_DIR) if f.lower().endswith(".mp4"))
        if files:
            return os.path.join(FALLBACK_BG_DIR, files[dt.date.today().toordinal() % len(files)])
    return None


def generate_scenes(cfg, secrets, scenes, out_dir):
    log("🎥 [2/5] Veo로 장면 영상 3컷 생성 중... (컷당 1~5분)")
    try:
        from google import genai
        from google.genai import types
        gclient = genai.Client(api_key=secrets.get("GEMINI_API_KEY"))
        clips = []
        for i, prompt in enumerate(scenes, 1):
            path = os.path.join(out_dir, f"scene{i}.mp4")
            for attempt in (1, 2):
                try:
                    _generate_clip(gclient, types, cfg["veo_model"], prompt, path)
                    break
                except Exception as e:
                    log(f"   컷{i} 시도{attempt} 실패: {e}")
                    if attempt == 2:
                        raise
            clips.append(path)
            log(f"   컷{i} 완료")
        bg = os.path.join(out_dir, "background.mp4")
        concat_clips(clips, bg)
        return bg, True
    except Exception as e:
        fb = _fallback_background()
        log(f"   ⚠️ Veo 실패 → {'예비 배경 사용' if fb else '단색 배경 사용'} ({e})")
        return fb, False


# ───────────────────────── 3. TTS ─────────────────────────
def pick_voice(cfg, history):
    ids = [v for v in cfg.get("voice_ids", []) if v]
    if not ids:
        raise ValueError("설정에서 ElevenLabs 목소리 ID를 1개 이상 넣어주세요.")
    last = next((h.get("voice_id") for h in reversed(history) if h.get("voice_id")), None)
    idx = dt.date.today().toordinal() % len(ids)
    voice = ids[idx]
    if voice == last and len(ids) > 1:
        voice = ids[(idx + 1) % len(ids)]
    return voice


def generate_tts(secrets, narration, voice_id, out_dir):
    log(f"🗣️ [3/5] 음성 합성 중... (목소리 {voice_id[:6]}…)")
    text = " ".join(narration)
    path = os.path.join(out_dir, "voice.mp3")
    return path, tts_with_timing(secrets, text, voice_id, path), text


def tts_with_timing(secrets, text, voice_id, path):
    """목소리 파일을 만들고, 글자마다 언제 읽히는지(초)를 함께 받아옴. 실패하면 일반 TTS로."""
    try:
        import base64
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": secrets.get("ELEVENLABS_API_KEY", ""), "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_multilingual_v2",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=120)
        r.raise_for_status()
        d = r.json()
        with open(path, "wb") as f:
            f.write(base64.b64decode(d["audio_base64"]))
        al = d.get("alignment") or {}
        if al.get("characters") and len(al["characters"]) == len(al.get("character_start_times_seconds", [])):
            return {"chars": al["characters"], "starts": al["character_start_times_seconds"],
                    "ends": al["character_end_times_seconds"]}
        return None
    except Exception as e:
        log(f"   (글자 타이밍 없이 진행: {str(e)[:80]})")
        tts_to_file(secrets, text, voice_id, path)
        return None


def tts_to_file(secrets, text, voice_id, path):
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": secrets.get("ELEVENLABS_API_KEY", ""), "Content-Type": "application/json"},
        json={"text": text, "model_id": "eleven_multilingual_v2",
              "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
        timeout=120)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return path


# ───────────────────────── 4. 합성 ─────────────────────────
def _audio_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _srt_time(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02}:{int(m):02}:{int(s):02},{int((s % 1) * 1000):03}"


def _ff_path(path):
    """FFmpeg 필터 안에 넣을 경로 (윈도우 C:\\ 대비)."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "")


def _wrap(line, width=14):
    rows, cur = [], ""
    for word in line.split():
        if cur and len(cur) + 1 + len(word) > width:
            rows.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    rows.append(cur)
    return "\n".join(rows)


_PUNCT = ".,!…~·"


def make_chunks(text, alignment=None, duration=None, max_words=3, max_chars=8):
    """한 줄 대사를 2~4단어씩 끊고 (시작, 끝, 글) 목록으로. alignment가 있으면 실제 말 타이밍,
    없으면 글자 수 비례."""
    words, pos = [], 0
    for w in text.split():
        i = text.index(w, pos)
        words.append((w, i, i + len(w) - 1))
        pos = i + len(w)
    groups, cur = [], []
    for w in words:
        # 큰 글씨로 한 줄에 들어가게: 더하면 넘치는 단어는 다음 자막으로
        if cur and sum(len(x[0]) for x in cur) + len(w[0]) > max_chars:
            groups.append(cur)
            cur = []
        cur.append(w)
        if len(cur) >= max_words or w[0][-1] in _PUNCT + "?":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    use_al = alignment and "".join(alignment["chars"]) == text
    total = max(len(text), 1)
    out = []
    for g in groups:
        a, b = g[0][1], g[-1][2]
        if use_al:
            st, en = alignment["starts"][a], alignment["ends"][b]
        else:
            st, en = duration * a / total, duration * (b + 1) / total
        label = " ".join(x[0] for x in g).strip(_PUNCT + " ")
        if label:
            out.append([st, en, label])
    for i in range(len(out) - 1):        # 자막이 깜빡이지 않게 다음 자막 직전까지 유지
        out[i][1] = max(out[i][1], out[i + 1][0])
    return [tuple(x) for x in out]


def _write_events(events, path):
    events = sorted(events)
    events = [(a, (events[i + 1][0] if i + 1 < len(events) and 0 < events[i + 1][0] - b < 0.8 else b), t)
              for i, (a, b, t) in enumerate(events)]   # 대사 사이 0.8초 미만 공백은 앞 자막을 이어서 보여줌
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, b, t) in enumerate(events, 1):
            f.write(f"{i}\n{_srt_time(a)} --> {_srt_time(b)}\n{t}\n\n")


def _style_dict(spec):
    return dict(x.split("=", 1) for x in spec.split(",") if "=" in x)


def _write_srt(narration, duration, path, timings=None):
    """timings가 있으면 그 구간(초)대로, 없으면 글자 수 비례로 자막 시간을 나눔."""
    if not timings:
        total = sum(len(s) for s in narration)
        t, timings = 0.0, []
        for line in narration:
            seg = duration * len(line) / total
            timings.append((t, t + seg))
            t += seg
    with open(path, "w", encoding="utf-8") as f:
        for i, (line, (a, b)) in enumerate(zip(narration, timings), 1):
            f.write(f"{i}\n{_srt_time(a)} --> {_srt_time(b)}\n{_wrap(line)}\n\n")


def _txt(out_dir, name, text):
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def render_video(cfg, bg_path, audio_path, narration, out_dir, base_video=None, timings=None,
                 platform="instagram", hook="", cta="", out_name="final_short.mp4", thumb=True, events=None):
    """bg_path(무음 배경)+audio_path, 또는 base_video(소리 포함 캐릭터 영상)에
    플랫폼별 자막 모양, 첫 화면 문구(hook), 엔딩 화면을 입힘."""
    duration = _audio_duration(base_video or audio_path)
    srt = os.path.join(out_dir, f"subs_{platform}.srt")
    video = os.path.join(out_dir, out_name)
    if events:
        _write_events(events, srt)
    else:
        _write_srt(narration, duration, srt, timings)
    if base_video:
        bg_input, audio_map = ["-i", base_video], "0:a"
    else:
        bg_input = (["-stream_loop", "-1", "-i", bg_path] if bg_path
                    else ["-f", "lavfi", "-i", "color=c=0x0b1430:s=1080x1920:r=30"])
        audio_map = "1:a"
    font = cfg.get("sub_font", "NanumGothic")
    vs = VARIANT_STYLE.get(platform, VARIANT_STYLE["instagram"])
    sd = _style_dict(vs["sub"])
    sd.update(Alignment="2", MarginL="30", MarginR="30", WrapStyle="2")
    if events:   # 짧게 끊는 자막: 크고 굵게, 가슴 높이 (MarginV는 288 기준 → 110 ≈ 화면 아래에서 38%)
        sd.update(FontSize=str(int(sd.get("FontSize", "16")) + 9), Bold="1", WrapStyle="0",
                  MarginV=str(int(sd.get("MarginV", "70")) + 40),
                  Outline=str(max(int(sd.get("Outline", "2")), 3)) if sd.get("BorderStyle") != "3" else sd.get("Outline", "6"))
    style = f"FontName={font}," + ",".join(f"{k}={v}" for k, v in sd.items())
    fc = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          f"subtitles='{_ff_path(srt)}':force_style='{style}'")
    inputs = bg_input if base_video else [*bg_input, "-i", audio_path]

    if hook:
        # 첫 2.5초: 화면 위쪽에 큰 글씨로 플랫폼별 첫 문구
        hk = _txt(out_dir, f"hook_{platform}.txt", hook)
        fc += (f",drawtext=font='{font}':textfile='{_ff_path(hk)}':fontsize=78:fontcolor=white"
               ":box=1:boxcolor=black@0.55:boxborderw=28:x=(w-tw)/2:y=h*0.11:enable='lt(t,2.5)'")

    brand = cfg.get("brand", {})
    if brand.get("end_card", True):
        # 마지막 3초: 화면 가운데에 로고 + 신당 이름 + 플랫폼별 안내 + 신당 예약 문구
        start = max(duration - 3.0, 0)
        on = f"enable='gte(t,{start:.2f})'"
        name_txt = _txt(out_dir, "end_name.txt", cfg["shindang"]["name"])
        cta_txt = _txt(out_dir, f"end_cta_{platform}.txt", cta or brand.get("cta", ""))
        fc += (f",drawbox=x=0:y=ih*0.30:w=iw:h=ih*0.36:color=black@0.62:t=fill:{on}"
               f",drawtext=font='{font}':textfile='{_ff_path(name_txt)}':fontsize=92:fontcolor=white"
               f":x=(w-tw)/2:y=h*0.505:{on}"
               f",drawtext=font='{font}':textfile='{_ff_path(cta_txt)}':fontsize=46:fontcolor=white@0.9"
               f":x=(w-tw)/2:y=h*0.575:{on}")
        if cta and brand.get("cta") and brand["cta"] != cta:
            sub_txt = _txt(out_dir, "end_brand_cta.txt", brand["cta"])
            fc += (f",drawtext=font='{font}':textfile='{_ff_path(sub_txt)}':fontsize=34:fontcolor=white@0.7"
                   f":x=(w-tw)/2:y=h*0.62:{on}")
        if os.path.exists(LOGO_PATH):
            logo_idx = inputs.count("-i")
            inputs += ["-i", LOGO_PATH]
            fc += f"[base];[{logo_idx}:v]scale=240:240:force_original_aspect_ratio=decrease[lg];"
            fc += f"[base][lg]overlay=x=(W-w)/2:y=H*0.335:{on}"
    fc += "[v]"

    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", fc, "-map", "[v]", "-map", audio_map,
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", video],
                   check=True)
    if thumb:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "3", "-i", video,
                        "-frames:v", "1", "-vf", "scale=360:-1", os.path.join(out_dir, "thumb.jpg")])
    return video


def video_file(run_id, platform):
    """플랫폼별 편집본이 있으면 그것, 없으면 기본(인스타그램) 영상."""
    d = os.path.join(OUTPUT_DIR, run_id)
    p = os.path.join(d, f"final_{platform}.mp4")
    return p if platform != "instagram" and os.path.exists(p) else os.path.join(d, "final_short.mp4")


def _variant_text(content, platform, cfg):
    v = (content.get("variants") or {}).get(platform) or {}
    hook = str(v.get("hook", "")).strip()[:20]
    cta = str(v.get("cta", "")).strip()[:30] or VARIANT_STYLE[platform]["cta"].format(**cfg["shindang"])
    return hook, cta


def _trim_indices(n):
    """X용 짧은 버전: 첫 줄, 둘째 줄, 마지막 줄만."""
    return list(range(n)) if n <= 3 else [0, 1, n - 1]


def render_variants(cfg, content, out_dir, platforms, scene=None, character=None):
    """같은 재료로 플랫폼별 편집본을 만듦. scene=(bg, audio) / character=(clips, durations, tail)."""
    log(f"🎬 [4/5] 플랫폼별로 편집하는 중... ({', '.join(platforms)})")
    narration = content["narration"]
    made = []
    for p in platforms:
        hook, cta = _variant_text(content, p, cfg)
        out_name = "final_short.mp4" if p == "instagram" else f"final_{p}.mp4"
        chunked = cfg.get("subtitle_mode", "chunk") == "chunk"
        if character:
            clips, durations, tail, line_events = character
            idx = _trim_indices(len(clips)) if VARIANT_STYLE[p]["trim"] else list(range(len(clips)))
            parts = [clips[i] for i in idx] + ([tail] if tail else [])
            base = os.path.join(out_dir, f"base_{p}.mp4")
            concat_av(parts, base)
            timings, events, t = [], [], 0.0
            for i in idx:
                timings.append((t, t + durations[i]))
                events += [(t + a, min(t + b, t + durations[i]), txt) for a, b, txt in line_events[i]]
                t += durations[i]
            render_video(cfg, None, None, [narration[i] for i in idx], out_dir, base_video=base, timings=timings,
                         platform=p, hook=hook, cta=cta, out_name=out_name, thumb=(p == "instagram"),
                         events=events if chunked else None)
        else:
            bg, audio, events = scene
            render_video(cfg, bg, audio, narration, out_dir, platform=p, hook=hook, cta=cta,
                         out_name=out_name, thumb=(p == "instagram"), events=events if chunked else None)
        made.append(p)
        log(f"   {p} 편집본 완성 ({_audio_duration(os.path.join(out_dir, out_name)):.1f}초)")
    return made


# ───────────────────────── 캐릭터가 말하는 영상 (fal.ai Kling AI Avatar) ─────────────────────────
def _mime(path):
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    return "application/octet-stream"


def _fal_file(path, key):
    """fal에 파일 넘기기: fal-client가 있으면 업로드, 없으면 data URI."""
    try:
        import fal_client
        os.environ.setdefault("FAL_KEY", key)
        return fal_client.upload_file(path)
    except ImportError:
        import base64
        with open(path, "rb") as f:
            return f"data:{_mime(path)};base64," + base64.b64encode(f.read()).decode()


def fal_run(model, arguments, key, timeout=900):
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    r = requests.post(f"https://queue.fal.run/{model}", headers=headers, json=arguments, timeout=120)
    r.raise_for_status()
    job = r.json()
    deadline = time.time() + timeout
    while True:
        st = requests.get(job["status_url"], headers=headers, timeout=30)
        st.raise_for_status()
        status = st.json().get("status")
        if status == "COMPLETED":
            break
        if status not in ("IN_QUEUE", "IN_PROGRESS"):
            raise RuntimeError(f"fal 작업 실패: {st.text[:200]}")
        if time.time() > deadline:
            raise TimeoutError("캐릭터 영상이 제한 시간 안에 끝나지 않았어요.")
        time.sleep(5)
    res = requests.get(job["response_url"], headers=headers, timeout=60)
    res.raise_for_status()
    return res.json()


def _still_clip(image, audio, out_path):
    """립싱크가 실패했을 때: 캐릭터 이미지를 천천히 확대하면서 목소리를 입힌 클립."""
    d = _audio_duration(audio)
    frames = int(d * 30) + 1
    vf = ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          f"zoompan=z='min(zoom+0.0006,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", image, "-i", audio,
                    "-vf", vf, "-t", f"{d:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", out_path], check=True)


def make_talking_clip(cfg, secrets, cid, audio_path, out_path):
    key = secrets.get("FAL_KEY", "")
    if not key:
        raise RuntimeError("fal.ai 키가 없어요")
    char = cfg["characters"][cid]
    result = fal_run(cfg.get("avatar_model", DEFAULT_CONFIG["avatar_model"]), {
        "image_url": _fal_file(char_image(cid), key),
        "audio_url": _fal_file(audio_path, key),
        "prompt": (f"{char.get('motion') or 'natural subtle head movement'}. Speaking directly to the camera "
                   "with natural blinking and expressive face, the background stays still"),
    }, key)
    url = (result.get("video") or {}).get("url")
    if not url:
        raise RuntimeError(f"영상 주소가 없어요: {str(result)[:200]}")
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)


def concat_av(clips, out_path):
    """소리 있는 클립들을 1080x1920 30fps로 맞춰 이어 붙임."""
    inputs, parts = [], []
    for i, c in enumerate(clips):
        inputs += ["-i", c]
        parts.append(f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1[v{i}];"
                     f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo[a{i}]")
    chain = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
    fc = ";".join(parts) + f";{chain}concat=n={len(clips)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc,
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", out_path], check=True)


def character_voices(cfg, history, needed=None):
    """캐릭터별 목소리. 지정 안 한 캐릭터는 목소리 목록에서 서로 겹치지 않게 배정. needed = 오늘 나오는 캐릭터."""
    ids = [v for v in cfg.get("voice_ids", []) if v]
    needed = [c for c in CHARACTER_IDS if c in (needed or CHARACTER_IDS)]
    out, used = {}, set()
    for c in needed:
        v = cfg["characters"][c].get("voice_id", "")
        if v:
            out[c] = v
            used.add(v)
    free = [v for v in ids if v not in used] or ids
    for k, c in enumerate(x for x in needed if x not in out):
        if not free:
            raise ValueError(f"{cfg['characters'][c]['name']}의 목소리를 정해주세요.")
        out[c] = free[k % len(free)]
    return out


def build_character_video(cfg, secrets, history, lines, out_dir):
    voices = character_voices(cfg, history, {l["speaker"] for l in lines})
    log("🗣️ [2/5] 캐릭터 목소리 만드는 중...")
    audios, line_events = [], []
    for i, line in enumerate(lines, 1):
        path = os.path.join(out_dir, f"line{i}.mp3")
        al = tts_with_timing(secrets, line["text"], voices[line["speaker"]], path)
        audios.append(path)
        line_events.append(make_chunks(line["text"], al, _audio_duration(path)))
    log(f"🎭 [3/5] 캐릭터가 말하는 장면 만드는 중... ({len(lines)}장면, 장면마다 1~3분)")
    clips, lipsync_ok = [], 0
    for i, (line, audio) in enumerate(zip(lines, audios), 1):
        cid = line["speaker"]
        clip = os.path.join(out_dir, f"talk{i}.mp4")
        for attempt in (1, 2):
            try:
                make_talking_clip(cfg, secrets, cid, audio, clip)
                lipsync_ok += 1
                break
            except Exception as e:
                log(f"   장면{i} 시도{attempt} 실패: {str(e)[:120]}")
                if attempt == 2:
                    log(f"   장면{i}: 정지 화면으로 대신해요")
                    _still_clip(char_image(cid), audio, clip)
        clips.append(clip)
        log(f"   장면{i} 완료 ({cfg['characters'][cid]['name']})")
    durations = [_audio_duration(c) for c in clips]
    tail = None
    if cfg.get("brand", {}).get("end_card", True):
        # 엔딩 화면이 말하는 얼굴을 가리지 않게, 대사가 끝난 뒤 3초를 따로 붙임
        tail = os.path.join(out_dir, "tail.mp4")
        _tail_clip(char_image(lines[-1]["speaker"]), tail, 3.0)
    return clips, durations, tail, line_events, voices, lipsync_ok


def _tail_clip(image, out_path, seconds):
    """엔딩 카드 밑에 깔 3초: 마지막 캐릭터 이미지를 흐리고 어둡게 + 무음."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", image,
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2,eq=brightness=-0.12,fps=30",
                    "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out_path],
                   check=True)


# ───────────────────────── 캐릭터 기준 이미지 ─────────────────────────
def _gemini_image(prompt, model, key):
    from google import genai
    client = genai.Client(api_key=key)
    try:   # Interactions API (Nano Banana 2.x)
        it = client.interactions.create(model=model, input=prompt,
                                        response_format={"type": "image", "aspect_ratio": "9:16", "image_size": "2K"})
        import base64
        return base64.b64decode(it.output_image.data)
    except AttributeError:
        pass
    from google.genai import types   # 예전 SDK: generateContent
    resp = client.models.generate_content(
        model=model if "flash-image" in model or "image" in model else "gemini-2.5-flash-image",
        contents=[prompt],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"],
                                           image_config=types.ImageConfig(aspect_ratio="9:16")))
    for part in resp.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            return part.inline_data.data
    raise RuntimeError("이미지가 만들어지지 않았어요 (안전 필터에 걸렸을 수 있어요)")


def generate_character_candidates(cid, n=2) -> list:
    cfg, s = load_config(), load_secrets()
    if cid not in CHARACTER_IDS:
        raise ValueError("알 수 없는 캐릭터")
    prompt = cfg["characters"][cid]["prompt"]
    stamp = dt.datetime.now().strftime("%H%M%S")
    names = []
    for k in range(n):
        data = _gemini_image(prompt, cfg.get("image_model", DEFAULT_CONFIG["image_model"]), s.get("GEMINI_API_KEY", ""))
        name = f"{cid}_cand_{stamp}_{k}.png"
        raw = os.path.join(CHAR_DIR, name + ".raw")
        with open(raw, "wb") as f:
            f.write(data)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-frames:v", "1",
                        os.path.join(CHAR_DIR, name)], check=True)
        os.remove(raw)
        names.append(name)
    return names


def list_candidates(cid) -> list:
    return sorted((f for f in os.listdir(CHAR_DIR) if f.startswith(f"{cid}_cand_") and f.endswith(".png")), reverse=True)


def choose_candidate(cid, name):
    if cid not in CHARACTER_IDS or not re.fullmatch(rf"{cid}_cand_\d+_\d+\.png", name):
        raise ValueError("고를 수 없는 이미지예요")
    src = os.path.join(CHAR_DIR, name)
    if not os.path.exists(src):
        raise ValueError("이미지가 없어요")
    os.replace(src, char_image(cid))
    for f in list_candidates(cid):
        os.remove(os.path.join(CHAR_DIR, f))


def save_character_upload(cid, data: bytes):
    if cid not in CHARACTER_IDS:
        raise ValueError("알 수 없는 캐릭터")
    raw = os.path.join(CHAR_DIR, f"{cid}.upload")
    with open(raw, "wb") as f:
        f.write(data)
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0", raw], capture_output=True, text=True, check=True).stdout.strip()
        w, h = (int(x) for x in out.split(",")[:2])
        if w / h > 0.62:
            # 가로로 넓은 이미지는 세로 9:16 화면에 맞춰 넣고, 남는 곳은 같은 그림을 흐리게 채움
            fc = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3,eq=brightness=-0.08[bg];"
                  "[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)*0.3")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-filter_complex", fc,
                            "-frames:v", "1", char_image(cid)], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-frames:v", "1", char_image(cid)], check=True)
    finally:
        os.remove(raw)


# ───────────────────────── 5. 업로드 ─────────────────────────
def upload_instagram(cfg, secrets, video_path, ig):
    gv = cfg.get("graph_version", "v23.0")
    base = f"https://graph.facebook.com/{gv}"
    uid, token = secrets.get("IG_USER_ID"), secrets.get("IG_ACCESS_TOKEN")
    caption = ig["caption"] + "\n\n" + " ".join(ig.get("hashtags", []))
    r = requests.post(f"{base}/{uid}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "access_token": token}, timeout=60)
    r.raise_for_status()
    cid = r.json()["id"]
    with open(video_path, "rb") as f:
        requests.post(f"https://rupload.facebook.com/ig-api-upload/{gv}/{cid}",
                      headers={"Authorization": f"OAuth {token}", "offset": "0",
                               "file_size": str(os.path.getsize(video_path))},
                      data=f, timeout=600).raise_for_status()
    for _ in range(60):
        st = requests.get(f"{base}/{cid}", params={"fields": "status_code", "access_token": token},
                          timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"인스타 영상 처리 실패: {st}")
        time.sleep(5)
    else:
        raise TimeoutError("인스타 영상 처리가 끝나지 않았어요.")
    r = requests.post(f"{base}/{uid}/media_publish",
                      data={"creation_id": cid, "access_token": token}, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def _youtube_client():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/youtube.upload",
              "https://www.googleapis.com/auth/youtube.force-ssl"]
    if not os.path.exists(YT_TOKEN):
        raise RuntimeError("유튜브 로그인이 안 돼 있어요. 이 컴퓨터에서 python youtube_login.py 를 한 번 실행하세요.")
    creds = Credentials.from_authorized_user_file(YT_TOKEN, scopes)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(YT_TOKEN, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_youtube(cfg, secrets, video_path, yt):
    from googleapiclient.http import MediaFileUpload
    youtube = _youtube_client()
    req = youtube.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": yt["title"][:100], "description": yt["description"], "categoryId": "22"},
              "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                         "containsSyntheticMedia": True}},
        media_body=MediaFileUpload(video_path, mimetype="video/mp4", resumable=True))
    response = None
    while response is None:
        _, response = req.next_chunk()
    vid = response["id"]
    youtube.commentThreads().insert(part="snippet", body={"snippet": {
        "videoId": vid, "topLevelComment": {"snippet": {"textOriginal": yt["pinned_comment"]}}}}).execute()
    return vid


def upload_x(cfg, secrets, video_path, x):
    base = "https://api.x.com/2"
    auth = {"Authorization": f"Bearer {secrets.get('X_USER_ACCESS_TOKEN', '')}"}
    r = requests.post(f"{base}/media/upload/initialize", headers=auth, json={
        "media_type": "video/mp4", "total_bytes": os.path.getsize(video_path),
        "media_category": "tweet_video"}, timeout=60)
    r.raise_for_status()
    mid = r.json()["data"]["id"]
    with open(video_path, "rb") as f:
        idx = 0
        while chunk := f.read(4 * 1024 * 1024):
            requests.post(f"{base}/media/upload/{mid}/append", headers=auth,
                          data={"segment_index": idx}, files={"media": ("chunk.mp4", chunk)},
                          timeout=120).raise_for_status()
            idx += 1
    r = requests.post(f"{base}/media/upload/{mid}/finalize", headers=auth, timeout=60)
    r.raise_for_status()
    info = r.json()["data"].get("processing_info")
    while info and info["state"] in ("pending", "in_progress"):
        time.sleep(info.get("check_after_secs", 2))
        info = requests.get(f"{base}/media/upload", headers=auth,
                            params={"command": "STATUS", "media_id": mid},
                            timeout=30).json()["data"].get("processing_info")
    if info and info["state"] == "failed":
        raise RuntimeError(f"X 영상 처리 실패: {info}")
    r = requests.post(f"{base}/tweets", headers=auth,
                      json={"text": x["tweet"], "media": {"media_ids": [mid]}}, timeout=60)
    r.raise_for_status()
    return r.json()["data"]["id"]


TT_API = "https://open.tiktokapis.com/v2"


def tiktok_access_token(secrets) -> str:
    """data/tiktok_token.json의 토큰을 쓰고, 만료되면 refresh_token으로 갱신."""
    if not os.path.exists(TT_TOKEN):
        raise RuntimeError("틱톡 로그인이 안 돼 있어요. 이 컴퓨터에서 python tiktok_login.py 를 한 번 실행하세요.")
    tok = _read_json(TT_TOKEN, {})
    if tok.get("access_token") and tok.get("expires_at", 0) > time.time() + 300:
        return tok["access_token"]
    r = requests.post(f"{TT_API}/oauth/token/", timeout=30, data={
        "client_key": secrets.get("TIKTOK_CLIENT_KEY", ""), "client_secret": secrets.get("TIKTOK_CLIENT_SECRET", ""),
        "grant_type": "refresh_token", "refresh_token": tok.get("refresh_token", "")})
    r.raise_for_status()
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"틱톡 토큰 갱신 실패: {str(d)[:200]}")
    tok.update(access_token=d["access_token"], refresh_token=d.get("refresh_token", tok.get("refresh_token")),
               expires_at=time.time() + int(d.get("expires_in", 86400)))
    _write_json(TT_TOKEN, tok)
    return tok["access_token"]


def _tt(path, token, body):
    r = requests.post(f"{TT_API}{path}", json=body, timeout=60,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    d = r.json() if r.content else {}
    err = (d.get("error") or {})
    if r.status_code >= 400 or err.get("code") not in (None, "ok"):
        raise RuntimeError(f"틱톡 오류 {r.status_code}: {err.get('message') or r.text[:200]}")
    return d.get("data", {})


def upload_tiktok(cfg, secrets, video_path, tt):
    """Content Posting API (Direct Post). 앱 심사 전에는 '나만 보기'로만 올라가요."""
    token = tiktok_access_token(secrets)
    info = _tt("/post/publish/creator_info/query/", token, {})
    options = info.get("privacy_level_options", [])
    privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else ("SELF_ONLY" if "SELF_ONLY" in options else (options or ["SELF_ONLY"])[0])
    max_sec = info.get("max_video_post_duration_sec")
    if max_sec and _audio_duration(video_path) > max_sec:
        raise RuntimeError(f"영상이 이 계정의 최대 길이({max_sec}초)보다 길어요")
    size = os.path.getsize(video_path)
    chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
    count = max(1, size // chunk)
    caption = tt.get("caption") or ""
    init = _tt("/post/publish/video/init/", token, {
        "post_info": {"title": caption[:2000], "privacy_level": privacy, "disable_comment": False,
                      "disable_duet": False, "disable_stitch": False, "video_cover_timestamp_ms": 3000,
                      "brand_content_toggle": False, "brand_organic_toggle": True, "is_aigc": True},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}})
    with open(video_path, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start)
            data = f.read(end - start + 1)
            r = requests.put(init["upload_url"], data=data, timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{size}"})
            if r.status_code >= 400:
                raise RuntimeError(f"틱톡 업로드 실패 {r.status_code}: {r.text[:200]}")
    for _ in range(60):
        st = _tt("/post/publish/status/fetch/", token, {"publish_id": init["publish_id"]})
        status = st.get("status")
        if status == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or []
            return str(ids[0]) if ids else init["publish_id"] + ("" if privacy == "PUBLIC_TO_EVERYONE" else " (나만 보기)")
        if status == "FAILED":
            raise RuntimeError(f"틱톡 게시 실패: {st.get('fail_reason', '')}")
        time.sleep(5)
    return init["publish_id"] + " (처리 중)"


UPLOADERS = {"instagram": ("인스타그램", upload_instagram),
             "youtube": ("유튜브", upload_youtube),
             "tiktok": ("틱톡", upload_tiktok),
             "x": ("X", upload_x)}


def _platform_content(content, key):
    if key == "tiktok" and not content.get("tiktok"):
        ig = content.get("instagram", {})
        return {"caption": (ig.get("caption", "") + "\n\n" + " ".join(ig.get("hashtags", [])[:5])).strip()}
    return content.get(key, {})


def upload_entry(run_id: str) -> dict:
    cfg, secrets = load_config(), load_secrets()
    entry = get_entry(run_id)
    if not entry:
        raise ValueError("해당 기록이 없어요.")
    entry["status"] = "uploading"
    _update_entry(entry)
    log("🚀 [5/5] 업로드 시작")
    results = entry.get("results", {})
    allowed = licensed_platforms()
    active = [k for k in UPLOADERS if cfg["platforms"].get(k) and k in allowed]
    for key, (name, fn) in UPLOADERS.items():
        if key not in active or results.get(key, {}).get("ok"):
            continue      # 꺼둔·라이선스 없는 플랫폼, 이미 성공한 플랫폼은 건너뜀
        try:
            pid = fn(cfg, secrets, video_file(entry["id"], key), _platform_content(entry["content"], key))
            results[key] = {"ok": True, "id": pid}
            log(f"   - {name} 완료 ✅")
        except Exception as e:
            results[key] = {"ok": False, "error": str(e)[:300]}
            log(f"   - {name} 실패 ❌ {e}")
    entry["results"] = results
    tried = [results[k] for k in active if k in results]
    entry["status"] = "done" if tried and all(r["ok"] for r in tried) else "partial"
    entry["uploaded_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _update_entry(entry)
    return entry


# ───────────────────────── 전체 실행 ─────────────────────────
def run(upload_mode: str | None = None, topic_override: str = "", trigger: str = "manual") -> dict:
    cfg, secrets = load_config(), load_secrets()
    mode = upload_mode or cfg["upload_mode"]
    history = load_history()
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(OUTPUT_DIR, run_id)
    os.makedirs(out_dir, exist_ok=True)
    entry = {"id": run_id, "date": dt.date.today().isoformat(), "trigger": trigger,
             "status": "generating", "mode": mode, "results": {}}
    _update_entry(entry)
    log("=" * 36)
    log(f"🚀 '{cfg['shindang']['name']}' 자동화 시작 ({run_id})")
    planned = cfg.get("plan", {}).get(entry["date"], "")
    try:
        content = generate_content(cfg, secrets, history, topic_override or planned)
        entry.update(topic=content["topic"], scene_summary=content.get("scene_summary", ""),
                     content=content)
        _update_entry(entry)
        targets = ["instagram"] + [p for p in PLATFORMS[1:] if cfg["platforms"].get(p) and p in licensed_platforms()]
        if content.get("lines"):
            clips, durations, tail, line_events, voices, ok = build_character_video(cfg, secrets, history, content["lines"], out_dir)
            variants = render_variants(cfg, content, out_dir, targets, character=(clips, durations, tail, line_events))
            entry.update(mode_kind="character", voices=voices,
                         lipsync=f"{ok}/{len(content['lines'])}", veo_used=None)
        else:
            bg, veo_ok = generate_scenes(cfg, secrets, content["scenes"], out_dir)
            voice = pick_voice(cfg, history)
            audio, al, spoken = generate_tts(secrets, content["narration"], voice, out_dir)
            events = make_chunks(spoken, al, _audio_duration(audio))
            variants = render_variants(cfg, content, out_dir, targets, scene=(bg, audio, events))
            entry.update(mode_kind="scene", voice_id=voice, veo_used=veo_ok)
        entry["variants"] = variants
        entry.update(status="review" if mode == "review" else "ready")
        _update_entry(entry)
    except Exception as e:
        entry.update(status="failed", error=str(e)[:500])
        _update_entry(entry)
        log(f"❌ 실패: {e}")
        raise
    if mode == "review":
        log("✅ 영상 완성. '기록' 탭에서 확인 후 업로드 버튼을 눌러주세요.")
        return entry
    return upload_entry(run_id)


# ───────────────────────── 연결 테스트 (설정 마법사) ─────────────────────────
def test_connection(service: str) -> dict:
    """각 서비스에 가볍게 요청을 보내 키가 맞는지 확인. 영상·글은 만들지 않음."""
    s = load_secrets()
    cfg = load_config()
    try:
        if service == "ffmpeg":
            out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
            return {"ok": True, "detail": out.stdout.split("\n")[0][:60]}
        if service == "claude":
            r = requests.get("https://api.anthropic.com/v1/models", timeout=20, headers={
                "x-api-key": s.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            return {"ok": True, "detail": "Claude 연결됨"}
        if service == "gemini":
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=20,
                             headers={"x-goog-api-key": s.get("GEMINI_API_KEY", "")})
            r.raise_for_status()
            return {"ok": True, "detail": "Gemini·Veo 연결됨"}
        if service == "elevenlabs":
            r = requests.get("https://api.elevenlabs.io/v1/user", timeout=20,
                             headers={"xi-api-key": s.get("ELEVENLABS_API_KEY", "")})
            r.raise_for_status()
            return {"ok": True, "detail": "ElevenLabs 연결됨"}
        if service == "fal":
            # 없는 작업 번호로 상태를 물어봐서 키만 확인 (비용 없음)
            r = requests.get("https://queue.fal.run/fal-ai/kling-video/requests/00000000-0000-0000-0000-000000000000/status",
                             headers={"Authorization": f"Key {s.get('FAL_KEY', '')}"}, timeout=20)
            if r.status_code in (401, 403):
                return {"ok": False, "detail": "키가 맞지 않아요"}
            return {"ok": True, "detail": "fal.ai 연결됨"}
        if service == "instagram":
            r = requests.get(f"https://graph.facebook.com/{cfg.get('graph_version', 'v23.0')}/{s.get('IG_USER_ID', '')}",
                             params={"fields": "username", "access_token": s.get("IG_ACCESS_TOKEN", "")}, timeout=20)
            r.raise_for_status()
            return {"ok": True, "detail": "@" + r.json().get("username", "")}
        if service == "x":
            r = requests.get("https://api.x.com/2/users/me", timeout=20,
                             headers={"Authorization": f"Bearer {s.get('X_USER_ACCESS_TOKEN', '')}"})
            r.raise_for_status()
            return {"ok": True, "detail": "@" + r.json()["data"].get("username", "")}
        if service == "tiktok":
            info = _tt("/post/publish/creator_info/query/", tiktok_access_token(s), {})
            opts = info.get("privacy_level_options", [])
            note = "" if "PUBLIC_TO_EVERYONE" in opts else " · 앱 심사 전이라 '나만 보기'로만 올라가요"
            return {"ok": True, "detail": "@" + info.get("creator_username", "") + note}
        if service == "youtube":
            ch = _youtube_client().channels().list(part="snippet", mine=True).execute()
            items = ch.get("items", [])
            return {"ok": bool(items), "detail": items[0]["snippet"]["title"] if items else "채널을 찾지 못했어요"}
        return {"ok": False, "detail": "알 수 없는 서비스"}
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else ""
        hint = "키나 토큰이 맞지 않아요" if code in (400, 401, 403) else f"요청 실패 ({code})"
        return {"ok": False, "detail": hint}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:160]}


def list_voices() -> list:
    """ElevenLabs 내 목소리 목록 (이름, ID, 미리듣기)."""
    r = requests.get("https://api.elevenlabs.io/v1/voices", timeout=20,
                     headers={"xi-api-key": load_secrets().get("ELEVENLABS_API_KEY", "")})
    r.raise_for_status()
    out = []
    for v in r.json().get("voices", []):
        labels = v.get("labels") or {}
        desc = " · ".join(x for x in (labels.get("gender"), labels.get("age"), labels.get("accent")) if x)
        out.append({"id": v["voice_id"], "name": v.get("name", ""), "desc": desc,
                    "preview": v.get("preview_url", "")})
    return out


# ───────────────────────── 콘텐츠 달력 ─────────────────────────
PLAN_PROMPT = """너는 무속·운세 숏폼 기획자야. 아래 날짜마다 숏폼 주제를 하나씩 정해.
신당: {name} ({features}), 지역: {location}
날짜: {dates}
최근 사용한 주제(겹치지 말 것): {recent}
이미 정해둔 주제(겹치지 말 것): {fixed}
규칙: 계절·절기·요일 분위기를 반영. 결과를 장담하는 표현 금지. 주제는 25자 이내.
JSON만 출력: {{"YYYY-MM-DD": "주제", ...}}"""


def suggest_plan(days: int = 7) -> dict:
    """비어 있는 날짜에 AI가 주제를 채워 넣음 (이미 정한 날짜는 그대로)."""
    from anthropic import Anthropic
    cfg = load_config()
    plan = dict(cfg.get("plan", {}))
    today = dt.date.today()
    dates = [(today + dt.timedelta(days=i)).isoformat() for i in range(days)]
    empty = [d for d in dates if not plan.get(d)]
    if not empty:
        return plan
    recent = [h["topic"] for h in load_history() if h.get("topic")][-30:]
    prompt = PLAN_PROMPT.format(**cfg["shindang"], dates=", ".join(empty),
                                recent=" / ".join(recent) or "없음",
                                fixed=" / ".join(plan[d] for d in dates if plan.get(d)) or "없음")
    client = Anthropic(api_key=load_secrets().get("ANTHROPIC_API_KEY"))
    resp = client.messages.create(model=CLAUDE_MODEL, max_tokens=1500,
                                  messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    match = re.search(r"\{.*\}", text, re.S)
    suggested = json.loads(match.group(0)) if match else {}
    for d in empty:
        if suggested.get(d):
            plan[d] = str(suggested[d]).strip()[:60]
    cfg["plan"] = {d: t for d, t in plan.items() if d >= today.isoformat()}
    save_config(cfg)
    return cfg["plan"]


# ───────────────────────── 성과 ─────────────────────────
def _metrics_youtube(ids):
    yt = _youtube_client()
    res = yt.videos().list(part="statistics", id=",".join(ids)).execute()
    out = {}
    for it in res.get("items", []):
        st = it.get("statistics", {})
        out[it["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                         "comments": int(st.get("commentCount", 0))}
    return out


def _metrics_instagram(cfg, s, media_id):
    base = f"https://graph.facebook.com/{cfg.get('graph_version', 'v23.0')}"
    tok = s.get("IG_ACCESS_TOKEN", "")
    m = requests.get(f"{base}/{media_id}", params={"fields": "like_count,comments_count", "access_token": tok},
                     timeout=20)
    m.raise_for_status()
    d = m.json()
    views = 0
    try:  # 릴스 조회수는 insights에서 (권한이 없으면 0)
        ins = requests.get(f"{base}/{media_id}/insights", params={"metric": "views", "access_token": tok},
                           timeout=20).json()
        views = int(ins["data"][0]["values"][0]["value"])
    except Exception:
        pass
    return {"views": views, "likes": int(d.get("like_count", 0)), "comments": int(d.get("comments_count", 0))}


def _metrics_x(s, ids):
    r = requests.get("https://api.x.com/2/tweets", timeout=20,
                     params={"ids": ",".join(ids), "tweet.fields": "public_metrics"},
                     headers={"Authorization": f"Bearer {s.get('X_USER_ACCESS_TOKEN', '')}"})
    r.raise_for_status()
    out = {}
    for t in r.json().get("data", []):
        pm = t.get("public_metrics", {})
        out[t["id"]] = {"views": int(pm.get("impression_count", 0)), "likes": int(pm.get("like_count", 0)),
                        "comments": int(pm.get("reply_count", 0))}
    return out


def refresh_metrics(days: int = 30) -> int:
    """최근 업로드 영상의 조회수·좋아요·댓글을 플랫폼에서 가져와 기록에 저장."""
    cfg, s = load_config(), load_secrets()
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    entries = [h for h in load_history() if h.get("date", "") >= since and h.get("results")]
    ids = {p: {} for p in UPLOADERS}
    for h in entries:
        for p, r in h["results"].items():
            if r.get("ok") and r.get("id"):
                ids[p][str(r["id"])] = h["id"]
    found = {}
    for p, fn in (("youtube", lambda i: _metrics_youtube(i)), ("x", lambda i: _metrics_x(s, i))):
        if ids[p]:
            try:
                for pid, m in fn(list(ids[p])).items():
                    found.setdefault(ids[p][pid], {})[p] = m
            except Exception as e:
                log(f"   {p} 성과 가져오기 실패: {e}")
    for pid, run_id in ids["instagram"].items():
        try:
            found.setdefault(run_id, {})["instagram"] = _metrics_instagram(cfg, s, pid)
        except Exception as e:
            log(f"   인스타 성과 가져오기 실패: {e}")
    now = dt.datetime.now().isoformat(timespec="minutes")
    for run_id, m in found.items():
        e = get_entry(run_id)
        if e:
            e["metrics"] = {**e.get("metrics", {}), **m}
            e["metrics_at"] = now
            _update_entry(e)
    cfg = load_config()
    cfg["metrics_at"] = now
    save_config(cfg)
    return len(found)
