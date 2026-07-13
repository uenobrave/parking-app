#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
駐車場 共有マップ サーバー
- Python標準ライブラリのみで動作します(追加インストール不要)
- データは parking_data.json に保存されます

使い方:
    python3 server.py
"""

import json
import os
import socket
import threading
import uuid
import hashlib
import hmac
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", 8000))
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parking_data.json")
LOCK = threading.Lock()

# ===== ここでパスワードを設定 =====
PASSWORD = "8008"
# ==================================

# セッション管理（メモリ内）
SESSIONS = set()
SESSION_LOCK = threading.Lock()

def make_token():
    return secrets.token_hex(32)

def check_token(token):
    with SESSION_LOCK:
        return token in SESSIONS

def add_token(token):
    with SESSION_LOCK:
        SESSIONS.add(token)

# レイアウト定義
LAYOUT = [
    {"type": "col",  "col": "A", "rows": 9},
    {"type": "col",  "col": "B", "rows": 9},
    {"type": "col",  "col": "C", "rows": 9},
    {"type": "road", "label": "通路"},
    {"type": "col",  "col": "E", "rows": 12},
    {"type": "col",  "col": "F", "rows": 12},
    {"type": "road", "label": "通路"},
    {"type": "col",  "col": "H", "rows": 15},
]

def _gen_slots():
    slots = []
    for item in LAYOUT:
        if item["type"] == "col":
            for row in range(1, item["rows"] + 1):
                slots.append(item["col"] + str(row))
    return slots

DEFAULT_DATA = {
    "slots":    _gen_slots(),
    "layout":   LAYOUT,
    "vehicles": [],
    "assignments": {},
}


def load_data():
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return {k: v for k, v in DEFAULT_DATA.items()}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            data = {k: v for k, v in DEFAULT_DATA.items()}
    for key in DEFAULT_DATA:
        if key not in data:
            data[key] = DEFAULT_DATA[key]
    return data


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


INDEX_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


class Handler(BaseHTTPRequestHandler):

    def _get_token(self):
        """CookieからセッショントークンをGet"""
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("session="):
                return part[len("session="):]
        return None

    def _is_auth(self):
        token = self._get_token()
        return token and check_token(token)

    def _send_json(self, obj, status=200, set_cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def _unauthorized(self):
        self._send_json({"error": "unauthorized"}, 401)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_html(INDEX_HTML_PATH)
        elif self.path == "/api/state":
            if not self._is_auth():
                self._unauthorized()
                return
            with LOCK:
                data = load_data()
            self._send_json(data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json({"error": "invalid json"}, 400)
            return

        # ログイン（認証不要）
        if self.path == "/api/login":
            entered = payload.get("password", "")
            if hmac.compare_digest(entered, PASSWORD):
                token = make_token()
                add_token(token)
                self._send_json({"ok": True}, set_cookie=f"session={token}; Path=/; HttpOnly; SameSite=Strict")
            else:
                self._send_json({"error": "パスワードが違います"}, 401)
            return

        # 以下は認証必須
        if not self._is_auth():
            self._unauthorized()
            return

        if self.path == "/api/assign":
            slot = payload.get("slot")
            vehicle_id = payload.get("vehicle_id")
            with LOCK:
                data = load_data()
                if slot not in data["slots"]:
                    self._send_json({"error": "invalid slot"}, 400)
                    return
                if vehicle_id:
                    for s in list(data["assignments"].keys()):
                        if data["assignments"][s] == vehicle_id:
                            del data["assignments"][s]
                    data["assignments"][slot] = vehicle_id
                else:
                    data["assignments"].pop(slot, None)
                save_data(data)
            self._send_json(data)

        elif self.path == "/api/reset":
            with LOCK:
                data = load_data()
                data["assignments"] = {}
                save_data(data)
            self._send_json(data)

        elif self.path == "/api/vehicle/add":
            name  = payload.get("name", "").strip()
            plate = payload.get("plate", "").strip()
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return
            with LOCK:
                data = load_data()
                data["vehicles"].append({
                    "id":    "v" + uuid.uuid4().hex[:8],
                    "name":  name,
                    "plate": plate,
                })
                save_data(data)
            self._send_json(data)

        elif self.path == "/api/vehicle/delete":
            vehicle_id = payload.get("vehicle_id")
            with LOCK:
                data = load_data()
                data["vehicles"] = [v for v in data["vehicles"] if v["id"] != vehicle_id]
                for s in list(data["assignments"].keys()):
                    if data["assignments"][s] == vehicle_id:
                        del data["assignments"][s]
                save_data(data)
            self._send_json(data)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print("[%s] %s" % (self.address_string(), format % args))


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    ip = get_local_ip()
    print("=" * 50)
    print("駐車場 共有マップ サーバーを起動しました")
    print("アクセスURL: http://%s:%d" % (ip, PORT))
    print("パスワード: %s" % PASSWORD)
    print("終了: Ctrl+C")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを終了します")
        server.shutdown()
