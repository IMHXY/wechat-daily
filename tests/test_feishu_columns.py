#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书两列 + 隐私边界自测：python3 tests/test_feishu_columns.py

钉死这几件事：
  1. 飞书表只有「日期 / 日报」两列 —— 写入请求体里**没有**「消息数」，也没有「内容」
  2. 原始记录不上传飞书（feishu_push 不写原始内容，feishu_pull 容错返回 None）
  3. 日报写入是**按日期 upsert**：找不到就新建一行（日期 + 日报一起写）
  4. 推送失败**不能**拖死日报（run_daily 照样走 summarize）
  5. 导出前会检查微信进程（只警告、不中断），doctor 的自检复用同一个判断
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


# ---------------------------------------------------------------- 假飞书

class FakeFeishu(object):
    """把 bridge 的 HTTP 层换成假的，记录每一次请求。"""

    def __init__(self, items=None):
        self.items = items or []
        self.calls = []

    def request(self, method, url, headers=None, body=None):
        self.calls.append({"method": method, "url": url, "body": body})
        if "tenant_access_token" in url:
            return {"code": 0, "tenant_access_token": "fake-token"}
        if url.endswith("/records/search"):
            return {"code": 0, "data": {"items": self.items}}
        return {"code": 0, "data": {"record_id": "recFake"}}

    def write_calls(self):
        return [c for c in self.calls
                if "/records" in c["url"] and not c["url"].endswith("/records/search")]

    def fields_of(self, call):
        return json.loads(call["body"].decode("utf-8"))["fields"]


def _feishu_env():
    os.environ["FS_APP_ID"] = "cli_fake"
    os.environ["FS_APP_SECRET"] = "fake-secret"
    os.environ["FS_TABLE_ID"] = "appFake/tblFake"


def test_report_write_is_two_columns():
    print("\n[日报写入：请求体只有「日期」和「日报」]")
    import bridge
    fake = FakeFeishu(items=[])
    old_request, old_table = bridge._request, bridge._feishu_table
    bridge._request = fake.request
    bridge._feishu_table = lambda: ("appFake", "tblFake")
    try:
        bridge.feishu_push_report("2026-11-22", "# 微信沟通日报 · 2026-11-22")
        writes = fake.write_calls()
        check("写了一行", len(writes) == 1, str(len(writes)))
        if writes:
            fields = fake.fields_of(writes[0])
            check("字段就是日期 + 日报", set(fields) == {"日期", "日报"}, str(sorted(fields)))
            check("没有「内容」列", "内容" not in fields, str(sorted(fields)))
            check("没有「消息数」列", "消息数" not in fields, str(sorted(fields)))
            check("日报内容对", fields.get("日报", "").startswith("# 微信沟通日报"), str(fields)[:120])
            check("日期对", fields.get("日期") == "2026-11-22", str(fields)[:120])
            check("没有这一行就新建（POST）", writes[0]["method"] == "POST", writes[0]["method"])
    finally:
        bridge._request, bridge._feishu_table = old_request, old_table


def test_report_write_is_upsert():
    print("\n[日报写入：按日期 upsert]")
    import bridge
    old_request, old_table = bridge._request, bridge._feishu_table

    # 已经有同日期的行 -> 更新那一行（PUT），不新建
    fake = FakeFeishu(items=[{"record_id": "rec1", "fields": {"日期": "2026-11-22",
                                                              "日报": "旧日报"}}])
    bridge._request = fake.request
    bridge._feishu_table = lambda: ("appFake", "tblFake")
    try:
        bridge.feishu_push_report("2026-11-22", "新日报")
        writes = fake.write_calls()
        check("只写一次", len(writes) == 1, str(len(writes)))
        if writes:
            check("更新已有行（PUT）", writes[0]["method"] == "PUT", writes[0]["method"])
            check("更新的是那一行", writes[0]["url"].endswith("/rec1"), writes[0]["url"])
            fields = fake.fields_of(writes[0])
            check("字段还是只有日期 + 日报", set(fields) == {"日期", "日报"}, str(sorted(fields)))
            check("日报换成新的", fields.get("日报") == "新日报", str(fields)[:120])
    finally:
        bridge._request, bridge._feishu_table = old_request, old_table


def test_raw_payload_never_reaches_feishu():
    print("\n[原始记录不上传飞书]")
    import bridge
    old_request, old_table = bridge._request, bridge._feishu_table
    bridge._feishu_table = lambda: ("appFake", "tblFake")
    payload = {"date": "2026-11-22", "stats": {"message_count": 586},
               "chats": [{"chat_name": "家人群", "messages": [{"content": "在吗"}]}]}
    try:
        # 表里没有这一天的行 -> 不许新建「只有日期、没有日报」的半成品行
        fake = FakeFeishu(items=[])
        bridge._request = fake.request
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bridge.feishu_push("wechat-daily/data/2026-11-22.json", payload)
        check("没有写任何一行", len(fake.write_calls()) == 0, str(fake.write_calls()))
        check("说明了为什么不写", "原始记录不上传飞书" in buf.getvalue(), buf.getvalue()[:200])

        # 表里已经有这一天的行 -> 只更新日期列，绝不带原始内容
        fake2 = FakeFeishu(items=[{"record_id": "rec1", "fields": {"日期": "2026-11-22",
                                                                   "日报": "日报"}}])
        bridge._request = fake2.request
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.feishu_push("wechat-daily/data/2026-11-22.json", payload)
        writes = fake2.write_calls()
        check("已有行时只更新一次", len(writes) == 1, str(len(writes)))
        if writes:
            fields = fake2.fields_of(writes[0])
            check("只写日期列", set(fields) == {"日期"}, str(sorted(fields)))
            check("没有「内容」列", "内容" not in fields, str(sorted(fields)))
            check("没有「消息数」列", "消息数" not in fields, str(sorted(fields)))
            body = writes[0]["body"].decode("utf-8")
            check("原始内容没进请求体", "在吗" not in body and "家人群" not in body, body[:200])
    finally:
        bridge._request, bridge._feishu_table = old_request, old_table


def test_pull_is_tolerant():
    print("\n[拉原始记录：容错，不抛异常]")
    import bridge
    old_request, old_table = bridge._request, bridge._feishu_table
    bridge._feishu_table = lambda: ("appFake", "tblFake")
    try:
        # 表里只有两列，没有「内容」列
        fake = FakeFeishu(items=[{"record_id": "rec1", "fields": {"日期": "2026-11-22",
                                                                  "日报": "日报"}}])
        bridge._request = fake.request
        got = bridge.feishu_pull("wechat-daily/data/2026-11-22.json")
        check("返回 None 而不是抛异常", got is None, repr(got))

        # 「内容」列在，但是空的
        fake2 = FakeFeishu(items=[{"record_id": "rec1", "fields": {"日期": "2026-11-22",
                                                                   "内容": ""}}])
        bridge._request = fake2.request
        got2 = bridge.feishu_pull("wechat-daily/data/2026-11-22.json")
        check("空列也返回 None", got2 is None, repr(got2))

        # 压根没有这一天的记录
        fake3 = FakeFeishu(items=[])
        bridge._request = fake3.request
        got3 = bridge.feishu_pull("wechat-daily/data/2026-11-22.json")
        check("没有记录也返回 None", got3 is None, repr(got3))
    finally:
        bridge._request, bridge._feishu_table = old_request, old_table


def test_run_daily_survives_push_failure():
    print("\n[推送失败不中断日报]")
    import bridge
    import run_daily
    import summarize_local
    tmp = tempfile.mkdtemp()
    old_home = os.environ.get("WEIXIN_HOME")
    old_kind = os.environ.get("WEIXIN_BRIDGE")
    old_push = bridge.do_push
    old_sum = summarize_local.main
    try:
        src = os.path.join(tmp, "export", "2026-11-18", "张三")
        os.makedirs(src)
        with open(os.path.join(src, "2026-11-18.json"), "w", encoding="utf-8") as f:
            json.dump([{"type": 1, "content": "方案我看了", "sender": "张三",
                        "timestamp": 1794963600}], f, ensure_ascii=False)
        os.environ["WEIXIN_HOME"] = tmp
        os.environ["WEIXIN_BRIDGE"] = "github"

        def boom(*args, **kwargs):
            raise RuntimeError("飞书接口 POST 失败：code=1254130 TooLargeCell")

        calls = []
        bridge.do_push = boom
        summarize_local.main = lambda argv=None: calls.append(list(argv or [])) or 0
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_daily.main(["--source", os.path.join(tmp, "export"),
                                     "--date", "2026-11-18", "--summarize", "--scope", "all"])
            text = buf.getvalue()
        finally:
            bridge.do_push = old_push
            summarize_local.main = old_sum

        check("退出码 0", rc == 0, str(rc))
        check("打印了推送失败警告",
              "[warn] 原始记录推送失败（不影响日报）" in text, text[-400:])
        check("还是去生成日报了", len(calls) == 1, str(calls))
        if calls:
            check("日报带 --push", "--push" in calls[0], str(calls[0]))
            check("日报带对日期", "2026-11-18" in calls[0], str(calls[0]))
    finally:
        bridge.do_push, summarize_local.main = old_push, old_sum
        if old_home is None:
            os.environ.pop("WEIXIN_HOME", None)
        else:
            os.environ["WEIXIN_HOME"] = old_home
        if old_kind is None:
            os.environ.pop("WEIXIN_BRIDGE", None)
        else:
            os.environ["WEIXIN_BRIDGE"] = old_kind
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_daily_skips_raw_push_for_feishu():
    print("\n[飞书这条路不推原始记录]")
    import bridge
    import run_daily
    import summarize_local
    tmp = tempfile.mkdtemp()
    old_home = os.environ.get("WEIXIN_HOME")
    old_kind = os.environ.get("WEIXIN_BRIDGE")
    old_push = bridge.do_push
    old_sum = summarize_local.main
    pushed = []
    try:
        src = os.path.join(tmp, "export", "2026-11-18", "张三")
        os.makedirs(src)
        with open(os.path.join(src, "2026-11-18.json"), "w", encoding="utf-8") as f:
            json.dump([{"type": 1, "content": "方案我看了", "sender": "张三",
                        "timestamp": 1794963600}], f, ensure_ascii=False)
        os.environ["WEIXIN_HOME"] = tmp
        os.environ["WEIXIN_BRIDGE"] = "feishu"
        bridge.do_push = lambda *a, **k: pushed.append(a)
        summarize_local.main = lambda argv=None: 0
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_daily.main(["--source", os.path.join(tmp, "export"),
                                     "--date", "2026-11-18", "--summarize", "--scope", "all"])
            text = buf.getvalue()
        finally:
            bridge.do_push, summarize_local.main = old_push, old_sum
        check("退出码 0", rc == 0, str(rc))
        check("没有推原始记录", not pushed, str(pushed))
        check("说明了原始记录留在本机", "原始记录留在本机" in text, text[-300:])
    finally:
        bridge.do_push, summarize_local.main = old_push, old_sum
        if old_home is None:
            os.environ.pop("WEIXIN_HOME", None)
        else:
            os.environ["WEIXIN_HOME"] = old_home
        if old_kind is None:
            os.environ.pop("WEIXIN_BRIDGE", None)
        else:
            os.environ["WEIXIN_BRIDGE"] = old_kind
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- doctor

class _FakeResp(object):
    def __init__(self, data):
        self._raw = json.dumps(data).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _doctor_check_feishu(fields):
    """跑一遍 doctor.check_feishu，返回它打印的内容。"""
    import doctor

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", req)
        if "tenant_access_token" in url:
            return _FakeResp({"code": 0, "tenant_access_token": "fake-token"})
        return _FakeResp({"code": 0,
                          "data": {"items": [{"field_name": f} for f in fields]}})

    old = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            doctor.check_feishu({
                "WEIXIN_BRIDGE": "feishu",
                "FS_APP_ID": "cli_fake",
                "FS_APP_SECRET": "fake-secret",
                "FS_TABLE_ID": "appFake/tblFake",
            })
    finally:
        urllib.request.urlopen = old
    return buf.getvalue()


def test_doctor_two_columns():
    print("\n[doctor：只有两列也通过]")
    text = _doctor_check_feishu(["日期", "日报"])
    check("两列都在", "两列都在 ✓" in text, text[-400:])
    check("没有报缺列", "缺列" not in text, text[-400:])
    check("消息数列不算错", "没有「消息数」列 ✓" in text, text[-400:])
    check("内容列不算错", "没有「内容」列 ✓" in text, text[-400:])


def test_doctor_requires_report_column():
    print("\n[doctor：缺日报列要报错]")
    text = _doctor_check_feishu(["日期"])
    check("报缺列", "缺列" in text and "日报" in text, text[-400:])


# ---------------------------------------------------------------- wechat4

def test_wechat_running_shared():
    print("\n[微信进程检测只有一份实现]")
    import doctor
    import wechat4
    old = wechat4.wechat_running
    try:
        wechat4.wechat_running = lambda: True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            doctor.check_wechat_running()
        check("微信在跑 -> OK", "[OK]" in buf.getvalue() and "Weixin.exe 正在运行" in buf.getvalue(),
              buf.getvalue())

        wechat4.wechat_running = lambda: False
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            doctor.check_wechat_running()
        check("微信没跑 -> 提示", "[~]" in buf2.getvalue() and "没找到 Weixin.exe" in buf2.getvalue(),
              buf2.getvalue())

        # 判不出来（比如不在 Windows）时保持安静，不下结论
        wechat4.wechat_running = lambda: None
        buf3 = io.StringIO()
        with contextlib.redirect_stdout(buf3):
            doctor.check_wechat_running()
        check("判不出来就不啰嗦", buf3.getvalue().strip() == "", buf3.getvalue())
    finally:
        wechat4.wechat_running = old


def test_export_warns_when_wechat_closed():
    print("\n[导出前检查微信进程：只警告、不中断]")
    import wechat4
    old_running = wechat4.wechat_running
    old_run_stream = wechat4.run_stream
    old_run = wechat4.run
    tmp = tempfile.mkdtemp()
    try:
        wechat4.wechat_running = lambda: False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            warned = wechat4.warn_if_wechat_closed()
        text = buf.getvalue()
        check("警告了", warned is True, str(warned))
        check("打印了进程没在跑", "[warn] 微信客户端（Weixin.exe）现在没在运行" in text, text[:200])
        check("说了可能没同步", "可能还没同步到电脑上" in text, text[:200])
        check("说了怎么补救", "再重跑" in text, text[:200])

        # 微信在跑的时候不啰嗦
        wechat4.wechat_running = lambda: True
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            warned2 = wechat4.warn_if_wechat_closed()
        check("微信在跑就不警告", warned2 is False and buf2.getvalue().strip() == "",
              buf2.getvalue())

        # do_export 入口就会先检查（警告排在导出前面）
        wechat4.wechat_running = lambda: False
        wechat4.run_stream = lambda *a, **k: (0, "", "")
        wechat4.run = lambda *a, **k: (0, "", "")
        buf3 = io.StringIO()
        with contextlib.redirect_stdout(buf3):
            wechat4.do_export("keeper.exe", tmp, os.path.join(tmp, "out"))
        text3 = buf3.getvalue()
        check("导出前会打印警告", "Weixin.exe" in text3, text3[-300:])
        check("警告在导出之前",
              text3.find("Weixin.exe") < text3.find("导出最近"), text3[:200])
    finally:
        wechat4.wechat_running = old_running
        wechat4.run_stream = old_run_stream
        wechat4.run = old_run
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _feishu_env()
    test_report_write_is_two_columns()
    test_report_write_is_upsert()
    test_raw_payload_never_reaches_feishu()
    test_pull_is_tolerant()
    test_run_daily_survives_push_failure()
    test_run_daily_skips_raw_push_for_feishu()
    test_doctor_two_columns()
    test_doctor_requires_report_column()
    test_wechat_running_shared()
    test_export_warns_when_wechat_closed()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
