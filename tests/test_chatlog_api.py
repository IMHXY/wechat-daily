#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集层自测：python3 tests/test_chatlog_api.py

对着假 chatlog 服务跑一遍 fetch_chatlog_api.py，验证会话列举、消息解析、归一化。
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

import fetch_chatlog_api  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def wait_ready(port, tries=50):
    last = None
    for _ in range(tries):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/v1/contact?format=json" % port,
                                   timeout=1).read()
            return True
        except Exception as exc:
            last = exc
            time.sleep(0.1)
    print("     (最后一次错误: %s: %s)" % (type(last).__name__, last))
    return False


def test_shapes():
    print("\n[各种返回结构]")
    check("_as_list 处理数组", fetch_chatlog_api._as_list([1, 2]) == [1, 2])
    check("_as_list 处理 data 包裹",
          fetch_chatlog_api._as_list({"data": [1]}) == [1])
    check("_as_list 处理嵌套 messages",
          fetch_chatlog_api._as_list({"data": {"messages": [1, 2]}}) == [1, 2])
    check("_as_list 处理空", fetch_chatlog_api._as_list(None) == [])

    tz = fetch_chatlog_api.normalize.get_timezone("Asia/Shanghai")
    raw = [{"sender": "wxid_a", "content": "hi", "timestamp": 1794963600, "type": 1}]
    msgs = fetch_chatlog_api.parse_messages(raw, "wxid_a", "张三", tz)
    check("解析出 1 条", len(msgs) == 1, str(len(msgs)))
    check("会话名用显示名", msgs[0]["chat_name"] == "张三", msgs[0]["chat_name"])
    check("内容正确", msgs[0]["content"] == "hi")

    wrapped = [{"msg": {"sender": "wxid_b", "content": "yo", "createTime": 1794963600}}]
    msgs2 = fetch_chatlog_api.parse_messages(wrapped, "wxid_b", "李四", tz)
    check("处理 msg 包裹结构", len(msgs2) == 1 and msgs2[0]["content"] == "yo",
          str(msgs2))

    no_time = [{"sender": "x", "content": "y"}]
    check("缺时间戳的记录被丢弃",
          len(fetch_chatlog_api.parse_messages(no_time, "x", "X", tz)) == 0)


def test_against_mock():
    print("\n[对着假 chatlog 服务采集]")
    port = 8795
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_chatlog.py"),
                             "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready(port):
            check("mock 服务启动", False)
            return
        check("mock 服务启动", True)

        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, "2026-11-18.json")
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "collector", "fetch_chatlog_api.py"),
                 "--date", "2026-11-18", "--base-url", "http://127.0.0.1:%d" % port,
                 "--out", out, "--scope", "all"],
                capture_output=True, text=True, timeout=120)
            check("采集退出码 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
            check("生成输出文件", os.path.isfile(out))
            if os.path.isfile(out):
                d = json.load(open(out, encoding="utf-8"))
                check("统计到 3 条消息", d["stats"]["message_count"] == 3,
                      str(d["stats"]["message_count"]))
                names = sorted(c["chat_name"] for c in d["chats"])
                check("会话名用昵称", names == ["张三", "项目组群"], str(names))
                check("群聊识别为 group",
                      any(c["chat_type"] == "group" for c in d["chats"]),
                      str([(c["chat_name"], c["chat_type"]) for c in d["chats"]]))
        finally:
            import shutil
            shutil.rmtree(tmp)
    finally:
        proc.terminate()
        proc.wait(timeout=10)



def test_feishu_error_mapping():
    """飞书错误码要转成人话，不能只丢一个 HTTPError。"""
    print("\n[飞书错误码映射]")
    import bridge
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = b'{"code":91403,"msg":"Forbidden"}'
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        try:
            bridge._request("POST", "http://127.0.0.1:%d/x" % port, {}, b"{}")
            check("失败要抛 RuntimeError", False, "没抛")
        except RuntimeError as exc:
            msg = str(exc)
            check("带出飞书错误码", "91403" in msg, msg[:160])
            check("带出可操作建议", "\u53ef\u7f16\u8f91" in msg or "\u53ef\u7ba1\u7406" in msg,
                  msg[:240])
            check("换行是真换行", "\\n" not in msg, msg[:240])
        except Exception as exc:
            check("失败要抛 RuntimeError", False, "%s: %s" % (type(exc).__name__, exc))
    finally:
        srv.shutdown()

    check("错误码表覆盖 91403", 91403 in bridge.FEISHU_ERROR_HINTS)
    check("错误码表覆盖 99991672", 99991672 in bridge.FEISHU_ERROR_HINTS)


if __name__ == "__main__":
    test_shapes()
    test_against_mock()
    test_feishu_error_mapping()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
