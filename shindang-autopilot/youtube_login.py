"""
유튜브 연결 (최초 1회)
1. Google Cloud Console에서 'YouTube Data API v3' 사용 설정
2. OAuth 클라이언트 ID(데스크톱 앱)를 만들고 JSON을 data/client_secret.json 으로 저장
3. 웹앱이 돌아가는 컴퓨터에서:  python youtube_login.py
   → 브라우저에서 유튜브 채널 계정으로 로그인하면 data/youtube_token.json 이 생겨요.
"""
import os

from google_auth_oauthlib.flow import InstalledAppFlow

import pipeline as pl

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
secret = os.path.join(pl.DATA_DIR, "client_secret.json")
if not os.path.exists(secret):
    raise SystemExit("data/client_secret.json 이 없어요. 위 설명 2번을 먼저 해주세요.")

creds = InstalledAppFlow.from_client_secrets_file(secret, SCOPES).run_local_server(port=0)
with open(pl.YT_TOKEN, "w") as f:
    f.write(creds.to_json())
print("✅ 유튜브 연결 완료! 웹앱 '연결' 탭에서 확인하세요.")
