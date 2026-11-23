#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日报范围：只留我参与过的会话 + 以我为中心裁剪上下文。

对应需求：
  1) 有些群我从不回、也不关心 —— 整个会话不该进日报
  2) 群里我发言前后才有信息量 —— 其余消息不用喂给模型
"""
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

import normalize  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print("  %s %s" % ("ok  " if cond else "FAIL", name))
    if not cond:
        FAILURES.append(name)


TZ = normalize.get_timezone("Asia/Shanghai")
BASE = dt.datetime(2026, 11, 22, 9, 0, tzinfo=TZ).timestamp()


def make_msgs():
    msgs = []
    # A群：我发过言，前面有 20 条别人的闲聊
    for i in range(20):
        msgs.append({"ts": BASE + i * 60, "chat_name": "A群", "sender": "别人",
                     "content": "闲聊%d" % i, "type": "text", "is_self": False})
    msgs.append({"ts": BASE + 3000, "chat_name": "A群", "sender": "我",
                 "content": "这个我来跟进", "type": "text", "is_self": True})
    # B群：我完全没参与
    for i in range(30):
        msgs.append({"ts": BASE + i * 60, "chat_name": "B群", "sender": "张三",
                     "content": "无关消息%d" % i, "type": "text", "is_self": False})
    # C私聊：正常闲聊
    msgs.append({"ts": BASE + 4000, "chat_name": "朋友", "sender": "我",
                 "content": "周末吃饭？", "type": "text", "is_self": True})
    msgs.append({"ts": BASE + 4060, "chat_name": "朋友", "sender": "朋友",
                 "content": "好啊", "type": "text", "is_self": False})
    return msgs


def build(**kw):
    return normalize.build_daily(make_msgs(), "2026-11-22", TZ, "Asia/Shanghai",
                                 {"我"}, only_mine=kw.get("only_mine", True),
                                 context_window=kw.get("context_window", 5))


def test_only_my_chats():
    print("\n[只留我参与过的会话]")
    d = build()
    st = d["stats"]
    names = [c["chat_name"] for c in d["chats"]]
    check("B群（我没参与）被跳过", "B群" not in names, str(names))
    check("保留的只有我参与过的会话", sorted(names) == ["A群", "朋友"], str(names))
    check("跳过数记进统计", st["skipped_chat_count"] == 1, str(st))
    check("总会话数也在", st["chat_count_total"] == 3, str(st))
    check("保留会话数", st["chat_count"] == 2, str(st))
    check("我的发言被保留", st["self_count"] == 2, str(st))


def test_context_trim():
    print("\n[以我为中心裁剪上下文]")
    d = build(context_window=5)
    a = [c for c in d["chats"] if c["chat_name"] == "A群"][0]
    check("A群被裁剪了", len(a["messages"]) < 21, str(len(a["messages"])))
    check("我的发言还在", any(m["is_self"] for m in a["messages"]))
    check("标记了 trimmed", a.get("trimmed") is True, str(a.get("trimmed")))
    check("裁剪后不超过窗口*2+1", len(a["messages"]) <= 11, str(len(a["messages"])))

    d0 = build(context_window=0)
    a0 = [c for c in d0["chats"] if c["chat_name"] == "A群"][0]
    check("窗口 0 = 只留我自己的", len(a0["messages"]) == 1, str(len(a0["messages"])))
    check("窗口 0 留的是我那条",
          a0["messages"][0]["content"] == "这个我来跟进",
          a0["messages"][0]["content"])

    dn = build(context_window=-1)
    an = [c for c in dn["chats"] if c["chat_name"] == "A群"][0]
    check("窗口 -1 = 不裁剪", len(an["messages"]) == 21, str(len(an["messages"])))
    check("不裁剪时 trimmed 为假", an.get("trimmed") is False, str(an.get("trimmed")))


def test_scope_all():
    print("\n[scope=all 保留全部]")
    d = build(only_mine=False, context_window=-1)
    st = d["stats"]
    names = sorted(c["chat_name"] for c in d["chats"])
    check("三个会话都在", names == ["A群", "B群", "朋友"], str(names))
    check("跳过数为 0", st["skipped_chat_count"] == 0, str(st))
    check("消息数不裁剪", st["message_count"] == 53, str(st["message_count"]))
    check("总数也在", st["message_count_total"] == 53, str(st))


def test_no_self_but_scope_all():
    """scope=all 时，没有我发言的会话不能被裁剪成空。"""
    print("\n[scope=all 且我没发言的会话不被裁空]")
    msgs = [{"ts": BASE + i * 60, "chat_name": "别人群", "sender": "张三",
             "content": "消息%d" % i, "type": "text", "is_self": False} for i in range(5)]
    d = normalize.build_daily(msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                               only_mine=False, context_window=5)
    check("消息没被裁掉", d["stats"]["message_count"] == 5,
          str(d["stats"]["message_count"]))


def test_cli_options():
    print("\n[命令行参数]")
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        with open(os.path.join(src, "m.json"), "w", encoding="utf-8") as f:
            json.dump([
                {"ts": BASE, "chat_name": "A群", "sender": "别人", "content": "x",
                 "type": "text", "is_self": False},
                {"ts": BASE + 60, "chat_name": "A群", "sender": "我", "content": "y",
                 "type": "text", "is_self": True},
                {"ts": BASE + 120, "chat_name": "B群", "sender": "张三", "content": "z",
                 "type": "text", "is_self": False},
            ], f, ensure_ascii=False)
        out = os.path.join(tmp, "daily.json")
        # 产物写进临时目录，别去动项目里（和用户）真实的 data/ 与 reports/
        env = dict(os.environ)
        env["WEIXIN_HOME"] = tmp
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "collector", "run_daily.py"),
             "--source", src, "--date", "2026-11-22", "--no-push",
             "--scope", "all", "--context-window", "-1"],
            capture_output=True, text=True, env=env, timeout=120)
        check("退出码 0", r.returncode == 0, (r.stdout + r.stderr)[-300:])
        check("提示了范围", "全部会话" in r.stdout, r.stdout[-300:])
        check("提示了不裁剪", "不裁剪" in r.stdout, r.stdout[-300:])
        # 默认是 mine
        out2 = os.path.join(tmp, "d2", "2026-11-22.json")
        r2 = subprocess.run(
            [sys.executable, os.path.join(ROOT, "collector", "run_daily.py"),
             "--source", src, "--date", "2026-11-22", "--no-push"],
            capture_output=True, text=True, env=env, timeout=120)
        check("默认走 mine", "只保留我参与过的会话" in r2.stdout, r2.stdout[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_config_env_scope():
    print("\n[config.env 里的范围配置]")
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        with open(os.path.join(src, "m.json"), "w", encoding="utf-8") as f:
            json.dump([
                {"ts": BASE, "chat_name": "A群", "sender": "别人", "content": "x",
                 "type": "text", "is_self": False},
                {"ts": BASE + 60, "chat_name": "A群", "sender": "我", "content": "y",
                 "type": "text", "is_self": True},
            ], f, ensure_ascii=False)
        env_file = os.path.join(tmp, "config.env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("DAILY_SCOPE=all\nDAILY_CONTEXT_WINDOW=-1\n")
        env = dict(os.environ)
        env["WEIXIN_ENV_FILE"] = env_file
        env["WEIXIN_HOME"] = tmp
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "collector", "run_daily.py"),
             "--source", src, "--date", "2026-11-22", "--no-push"],
            capture_output=True, text=True, env=env, timeout=120)
        check("退出码 0", r.returncode == 0, (r.stdout + r.stderr)[-300:])
        check("读了 config.env 的 scope", "全部会话" in r.stdout, r.stdout[-300:])
        check("读了 config.env 的窗口", "不裁剪" in r.stdout, r.stdout[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_scope_command():
    """wechat4.py scope：查看 / 改 / 重置 / 预览。"""
    print("\n[scope 命令]")
    import wechat4
    tmp = tempfile.mkdtemp()
    old_env = os.environ.get("WEIXIN_ENV_FILE")
    try:
        cfg = os.path.join(tmp, "config.env")
        os.environ["WEIXIN_ENV_FILE"] = cfg
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("# 注释要保留\nDAILY_SCOPE=mine\nDAILY_CONTEXT_WINDOW=5\n")

        rc = wechat4.cmd_scope(type("A", (), {
            "scope": "all", "window": 10, "reset": False, "preview": False,
            "date": None})())
        check("退出码 0", rc == 0, str(rc))
        text = open(cfg, encoding="utf-8").read()
        check("scope 写进去了", "DAILY_SCOPE=all" in text, text)
        check("窗口写进去了", "DAILY_CONTEXT_WINDOW=10" in text, text)
        check("注释没丢", "# 注释要保留" in text, text)

        rc = wechat4.cmd_scope(type("A", (), {
            "scope": None, "window": None, "reset": True, "preview": False,
            "date": None})())
        text2 = open(cfg, encoding="utf-8").read()
        check("reset 回默认 mine", "DAILY_SCOPE=mine" in text2, text2)
        check("reset 回默认 5", "DAILY_CONTEXT_WINDOW=5" in text2, text2)
    finally:
        if old_env is None:
            os.environ.pop("WEIXIN_ENV_FILE", None)
        else:
            os.environ["WEIXIN_ENV_FILE"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_matches_build_daily():
    """预览的结果必须和真跑一遍的结果一致（不然预览就是骗人的）。"""
    print("\n[预览和实际过滤一致]")
    msgs = make_msgs()
    for only_mine in (True, False):
        for window in (0, 5, -1):
            rows = normalize.preview_scope(
                msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                only_mine=only_mine, context_window=window)
            d = normalize.build_daily(
                msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                only_mine=only_mine, context_window=window)
            real = {c["chat_name"]: c["message_count"] for c in d["chats"]}
            prev = {r["chat_name"]: r["kept"] for r in rows if r["included"]}
            check("scope=%s window=%d 一致" % (only_mine, window), real == prev,
                  "实际=%s 预览=%s" % (real, prev))
            skipped = {r["chat_name"] for r in rows if not r["included"]}
            check("跳过的会话确实不在结果里" if only_mine else "scope=all 不跳过",
                  (not skipped) or not (skipped & set(real)),
                  str(skipped))




def test_scope_preview_runs_export():
    """--preview 要能真的跑起 EXPORT_CMD（之前这里引用了不存在的全局变量会崩）。"""
    print("\n[scope --preview 跑导出]")
    import wechat4
    tmp = tempfile.mkdtemp()
    old_env = os.environ.get("WEIXIN_ENV_FILE")
    try:
        outdir = os.path.join(tmp, "export")
        os.makedirs(outdir)
        writer = os.path.join(tmp, "fake_export.py")
        with open(writer, "w", encoding="utf-8") as f:
            f.write(
                "import json, sys, os\n"
                "out = sys.argv[1]\n"
                "os.makedirs(out, exist_ok=True)\n"
                "json.dump([{'ts': %d, 'sender': '我',\n" % int(
                    dt.datetime(2026, 11, 18, 9, 12, tzinfo=TZ).timestamp()) +
                "  'chat_room': '张三', 'content': '在吗', 'msg_type': 1,\n"
                "  'is_self': True, 'is_group_chat': False}],\n"
                "  open(os.path.join(out, 'wechat_messages.json'), 'w'),\n"
                "  ensure_ascii=False)\n")
        cfg = os.path.join(tmp, "config.env")
        os.environ["WEIXIN_ENV_FILE"] = cfg
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('EXPORT_CMD="%s %s %s"\n' % (sys.executable, writer, outdir))
            f.write("EXPORT_DIR=%s\n" % outdir)
            f.write("DAILY_SCOPE=mine\nDAILY_CONTEXT_WINDOW=5\n")
        rc = wechat4.cmd_scope(type("A", (), {
            "scope": None, "window": None, "reset": False, "preview": True,
            "date": "2026-11-18"})())
        check("退出码 0", rc == 0, str(rc))
        check("导出真的跑了", os.path.isfile(os.path.join(outdir, "wechat_messages.json")))
    finally:
        if old_env is None:
            os.environ.pop("WEIXIN_ENV_FILE", None)
        else:
            os.environ["WEIXIN_ENV_FILE"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)


def test_version_flag():
    print("\n[--version]")
    import wechat4
    check("有版本号", bool(getattr(wechat4, "VERSION", "")), str(getattr(wechat4, "VERSION", "")))
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        wechat4.cmd_version(type("A", (), {})())
    finally:
        sys.stdout = old
    text = buf.getvalue()
    check("打印了版本", wechat4.VERSION in text, text)
    check("列出了 scope 命令", "scope" in text, text)



def _wl_msgs():
    """一个我完全没说话的群 + 一个我说话的群 + 一个无关群。"""
    msgs = []
    for i in range(80):
        msgs.append({"ts": BASE + i * 30, "chat_name": "拼单群", "sender": "张三",
                     "content": "拼单%d" % i, "type": "text", "is_self": False})
    msgs.append({"ts": BASE + 5000, "chat_name": "项目组群", "sender": "我",
                 "content": "我来做", "type": "text", "is_self": True})
    for i in range(10):
        msgs.append({"ts": BASE + 6000 + i * 30, "chat_name": "XX项目组群",
                     "sender": "李四", "content": "讨论%d" % i, "type": "text",
                     "is_self": False})
    return msgs


def test_include_chats_whitelist():
    print("\n[白名单群：我不说话也进日报]")
    msgs = _wl_msgs()
    d = normalize.build_daily(msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                               only_mine=True, context_window=5)
    check("白名单前：没说话的群被跳过",
          "拼单群" not in [c["chat_name"] for c in d["chats"]],
          str([c["chat_name"] for c in d["chats"]]))

    d = normalize.build_daily(msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                               only_mine=True, context_window=5,
                               include_chats="拼单群")
    names = [c["chat_name"] for c in d["chats"]]
    check("白名单群进来了", "拼单群" in names, str(names))
    check("无关群还是没进", "XX项目组群" not in names, str(names))
    wl = [c for c in d["chats"] if c["chat_name"] == "拼单群"][0]
    check("白名单群最多留 50 条", wl["message_count"] == 50, str(wl["message_count"]))

    d = normalize.build_daily(msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                               only_mine=True, context_window=5,
                               include_chats="拼单群", include_max=20)
    wl = [c for c in d["chats"] if c["chat_name"] == "拼单群"][0]
    check("include_max 能调", wl["message_count"] == 20, str(wl["message_count"]))

    d = normalize.build_daily(msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                               only_mine=True, context_window=5,
                               include_chats="项目组")
    names = [c["chat_name"] for c in d["chats"]]
    check("关键字子串命中 XX项目组群", "XX项目组群" in names, str(names))


def test_include_chats_preview():
    print("\n[白名单在预览里标出来]")
    msgs = _wl_msgs()
    rows = normalize.preview_scope(msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                                   only_mine=True, context_window=5,
                                   include_chats="拼单群")
    by_name = {r["chat_name"]: r for r in rows}
    check("白名单群 included", by_name["拼单群"]["included"] is True)
    check("标了 by_whitelist", by_name["拼单群"]["by_whitelist"] is True)
    check("预览条数和实际一致", by_name["拼单群"]["kept"] == 50,
          str(by_name["拼单群"]["kept"]))
    check("无关群没进", by_name["XX项目组群"]["included"] is False)


def test_scope_command_include():
    print("\n[scope 命令管白名单]")
    import wechat4
    tmp = tempfile.mkdtemp()
    old_env = os.environ.get("WEIXIN_ENV_FILE")
    try:
        cfg = os.path.join(tmp, "config.env")
        os.environ["WEIXIN_ENV_FILE"] = cfg
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("# 注释要保留\nDAILY_SCOPE=mine\nDAILY_CONTEXT_WINDOW=5\n")
        wechat4.cmd_scope(type("A", (), {
            "scope": None, "window": None, "reset": False, "preview": False,
            "date": None, "include": "群A,群B", "clear_include": False,
            "include_max": 30})())
        text = open(cfg, encoding="utf-8").read()
        check("白名单写进去了", "DAILY_INCLUDE_CHATS=群A,群B" in text, text)
        check("include_max 写进去了", "DAILY_INCLUDE_MAX=30" in text, text)
        check("注释没丢", "# 注释要保留" in text, text)

        wechat4.cmd_scope(type("A", (), {
            "scope": None, "window": None, "reset": False, "preview": False,
            "date": None, "include": None, "clear_include": True,
            "include_max": None})())
        text2 = open(cfg, encoding="utf-8").read()
        check("能清空白名单", "DAILY_INCLUDE_CHATS=\n" in text2, text2)
    finally:
        if old_env is None:
            os.environ.pop("WEIXIN_ENV_FILE", None)
        else:
            os.environ["WEIXIN_ENV_FILE"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)




def test_silent_whitelisted_chat():
    """白名单群今天完全没人发言 -> 也要列出来（沉默也是信息）。"""
    print("\n[白名单群今天没人发言]")
    import summarize_local
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "export"))
        # 一个有人说话的会话
        with open(os.path.join(tmp, "export", "wechat_messages.json"), "w",
                  encoding="utf-8") as f:
            json.dump([{"ts": BASE, "sender": "我", "chat_room": "项目组群",
                        "content": "在吗", "msg_type": 1, "is_self": True,
                        "is_group_chat": True}], f, ensure_ascii=False)
        # 会话名单里有三个群，其中一个今天没人说话
        with open(os.path.join(tmp, "export", "wechat_directory.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"source": "wechat", "available": True, "conversations": [
                {"conversation_id": "r1", "label": "项目组群",
                 "conversation_type": "group", "message_count": 1},
                {"conversation_id": "r2", "label": "安静群",
                 "conversation_type": "group", "message_count": 5},
                {"conversation_id": "r3", "label": "无关群",
                 "conversation_type": "group", "message_count": 9},
            ]}, f, ensure_ascii=False)

        msgs = normalize.collect_sources([os.path.join(tmp, "export")], TZ)
        check("名单文件不当消息解析", len(msgs) == 1, str(len(msgs)))
        known = normalize.collect_directory([os.path.join(tmp, "export")])
        check("读出 3 个会话", len(known) == 3, str(list(known)))

        d = normalize.build_daily(msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                                   only_mine=True, context_window=5,
                                   include_chats="安静群", known_chats=known)
        names = [c["chat_name"] for c in d["chats"]]
        check("静默的白名单群进来了", "安静群" in names, str(names))
        check("无关群没进来", "无关群" not in names, str(names))
        silent = [c for c in d["chats"] if c["chat_name"] == "安静群"][0]
        check("标了 silent", silent.get("silent") is True, str(silent.get("silent")))
        check("0 条消息", silent["message_count"] == 0, str(silent["message_count"]))
        check("统计里有静默数", d["stats"]["silent_chat_count"] == 1,
              str(d["stats"]))

        # 渲染出来要明确说「今天没有发言」，不能让模型以为有内容
        text = summarize_local.render_chat(silent)
        check("渲染写明没有发言", "今天没有发言" in text, text)
        check("提示别编内容", "不要编" in text, text)

        # 概览里要提到静默会话
        stats = summarize_local.render_stats(d)
        check("概览写了无人发言", "无人发言" in stats, stats)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_directory_file_no_silent():
    """没有会话名单文件时，行为退回原样（不会凭空造会话）。"""
    print("\n[没有名单文件就不造会话]")
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "export"))
        with open(os.path.join(tmp, "export", "wechat_messages.json"), "w",
                  encoding="utf-8") as f:
            json.dump([{"ts": BASE, "sender": "我", "chat_room": "项目组群",
                        "content": "在吗", "msg_type": 1, "is_self": True,
                        "is_group_chat": True}], f, ensure_ascii=False)
        msgs = normalize.collect_sources([os.path.join(tmp, "export")], TZ)
        known = normalize.collect_directory([os.path.join(tmp, "export")])
        check("名单为空", known == {}, str(known))
        d = normalize.build_daily(msgs, "2026-11-22", TZ, "Asia/Shanghai", {"我"},
                                   only_mine=True, context_window=5,
                                   include_chats="安静群", known_chats=known)
        check("不会造出会话", len(d["chats"]) == 1, str([c["chat_name"] for c in d["chats"]]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



if __name__ == "__main__":
    test_only_my_chats()
    test_context_trim()
    test_scope_all()
    test_no_self_but_scope_all()
    test_cli_options()
    test_config_env_scope()
    test_scope_command()
    test_preview_matches_build_daily()
    test_scope_preview_runs_export()
    test_version_flag()
    test_include_chats_whitelist()
    test_include_chats_preview()
    test_scope_command_include()
    test_silent_whitelisted_chat()
    test_no_directory_file_no_silent()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
