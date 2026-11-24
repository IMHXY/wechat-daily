#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把不同来源的微信聊天导出归一化成统一的「每日消息」JSON。

支持三类来源（自动识别）：
  1. chatlog (zjhzx/chatlog) 导出的 JSON —— 每会话一个目录，文件名形如 2026-11-18.json
  2. 留痕 / WeChatMsg 导出的 CSV
  3. 通用 JSON 数组（字段名做了多别名兼容）

输出结构：
  {
    "date": "2026-11-18",
    "timezone": "Asia/Shanghai",
    "generated_at": "2026-11-18T23:00:12+08:00",
    "stats": {"message_count": 128, "chat_count": 6, "self_count": 51,
              "active_hours": ["08:12", "22:41"], "peak_hour": 14},
    "chats": [
      {"chat_name": "张三", "chat_type": "single",
       "messages": [{"ts": 1763456789, "time": "2026-11-18 09:12:33", "hour": 9,
                     "sender": "我", "is_self": true, "type": "text", "content": "……"}]}
    ]
  }

只依赖 Python 标准库，Python 3.7+ 可跑（Windows / macOS / Linux 通用）。
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

# ---------------------------------------------------------------- 时区处理

def get_timezone(name):
    """拿到 tzinfo；Windows 上没有 IANA 库时对 Asia/Shanghai 退化为固定 +08:00。"""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        if name in ("Asia/Shanghai", "Asia/Chongqing", "PRC"):
            return dt.timezone(dt.timedelta(hours=8), name)
        return dt.timezone.utc


# ---------------------------------------------------------------- 字段别名

TIME_KEYS = ["timestamp", "ts", "time", "createTime", "CreateTime",
             "create_time", "localCreateTime", "LocalCreateTime", "msgTime", "date"]
SENDER_KEYS = ["sender_display_name", "sender", "senderName", "sender_name", "sender_wxid",
               "sender_label", "nickname", "NickName", "talker", "who", "from",
               "displayName", "DisplayName", "wxid"]
CONTENT_KEYS = ["content", "Content", "strContent", "StrContent", "message", "text",
                "msg", "MsgContent", "displayContent"]
CHAT_KEYS = ["chat_display_name", "chat_room", "chatName", "chat_name", "roomName",
             "talkerName", "chat", "talker", "room", "chatroom",
             "conversation_id", "conversation", "group", "nickname"]
# chat_room / conversation_id 是 chatlog-keeper 的导出字段：
#   chat_room        = 会话显示名（群名 / 对方昵称）
#   conversation_id  = 会话 ID（wxid 或 xxx@chatroom），兜底用
# 少了这两个，1184 条消息会被全归到一个叫「wechat_messages」的会话里。
TYPE_KEYS = ["type", "msgType", "MsgType", "messageType", "msg_type"]
DATE_FILE_RE = re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})")
MD_LINE_RE = re.compile(
    r"^\s*[\[【]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\s*[\]】]?"
    r"\s*[-—|]?\s*([^:：\n]{1,40}?)\s*[:：]\s*(.*)$")


def _first_key(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _scale_epoch(v):
    """按量级判断时间戳单位：秒 / 毫秒 / 微秒。"""
    v = float(v)
    if v > 1e14:      # 微秒
        return v / 1e6
    if v > 1e11:      # 毫秒
        return v / 1000.0
    return v          # 秒（2026 年约 1.79e9）


def _as_epoch(value, tz):
    """把各种时间表示转成 epoch 秒（float）。解析不了返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _scale_epoch(value)
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return _scale_epoch(int(s))
    s = s.replace("/", "-").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y-%m-%d %H:%M:%S.%f", "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日 %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=tz).timestamp()
        except ValueError:
            continue
    try:
        parsed = dt.datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.timestamp()
    except ValueError:
        return None


MSG_TYPE_NAMES = {
    1: "text", 3: "image", 34: "voice", 43: "video", 47: "emoji", 42: "card",
    49: "app", 48: "location", 50: "call", 10000: "system", 10002: "recall",
    10005: "quote", 2000: "transfer", 2001: "redpacket",
}


def _msg_type(value):
    if value is None:
        return "text"
    if isinstance(value, (int, float)):
        return MSG_TYPE_NAMES.get(int(value), "type%s" % int(value))
    s = str(value).strip().lower()
    if s in ("text", "image", "voice", "video", "file", "link", "emoji", "system"):
        return s
    if s.isdigit():
        return MSG_TYPE_NAMES.get(int(s), "type%s" % int(s))
    return s


def parse_history_line(text):
    """解析 wxq / 留痕那种 "[2026-11-18 09:12] 张三: 内容" 的文本行。"""
    m = MD_LINE_RE.match(text.strip())
    if not m:
        return None
    time_str, sender, content = m.groups()
    ts = _as_epoch(time_str, None)
    if ts is None:
        return None
    return {"timestamp": ts, "sender": sender.strip() or "未知",
            "content": content.strip(), "type": "text"}


def _looks_like_message(obj):
    if not isinstance(obj, dict):
        return False
    if _first_key(obj, TIME_KEYS) is None:
        return False
    return (_first_key(obj, CONTENT_KEYS) is not None
            or _first_key(obj, SENDER_KEYS) is not None)


def _extract_records(obj, default_chat=None):
    """从任意嵌套结构里把消息记录抠出来，返回 (records, chat_hint)。"""
    records = []
    # container_chat：容器里自带的会话名（比默认名优先）
    container_chat = None

    def walk(node, chat):
        nonlocal container_chat
        if isinstance(node, list):
            for item in node:
                walk(item, chat)
            return
        if isinstance(node, str):
            rec = parse_history_line(node)
            if rec:
                rec["chat_name"] = chat or container_chat or default_chat or "未知会话"
                records.append((rec, chat))
            return
        if not isinstance(node, dict):
            return
        if _looks_like_message(node):
            records.append((node, chat))
            return
        # 会话容器： {"chat": "...", "messages": [...]}
        c = _first_key(node, CHAT_KEYS)
        if c:
            container_chat = container_chat or str(c)
        for key in ("messages", "Messages", "msgList", "list", "data", "records",
                    "chatList", "conversationList"):
            if key in node and isinstance(node[key], (list, dict)):
                walk(node[key], container_chat or chat)
        # 单条消息被包了一层
        for key, value in node.items():
            if key in TIME_KEYS and isinstance(value, (int, float, str)):
                records.append((node, container_chat or chat))
                return

    walk(obj, default_chat)
    return records, container_chat


# ---------------------------------------------------------------- 来源解析

def _chat_from_path(path):
    """从文件路径反推会话名。

    chatlog 的解密输出常见两种结构，都要支持：
      <work-dir>/<会话名>/<日期>.json   -> 会话名 = 父目录名
      <work-dir>/<会话名>.json           -> 会话名 = 文件名（不含扩展名）
      <work-dir>/<会话名>_<日期>.json    -> 会话名 = 日期前那段
    """
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))

    if DATE_FILE_RE.fullmatch(stem):
        # 文件名就是日期，会话名在父目录
        return parent if parent not in ("", "out", "output", "json", "export", "data") else None
    # 「会话名_日期」这种
    m = DATE_FILE_RE.search(stem)
    if m:
        prefix = stem[:m.start()].strip("_- ")
        if prefix:
            return prefix
    return stem or parent or None


def parse_json_file(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return []
    default_chat = _chat_from_path(path)
    records, chat_hint = _extract_records(data, default_chat)
    out = []
    for node, chat in records:
        ts = _as_epoch(_first_key(node, TIME_KEYS), None)
        if ts is None:
            continue
        sender = _first_key(node, SENDER_KEYS)
        content = _first_key(node, CONTENT_KEYS)
        chat_name = _first_key(node, CHAT_KEYS) or chat or default_chat or "未知会话"
        is_self_known = "is_self" in node
        is_self = node.get("is_self")
        if is_self is None:
            is_self = False
        elif isinstance(is_self, str):
            is_self = is_self.strip().lower() in ("1", "true", "yes")
        else:
            is_self = bool(is_self)
        out.append({
            "ts": ts,
            "sender": str(sender) if sender is not None else "未知",
            "is_self": is_self,
            "is_self_known": is_self_known,
            "is_group": bool(node.get("is_group_chat") or node.get("is_group") or False),
            "type": _msg_type(_first_key(node, TYPE_KEYS)),
            "content": "" if content is None else str(content),
            "chat_name": str(chat_name),
        })
    return out


CSV_HEADER_ALIASES = {
    "time": ["发送时间", "时间戳", "时间", "日期", "聊天时间", "消息时间",
             "timestamp", "createTime", "time", "date"],
    "sender": ["发送人昵称", "发送人", "发送者", "对方昵称", "备注名", "昵称", "群昵称",
               "sender", "nickname", "talker", "from"],
    "content": ["消息内容", "内容", "消息正文", "文本内容", "聊天内容", "消息",
                "content", "strContent", "message", "text"],
    "chat": ["会话名称", "聊天对象", "群名称", "群名", "会话", "备注",
             "chat", "talker", "room", "group"],
    "type": ["消息类型", "类型", "type", "msgType"],
}


def _match_column(header, aliases):
    h = (header or "").strip().lower()
    for alias in aliases:
        if alias.lower() == h:
            return header
    for alias in aliases:
        if len(alias) >= 2 and alias.lower() in h:
            return header
    return None


def _detect_columns(header):
    """两轮匹配：先精确匹配表头，再退到包含匹配，避免「备注」这类短名互相抢列。"""
    col = {}
    # 第一轮：精确匹配
    for i, h in enumerate(header):
        for key, aliases in CSV_HEADER_ALIASES.items():
            if key in col:
                continue
            if (h or "").strip().lower() in [a.lower() for a in aliases]:
                col[key] = i
                break
    # 第二轮：包含匹配（取最长别名）
    for i, h in enumerate(header):
        for key, aliases in CSV_HEADER_ALIASES.items():
            if key in col:
                continue
            best = None
            for alias in aliases:
                if len(alias) >= 2 and alias.lower() in (h or "").strip().lower():
                    if best is None or len(alias) > len(best):
                        best = alias
            if best:
                col[key] = i
    return col


def parse_csv_file(path, tz):
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        rows = list(reader)
    if not rows:
        return []
    header = rows[0]
    col = _detect_columns(header)
    if "time" not in col or "content" not in col:
        raise ValueError("CSV 表头没识别出时间/内容列，实际表头: %s" % header)
    out = []
    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        ts = _as_epoch(row[col["time"]] if "time" in col and col["time"] < len(row) else None, tz)
        if ts is None:
            continue
        sender = row[col["sender"]] if "sender" in col and col["sender"] < len(row) else "未知"
        content = row[col["content"]] if "content" in col and col["content"] < len(row) else ""
        chat = row[col["chat"]] if "chat" in col and col["chat"] < len(row) else "未标注会话"
        out.append({
            "ts": ts,
            "sender": sender.strip() or "未知",
            "is_self": False,
            "type": _msg_type(row[col["type"]] if "type" in col and col["type"] < len(row) else None),
            "content": content.strip(),
            "chat_name": chat.strip() or "未标注会话",
        })
    return out


def parse_markdown_file(path, tz):
    """支持 wechat-cli / wxchat-export 的 Markdown 导出：
       [2026-04-17 12:34:56] 张三: 你好
       会话名取自文件名（`张三__wxid_abc.md` -> 张三）。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    chat = stem.split("__")[0].strip() or "未知会话"
    out = []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            m = MD_LINE_RE.match(line)
            if not m:
                continue
            time_str, sender, content = m.groups()
            ts = _as_epoch(time_str, tz)
            if ts is None:
                continue
            out.append({
                "ts": ts,
                "sender": sender.strip() or "未知",
                "is_self": False,
                "type": "text",
                "content": content.strip(),
                "chat_name": chat,
            })
    return out


def parse_file(path, tz):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".json", ".jsonl"):
        if ext == ".jsonl":
            msgs = []
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msgs.extend(parse_json_obj(obj, _chat_from_path(path), tz))
            return msgs
        return parse_json_file(path)
    if ext in (".csv", ".txt"):
        return parse_csv_file(path, tz)
    if ext in (".md", ".markdown"):
        return parse_markdown_file(path, tz)
    return []


def parse_json_obj(obj, default_chat, tz):
    records, _ = _extract_records(obj, default_chat)
    out = []
    for node, chat in records:
        ts = _as_epoch(_first_key(node, TIME_KEYS), tz)
        if ts is None:
            continue
        sender = _first_key(node, SENDER_KEYS)
        content = _first_key(node, CONTENT_KEYS)
        out.append({
            "ts": ts,
            "sender": str(sender) if sender is not None else "未知",
            "is_self": False,
            "is_group": bool(node.get("is_group_chat") or node.get("is_group") or False),
            "type": _msg_type(_first_key(node, TYPE_KEYS)),
            "content": "" if content is None else str(content),
            "chat_name": str(_first_key(node, CHAT_KEYS) or chat or default_chat or "未知会话"),
        })
    return out


DIRECTORY_FILENAME = "wechat_directory.json"


def parse_directory_file(path):
    """解析 chatlog-keeper 的会话名单（directory 子命令的输出）。

    返回 {显示名: {"conversation_type": ..., "message_count": ...}}。
    这份名单包含**所有**会话，包括当天一条消息都没有的——
    白名单群要靠它才能在「今天没人说话」时也出现在日报里。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out_map = {}
    for item in data.get("conversations") or []:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").strip()
        if not label:
            continue
        out_map[label] = {
            "conversation_type": item.get("conversation_type") or "",
            "message_count": item.get("message_count") or 0,
            "conversation_id": item.get("conversation_id") or "",
        }
    return out_map


def collect_directory(sources):
    """在导出目录里找会话名单文件，合并成一份 {显示名: 信息}。"""
    files = []
    for src in sources:
        if os.path.isdir(src):
            for root, _dirs, names in os.walk(src):
                for n in names:
                    if n == DIRECTORY_FILENAME:
                        files.append(os.path.join(root, n))
        elif os.path.isfile(src) and os.path.basename(src) == DIRECTORY_FILENAME:
            files.append(src)
    merged = {}
    for f in sorted(set(files)):
        merged.update(parse_directory_file(f))
    return merged


def collect_sources(sources, tz):
    """sources: 文件或目录；目录递归找 .json/.csv/.txt。"""
    files = []
    for src in sources:
        if os.path.isdir(src):
            for root, _dirs, names in os.walk(src):
                for n in names:
                    if n == DIRECTORY_FILENAME:
                        continue    # 会话名单，不是消息
                    if os.path.splitext(n)[1].lower() in (".json", ".jsonl", ".csv", ".txt", ".md", ".markdown"):
                        files.append(os.path.join(root, n))
        elif os.path.isfile(src):
            files.append(src)
        else:
            sys.stderr.write("[warn] 找不到来源: %s\n" % src)
    msgs = []
    for f in sorted(set(files)):
        try:
            got = parse_file(f, tz)
        except Exception as exc:  # 单个文件坏掉不该拖垮整轮
            sys.stderr.write("[warn] 解析失败 %s: %s\n" % (f, exc))
            continue
        if not got:
            probe = _probe_json(f)
            if probe:
                sys.stderr.write("[warn] %s 解析出 0 条消息。文件结构：%s\n"
                                 "       （字段名不认？把这段发给开发者，加个别名即可）\n"
                                 % (f, probe))
            continue
        msgs.extend(got)
    return msgs


def _probe_json(path, limit=900):
    """文件有内容却一条也没解析出来时，dump 结构，方便定位字段名。"""
    try:
        if os.path.getsize(path) < 20:
            return None
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return None
    if isinstance(data, list):
        sample = data[0] if data else None
        shape = "list[%d]" % len(data)
    elif isinstance(data, dict):
        keys = list(data.keys())[:6]
        sample = data.get(keys[0]) if keys else None
        shape = "dict{%s}" % ",".join(keys)
    else:
        return "type=%s" % type(data).__name__
    if isinstance(sample, dict):
        fields = ", ".join("%s:%s" % (k, type(v).__name__) for k, v in list(sample.items())[:10])
        return "%s first{%s}" % (shape, fields)
    return "%s first=%s" % (shape, str(sample)[:80])


# ---------------------------------------------------------------- 归一化

def _include_match(chat_name, include_chats):
    """白名单匹配：群名包含关键字即命中（大小写不敏感）。

    include_chats 可以是字符串（逗号分隔）或列表。
    """
    if not include_chats:
        return False
    if isinstance(include_chats, str):
        include_chats = [c for c in include_chats.replace("，", ",").split(",")]
    name = (chat_name or "").strip().lower()
    for raw in include_chats:
        key = (raw or "").strip().lower()
        if key and key in name:
            return True
    return False



def date_histogram(msgs, tz):
    """按天统计消息条数：{日期: 条数}。

    用来回答「为什么这一天是 0 条？」——
    是当天真没消息，还是字段没认出来导致一条都没解析出来。
    """
    hist = {}
    for m in msgs:
        try:
            local = dt.datetime.fromtimestamp(m["ts"], tz)
        except Exception:
            continue
        key = local.strftime("%Y-%m-%d")
        hist[key] = hist.get(key, 0) + 1
    return hist


def build_daily(msgs, date_str, tz, tz_name, self_aliases, name_map=None,
                 only_mine=True, context_window=5, include_chats=None,
                 include_max=50, known_chats=None):
    day = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    name_map = name_map or {}
    day_msgs = []
    for m in msgs:
        local = dt.datetime.fromtimestamp(m["ts"], tz)
        if local.date() == day:
            m = dict(m)
            m["local"] = local
            m["chat_name"] = name_map.get(m["chat_name"], m["chat_name"])
            m["sender"] = name_map.get(m["sender"], m["sender"])
            day_msgs.append(m)

    for m in day_msgs:
        # 记录里自带 is_self（chatlog-keeper 就是这样）就信它，别用别名猜
        if not m.get("is_self_known"):
            sender = m["sender"].strip()
            m["is_self"] = bool(sender) and sender in self_aliases

    day_msgs.sort(key=lambda x: x["ts"])

    groups = {}
    for m in day_msgs:
        groups.setdefault(m["chat_name"], []).append(m)

    total_chats = len(groups)
    total_msgs = len(day_msgs)

    # 只留「我参与过」的会话：完全没回过话的群/私聊不进日报
    skipped_chats = 0
    if only_mine:
        keep = {}
        for name, items in groups.items():
            if any(x.get("is_self") for x in items) or _include_match(name, include_chats):
                keep[name] = items
            else:
                skipped_chats += 1
        groups = keep

    chats = []
    kept_msgs = 0
    for chat_name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda x: x["ts"])
        orig_len = len(items)
        self_idx = [i for i, m in enumerate(items) if m.get("is_self")]
        # 以我为中心裁剪上下文：留我发言前后 context_window 条，其余丢弃
        if self_idx and context_window is not None and context_window >= 0:
            keep_idx = set()
            for i in self_idx:
                lo = max(0, i - context_window)
                hi = min(len(items), i + context_window + 1)
                keep_idx.update(range(lo, hi))
            items = [items[i] for i in sorted(keep_idx)]
        elif not self_idx and _include_match(chat_name, include_chats) \
                and include_max is not None and include_max >= 0 \
                and len(items) > include_max:
            # 白名单强制纳入、但我一句都没说 -> 取最近 N 条，别把长群整个灌进模型
            items = items[-include_max:]
        msgs_out = []
        for m in items:
            local = m["local"]
            msgs_out.append({
                "ts": int(m["ts"]),
                "time": local.strftime("%Y-%m-%d %H:%M:%S"),
                "hour": local.hour,
                "sender": m["sender"],
                "is_self": m["is_self"],
                "type": m["type"],
                "content": m["content"],
            })
        kept_msgs += len(msgs_out)
        is_group = any(m.get("is_group") for m in items)
        chats.append({
            "chat_name": chat_name,
            "chat_type": "group" if (is_group or _looks_like_group(chat_name)) else "single",
            "message_count": len(msgs_out),
            "trimmed": len(msgs_out) < orig_len,
            "messages": msgs_out,
        })

    # 白名单群今天一条消息都没有 -> 也列出来（沉默也是信息）
    if include_chats and known_chats:
        seen = {c["chat_name"] for c in chats}
        for label, info in known_chats.items():
            if label in seen or not _include_match(label, include_chats):
                continue
            chats.append({
                "chat_name": label,
                "chat_type": "group" if (info.get("conversation_type") or "").lower()
                             in ("group", "chatroom") else "single",
                "message_count": 0,
                "trimmed": False,
                "silent": True,
                "messages": [],
            })

    hours = [m["local"].hour for m in day_msgs]
    hour_hist = {}
    for h in hours:
        hour_hist[h] = hour_hist.get(h, 0) + 1
    peak = max(hour_hist, key=hour_hist.get) if hour_hist else None

    kept_self = sum(1 for c in chats for m in c["messages"] if m["is_self"])

    return {
        "date": date_str,
        "timezone": tz_name,
        "generated_at": dt.datetime.now(tz).isoformat(timespec="seconds"),
        "stats": {
            "message_count": kept_msgs,
            "message_count_total": total_msgs,
            "chat_count": len(chats),
            "chat_count_total": total_chats,
            "skipped_chat_count": skipped_chats,
            "silent_chat_count": sum(1 for c in chats if c.get("silent")),
            "self_count": kept_self,
            "active_hours": ([dt.datetime.fromtimestamp(m["ts"], tz).strftime("%H:%M")
                              for m in (day_msgs[0], day_msgs[-1])] if day_msgs else []),
            "peak_hour": peak,
        },
        "chats": chats,
    }



def preview_scope(msgs, date_str, tz, tz_name, self_aliases, only_mine=True,
                  context_window=5, name_map=None, include_chats=None,
                  include_max=50, known_chats=None):
    """预览范围过滤会留下什么、跳过什么 —— 和 build_daily 用同一套规则。

    返回每个会话一条：{chat_name, chat_type, total, mine_count, kept, included}
    """
    day = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    name_map = name_map or {}
    day_msgs = []
    for m in msgs:
        local = dt.datetime.fromtimestamp(m["ts"], tz)
        if local.date() != day:
            continue
        m = dict(m)
        m["sender"] = name_map.get(m["sender"], m["sender"])
        m["chat_name"] = name_map.get(m["chat_name"], m["chat_name"])
        if not m.get("is_self_known"):
            sender = (m.get("sender") or "").strip()
            m["is_self"] = bool(sender) and sender in self_aliases
        day_msgs.append(m)

    groups = {}
    for m in day_msgs:
        groups.setdefault(m["chat_name"], []).append(m)

    out = []
    for chat_name, items in groups.items():
        items.sort(key=lambda x: x["ts"])
        mine = sum(1 for m in items if m.get("is_self"))
        whitelisted = _include_match(chat_name, include_chats)
        included = (not only_mine) or mine > 0 or whitelisted
        kept = len(items)
        if mine and context_window is not None and context_window >= 0:
            keep_idx = set()
            for i, m in enumerate(items):
                if m.get("is_self"):
                    keep_idx.update(range(max(0, i - context_window),
                                          min(len(items), i + context_window + 1)))
            kept = len(keep_idx)
        elif (not mine) and whitelisted and include_max is not None \
                and include_max >= 0:
            kept = min(len(items), include_max)
        out.append({
            "chat_name": chat_name,
            "chat_type": "group" if (any(m.get("is_group") for m in items)
                                     or _looks_like_group(chat_name)) else "single",
            "total": len(items),
            "mine_count": mine,
            "kept": kept,
            "included": included,
            "by_whitelist": whitelisted and mine == 0,
        })
    # 白名单群今天完全没人发言 -> 也列出来
    if include_chats and known_chats:
        seen = {r["chat_name"] for r in out}
        for label, info in known_chats.items():
            if label in seen or not _include_match(label, include_chats):
                continue
            out.append({
                "chat_name": label,
                "chat_type": "group" if (info.get("conversation_type") or "").lower()
                             in ("group", "chatroom") else "single",
                "total": 0,
                "mine_count": 0,
                "kept": 0,
                "included": True,
                "by_whitelist": True,
                "silent": True,
            })
    out.sort(key=lambda x: (not x["included"], x.get("silent", False), -x["total"]))
    return out


def _looks_like_group(name):
    n = (name or "").lower()
    if "@chatroom" in n or "group" in n:
        return True
    if n.startswith("群") or n.endswith("群") or "群" in n:
        return True
    return len(name) > 12


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="微信聊天记录归一化")
    ap.add_argument("sources", nargs="+", help="导出文件或目录（chatlog 输出目录 / CSV 导出）")
    ap.add_argument("--date", required=True, help="目标日期 YYYY-MM-DD")
    ap.add_argument("--tz", default="Asia/Shanghai")
    ap.add_argument("--self", default="我,自己,Self,自己本人",
                    help="逗号分隔的自己昵称别名，用于标记 is_self")
    ap.add_argument("--name-map", help="昵称映射 JSON，如 {\"wxid_abc\": \"张三\"}，"
                                       "用于把导出的 wxid 换成备注名")
    ap.add_argument("--out", help="输出文件；缺省打印到 stdout")
    args = ap.parse_args(argv)

    name_map = {}
    if args.name_map:
        with open(args.name_map, "r", encoding="utf-8-sig") as f:
            name_map = json.load(f)

    tz = get_timezone(args.tz)
    aliases = set(a.strip() for a in args.self.split(",") if a.strip())
    msgs = collect_sources(args.sources, tz)
    daily = build_daily(msgs, args.date, tz, args.tz, aliases, name_map)
    text = json.dumps(daily, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("[ok] %s -> %s (%d 条)\n"
                         % (args.date, args.out, daily["stats"]["message_count"]))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
