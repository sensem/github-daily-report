#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地小服务：让日报里的 ☆ / × 按钮真正一键生效，不需要再复制命令。

为什么需要它：日报是本地静态 HTML 文件，浏览器不允许网页直接写磁盘。
起一个只监听 127.0.0.1 的小服务，页面点按钮时 POST 过来，服务写
blacklist.json / watchlist.json，然后自动重渲染日报。

用法:
    python github-daily\\scripts\\local_server.py            # 默认端口 8765
    python github-daily\\scripts\\local_server.py --port 8790
    python github-daily\\scripts\\local_server.py --no-rebuild

接口:
    GET  /ping        -> {"ok": true}
    POST /add         -> {"type": "block"|"watch", "repo": "owner/name"}
                         写入对应名单并触发一次 --no-fetch 重渲染

安全边界：只绑定 127.0.0.1，只接受这两个动作，仓库名做格式校验。
用途仅限本机自动化，不要改成监听 0.0.0.0。
"""

import argparse
import json
import re
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # github-daily/
DATA = ROOT / "data"
RUN_DAILY = ROOT / "scripts" / "run_daily.py"

FILES = {"block": "blacklist.json", "watch": "watchlist.json"}
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_rebuild_lock = threading.Lock()
_rebuild_busy = threading.Event()


def _load(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def add_repo(kind, repo):
    """写入名单；已存在则原样返回 False（幂等）。"""
    path = DATA / FILES[kind]
    doc = _load(path, {"repos": {}})
    repos = doc.setdefault("repos", {})
    if repo in repos:
        return False
    entry = {"since": datetime.now().strftime("%Y-%m-%d")}
    if kind == "watch":
        entry["note"] = ""
    repos[repo] = entry
    _save(path, doc)
    return True


def rebuild_once():
    """触发一次重渲染；并发点击时只跑一个，避免互相踩文件。"""
    if _rebuild_busy.is_set():
        return "busy"
    if not _rebuild_lock.acquire(blocking=False):
        return "busy"
    try:
        _rebuild_busy.set()
        proc = subprocess.run(
            [sys.executable, str(RUN_DAILY), "--no-fetch"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        )
        return "ok" if proc.returncode == 0 else "failed"
    except Exception as exc:                      # noqa: BLE001
        print("[rebuild] 失败：%s" % exc, flush=True)
        return "failed"
    finally:
        _rebuild_busy.clear()
        _rebuild_lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "GitHubDailyLocal/1.0"

    def _cors(self):
        # 报告以 file:// 打开，Origin 是 null，这里放开到本机即可
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):            # 精简日志
        print("[%s] %s" % (self.address_string(), fmt % args), flush=True)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/ping":
            return self._json(200, {"ok": True, "actions": list(FILES)})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/add":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json"})
        kind = (payload.get("type") or "").strip()
        repo = (payload.get("repo") or "").strip()
        if kind not in FILES:
            return self._json(400, {"ok": False, "error": "type 只能是 block / watch"})
        if not REPO_RE.match(repo):
            return self._json(400, {"ok": False, "error": "repo 要写成 owner/name"})
        added = add_repo(kind, repo)
        result = rebuild_once()
        action = "加入" if kind == "watch" else "屏蔽"
        print("[%s] %s %s -> 写入=%s 重渲染=%s"
              % (datetime.now().strftime("%H:%M:%S"), action, repo, added, result), flush=True)
        return self._json(200, {"ok": True, "added": added, "rebuild": result,
                                "file": FILES[kind]})


def main():
    ap = argparse.ArgumentParser(description="日报本地按钮服务")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print("端口 %d 起不来（可能已在运行）：%s" % (args.port, exc), file=sys.stderr)
        return 1
    print("日报本地服务已启动：http://%s:%d  （Ctrl+C 停止）" % (args.host, args.port), flush=True)
    print("报告里的 ☆ / × 会直接写入名单并重渲染；服务不在时按钮会退回「复制命令」模式。",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
