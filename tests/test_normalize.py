#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归一化层的自测：python3 tests/test_normalize.py"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "collector"))

import normalize  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def test_chat_name_from_path():
    print("\n[会话名推断：两种导出结构]")
    tmp = tempfile.mkdtemp()
    try:
        # A: <work-dir>/<会话名>/<日期>.json
        a = os.path.join(tmp, "out", "张三", "2026-11-18.json")
        os.makedirs(os.path.dirname(a))
        with open(a, "w", encoding="utf-8") as f:
            json.dump([{"type": 1, "content": "hi", "sender": "张三", "timestamp": 1794963600}],
                      f, ensure_ascii=False)
        # B: <work-dir>/<会话名>.json  （扁平，一个会话一个文件）
        b = os.path.join(tmp, "flat", "李四.json")
        os.makedirs(os.path.dirname(b))
        with open(b, "w", encoding="utf-8") as f:
            json.dump([{"type": 1, "content": "yo", "sender": "李四", "timestamp": 1794963600}],
                      f, ensure_ascii=False)
        # C: <work-dir>/<会话名>_<日期>.json
        c = os.path.join(tmp, "mix", "王五_2026-11-18.json")
        os.makedirs(os.path.dirname(c))
        with open(c, "w", encoding="utf-8") as f:
            json.dump([{"type": 1, "content": "ok", "sender": "王五", "timestamp": 1794963600}],
                      f, ensure_ascii=False)

        tz = normalize.get_timezone("Asia/Shanghai")
        got = {}
        for label, path in (("A", a), ("B", b), ("C", c)):
            msgs = normalize.collect_sources([os.path.dirname(path)], tz)
            got[label] = sorted({m["chat_name"] for m in msgs})
        check("A 目录+日期文件 -> 父目录名", got["A"] == ["张三"], str(got["A"]))
        check("B 扁平单文件 -> 文件名", got["B"] == ["李四"], str(got["B"]))
        check("C 名称+日期 -> 名称部分", got["C"] == ["王五"], str(got["C"]))
    finally:
        shutil.rmtree(tmp)


def test_name_map():
    print("\n[昵称映射]")
    tz = normalize.get_timezone("Asia/Shanghai")
    msgs = [{"ts": 1794963600.0, "sender": "wxid_abc", "is_self": False, "type": "text",
             "content": "hi", "chat_name": "wxid_abc"}]
    daily = normalize.build_daily(msgs, "2026-11-18", tz, "Asia/Shanghai", {"我"},
                                  {"wxid_abc": "张三"}, only_mine=False)
    check("会话名被替换", daily["chats"][0]["chat_name"] == "张三",
          daily["chats"][0]["chat_name"])
    check("发送人被替换", daily["chats"][0]["messages"][0]["sender"] == "张三",
          daily["chats"][0]["messages"][0]["sender"])


def test_chatlog_json():
    print("\n[chatlog 风格 JSON]")
    tmp = tempfile.mkdtemp()
    try:
        chat_dir = os.path.join(tmp, "张三")
        os.makedirs(chat_dir)
        # 2026-11-18 09:00:00 +08:00 -> epoch
        with open(os.path.join(chat_dir, "2026-11-18.json"), "w", encoding="utf-8") as f:
            json.dump([
                {"type": 1, "content": "在吗？", "sender": "wxid_zhang", "timestamp": 1794963600},
                {"type": 1, "content": "在的，你说", "sender": "我", "timestamp": 1794963660},
                {"type": 3, "content": "[图片]", "sender": "wxid_zhang", "timestamp": 1794963720},
            ], f, ensure_ascii=False)
        msgs = normalize.collect_sources([tmp], normalize.get_timezone("Asia/Shanghai"))
        daily = normalize.build_daily(msgs, "2026-11-18", normalize.get_timezone("Asia/Shanghai"),
                                      "Asia/Shanghai", {"我"}, only_mine=False)
        check("解析出 3 条", daily["stats"]["message_count"] == 3,
              str(daily["stats"]["message_count"]))
        check("会话名从目录推断", daily["chats"][0]["chat_name"] == "张三",
              daily["chats"][0]["chat_name"])
        check("is_self 正确", daily["chats"][0]["messages"][1]["is_self"] is True)
        check("图片类型识别", daily["chats"][0]["messages"][2]["type"] == "image")
        check("时间本地化", daily["chats"][0]["messages"][0]["time"].startswith("2026-11-18 09:"),
              daily["chats"][0]["messages"][0]["time"])
    finally:
        shutil.rmtree(tmp)


def test_date_filter():
    print("\n[日期过滤]")
    tz = normalize.get_timezone("Asia/Shanghai")
    msgs = [
        {"ts": 1794978000.0, "sender": "A", "is_self": False, "type": "text",
         "content": "今天", "chat_name": "X"},
        {"ts": 1794891600.0, "sender": "A", "is_self": False, "type": "text",
         "content": "昨天", "chat_name": "X"},
    ]
    daily = normalize.build_daily(msgs, "2026-11-18", tz, "Asia/Shanghai", set(), only_mine=False)
    check("只保留当天", daily["stats"]["message_count"] == 1,
          str(daily["stats"]["message_count"]))


def test_csv():
    print("\n[CSV 导出]")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "chat.csv")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("消息类型,消息内容,发送人,发送时间,会话\n")
            f.write("文本,你好,张三,2026-11-18 10:00:00,同事群\n")
            f.write("文本,好的,李四,2026-11-18 10:01:00,同事群\n")
        tz = normalize.get_timezone("Asia/Shanghai")
        msgs = normalize.collect_sources([tmp], tz)
        daily = normalize.build_daily(msgs, "2026-11-18", tz, "Asia/Shanghai", set(), only_mine=False)
        check("CSV 解析出 2 条", daily["stats"]["message_count"] == 2,
              str(daily["stats"]["message_count"]))
        check("CSV 会话名", daily["chats"][0]["chat_name"] == "同事群",
              daily["chats"][0]["chat_name"])
    finally:
        shutil.rmtree(tmp)


def test_markdown():
    print("\n[Markdown 导出]")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "王五__wxid_wang.jsonl.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("[2026-11-18 14:03:21] 王五: 文件发你了\n")
            f.write("[2026-11-18 14:05:02] 我: 收到，晚上看\n")
        tz = normalize.get_timezone("Asia/Shanghai")
        msgs = normalize.collect_sources([tmp], tz)
        daily = normalize.build_daily(msgs, "2026-11-18", tz, "Asia/Shanghai", {"我"}, only_mine=False)
        check("Markdown 解析出 2 条", daily["stats"]["message_count"] == 2,
              str(daily["stats"]["message_count"]))
        check("Markdown 会话名去后缀", daily["chats"][0]["chat_name"] == "王五",
              daily["chats"][0]["chat_name"])
        check("Markdown 内容", "文件发你了" in daily["chats"][0]["messages"][0]["content"])
    finally:
        shutil.rmtree(tmp)


def test_generic_alias():
    print("\n[字段别名兼容]")
    tz = normalize.get_timezone("Asia/Shanghai")
    recs, _ = normalize._extract_records(
        {"messages": [{"CreateTime": 1794978000, "NickName": "赵六",
                       "StrContent": "hi", "MsgType": 1}]}, "会话")
    check("识别别名", len(recs) == 1, str(len(recs)))
    out = normalize.parse_json_obj(
        {"messages": [{"CreateTime": 1794978000, "NickName": "赵六",
                       "StrContent": "hi", "MsgType": 1}]}, "会话", tz)
    check("时间解析", out and out[0]["ts"] == 1794978000, str(out))
    check("内容解析", out and out[0]["content"] == "hi")


def test_empty_day():
    print("\n[空数据]")
    tz = normalize.get_timezone("Asia/Shanghai")
    daily = normalize.build_daily([], "2026-11-18", tz, "Asia/Shanghai", set(), only_mine=False)
    check("空数据不崩", daily["stats"]["message_count"] == 0)
    check("空数据无会话", daily["chats"] == [])


def test_wxq_string_lines():
    print("\n[wxq 真实格式：messages 是字符串行]")
    tmp = tempfile.mkdtemp()
    try:
        # wxq history --format json 的真实结构
        path = os.path.join(tmp, "wxq_2026-11-18.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "chat": "张三", "username": "wxid_a", "is_group": False, "count": 2,
                "offset": 0, "limit": 100, "start_time": None, "end_time": None,
                "type": None,
                "messages": ["[2026-11-18 09:12] 张三: 方案我看了",
                             "[2026-11-18 09:25] 我: 好，下午给你"],
                "failures": None,
            }, f, ensure_ascii=False)
        tz = normalize.get_timezone("Asia/Shanghai")
        msgs = normalize.collect_sources([tmp], tz)
        check("解析出 2 条", len(msgs) == 2, str(len(msgs)))
        if len(msgs) == 2:
            check("内容正确", msgs[0]["content"] == "方案我看了", msgs[0]["content"])
            check("发送者正确", msgs[0]["sender"] == "张三", msgs[0]["sender"])
            check("会话名正确", msgs[0]["chat_name"] == "张三", msgs[0]["chat_name"])
            check("时间正确", msgs[0]["ts"] == 1794964320.0, str(msgs[0]["ts"]))
    finally:
        shutil.rmtree(tmp)


def test_chatlog_keeper_schema():
    """chatlog-keeper 真实的导出结构（照其源码 cli.py 的 _export_wechat 记录格式）。"""
    print("\n[chatlog-keeper 导出结构]")
    tmp = tempfile.mkdtemp()
    try:
        # 真实字段：ts(epoch秒) / sender(显示名) / chat_room(会话显示名)
        #           conversation_id / is_self / is_group_chat
        path = os.path.join(tmp, "wechat_messages.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([
                {"ts": 1794964320, "sender": "张三", "sender_wxid": "wxid_zhang",
                 "chat_room": "项目组群", "conversation_id": "123@chatroom",
                 "content": "方案我看了", "msg_type": 1, "is_self": False,
                 "account_id": "wxid_me", "conversation_type": "group",
                 "is_group_chat": True},
                {"ts": 1794967200, "sender": "我", "sender_wxid": "wxid_me",
                 "chat_room": "李四", "conversation_id": "wxid_li",
                 "content": "数据更新完了", "msg_type": 1, "is_self": True,
                 "account_id": "wxid_me", "conversation_type": "direct",
                 "is_group_chat": False},
            ], f, ensure_ascii=False)
        tz = normalize.get_timezone("Asia/Shanghai")
        msgs = normalize.collect_sources([tmp], tz)
        daily = normalize.build_daily(msgs, "2026-11-18", tz, "Asia/Shanghai", {"我"}, only_mine=False)
        check("解析出 2 条", daily["stats"]["message_count"] == 2,
              str(daily["stats"]["message_count"]))
        names = sorted(c["chat_name"] for c in daily["chats"])
        check("会话名取 chat_room（不是文件名）", names == ["李四", "项目组群"], str(names))
        group = [c for c in daily["chats"] if c["chat_name"] == "项目组群"]
        check("is_group_chat 识别群聊",
              group and group[0]["chat_type"] == "group",
              str([(c["chat_name"], c["chat_type"]) for c in daily["chats"]]))
        senders = {m["sender"] for c in daily["chats"] for m in c["messages"]}
        check("发送者取显示名不是 wxid", senders == {"张三", "我"}, str(senders))
        self_msgs = [m for c in daily["chats"] for m in c["messages"] if m.get("is_self")]
        check("沿用记录自带的 is_self", len(self_msgs) == 1
              and self_msgs[0]["sender"] == "我", str(self_msgs))
        check("epoch 秒被正确换算",
              daily["chats"][0]["messages"][0]["time"].startswith("2026-11-18"),
              daily["chats"][0]["messages"][0]["time"])
    finally:
        shutil.rmtree(tmp)


def test_probe():
    print("\n[结构探针]")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "unknown.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"foo": 1, "bar": "x"}] * 5, f)
        info = normalize._probe_json(path)
        check("探到字段名", info and "foo" in info, str(info))
        check("探到类型", info and "int" in info, str(info))
        small = os.path.join(tmp, "tiny.json")
        with open(small, "w", encoding="utf-8") as f:
            f.write("[]")
        check("小文件不探", normalize._probe_json(small) is None)
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_chat_name_from_path()
    test_name_map()
    test_chatlog_keeper_schema()
    test_wxq_string_lines()
    test_probe()
    test_chatlog_json()
    test_date_filter()
    test_csv()
    test_markdown()
    test_generic_alias()
    test_empty_day()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
