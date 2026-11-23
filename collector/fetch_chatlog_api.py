#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 chatlog 的 HTTP API 采集当天聊天记录。

背景：chatlog decrypt 解密出来的是 .db 文件（MSG0.db / MicroMsg.db），
聊天记录本身要通过它起的 HTTP 服务读：

    GET /api/v1/contact?format=json      联系人
    GET /api/v1/chatroom?format=json      群聊
    GET /api/v1/chatlog?time=...&talker=...&format=json   消息

本模块负责：必要时拉起 chatlog server -> 列出所有会话 -> 逐个拉当天消息 ->
归一化成 data/<日期>.json。

只用标准库，Python 3.7+ 可跑。
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import normalize  # noqa: E402
import bridge  # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:5030"
TIMEOUT = 60

SENDER_KEYS_API = ["sender", "senderName", "sender_name", "sender_wxid", "fromUser",
                   "from", "nickname", "nickName", "displayName", "DisplayName",
                   "remark", "alias"]
CONTENT_KEYS_API = ["content", "Content", "StrContent", "strContent", "message",
                    "text", "msg", "MsgContent"]
TIME_KEYS_API = ["timestamp", "ts", "time", "createTime", "CreateTime",
                 "localCreateTime", "LocalCreateTime", "msgTime", "sendTime", "date"]
TYPE_KEYS_API = ["type", "msgType", "MsgType", "messageType", "typeId"]


def _pick(d, keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, ""):
            return d[k]
    return None


# ---------------------------------------------------------------- HTTP

def api_get(base_url, path, params=None, timeout=TIMEOUT):
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError("chatlog API %s 返回 %s" % (path, e.code))
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("chatlog API %s 返回的不是 JSON：%s" % (path, raw[:200]))


def _as_list(payload):
    """API 可能返回数组，也可能返回 {"data": [...]} / {"messages": [...]}。"""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "messages", "list", "records", "items", "result"):
            if isinstance(payload.get(key), list):
                return payload[key]
            if isinstance(payload.get(key), dict):
                nested = _as_list(payload[key])
                if nested:
                    return nested
    return []


def server_alive(base_url):
    try:
        api_get(base_url, "/api/v1/contact", {"format": "json"}, timeout=5)
        return True
    except Exception:
        return False


def start_server(base_url, data_dir=None, data_key=None, work_dir=None, version=None):
    """拉起 chatlog server，等它就绪。返回 (进程, 是否是我们拉起的)。"""
    if server_alive(base_url):
        print("[chatlog] 复用已在运行的服务 %s" % base_url)
        return None, False

    if not shutil.which("chatlog"):
        raise RuntimeError("找不到 chatlog 可执行文件，且 %s 上没有在跑的服务" % base_url)

    cmd = ["chatlog", "server", "--http-addr", base_url.split("://")[-1]]
    for flag, value in (("--data-dir", data_dir), ("--data-key", data_key),
                        ("--work-dir", work_dir), ("--version", version)):
        if value:
            cmd += [flag, str(value)]
    print("[chatlog] 启动: %s" % " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(60):
        time.sleep(1)
        if server_alive(base_url):
            print("[chatlog] 服务就绪")
            return proc, True
        if proc.poll() is not None:
            raise RuntimeError("chatlog server 启动即退出（exit %s）" % proc.returncode)
    proc.terminate()
    raise RuntimeError("chatlog server 60 秒内没就绪")


# ---------------------------------------------------------------- 会话与消息

def list_talkers(base_url):
    """返回 [(talker_id, 显示名)]，联系人 + 群聊。"""
    talkers = []
    seen = set()

    for path, id_keys, name_keys in (
            ("/api/v1/contact", ["wxid", "username", "id", "talker", "userName"],
             ["nickname", "remark", "name", "displayName", "nickName"]),
            ("/api/v1/chatroom", ["chatroom", "id", "username", "talker", "wxid"],
             ["name", "nickname", "displayName", "remark"])):
        try:
            items = _as_list(api_get(base_url, path, {"format": "json"}))
        except Exception as exc:
            print("[chatlog] %s 拉取失败：%s（跳过）" % (path, exc))
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            tid = _pick(item, id_keys)
            if not tid or str(tid) in seen:
                continue
            seen.add(str(tid))
            name = _pick(item, name_keys) or str(tid)
            talkers.append((str(tid), str(name)))
    return talkers


def fetch_messages(base_url, talker, date_str, limit=1000):
    """拉某个会话在 date_str 当天的消息，返回原始 dict 列表。"""
    out = []
    offset = 0
    while True:
        params = {"time": "%s~%s" % (date_str, date_str), "talker": talker,
                  "limit": limit, "offset": offset, "format": "json", "sort": "asc"}
        payload = api_get(base_url, "/api/v1/chatlog", params)
        items = _as_list(payload)
        if not items:
            break
        out.extend(items)
        if len(items) < limit:
            break
        offset += len(items)
        if offset > 200000:
            break
    return out


def parse_messages(raw_items, talker_id, talker_name, tz):
    """把 API 原始消息转成归一化层的记录。"""
    msgs = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        # API 可能把消息包在 {"msg": {...}} 里
        inner = item.get("msg") if isinstance(item.get("msg"), dict) else None
        node = inner or item

        ts = normalize._as_epoch(_pick(node, TIME_KEYS_API), tz)
        if ts is None:
            continue
        sender = _pick(node, SENDER_KEYS_API)
        content = _pick(node, CONTENT_KEYS_API)
        msgs.append({
            "ts": ts,
            "sender": str(sender) if sender is not None else talker_name,
            "is_self": False,
            "type": normalize._msg_type(_pick(node, TYPE_KEYS_API)),
            "content": "" if content is None else str(content),
            "chat_name": talker_name,
        })
    return msgs


def main(argv=None):
    ap = argparse.ArgumentParser(description="通过 chatlog API 采集微信聊天记录")
    ap.add_argument("--date", default=None, help="目标日期，缺省取今天")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--data-dir", help="微信数据目录（要起服务时用）")
    ap.add_argument("--data-key", help="数据库密钥（要起服务时用）")
    ap.add_argument("--work-dir", help="解密后的 db 目录（要起服务时用）")
    ap.add_argument("--version", help="微信版本，3 或 4")
    ap.add_argument("--tz", default="Asia/Shanghai")
    ap.add_argument("--self", default="我,自己,Self")
    ap.add_argument("--scope", default=None, choices=["mine", "all"],
                    help="日报范围：mine=只保留我发过言的会话（默认）；all=全部")
    ap.add_argument("--name-map", help="wxid -> 备注名 的 JSON")
    ap.add_argument("--out", help="输出文件；缺省 data/<date>.json")
    ap.add_argument("--keep-server", action="store_true", help="结束后不关掉服务")
    args = ap.parse_args(argv)

    tz = normalize.get_timezone(args.tz)
    date_str = args.date or dt.datetime.now(tz).strftime("%Y-%m-%d")
    proc = None
    started = False
    try:
        proc, started = start_server(args.base_url, args.data_dir, args.data_key,
                                     args.work_dir, args.version)
        talkers = list_talkers(args.base_url)
        if not talkers:
            raise RuntimeError("没列出会话：chatlog 的联系人/群聊接口返回空。"
                               "确认微信已登录，且解密出的 db 在 --work-dir 里")
        print("[chatlog] %d 个会话，开始拉取 %s" % (len(talkers), date_str))

        all_msgs = []
        for tid, name in talkers:
            try:
                raw = fetch_messages(args.base_url, tid, date_str)
            except Exception as exc:
                print("[chatlog] %s 拉取失败：%s" % (name, exc))
                continue
            if not raw:
                continue
            all_msgs.extend(parse_messages(raw, tid, name, tz))
        if not all_msgs:
            raise RuntimeError("拉到了 %d 个会话但当天没有解析出消息。"
                               "可能是日期不对，或 API 返回的字段名与预期不同" % len(talkers))

        aliases = set(a.strip() for a in args.self.split(",") if a.strip())
        name_map = {}
        if args.name_map:
            with open(args.name_map, "r", encoding="utf-8-sig") as f:
                name_map = json.load(f)
        scope = (args.scope or os.environ.get("DAILY_SCOPE") or "mine").strip().lower()
        only_mine = (scope != "all")
        try:
            context_window = int(os.environ.get("DAILY_CONTEXT_WINDOW", "5"))
        except (TypeError, ValueError):
            context_window = 5
        include_chats = os.environ.get("DAILY_INCLUDE_CHATS", "")
        try:
            include_max = int(os.environ.get("DAILY_INCLUDE_MAX", "50"))
        except (TypeError, ValueError):
            include_max = 50
        known_chats = normalize.collect_directory([args.out_dir] if getattr(args, "out_dir", None) else [])
        daily = normalize.build_daily(all_msgs, date_str, tz, args.tz, aliases, name_map,
                                      only_mine=only_mine, context_window=context_window,
                                      include_chats=include_chats, include_max=include_max,
                                      known_chats=known_chats)

        out = args.out or os.path.join(bridge.data_home(), "data", date_str + ".json")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(daily, f, ensure_ascii=False, indent=2)
        print("[ok] %s -> %s（%d 条 / %d 个会话）"
              % (date_str, out, daily["stats"]["message_count"], daily["stats"]["chat_count"]))
        return 0
    except Exception as exc:
        sys.stderr.write("[error] %s\n" % exc)
        return 1
    finally:
        if proc is not None and started and not args.keep_server:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
