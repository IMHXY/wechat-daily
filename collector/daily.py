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
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bridge  # noqa: E402
import run_daily  # noqa: E402
import summarize_local  # noqa: E402


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
    try:
        # cwd 固定为项目根，这样 config.env 里可以用相对路径写 EXPORT_CMD，
        # 整个文件夹挪位置也不会失效
        proc = subprocess.run(cmd, shell=True, cwd=ROOT)
    except Exception as exc:
        sys.stderr.write("[error] 导出命令起不来: %s\n" % exc)
        return 1
    if proc.returncode != 0:
        sys.stderr.write("[error] 导出命令退出码 %d。\n"
                         "        常见原因：微信没运行/没登录、密钥没取到、路径不对。\n"
                         % proc.returncode)
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
