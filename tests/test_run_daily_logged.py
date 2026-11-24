#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计划任务入口 run_daily_logged.py 的输出行为。

要点：输出要**同时**进日志文件和屏幕。

  - 计划任务用 pythonw 跑，没有控制台（console 为 None）→ 只进日志文件，
    这样任务计划程序的历史记录里能看到全部过程，出问题能查。
  - 手动在命令行跑时，屏幕上也照样实时滚动 —— 否则手动跑一次「一点反应都没有」，
    分不清是跑完了还是卡住了（2026-11-24 演练时踩到）。
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

import run_daily_logged  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print("  %s %s" % ("ok  " if cond else "FAIL", name))
    if not cond:
        FAILURES.append(name)


def test_tee_writes_both():
    print("\n[同时写屏幕和日志]")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    tmp.close()
    console = io.StringIO()
    try:
        with io.open(tmp.name, "a", encoding="utf-8") as log:
            tee = run_daily_logged.Tee(log, console)
            tee.write("第一行\n")
            tee.write("第二行\n")
            tee.flush()
        with io.open(tmp.name, encoding="utf-8") as f:
            log_text = f.read()
    finally:
        os.unlink(tmp.name)
    check("日志里有内容", "第一行" in log_text and "第二行" in log_text, log_text[:100])
    check("屏幕上也同步了", "第一行" in console.getvalue(), console.getvalue()[:100])
    check("两边内容一致",
          console.getvalue().replace("\r\n", "\n") == log_text.replace("\r\n", "\n"),
          "console=%r log=%r" % (console.getvalue()[:80], log_text[:80]))


def test_tee_without_console():
    """pythonw 下没有控制台，不能因此报错。"""
    print("\n[没有控制台（pythonw）]")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    tmp.close()
    try:
        with io.open(tmp.name, "a", encoding="utf-8") as log:
            tee = run_daily_logged.Tee(log, None)
            tee.write("只有日志\n")
            tee.flush()
        with io.open(tmp.name, encoding="utf-8") as f:
            text = f.read()
    finally:
        os.unlink(tmp.name)
    check("照样写进日志", "只有日志" in text, text[:100])
    check("write 返回长度（print 依赖返回值）",
          tee.write("x") == 1, "write 没返回长度")


def test_tee_is_not_tty():
    """isatty 必须是 False —— 否则某些库会去做彩色/交互处理。"""
    print("\n[非 TTY]")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    tmp.close()
    console = io.StringIO()
    try:
        with io.open(tmp.name, "a", encoding="utf-8") as log:
            tee = run_daily_logged.Tee(log, console)
            check("isatty 是 False", tee.isatty() is False, str(tee.isatty()))
            check("encoding 是 utf-8", tee.encoding == "utf-8", tee.encoding)
            check("有 fileno（有些库要）", isinstance(tee.fileno(), int), str(tee.fileno()))
    finally:
        os.unlink(tmp.name)


def test_log_path():
    print("\n[日志路径]")
    check("默认写到项目根的 wechat_daily.log",
          run_daily_logged.LOG_PATH.endswith("wechat_daily.log"),
          run_daily_logged.LOG_PATH)
    check("可以用 WEIXIN_LOG_FILE 改",
          "WEIXIN_LOG_FILE" in open(
              os.path.join(ROOT, "collector", "run_daily_logged.py"),
              encoding="utf-8").read(), "")


if __name__ == "__main__":
    test_tee_writes_both()
    test_tee_without_console()
    test_tee_is_not_tty()
    test_log_path()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
