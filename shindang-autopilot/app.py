"""
신당 오토파일럿 웹앱
실행:  python app.py   →  브라우저에서 http://서버주소:8000
"""

import datetime as dt
import hmac
import os
import re
import secrets as pysecrets
import threading
import time
from collections import deque
from functools import wraps

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_from_directory, session)

import pipeline as pl

app = Flask(__name__)

# ───────────────────────── 로그인 비밀번호 ─────────────────────────
PW_FILE = os.path.join(pl.DATA_DIR, "app_password.txt")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
if not APP_PASSWORD:
    if not os.path.exists(PW_FILE):
        with open(PW_FILE, "w") as f:
            f.write(pysecrets.token_urlsafe(9))
    APP_PASSWORD = open(PW_FILE).read().strip()

KEY_FILE = os.path.join(pl.DATA_DIR, "flask_secret.txt")
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, "w") as f:
        f.write(pysecrets.token_hex(32))
app.secret_key = open(KEY_FILE).read().strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30))


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("ok"):
            if request.path.startswith("/api/"):
                return jsonify(error="로그인이 필요해요"), 401
            return redirect("/login")
        return fn(*a, **kw)
    return wrapper


# ───────────────────────── 작업 관리 (한 번에 하나) ─────────────────────────
LOGS = deque(maxlen=400)
JOB = {"running": False, "kind": None, "run_id": None, "started": None}
JOB_LOCK = threading.Lock()


def _log(msg):
    LOGS.append(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}")
    print(msg, flush=True)


pl.set_logger(_log)


def start_job(kind, target, *args):
    with JOB_LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, kind=kind, started=time.time())

    def work():
        try:
            target(*args)
        except Exception as e:
            _log(f"❌ 작업 중단: {e}")
        finally:
            JOB.update(running=False, kind=None)

    threading.Thread(target=work, daemon=True).start()
    return True


# ───────────────────────── 매일 자동 실행 ─────────────────────────
def next_run_time(cfg):
    if not cfg.get("schedule_enabled"):
        return None
    hh, mm = map(int, cfg["schedule_time"].split(":"))
    now = dt.datetime.now()
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    ran_today = any(h["date"] == now.date().isoformat() and h.get("trigger") == "schedule"
                    for h in pl.load_history())
    if t <= now or ran_today:
        t += dt.timedelta(days=1)
    return t


def scheduler_tick(now=None) -> bool:
    cfg = pl.load_config()
    if not cfg.get("schedule_enabled"):
        return False
    now = now or dt.datetime.now()
    hh, mm = map(int, cfg["schedule_time"].split(":"))
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    # 예약 시각부터 30분 안에서만 실행 (지난 시각으로 설정했다고 바로 돌지 않게)
    due = t <= now < t + dt.timedelta(minutes=30)
    ran_today = any(h["date"] == now.date().isoformat() and h.get("trigger") == "schedule"
                    for h in pl.load_history())
    if due and not ran_today:
        _log("⏰ 예약 시간이 되어 자동 실행합니다.")
        return start_job("generate", pl.run, None, "", "schedule")
    return False


def scheduler_loop():
    while True:
        try:
            scheduler_tick()
        except Exception as e:
            _log(f"스케줄러 오류: {e}")
        time.sleep(30)


# ───────────────────────── 화면 ─────────────────────────
@app.get("/login")
def login_page():
    return render_template("login.html", error=None)


@app.post("/login")
def login_submit():
    if hmac.compare_digest(request.form.get("password", ""), APP_PASSWORD):
        session.permanent = True
        session["ok"] = True
        return redirect("/")
    time.sleep(1)
    return render_template("login.html", error="비밀번호가 맞지 않아요")


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/")
@login_required
def index():
    return render_template("index.html")


@app.get("/media/<run_id>/<name>")
@login_required
def media(run_id, name):
    if name not in ("final_short.mp4", "thumb.jpg", "final_youtube.mp4", "final_tiktok.mp4", "final_x.mp4"):
        return "", 404
    return send_from_directory(os.path.join(pl.OUTPUT_DIR, os.path.basename(run_id)), name)


# ───────────────────────── API ─────────────────────────
def _mask(v):
    return (v[:4] + "•" * 6 + v[-3:]) if v and len(v) > 10 else ("•" * 6 if v else "")


@app.get("/api/state")
@login_required
def api_state():
    cfg = pl.load_config()
    sec = pl.load_secrets()
    hist = list(reversed(pl.load_history()))[:60]
    nxt = next_run_time(cfg)
    return jsonify(
        config=cfg,
        secrets={k: _mask(sec.get(k, "")) for k in pl.SECRET_KEYS},
        youtube_connected=os.path.exists(pl.YT_TOKEN),
        history=hist,
        job={k: JOB[k] for k in ("running", "kind")},
        logs=list(LOGS)[-120:],
        next_run=nxt.strftime("%m월 %d일 %H:%M") if nxt else None,
        has_logo=os.path.exists(pl.LOGO_PATH),
        char_images={c: (int(os.path.getmtime(pl.char_image(c))) if os.path.exists(pl.char_image(c)) else 0)
                     for c in pl.CHARACTER_IDS},
        char_candidates={c: pl.list_candidates(c) for c in pl.CHARACTER_IDS},
        char_busy=sorted(CHAR_BUSY),
        cast=pl.active_characters(cfg),
        license=pl.license_info(),
        tiktok_connected=os.path.exists(pl.TT_TOKEN),
        metrics_running=METRICS["running"],
        today=dt.date.today().isoformat(),
    )


@app.post("/api/config")
@login_required
def api_config():
    body = request.get_json(force=True)
    cfg = pl.load_config()
    for key in ("shindang", "platforms", "brand"):
        if isinstance(body.get(key), dict):
            cfg[key].update(body[key])
    allowed = pl.licensed_platforms()
    for plat in pl.PLATFORMS:   # 라이선스에 없는 플랫폼은 켤 수 없음
        if plat not in allowed:
            cfg["platforms"][plat] = False
    cfg["platforms"]["instagram"] = True
    if isinstance(body.get("plan"), dict):
        cfg["plan"] = {d: str(t).strip()[:60] for d, t in body["plan"].items() if str(t).strip()}
    if "setup_done" in body:
        cfg["setup_done"] = bool(body["setup_done"])
    seal = cfg["brand"].get("seal", "")
    cfg["brand"]["seal"] = seal.strip()[:2]
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", cfg["brand"].get("accent", "")):
        cfg["brand"]["accent"] = "#B3302A"
    for key in ("topic_fixed", "veo_model", "schedule_time", "upload_mode", "sub_font", "avatar_model", "image_model"):
        if key in body:
            cfg[key] = str(body[key]).strip()
    if body.get("video_engine") in ("comfyui", "cloud"):
        cfg["video_engine"] = body["video_engine"]
    if isinstance(body.get("comfyui"), dict):
        cc = cfg.setdefault("comfyui", {})
        for k, v in body["comfyui"].items():
            if k in pl.comfy.DEFAULT_COMFY:
                cc[k] = str(v).strip()[:300]
        url = cc.get("url", "")
        if url and not re.fullmatch(r"https?://[^\s/]+(:\d+)?/?", url):
            return jsonify(error="ComfyUI 주소는 http://127.0.0.1:8188 형식으로 넣어주세요"), 400
    if "character_mode" in body:
        cfg["character_mode"] = bool(body["character_mode"])
    if body.get("cast_mode") in ("ensemble", "solo"):
        cfg["cast_mode"] = body["cast_mode"]
    if body.get("solo_id") in pl.CHARACTER_IDS:
        cfg["solo_id"] = body["solo_id"]
    if body.get("subtitle_mode") in ("chunk", "sentence"):
        cfg["subtitle_mode"] = body["subtitle_mode"]
    if isinstance(body.get("characters"), dict):
        for cid, cv in body["characters"].items():
            if cid in pl.CHARACTER_IDS and isinstance(cv, dict):
                for f in ("name", "role", "prompt", "voice_id", "motion"):
                    if f in cv:
                        cfg["characters"][cid][f] = str(cv[f]).strip()[:2000]
                if "on" in cv:
                    cfg["characters"][cid]["on"] = bool(cv["on"])
    if "schedule_enabled" in body:
        cfg["schedule_enabled"] = bool(body["schedule_enabled"])
    if "voice_ids" in body:
        raw = body["voice_ids"]
        items = raw if isinstance(raw, list) else str(raw).replace("\n", ",").split(",")
        cfg["voice_ids"] = [v.strip() for v in items if v.strip()]
    try:
        hh, mm = map(int, cfg["schedule_time"].split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception:
        return jsonify(error="실행 시각은 08:37 형식으로 넣어주세요"), 400
    if cfg["upload_mode"] not in ("review", "auto"):
        cfg["upload_mode"] = "review"
    pl.save_config(cfg)
    return jsonify(ok=True)


@app.post("/api/secrets")
@login_required
def api_secrets():
    pl.save_secrets(request.get_json(force=True))
    return jsonify(ok=True)


@app.post("/api/run")
@login_required
def api_run():
    body = request.get_json(force=True) or {}
    mode = body.get("mode") if body.get("mode") in ("review", "auto") else None
    if not start_job("generate", pl.run, mode, str(body.get("topic", "")).strip(), "manual"):
        return jsonify(error="이미 작업이 진행 중이에요"), 409
    return jsonify(ok=True)


@app.post("/api/runs/<run_id>/upload")
@login_required
def api_upload(run_id):
    if not pl.get_entry(run_id):
        return jsonify(error="기록이 없어요"), 404
    if not start_job("upload", pl.upload_entry, run_id):
        return jsonify(error="이미 작업이 진행 중이에요"), 409
    return jsonify(ok=True)


@app.post("/api/runs/<run_id>/skip")
@login_required
def api_skip(run_id):
    entry = pl.get_entry(run_id)
    if not entry:
        return jsonify(error="기록이 없어요"), 404
    entry["status"] = "skipped"
    pl._update_entry(entry)
    return jsonify(ok=True)


def recover_interrupted():
    """서버가 작업 도중 꺼졌던 기록을 정리."""
    for h in pl.load_history():
        if h.get("status") in ("generating", "uploading"):
            h["status"] = "failed" if h["status"] == "generating" else "partial"
            h["error"] = "작업 도중 서버가 꺼졌어요. 다시 실행해 주세요."
            pl._update_entry(h)


# ───────────────────────── 설정 마법사·달력·성과·브랜딩 ─────────────────────────
METRICS = {"running": False}


@app.post("/api/test/<service>")
@login_required
def api_test(service):
    return jsonify(pl.test_connection(service))


@app.get("/api/voices")
@login_required
def api_voices():
    try:
        return jsonify(voices=pl.list_voices())
    except Exception as e:
        return jsonify(error=f"목소리 목록을 가져오지 못했어요. ElevenLabs 키를 확인하세요. ({str(e)[:80]})"), 400


@app.post("/api/plan/suggest")
@login_required
def api_plan_suggest():
    try:
        return jsonify(plan=pl.suggest_plan(int((request.get_json(silent=True) or {}).get("days", 7))))
    except Exception as e:
        return jsonify(error=f"주제를 추천하지 못했어요. Claude 키를 확인하세요. ({str(e)[:80]})"), 400


@app.post("/api/metrics/refresh")
@login_required
def api_metrics_refresh():
    if METRICS["running"]:
        return jsonify(ok=True)
    METRICS["running"] = True

    def work():
        try:
            n = pl.refresh_metrics()
            _log(f"📈 성과 업데이트: 영상 {n}편")
        except Exception as e:
            _log(f"성과 업데이트 실패: {e}")
        finally:
            METRICS["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/brand/logo")
@login_required
def api_logo_upload():
    f = request.files.get("logo")
    if not f:
        return jsonify(error="이미지를 골라주세요"), 400
    data = f.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        return jsonify(error="5MB 이하 이미지만 올릴 수 있어요"), 400
    if not (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"):
        return jsonify(error="PNG나 JPG 이미지만 올릴 수 있어요"), 400
    with open(pl.LOGO_PATH, "wb") as out:
        out.write(data)
    return jsonify(ok=True)


@app.post("/api/brand/logo/delete")
@login_required
def api_logo_delete():
    if os.path.exists(pl.LOGO_PATH):
        os.remove(pl.LOGO_PATH)
    return jsonify(ok=True)


@app.get("/brand/logo")
@login_required
def brand_logo():
    if not os.path.exists(pl.LOGO_PATH):
        return "", 404
    return send_from_directory(pl.BRAND_DIR, "logo.png", max_age=0)


@app.post("/api/license")
@login_required
def api_license():
    f = request.files.get("license")
    if not f:
        return jsonify(error="라이선스 파일을 골라주세요"), 400
    try:
        info = pl.save_license(f.read(64 * 1024))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, license=info)


CHAR_BUSY = set()


@app.post("/api/characters/<cid>/generate")
@login_required
def api_char_generate(cid):
    if cid not in pl.CHARACTER_IDS:
        return jsonify(error="알 수 없는 캐릭터"), 404
    if cid in CHAR_BUSY:
        return jsonify(ok=True)
    CHAR_BUSY.add(cid)

    def work():
        try:
            name = pl.load_config()["characters"][cid]["name"]
            _log(f"🎨 {name} 후보 이미지 만드는 중...")
            pl.generate_character_candidates(cid, 2)
            _log(f"🎨 {name} 후보 2장 완성. 마음에 드는 걸 골라주세요.")
        except Exception as e:
            _log(f"🎨 캐릭터 이미지 만들기 실패: {str(e)[:160]}")
        finally:
            CHAR_BUSY.discard(cid)

    threading.Thread(target=work, daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/characters/<cid>/choose")
@login_required
def api_char_choose(cid):
    try:
        pl.choose_candidate(cid, (request.get_json(force=True) or {}).get("name", ""))
        return jsonify(ok=True)
    except ValueError as e:
        return jsonify(error=str(e)), 400


@app.post("/api/characters/<cid>/upload")
@login_required
def api_char_upload(cid):
    f = request.files.get("image")
    if not f:
        return jsonify(error="이미지를 골라주세요"), 400
    data = f.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        return jsonify(error="10MB 이하 이미지만 올릴 수 있어요"), 400
    if not (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
        return jsonify(error="PNG, JPG, WEBP 이미지만 올릴 수 있어요"), 400
    try:
        pl.save_character_upload(cid, data)
    except Exception:
        return jsonify(error="이미지를 읽지 못했어요"), 400
    return jsonify(ok=True)


@app.get("/characters/<name>")
@login_required
def char_file(name):
    if not re.fullmatch(r"(dongja|seonbi|seonnyeo|mudang)(_cand_\d+_\d+)?\.png", name):
        return "", 404
    return send_from_directory(pl.CHAR_DIR, name, max_age=0)


def metrics_loop():
    """하루 두 번(오전 7시대, 오후 7시대) 성과 자동 업데이트."""
    done = set()
    while True:
        now = dt.datetime.now()
        slot = (now.date().isoformat(), now.hour)
        if now.hour in (7, 19) and slot not in done and not METRICS["running"]:
            done.add(slot)
            try:
                METRICS["running"] = True
                pl.refresh_metrics()
            except Exception as e:
                _log(f"성과 자동 업데이트 실패: {e}")
            finally:
                METRICS["running"] = False
        time.sleep(300)


if __name__ == "__main__":
    recover_interrupted()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=metrics_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8000))
    print("=" * 44)
    print(f" 신당 오토파일럿 실행 중 → http://localhost:{port}")
    print(f" 로그인 비밀번호: {APP_PASSWORD}")
    print("=" * 44, flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True)
