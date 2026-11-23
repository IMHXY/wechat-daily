#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计划任务用的入口：把每天的运行结果写进 wechat_daily.log。

为什么不用 .bat：
    .bat 会弹一个黑色控制台窗口。22:55 的时候你可能顺手 Ctrl+C 一下，
    或者直接把它关了 —— 导出进程当场被杀（退出码 0xC000013A），
    流水线半路断掉，日报根本没推上去。
    2026-11-23 那天的日报就是这么丢的（见 wechat_daily.log 里的 ^C）。

    计划任务改用 pythonw.exe 跑这个脚本就没有窗口，没东西可误触，
    日志照旧写 wechat_daily.log。

用法：
    python collector\\run_daily_logged.py            # 正常跑
    python collector\\run_daily_logged.py --date 2026-11-23
"""

import datetime as dt
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

LOG_PATH = os.environ.get("WEIXIN_LOG_FILE", "").strip() \
    or os.path.join(ROOT, "wechat_daily.log")


def timestamp():
    now = dt.datetime.now()
    return now.strftime("%a %Y/%m/%d %H:%M:%S") + ".%03d" % (now.microsecond // 1000)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    os.makedirs(ROOT, exist_ok=True)
    log = io.open(LOG_PATH, "a", encoding="utf-8", errors="replace")

    # pythonw.exe 启动的进程里 sys.stdout / sys.stderr 是 None，
    # 所以这里无条件换成日志文件，不能指望原来那两个。
    sys.stdout = log
    sys.stderr = log
    try:
        sys.stdin = io.open(os.devnull, "r", encoding="utf-8")
    except OSError:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass

    print("")
    print("==== %s ====" % timestamp())
    log.flush()

    import daily
    try:
        rc = daily.main(argv)
    except Exception as exc:  # 兜底：别让异常把日志截断在半路
        import traceback
        traceback.print_exc()
        print("[error] 任务异常退出: %s" % exc)
        rc = 1
    print("[exit] %d" % rc)
    log.flush()
    try:
        log.close()
    except OSError:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
