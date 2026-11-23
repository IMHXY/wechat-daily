#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机摘要链路的端到端自测：python3 tests/test_summarize.py

起一个假的 OpenAI 兼容接口，用合成数据跑一遍 summarize_local.py，
确认：请求确实发出去了、聊天内容进了 prompt、日报文件落盘了。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

import summarize_local  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def wait_ready(port, tries=40):
    for _ in range(tries):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/requests" % port, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.1)
    return False


def test_render():
    print("\n[渲染]")
    daily = {
        "stats": {"message_count": 2, "chat_count": 1, "peak_hour": 9,
                  "active_hours": ["09:12", "09:20"]},
        "chats": [{
            "chat_name": "张三", "chat_type": "single", "message_count": 2,
            "messages": [
                {"time": "2026-11-18 09:12:00", "sender": "张三", "is_self": False,
                 "type": "text", "content": "在吗"},
                {"time": "2026-11-18 09:20:00", "sender": "我", "is_self": True,
                 "type": "text", "content": "在的"},
            ],
        }],
    }
    block = summarize_local.render_chat(daily["chats"][0])
    check("包含会话名", "张三" in block)
    check("自己标为「我」", "我: 在的" in block, block)
    check("统计串正确", "消息 2 条" in summarize_local.render_stats(daily))


def test_split():
    print("\n[分块]")
    lines = ["line %d %s" % (i, "x" * 100) for i in range(100)]
    chunks = summarize_local.split_text("\n".join(lines), limit=1000)
    check("切成多块", len(chunks) > 1, str(len(chunks)))
    check("每块不超限", all(len(c) <= 1200 for c in chunks))
    check("不丢行", sum(len(c.splitlines()) for c in chunks) == 100)


def test_end_to_end():
    print("\n[端到端：假接口 + 合成数据]")
    port = 8791
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py"),
                             "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready(port):
            check("mock 服务启动", False)
            return
        check("mock 服务启动", True)

        tmp = tempfile.mkdtemp()
        try:
            # 造一份 daily json
            daily = {
                "date": "2026-11-18",
                "stats": {"message_count": 2, "chat_count": 1, "peak_hour": 9,
                          "active_hours": ["09:12", "09:20"]},
                "chats": [{
                    "chat_name": "张三", "chat_type": "single", "message_count": 2,
                    "messages": [
                        {"time": "2026-11-18 09:12:00", "sender": "张三", "is_self": False,
                         "type": "text", "content": "在吗"},
                        {"time": "2026-11-18 09:20:00", "sender": "我", "is_self": True,
                         "type": "text", "content": "在的"},
                    ],
                }],
            }
            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)
            with open(os.path.join(data_dir, "2026-11-18.json"), "w", encoding="utf-8") as f:
                json.dump(daily, f, ensure_ascii=False)

            out = os.path.join(tmp, "reports", "2026-11-18.md")
            env = dict(os.environ)
            env.update({
                "LLM_BASE_URL": "http://127.0.0.1:%d/v1" % port,
                "LLM_API_KEY": "mock-key",
                "LLM_MODEL": "MiniMax-M3.1-Flash",
            })
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "collector", "summarize_local.py"),
                 "--date", "2026-11-18", "--data", os.path.join(data_dir, "2026-11-18.json"),
                 "--out", out],
                capture_output=True, text=True, env=env, timeout=120)
            check("脚本退出码 0", r.returncode == 0, r.stderr[-300:])
            check("日报文件生成", os.path.isfile(out))
            if os.path.isfile(out):
                text = open(out, encoding="utf-8").read()
                check("日报含 mock 正文", "正文占位" in text, text[:120])
                check("标题日期被脚本定死",
                      text.splitlines()[0] == "# 微信沟通日报 · 2026-11-18",
                      text.splitlines()[0])

            reqs = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:%d/requests" % port, timeout=5).read().decode())
            check("发出 1 次请求", len(reqs) == 1, str(len(reqs)))
            if reqs:
                body = reqs[0].get("payload", reqs[0])
                user_msgs = [m for m in body.get("messages", []) if m.get("role") == "user"]
                check("请求带 user 消息", bool(user_msgs), str(body)[:200])
                user = user_msgs[0]["content"] if user_msgs else ""
                check("prompt 含聊天内容", "在吗" in user, user[:120])
                check("prompt 含统计", "消息 2 条" in user)
                check("prompt 含规范", "关键进展" in body["messages"][0]["content"],
                      body["messages"][0]["content"][:120])
                check("模型名正确", body.get("model") == "MiniMax-M3.1-Flash", str(body.get("model")))
                check("带 reasoning_effort", body.get("reasoning_effort") == "medium",
                      str(body.get("reasoning_effort")))
        finally:
            shutil.rmtree(tmp)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_no_key():
    print("\n[缺少 key 时报错]")
    env = dict(os.environ)
    env.pop("LLM_API_KEY", None)
    env.pop("MINIMAX_API_KEY", None)
    env["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"
    tmp = tempfile.mkdtemp()
    try:
        daily = {"stats": {"message_count": 1}, "chats": [{
            "chat_name": "A", "chat_type": "single", "message_count": 1,
            "messages": [{"time": "2026-11-18 09:00:00", "sender": "A", "is_self": False,
                          "type": "text", "content": "hi"}]}]}
        p = os.path.join(tmp, "d.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(daily, f)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "collector", "summarize_local.py"),
                            "--date", "2026-11-18", "--data", p,
                            "--out", os.path.join(tmp, "o.md")],
                           capture_output=True, text=True, env=env, timeout=60)
        check("非零退出", r.returncode != 0)
        check("提示缺 key", "API key" in r.stderr, r.stderr[-200:])
    finally:
        shutil.rmtree(tmp)



def test_probe_llm_fallback():
    """探测：第一个模型 400 时要能退到下一个可用模型。"""
    print("\n[模型探测回退]")
    import summarize_local
    calls = []

    def fake_http(base, key, body, timeout):
        calls.append((body.get("model"), list(body.keys())))
        if body.get("model") == "MiniMax-M3.1-Flash":
            raise urllib.error.HTTPError(
                "u", 400, "bad", {},
                __import__("io").BytesIO(b'{"error":{"message":"model not available"}}'))
        return {"choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}]}

    old = summarize_local._http_chat
    summarize_local._http_chat = fake_http
    try:
        b, m, tok_key, eff, errors = summarize_local.probe_llm(
            base="http://x", key="key123", models=["MiniMax-M3.1-Flash", "MiniMax-M3"],
            bases=["http://x"])
        check("退到了可用的模型", m == "MiniMax-M3", str(m))
        check("域名也一起返回了", b == "http://x", str(b))
        check("记下了失败原因", len(errors) >= 1, str(errors))
    finally:
        summarize_local._http_chat = old


def test_probe_llm_switches_domain():
    """key 在 A 域名 401，要自动换到 B 域名。"""
    print("\n[401 后自动换域名]")
    import summarize_local

    def fake_http(base, key, body, timeout):
        if "minimax.cn" in base:
            raise urllib.error.HTTPError(
                "u", 401, "bad", {}, __import__("io").BytesIO(b'{"error":"1004"}'))
        return {"choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}]}

    old = summarize_local._http_chat
    summarize_local._http_chat = fake_http
    try:
        b, m, tok_key, eff, errors = summarize_local.probe_llm(
            base="https://api.minimax.cn/v1", key="key123",
            models=["MiniMax-M3"], bases=["https://api.minimax.cn/v1", "https://api.minimax.io/v1"])
        check("换到了另一个域名", b == "https://api.minimax.io/v1", str(b))
        check("记录了 401", any("401" in e for e in errors), str(errors[:2]))
    finally:
        summarize_local._http_chat = old


def test_extract_text_truncated():
    """思考把额度吃光时，要报「截断」而不是干巴巴的「没有正文」。"""
    print("\n[输出截断提示]")
    import summarize_local
    res = {"choices": [{"finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "x" * 900}}]}
    try:
        summarize_local._extract_text(res)
        check("应该抛异常", False)
    except RuntimeError as e:
        check("提示是截断", "截断" in str(e), str(e))



def test_error_hints():
    """401/402 要翻译成中文提示，不能只丢原始 JSON。"""
    print("\n[错误码提示]")
    import summarize_local
    check("402 说余额不足",
          "余额不足" in summarize_local._explain("HTTP 402: insufficient balance (1008)"),
          summarize_local._explain("HTTP 402: x"))
    check("401 说 key 无效",
          "key 无效" in summarize_local._explain("HTTP 401: login fail (1004)"),
          summarize_local._explain("HTTP 401: x"))
    check("不认识的错误原样返回",
          summarize_local._explain("HTTP 500: boom").startswith("HTTP 500"),
          summarize_local._explain("HTTP 500: boom"))



def test_key_kind():
    """看前缀就能分清「按量付费」和「订阅」两种凭据。"""
    print("\n[key 类型识别]")
    import summarize_local
    check("sk-api 是按量付费",
          summarize_local.key_kind("sk-api-xxxx") == "payg")
    check("sk-cp 是订阅",
          summarize_local.key_kind("sk-cp-xxxx") == "subscription")
    check("其它不乱猜",
          summarize_local.key_kind("whatever") == "unknown")
    msg = summarize_local._explain("HTTP 402: insufficient balance (1008)", "sk-api-xxx")
    check("402 要提示去拿 Subscription Key", "sk-cp-" in msg, msg)
    msg2 = summarize_local._explain("HTTP 402: insufficient balance (1008)", "sk-cp-xxx")
    check("订阅 key 的 402 提示额度窗口", "5 小时窗口" in msg2, msg2)


def test_ensure_title():
    """标题日期由脚本定死：模型写错也必须被纠正。"""
    print("\n[标题日期纠正]")
    import summarize_local
    out = summarize_local.ensure_title(
        "# 微信沟通日报 · 2026-10-24\n\n正文", "2026-11-22")
    check("错误日期被覆盖", out.splitlines()[0] == "# 微信沟通日报 · 2026-11-22",
          out.splitlines()[0])
    check("正文保留", "正文" in out)
    out2 = summarize_local.ensure_title("> 没有标题的日报", "2026-11-22")
    check("缺标题就补上", out2.splitlines()[0] == "# 微信沟通日报 · 2026-11-22",
          out2.splitlines()[0])
    check("没有日期就不动",
          summarize_local.ensure_title("# 微信沟通日报 · 2026-10-24", "")
          == "# 微信沟通日报 · 2026-10-24")


def test_date_in_prompt():
    """日期必须出现在给模型的 prompt 里，否则模型会自己编。"""
    print("\n[日期进 prompt]")
    import summarize_local
    calls = []

    def fake_call(spec, user, max_tokens=None):
        calls.append(user)
        return "ok"

    old = summarize_local.call_llm
    summarize_local.call_llm = fake_call
    try:
        daily = {
            "date": "2026-11-22",
            "stats": {"message_count": 1, "chat_count": 1, "peak_hour": 9,
                      "active_hours": ["09:00"]},
            "chats": [{
                "chat_name": "张三", "chat_type": "single", "message_count": 1,
                "messages": [{"time": "2026-11-22 09:00:00", "sender": "张三",
                              "is_self": False, "type": "text", "content": "在吗"}],
            }],
        }
        summarize_local.summarize(daily, "spec")
        check("prompt 里有日报日期", calls and "2026-11-22" in calls[0],
              calls[0][:120] if calls else "no call")
        check("prompt 里带标签", calls and "日报日期" in calls[0],
              calls[0][:120] if calls else "no call")
    finally:
        summarize_local.call_llm = old


if __name__ == "__main__":
    test_render()
    test_split()
    test_end_to_end()
    test_no_key()
    test_probe_llm_fallback()
    test_probe_llm_switches_domain()
    test_extract_text_truncated()
    test_error_hints()
    test_key_kind()
    test_ensure_title()
    test_date_in_prompt()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
