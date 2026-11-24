#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键跑完「采集 → 归一化 → 推送」，在你自己的电脑上执行。

用法：
    python3 run_daily.py --source ~/wechat_export --date 2026-11-18

--source 指向微信聊天记录的导出目录（chatlog 输出的 JSON 目录、或留痕导出的 CSV 目录）。
数据会先落到本地 data/<日期>.json，再按配置推到中转存储。
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bridge  # noqa: E402
import normalize  # noqa: E402


def main(argv=None):
    bridge.load_env()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="微信日报数据管道")
    ap.add_argument("--source", required=True, help="导出目录或文件")
    ap.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD，缺省取今天")
    ap.add_argument("--tz", default="Asia/Shanghai")
    ap.add_argument("--self", default="我,自己,Self", help="自己的昵称别名，逗号分隔")
    ap.add_argument("--name-map", help="昵称映射 JSON（wxid -> 备注名）")
    ap.add_argument("--bridge", default="", help="github / feishu / http")
    ap.add_argument("--no-push", action="store_true", help="只落地本地文件，不推送")
    ap.add_argument("--summarize", action="store_true",
                    help="采集完成后接着用本机 API key 生成日报（Windows 计划任务用这个）")
    ap.add_argument("--scope", default=None, choices=["mine", "all"],
                    help="日报范围：mine=只保留我发过言的会话（默认）；all=全部会话")
    ap.add_argument("--context-window", type=int, default=None,
                    help="以我为中心裁剪上下文：保留我发言前后 N 条，0=只留我自己的，-1=不裁剪")
    ap.add_argument("--include", default=None,
                    help="白名单群名（逗号分隔）：不管我有没有说话都进日报")
    args = ap.parse_args(argv)

    tz = normalize.get_timezone(args.tz)
    date_str = args.date or dt.datetime.now(tz).strftime("%Y-%m-%d")

    root = bridge.data_home()
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    local_file = os.path.join(data_dir, date_str + ".json")

    msgs = normalize.collect_sources([args.source], tz)
    aliases = set(a.strip() for a in args.self.split(",") if a.strip())
    name_map = {}
    if args.name_map:
        with open(args.name_map, "r", encoding="utf-8-sig") as f:
            name_map = json.load(f)
    scope = (args.scope or os.environ.get("DAILY_SCOPE") or "mine").strip().lower()
    only_mine = (scope != "all")
    cw_raw = args.context_window
    if cw_raw is None:
        cw_raw = os.environ.get("DAILY_CONTEXT_WINDOW", "5")
    try:
        context_window = int(str(cw_raw).strip())
    except (TypeError, ValueError):
        context_window = 5
    include_chats = (args.include if args.include is not None
                     else os.environ.get("DAILY_INCLUDE_CHATS", ""))
    try:
        include_max = int(os.environ.get("DAILY_INCLUDE_MAX", "50"))
    except (TypeError, ValueError):
        include_max = 50
    print("[1/3] 解析来源: %s" % args.source)
    print("      范围=%s，上下文窗口=%s"
          % ("只保留我参与过的会话" if only_mine else "全部会话",
             "不裁剪" if context_window < 0 else context_window))
    if include_chats.strip():
        print("      白名单群（必进）：%s" % include_chats.strip())
    known_chats = normalize.collect_directory([args.source])
    if known_chats:
        print("      会话名单：%d 个（白名单里今天没人说话的也会列出来）" % len(known_chats))
    daily = normalize.build_daily(msgs, date_str, tz, args.tz, aliases, name_map,
                                   only_mine=only_mine, context_window=context_window,
                                   include_chats=include_chats, include_max=include_max,
                                   known_chats=known_chats)

    with open(local_file, "w", encoding="utf-8") as f:
        json.dump(daily, f, ensure_ascii=False, indent=2)
    print("[2/3] 已写入 %s（%d 条 / %d 个会话）"
          % (local_file, daily["stats"]["message_count"], daily["stats"]["chat_count"]))

    if daily["stats"]["message_count"] == 0:
        # 光说「没解析到消息」没用，要把「导出里到底有哪些日期」摆出来。
        # 常见情况是「早上 8 点跑，当天确实一条都还没有」—— 那不是故障。
        hist = normalize.date_histogram(msgs, tz)
        if hist:
            print("[warn] 这一天（%s）没有消息。" % date_str)
            print("       导出文件里实际有的日期：")
            for day, n in sorted(hist.items()):
                mark = "   ← 就是今天" if day == date_str else ""
                print("         %s   %d 条%s" % (day, n, mark))
            if date_str not in hist:
                print("       → 当天确实还没有消息，等有了再跑就行（不是故障）。")
            else:
                print("       → 有这天的消息却被过滤掉了，多半是「只保留我参与过的会话」"
                      "把它筛没了，可以加 --scope all 看看。")
        else:
            print("[warn] 这一天没有解析到任何消息，检查 --date 和 --source")
        return 1

    if args.no_push:
        print("[3/3] --no-push，跳过推送")
    else:
        kind = (args.bridge or bridge.detect_bridge()).lower()
        if not kind:
            print("[3/3] 未配置中转，文件留在本地（设置 WEIXIN_BRIDGE 后可推送）")
        else:
            print("[3/3] 推送到 %s ..." % kind)
            bridge.do_push(date_str, daily, kind)
            print("[ok] 原始记录已推送")

    if args.summarize:
        print("[4/4] 生成本机日报 ...")
        import summarize_local
        rc = summarize_local.main(["--date", date_str, "--push",
                                   "--bridge", args.bridge] if args.bridge
                                  else ["--date", date_str, "--push"])
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
