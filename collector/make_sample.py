#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成一份合成的聊天记录，用来演练和验证整条链路（不涉及真实隐私数据）。

    python3 make_sample.py --date 2026-11-18

产物：sample/<日期>.json（chatlog 风格导出）+ sample/<日期>.daily.json（归一化结果）
"""

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import normalize  # noqa: E402

# (会话目录名, [(时刻, 发送人, 内容)])
#
# 注意：这里的内容是**完全虚构**的日常场景（读书会 / 骑行 / 咖啡店 / 摄影 / 家人），
# 不来自任何真实聊天记录，也不指向任何真实的人或机构。
SAMPLE = {
    "读书会群": [
        ("09:12", "老周", "周六读书会定在下午三点，地点还是图书馆三楼会议室。"),
        ("09:15", "我", "收到，我把上次没读完的那本带过去。"),
        ("09:16", "老周", "好，另外有位新朋友想加入，人数我报给管理员了。"),
        ("09:19", "我", "那我多带两本书，有备用的。"),
        ("09:22", "小林", "请问现场可以带饮品吗？"),
    ],
    "周末骑行群": [
        ("10:30", "阿凯", "这周路线改成 40 公里 环线，起点还是地铁站 B 口。"),
        ("10:31", "我", "行，我报名。"),
        ("10:33", "阿凯", "八点集合，迟到不等。"),
        ("10:35", "我", "明白，我七点半到。"),
        ("10:36", "阿凯", "路上有段逆风，抗不抗得住？"),
        ("10:38", "我", "没问题，慢慢骑。"),
    ],
    "楼下咖啡店": [
        ("14:05", "店员小陈", "您好，明早的豆子换成耶加雪菲了，风味偏花香。"),
        ("14:07", "我", "听起来不错，那我明早试试。"),
        ("14:08", "店员小陈", "好嘞，慢走。"),
    ],
    "摄影爱好者群": [
        ("16:40", "阿凯", "上周拍的片子我挑了 12 张发群里了。"),
        ("16:42", "我", "看到了，第 7 张构图很稳。"),
        ("16:43", "阿凯", "那张其实是在等云散开，等了四十分钟。"),
        ("16:45", "我", "值得。"),
    ],
    "家人群": [
        ("20:12", "妈", "今天回来吃饭吗？"),
        ("20:14", "我", "回，八点到。"),
        ("20:15", "妈", "那我把汤炖上，你少吃点外面的。"),
        ("20:16", "我", "好。"),
    ],
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成合成聊天记录")
    ap.add_argument("--date", default="2026-11-18")
    ap.add_argument("--tz", default="Asia/Shanghai")
    args = ap.parse_args(argv)

    tz = normalize.get_timezone(args.tz)
    day = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
    out_dir = os.path.join(ROOT, "sample", args.date)
    os.makedirs(out_dir, exist_ok=True)

    for chat, items in SAMPLE.items():
        rows = []
        for hhmm, sender, content in items:
            h, m = hhmm.split(":")
            ts = dt.datetime(day.year, day.month, day.day, int(h), int(m),
                             tzinfo=tz).timestamp()
            rows.append({"type": 1, "content": content, "sender": sender, "timestamp": int(ts)})
        path = os.path.join(out_dir, chat, "%s.json" % args.date)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)

    msgs = normalize.collect_sources([out_dir], tz)
    daily = normalize.build_daily(msgs, args.date, tz, args.tz, {"我"})
    daily_path = os.path.join(ROOT, "sample", "%s.daily.json" % args.date)
    with open(daily_path, "w", encoding="utf-8") as f:
        json.dump(daily, f, ensure_ascii=False, indent=2)
    print("[ok] 合成数据: %s（%d 条 / %d 个会话）"
          % (daily_path, daily["stats"]["message_count"], daily["stats"]["chat_count"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
