"""
틱톡 연결 (최초 1회)

준비
1. developers.tiktok.com 에서 앱을 만들고 Login Kit + Content Posting API(Direct Post)를 추가
2. 스코프: user.info.basic, video.publish
3. Redirect URI를 하나 등록 (예: https://내도메인/tiktok/callback — 실제로 열리지 않아도 돼요)
4. 웹앱 '설정 → 연결'에 Client key, Client secret 저장

실행
    python tiktok_login.py https://내도메인/tiktok/callback
→ 화면에 나온 주소를 브라우저에서 열어 신당 틱톡 계정으로 로그인
→ 로그인 후 이동한 주소창의 전체 주소를 복사해서 붙여넣기
→ data/tiktok_token.json 이 생기면 끝

주의: 틱톡 앱 심사를 통과하기 전에는 올린 영상이 '나만 보기'로만 올라가요.
"""
import secrets
import sys
import time
import urllib.parse

import requests

import pipeline as pl

if len(sys.argv) < 2:
    raise SystemExit("사용법: python tiktok_login.py <틱톡 앱에 등록한 Redirect URI>")
redirect = sys.argv[1]
keys = pl.load_secrets()
ck, cs = keys.get("TIKTOK_CLIENT_KEY"), keys.get("TIKTOK_CLIENT_SECRET")
if not ck or not cs:
    raise SystemExit("웹앱 '설정 → 연결'에 틱톡 Client key와 Client secret을 먼저 저장하세요.")

state = secrets.token_urlsafe(12)
url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
    "client_key": ck, "scope": "user.info.basic,video.publish", "response_type": "code",
    "redirect_uri": redirect, "state": state})
print("\n1) 아래 주소를 브라우저에서 열고 틱톡 계정으로 로그인하세요:\n")
print(url)
back = input("\n2) 로그인 후 이동한 페이지의 전체 주소를 붙여넣으세요:\n> ").strip()
q = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
if q.get("state", [""])[0] != state:
    raise SystemExit("주소가 맞지 않아요 (state 불일치). 처음부터 다시 해주세요.")
code = q.get("code", [""])[0]
if not code:
    raise SystemExit("주소에 code가 없어요. 로그인을 끝까지 했는지 확인하세요.")

r = requests.post(f"{pl.TT_API}/oauth/token/", timeout=30, data={
    "client_key": ck, "client_secret": cs, "code": code,
    "grant_type": "authorization_code", "redirect_uri": redirect})
d = r.json()
if "access_token" not in d:
    raise SystemExit(f"토큰을 받지 못했어요: {d}")
pl._write_json(pl.TT_TOKEN, {"access_token": d["access_token"], "refresh_token": d.get("refresh_token", ""),
                             "expires_at": time.time() + int(d.get("expires_in", 86400)), "open_id": d.get("open_id", "")})
print("\n✅ 틱톡 연결 완료! 웹앱 '설정 → 연결'에서 '연결 확인'을 눌러보세요.")
