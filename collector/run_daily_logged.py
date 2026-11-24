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


class Tee(io.TextIOBase):
    """同时写到日志文件**和**原来的控制台。

    计划任务用 pythonw 跑，没有控制台（console 为 None）→ 只进日志文件。
    手动在命令行跑时，屏幕上也照样能实时看到进度 ——
    否则手动跑一次「什么反应都没有」，根本没法判断是跑完了还是卡住了。
    """

    def __init__(self, log, console):
        self._log = log
        self._console = console

    # --- 写 ---
    def write(self, s):
        try:
            self._log.write(s)
        except Exception:
            pass
        if self._console is not None:
            try:
                self._console.write(s)
                self._console.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for f in (self._log, self._console):
            try:
                if f is not None:
                    f.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def fileno(self):
        return self._log.fileno()

    @property
    def encoding(self):
        return "utf-8"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    os.makedirs(ROOT, exist_ok=True)
    log = io.open(LOG_PATH, "a", encoding="utf-8", errors="replace")

    # pythonw.exe 启动的进程里 sys.stdout / sys.stderr 是 None，
    # 所以这里无条件换成日志文件，不能指望原来那两个。
    # 有控制台时用 Tee 兼顾屏幕（手动跑要看得见），没有就只写日志。
    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout = Tee(log, orig_out)
    sys.stderr = Tee(log, orig_err)
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
