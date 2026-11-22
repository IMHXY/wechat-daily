#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 wxq（微信本地记录查询工具）的输出转成统一格式。

用法：
    wxq init                      # 先取一次密钥（要管理员权限，微信得开着）
    python export_via_wxq.py --date 2026-11-21

它做三件事：
  1. 问 wxq 有哪些会话（wxq sessions --format json）
  2. 逐个会话问当天说了什么（wxq history "<会话>" --start-time ... --end-time ...）
  3. 合并成一个 JSON 数组写到导出目录，后面的归一化层就能吃

wxq 的 JSON 字段名如果和预期对不上，这里会打印实际结构，方便直接加别名。
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import normalize  # noqa: E402

# 注意：wxq 的 sessions 里 "chat" 是显示名，"username" 是 wxid，顺序不能反
NAME_KEYS = ["chat", "name", "display_name", "displayName", "nickname", "nickName",
             "remark", "session_name", "sessionName", "talkerName", "title",
             "chat_name", "chatName", "talker", "username", "id", "wxid"]


def run_wxq(args, timeout=180):
    exe = os.environ.get("WXQ_CMD", "wxq")
    cmd = exe.split() + args if " " in exe else [exe] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("%s 退出码 %d：%s"
                           % (" ".join(cmd), proc.returncode,
                              (proc.stderr or proc.stdout)[-300:]))
    raw = proc.stdout.strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("wxq 输出不是 JSON：%s" % raw[:300])


def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "messages", "sessions", "list", "records", "items", "result"):
            if isinstance(payload.get(key), list):
                return payload[key]
            if isinstance(payload.get(key), dict):
                nested = as_list(payload[key])
                if nested:
                    return nested
    return []


def conversation_name(item, index):
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return "会话%d" % index
    for key in NAME_KEYS:
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # 实在认不出就退回任意一个像名字的字符串字段
    for val in item.values():
        if isinstance(val, str) and val.strip() and not val.startswith("wxid_"):
            return val.strip()
    return "会话%d" % index



# 微信 4.1.10.31+ 的密钥是 XOR 混淆态，wxq 扫不到（只找明文 keyspec）。
NEW_KEY_API = (4, 1, 10, 31)
_VER_RE = re.compile(r"(\d+(?:\.\d+)*)")


def _wx_version():
    """尽力读一下微信主程序版本；读不到返回 None。"""
    cands = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        root = os.environ.get(env)
        if root:
            cands.append(os.path.join(root, "Tencent", "Weixin", "Weixin.exe"))
    for exe in cands:
        if not os.path.isfile(exe):
            continue
        ps = ("$ErrorActionPreference='Stop';"
              "(Get-Item -LiteralPath '%s').VersionInfo.ProductVersion"
              % exe.replace("'", "''"))
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                  capture_output=True, text=True, timeout=60)
        except Exception:
            continue
        if proc.returncode == 0:
            lines = (proc.stdout or "").strip().splitlines()
            if lines:
                m = _VER_RE.match(lines[-1].strip())
                if m:
                    return m.group(1)
    return None


def _too_new(ver):
    parts = [int(x) for x in ver.split(".")]
    parts += [0] * (4 - len(parts))
    return tuple(parts[:4]) >= NEW_KEY_API


def main(argv=None):
    ver = _wx_version()
    if ver and _too_new(ver):
        print("[X] 微信版本 %s 不支持 wxq（4.1.10.31+ 密钥是 XOR 混淆态，wxq 只找明文）。" % ver)
        print("    请改用 collector\\wechat4.py export（跑一次 setup.bat）。")
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="用 wxq 导出当天聊天记录")
    ap.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD，缺省今天")
    ap.add_argument("--out", help="导出目录；缺省用 config.env 的 EXPORT_DIR")
    ap.add_argument("--tz", default="Asia/Shanghai")
    args = ap.parse_args(argv)

    tz = normalize.get_timezone(args.tz)
    date_str = args.date or dt.datetime.now(tz).strftime("%Y-%m-%d")

    out_dir = args.out or os.environ.get("EXPORT_DIR", "").strip()
    if not out_dir:
        sys.stderr.write("[error] 没有指定导出目录：加 --out 或在 config.env 里设 EXPORT_DIR\n")
        return 2

    if not shutil.which("wxq") and " " not in os.environ.get("WXQ_CMD", ""):
        sys.stderr.write("[error] 找不到 wxq 命令。装好之后把它的可执行路径写进 config.env 的 WXQ_CMD\n")
        return 2

    print("[1/3] 读取会话列表 ...")
    try:
        sessions = as_list(run_wxq(["sessions", "--format", "json"]))
    except Exception as exc:
        sys.stderr.write("[error] %s\n" % exc)
        sys.stderr.write("       微信开着并登录了吗？先跑一次 wxq init 取密钥。\n")
        return 1
    if not sessions:
        sys.stderr.write("[error] wxq 没列出会话。先确认微信已登录且本机有聊天记录。\n")
        return 1
    print("       %d 个会话" % len(sessions))

    print("[2/3] 逐个拉取 %s 的消息 ..." % date_str)
    merged = []
    failed = 0
    for i, item in enumerate(sessions, 1):
        name = conversation_name(item, i)
        try:
            raw = run_wxq(["history", name, "--start-time", "%s 00:00:00" % date_str,
                           "--end-time", "%s 23:59:59" % date_str, "--format", "json"])
        except Exception as exc:
            failed += 1
            if failed <= 3:
                sys.stderr.write("[warn] %s 拉取失败：%s\n" % (name, exc))
            continue
        records, _ = normalize._extract_records(raw, name)
        for node, _chat in records:
            if not isinstance(node, dict):
                continue
            rec = dict(node)
            rec.setdefault("chat_name", name)
            rec["chat_name"] = name
            merged.append(rec)
        if i % 20 == 0:
            print("       %d/%d 个会话，已取 %d 条" % (i, len(sessions), len(merged)))

    print("[3/3] 合并 %d 条消息" % len(merged))
    if not merged:
        probe = None
        if sessions:
            try:
                first = as_list(run_wxq(["history", conversation_name(sessions[0], 1),
                                         "--start-time", "%s 00:00:00" % date_str,
                                         "--end-time", "%s 23:59:59" % date_str,
                                         "--format", "json"]))
                if first and isinstance(first[0], dict):
                    probe = ", ".join("%s:%s" % (k, type(v).__name__)
                                      for k, v in list(first[0].items())[:10])
            except Exception:
                pass
        sys.stderr.write("[error] 一条消息都没取到。%s\n"
                         % ("wxq 返回的字段是：%s（把这段发我，加个别名就行）" % probe
                            if probe else "确认当天确实有聊天记录。"))
        return 1

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "wxq_%s.json" % date_str)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False)
    print("[ok] %s（%d 条）" % (path, len(merged)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
