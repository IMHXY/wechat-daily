#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows / macOS / Linux 的每日编排入口。

    python collector/daily.py

做三件事：
  1. 读 config.env 里的 EXPORT_CMD，跑一次解密导出（外部工具，命令由你定）
  2. 把导出目录归一化成 data/<日期>.json
  3. 用本机 API key 生成日报，并推到中转

放在 Python 里做编排，是为了让 config.env 的解析和带引号路径的处理只有一份实现，
不用在 batch 里再写一遍（batch 解析含引号路径很容易出错）。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bridge  # noqa: E402
import procutil  # noqa: E402
import run_daily  # noqa: E402
import summarize_local  # noqa: E402

# 导出命令最长允许跑多久（秒）。默认 30 分钟：实测正常导出只要几秒到几分钟，
# 超过半小时基本就是卡住了，等下去只是白白占着这个窗口。
EXPORT_TIMEOUT_DEFAULT = 1800

# Windows 上常见的退出码 -> (说明, 该怎么办)
EXIT_HINTS = {
    # 0xC000013A STATUS_CONTROL_C_EXIT：进程被 Ctrl+C 或关掉控制台窗口打断
    3221225786: (
        "导出进程被中断了（Ctrl+C，或控制台窗口被关掉）。",
        "这不是微信/密钥/路径的问题，是导出跑到一半被杀了。"
        "重跑一次，中途别碰那个窗口；计划任务已经改成无窗口运行，不会再弹窗。"),
    # 0xC0000005 STATUS_ACCESS_VIOLATION：程序崩了
    3221225477: (
        "导出程序崩了（访问冲突）。",
        "常见是微信正在升级、数据库被占用。退出微信再重跑一次。"),
    # cmd.exe：找不到这个命令
    9009: (
        "系统找不到这个命令。",
        "EXPORT_CMD 里的路径写错了，或者那个文件被删了/挪走了。"),
}


def describe_exit(code):
    """把退出码翻译成人话。返回 (说明, 建议) 两行，没有就返回通用文案。"""
    return procutil.describe_exit(code, EXIT_HINTS)


def export_timeout():
    raw = os.environ.get("EXPORT_TIMEOUT", "").strip()
    if not raw:
        return EXPORT_TIMEOUT_DEFAULT
    try:
        val = int(raw)
    except ValueError:
        return EXPORT_TIMEOUT_DEFAULT
    return val if val > 0 else EXPORT_TIMEOUT_DEFAULT


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    argv = list(sys.argv[1:] if argv is None else argv)
    date_str = None
    if "--date" in argv:
        i = argv.index("--date")
        date_str = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]

    bridge.load_env()
    cmd = os.environ.get("EXPORT_CMD", "").strip()
    export_dir = os.environ.get("EXPORT_DIR", "").strip()

    if not cmd:
        sys.stderr.write("[error] config.env 里没有 EXPORT_CMD。\n"
                         "        它是你选用的解密导出工具的命令，详见 README。\n")
        return 2
    if not export_dir:
        sys.stderr.write("[error] config.env 里没有 EXPORT_DIR。\n"
                         "        填成导出工具写文件的那个目录，例如 C:\\wx_export\n")
        return 2

    print("[1/2] 导出当天消息：%s" % cmd)
    timeout = export_timeout()
    try:
        # cwd 固定为项目根，这样 config.env 里可以用相对路径写 EXPORT_CMD，
        # 整个文件夹挪位置也不会失效。
        # 边跑边打印子进程的输出：控制台上能看到实时进度，
        # 不会因为「什么都不出」被误判成卡死然后挨一记 Ctrl+C。
        code, _, _ = procutil.run_stream(cmd, cwd=ROOT, timeout=timeout)
    except Exception as exc:
        sys.stderr.write("[error] 导出命令起不来: %s\n" % exc)
        return 1

    if code != 0:
        why, advice = describe_exit(code)
        sys.stderr.write("[error] %s\n" % why)
        sys.stderr.write("        %s\n" % advice)
        return 1

    print("[2/2] 归一化 + 推送 + 生成本机日报")
    args = ["--source", export_dir, "--summarize"]
    if date_str:
        args += ["--date", date_str]
    rc = run_daily.main(args)
    if rc != 0:
        return rc
    print("[ok] 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
