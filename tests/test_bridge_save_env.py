#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge.save_env：就地改配置，保留注释和其它值。"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "collector"))

import bridge  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print("  %s %s" % ("ok  " if cond else "FAIL", name))
    if not cond:
        FAILURES.append(name)


def test_save_env():
    print("\n[save_env 写配置]")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "config.env")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 注释要保留\nLLM_API_KEY=abc\nLLM_BASE_URL=https://api.minimax.cn/v1\n")
        bridge.save_env("LLM_BASE_URL", "https://api.minimax.io/v1", path=path)
        text = open(path, encoding="utf-8").read()
        check("值改掉了", "LLM_BASE_URL=https://api.minimax.io/v1" in text, text)
        check("注释保留", "# 注释要保留" in text, text)
        check("别的键没动", "LLM_API_KEY=abc" in text, text)
        # 新键追加
        bridge.save_env("LLM_MODEL", "MiniMax-M3", path=path)
        text2 = open(path, encoding="utf-8").read()
        check("新键追加了", "LLM_MODEL=MiniMax-M3" in text2, text2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_save_env()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
