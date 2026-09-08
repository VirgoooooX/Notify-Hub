# -*- coding: utf-8 -*-
import json
import time
import urllib.request
import urllib.error

PUBLISHER_API_URL = "http://192.168.31.100:8790"
ACCESS_TOKEN = "pub_7f9b8c1d4e2a6f0e8b3c5a1d9e2f4a6b"

title = "Codex用量重置(9/8 12:20)"
print(f"Title: {repr(title)}, length: {len(title)}")

body_text = (
    "OpenAI 团队成员已确认，所有用户的用量额度已全部重置完成（对应北京时间 9月8日 12:20）。大家可以正常使用本周额度。\n\n"
    "原推作者：@thsottiaux\n"
    "原推内容：All reset for everyone. Enjoy the week with Astra.\n\n"
    "#OpenAI #Codex #ChatGPT #AI编程"
)

job_payload = {
    "client_request_id": f"codex-xhs-private-{int(time.time())}",
    "platform": "xiaohongshu",
    "mode": "publish",
    "content": {
        "title": title,
        "body_text": body_text,
        "visibility": "private"
    },
    "media": [
        {
            "kind": "uploaded",
            "media_id": "med_aaab228c2c3b4d64"
        }
    ],
    "topics": ["codex", "openai", "ChatGPT", "AI编程"]
}

req = urllib.request.Request(
    f"{PUBLISHER_API_URL}/v1/jobs",
    data=json.dumps(job_payload, ensure_ascii=False).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json; charset=utf-8"
    },
    method="POST"
)

try:
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        print("Job submitted successfully!")
        print("Job ID:", res.get("id"))
        print("Status:", res.get("status"))
        print("Created at:", res.get("created_at"))
except urllib.error.HTTPError as e:
    print("HTTPError:", e.code)
    print(e.read().decode("utf-8"))
