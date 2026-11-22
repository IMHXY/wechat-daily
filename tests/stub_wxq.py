#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""假的 wxq 命令，用来验证 export_via_wxq.py。

    python tests/stub_wxq.py sessions --format json
    python tests/stub_wxq.py history 张三 --start-time ... --format json
"""

import json
import sys

# 真实 wxq 的 sessions --format json 输出
SESSIONS = [
    {"username": "wxid_a", "chat": "张三", "is_group": False, "unread": 0,
     "last_message": "方案我看了", "msg_type": "文本", "timestamp": 1794963600,
     "time_str": "2026-11-18 09:12"},
    {"username": "@chatroom_x", "chat": "项目组群", "is_group": True, "unread": 2,
     "last_message": "数据更新完了", "msg_type": "文本", "timestamp": 1794967200,
     "time_str": "2026-11-18 10:00"},
]

# 真实 wxq 的 history --format json 输出：messages 是 "[时间] 发送者: 内容" 字符串行
HISTORY = {
    "张三": [
        "[2026-11-18 09:12] 张三: 方案我看了",
        "[2026-11-18 09:25] 我: 好，下午给你",
    ],
    "项目组群": [
        "[2026-11-18 10:00] 李四: 数据更新完了",
    ],
}


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: stub_wxq.py <子命令> [参数]", file=sys.stderr)
        return 2
    cmd = args[0]
    if cmd == "sessions":
        print(json.dumps(SESSIONS, ensure_ascii=False))
        return 0
    if cmd == "history":
        if len(args) < 2:
            print("缺少会话名", file=sys.stderr)
            return 2
        name = args[1]
        rows = HISTORY.get(name, [])
        print(json.dumps({
            "chat": name,
            "username": "wxid_x",
            "is_group": name.startswith("项目"),
            "count": len(rows),
            "offset": 0,
            "limit": 100,
            "start_time": None,
            "end_time": None,
            "type": None,
            "messages": rows,
            "failures": None,
        }, ensure_ascii=False))
        return 0
    print("未知命令", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
