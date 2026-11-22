#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""假的 chatlog HTTP 服务，用来验证采集层。

    python3 tests/mock_chatlog.py --port 8795

实现 /api/v1/contact、/api/v1/chatroom、/api/v1/chatlog 三个接口。
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

MESSAGES = {
    "wxid_zhang": [
        {"talker": "wxid_zhang", "sender": "wxid_zhang", "content": "方案我看了",
         "timestamp": 1794963600, "type": 1},
        {"talker": "wxid_zhang", "sender": "wxid_me", "content": "好，下午给你",
         "timestamp": 1794963900, "type": 1},
    ],
    "@chatroom_group": [
        {"talker": "@chatroom_group", "sender": "wxid_li", "content": "数据更新完了",
         "timestamp": 1794967200, "type": 1},
    ],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        path = parsed.path
        if path.endswith("/contact"):
            self._send([{"wxid": "wxid_zhang", "nickname": "张三", "remark": "张三"},
                        {"wxid": "wxid_me", "nickname": "我"}])
        elif path.endswith("/chatroom"):
            self._send([{"chatroom": "@chatroom_group", "name": "项目组群"}])
        elif path.endswith("/chatlog"):
            talker = (q.get("talker") or [""])[0]
            self._send(MESSAGES.get(talker, []))
        else:
            self._send({"error": "not found"}, code=404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = HTTPServer((args.host, args.port), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
