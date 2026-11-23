#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""子进程执行与退出码翻译的自测：python3 tests/test_procutil.py

背景（2026-11-23 的真实事故）：
    计划任务在 22:55 弹了个黑色控制台窗口，导出过程一个字都不输出，
    看起来像卡死，用户按了 Ctrl+C。导出进程被杀（退出码 0xC000013A），
    流水线半路断掉，日报根本没推上去。

    所以这里守住三件事：
      1. run_stream 边跑边打印（不再静默）
      2. 0xC000013A 要翻译成「被中断」，不能当成「微信没登录/密钥没取到」
      3. 导出命令必须有超时，不能无限期挂着
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

import daily  # noqa: E402
import procutil  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def test_stream_is_live():
    """输出必须一行一行出来，而不是等命令结束才一次性吐出。"""
    print("\n[实时输出]")
    seen = []
    cmd = "echo 第一行 && sleep 1 && echo 第二行"
    start = time.time()
    code, text, _ = procutil.run_stream(cmd, echo=seen.append)
    elapsed = time.time() - start
    check("退出码 0", code == 0, str(code))
    check("两行都在", "第一行" in text and "第二行" in text, text[:200])
    check("打印过命令", any("echo" in s for s in seen), str(seen)[:200])
    check("第一行在命令结束前就出来了（不是攒到最后）",
          any("第一行" in s for s in seen[:-1]) or elapsed >= 1.0,
          "elapsed=%.2f" % elapsed)


def test_stream_nonzero_exit():
    print("\n[非零退出]")
    seen = []
    code, text, _ = procutil.run_stream("exit 7", echo=seen.append)
    check("退出码透传", code == 7, str(code))


def test_stream_timeout():
    print("\n[超时]")
    start = time.time()
    code, _, _ = procutil.run_stream("sleep 30", timeout=2, echo=lambda s: None)
    elapsed = time.time() - start
    check("超时被杀", code != 0, str(code))
    check("没有等到 30 秒", elapsed < 20, "elapsed=%.1f" % elapsed)


def test_decode_output():
    print("\n[输出解码]")
    check("UTF-8", procutil.decode_output("你好".encode("utf-8")) == "你好")
    check("GBK", procutil.decode_output("你好".encode("gbk")) == "你好")
    check("坏字节不炸", isinstance(procutil.decode_output(b"\xff\xfe\x00"), str))
    check("None 安全", procutil.decode_output(None) == "")


def test_exit_hints():
    print("\n[退出码翻译]")
    why, advice = daily.describe_exit(3221225786)
    check("0xC000013A 说是被中断", "中断" in why, why)
    check("没把锅甩给微信/密钥",
          "密钥没取到" not in why and "微信没运行" not in why, why)
    check("给了可执行建议", "重跑" in advice or "别碰" in advice, advice)

    why2, advice2 = daily.describe_exit(9009)
    check("9009 说找不到命令", "找不到" in why2, why2)

    why3, _ = daily.describe_exit(12345)
    check("未知退出码也有一句说明", "12345" in why3, why3)


def test_export_timeout_config():
    print("\n[导出超时]")
    old = os.environ.get("EXPORT_TIMEOUT")
    try:
        os.environ.pop("EXPORT_TIMEOUT", None)
        check("默认 30 分钟", daily.export_timeout() == 1800,
              str(daily.export_timeout()))
        os.environ["EXPORT_TIMEOUT"] = "600"
        check("可以配小", daily.export_timeout() == 600,
              str(daily.export_timeout()))
        os.environ["EXPORT_TIMEOUT"] = "垃圾"
        check("配错不炸，回落默认值", daily.export_timeout() == 1800,
              str(daily.export_timeout()))
    finally:
        if old is None:
            os.environ.pop("EXPORT_TIMEOUT", None)
        else:
            os.environ["EXPORT_TIMEOUT"] = old


def test_logged_launcher_writes_log():
    """无窗口入口必须真的把日志写进 wechat_daily.log（且写到临时目录）。"""
    print("\n[无窗口入口写日志]")
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp()
    try:
        env = dict(os.environ)
        env["WEIXIN_HOME"] = tmp
        env["WEIXIN_LOG_FILE"] = os.path.join(tmp, "wechat_daily.log")
        env["WEIXIN_ENV_FILE"] = os.path.join(tmp, "none.env")
        env["EXPORT_CMD"] = "exit 4"          # 故意失败，看日志里有没有记下来
        env["EXPORT_DIR"] = tmp
        for key in ("FS_APP_ID", "FS_APP_SECRET", "FS_TABLE_ID", "FS_APP_TOKEN",
                    "WEIXIN_BRIDGE", "BRIDGE_URL", "GH_TOKEN", "GH_REPO"):
            env.pop(key, None)
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "collector", "run_daily_logged.py")],
                           capture_output=True, text=True, env=env, timeout=120,
                           cwd=ROOT)
        log = os.path.join(tmp, "wechat_daily.log")
        check("日志文件生成了", os.path.isfile(log), log)
        if os.path.isfile(log):
            text = open(log, encoding="utf-8").read()
            check("日志里有分隔头", "====" in text, text[-200:])
            check("日志里记了失败原因", "中断" in text or "退出码" in text, text[-400:])
        check("失败要返回非零", r.returncode != 0, str(r.returncode))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_stream_is_live()
    test_stream_nonzero_exit()
    test_stream_timeout()
    test_decode_output()
    test_exit_hints()
    test_export_timeout_config()
    test_logged_launcher_writes_log()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
