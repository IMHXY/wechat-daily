#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wxq 适配器自测：python3 tests/test_export_wxq.py

用假 wxq 命令跑 export_via_wxq.py，再喂给归一化层，确认整条路通。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COLLECTOR = os.path.join(ROOT, "collector")

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def test_export_via_wxq():
    print("\n[用假 wxq 导出]")
    tmp = tempfile.mkdtemp()
    try:
        stub = os.path.join(HERE, "stub_wxq.py")
        out_dir = os.path.join(tmp, "export")
        env = dict(os.environ)
        env["WXQ_CMD"] = "%s %s" % (sys.executable, stub)
        r = subprocess.run(
            [sys.executable, os.path.join(COLLECTOR, "export_via_wxq.py"),
             "--date", "2026-11-18", "--out", out_dir],
            capture_output=True, text=True, env=env, timeout=180)
        check("退出码 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])

        files = [f for f in os.listdir(out_dir)] if os.path.isdir(out_dir) else []
        check("产出 JSON", bool(files), str(files))
        if not files:
            return
        path = os.path.join(out_dir, files[0])
        merged = json.load(open(path, encoding="utf-8"))
        check("合并出 3 条", len(merged) == 3, str(len(merged)))
        chats = sorted({m.get("chat_name") for m in merged})
        check("会话名带上", chats == ["张三", "项目组群"], str(chats))

        # 再喂给归一化层
        sys.path.insert(0, COLLECTOR)
        import normalize
        tz = normalize.get_timezone("Asia/Shanghai")
        msgs = normalize.collect_sources([out_dir], tz)
        daily = normalize.build_daily(msgs, "2026-11-18", tz, "Asia/Shanghai", {"我"}, only_mine=False)
        check("归一化出 3 条", daily["stats"]["message_count"] == 3,
              str(daily["stats"]["message_count"]))
        check("会话名正确",
              sorted(c["chat_name"] for c in daily["chats"]) == ["张三", "项目组群"],
              str([c["chat_name"] for c in daily["chats"]]))
        check("内容正确", "方案我看了" in daily["chats"][0]["messages"][0]["content"],
              daily["chats"][0]["messages"][0]["content"])
    finally:
        shutil.rmtree(tmp)


def test_missing_wxq():
    print("\n[没装 wxq 时报错]")
    tmp = tempfile.mkdtemp()
    try:
        env = dict(os.environ)
        env["WXQ_CMD"] = "wxq_definitely_not_installed_xyz"
        r = subprocess.run(
            [sys.executable, os.path.join(COLLECTOR, "export_via_wxq.py"),
             "--date", "2026-11-18", "--out", tmp],
            capture_output=True, text=True, env=env, timeout=60)
        check("非零退出", r.returncode != 0, str(r.returncode))
        check("提示找不到 wxq", "wxq" in r.stderr, r.stderr[-200:])
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_export_via_wxq()
    test_missing_wxq()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
