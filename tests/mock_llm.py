#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""假后端，一次顶三：假 OpenAI 兼容接口 + 假 HTTP 中转。

    python3 tests/mock_llm.py            # 默认 127.0.0.1:8765

路由：
  POST /v1/chat/completions   -> 返回固定日报（假模型）
  POST /wechat                -> 收下原始记录（假中转）
  GET  /wechat?date=          -> 取回原始记录
  POST /wechat/report         -> 收下日报成品
  GET  /wechat/report?date=   -> 取回日报成品
  GET  /requests              -> 全部收到的请求（测试用）
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

REQUESTS = []
STORE = {"data": {}, "report": {}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静音
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _date(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        return (q.get("date") or [""])[0]

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/requests"):
            self._send(REQUESTS)
        elif path.rstrip("/").endswith("/report"):
            date = self._date()
            if date in STORE["report"]:
                self._send({"date": date, "report": STORE["report"][date]})
            else:
                self._send({"error": "no report for %s" % date}, code=404)
        elif path.rstrip("/").endswith("/wechat"):
            date = self._date()
            if date in STORE["data"]:
                self._send({"date": date, "payload": STORE["data"][date]})
            else:
                self._send({"error": "no data for %s" % date}, code=404)
        else:
            self._send({"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"_raw": raw}
        REQUESTS.append({"path": self.path, "payload": payload})
        path = self.path.split("?")[0]

        if path.rstrip("/").endswith("/chat/completions"):
            user = ""
            for m in payload.get("messages", []):
                if m.get("role") == "user":
                    user = m.get("content", "")
            self._send({
                "choices": [{"message": {
                    "content": "# 微信沟通日报（mock）\n\n- 收到 %d 字聊天内容\n\n正文占位。" % len(user),
                    "reasoning_content": "mock thinking",
                }}],
                "usage": {"total_tokens": 10},
            })
        elif path.rstrip("/").endswith("/report"):
            date = payload.get("date", "")
            STORE["report"][date] = payload.get("report", "")
            self._send({"ok": True, "date": date})
        elif path.rstrip("/").endswith("/wechat"):
            date = payload.get("date", "")
            STORE["data"][date] = payload.get("payload")
            self._send({"ok": True, "date": date})
        else:
            self._send({"ok": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "mock_requests.log"))
    args = ap.parse_args()

    srv = HTTPServer((args.host, args.port), Handler)
    sys.stderr.write("[mock] listening on http://%s:%d\n" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with open(args.log, "w", encoding="utf-8") as f:
            json.dump(REQUESTS, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
