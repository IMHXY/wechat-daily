#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑外部命令的公共工具。

核心是 run_stream()：**边跑边打印**子进程的输出。

为什么不能一股脑 capture 起来最后再打印：
    chatlog-keeper 导出要几十秒到几分钟。如果输出全被 capture 住，
    控制台上一个字都不出，看起来像卡死 —— 人一急就按 Ctrl+C，
    导出进程被杀（退出码 0xC000013A），流水线半路断掉，
    日报根本没推上去。2026-11-23 那天的日报就是这么丢的。

run_stream 把子进程的 stdout/stderr 合并成一条流，边读边打，
既不会「看起来像卡死」，也不会因为两条管道互相等死而卡住。
"""

import os
import subprocess
import sys
import threading


def decode_output(raw):
    """把子进程的原始字节解成文本。

    Go 写的 chatlog-keeper 用 UTF-8；cmd.exe 自己的报错（中文 Windows）
    是 GBK。两种都试一遍，都不行就按 UTF-8 替换掉坏字节。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _child_env(utf8_child):
    if not utf8_child:
        return None
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_stream(cmd, cwd=None, timeout=None, utf8_child=False, echo=None):
    """跑子进程并实时打印它的输出。

    返回 (退出码, 合并后的输出, "")。
    超时会杀掉子进程并返回退出码 1。

    读输出放在后台线程里，主线程只管等进程结束 —— 因为
    readline() 是阻塞的，安静的命令（比如 sleep、或者正在解密的大库导出）
    一行都不吐，光靠「读到一行才检查超时」是等不到的。
    """
    if echo is None:
        echo = _default_echo
    echo("  > %s" % cmd)
    proc = subprocess.Popen(cmd, shell=isinstance(cmd, str), cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=_child_env(utf8_child))
    chunks = []

    def _reader():
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = decode_output(raw)
                chunks.append(line)
                echo(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        echo("  [X] 超时")
        return 1, "".join(chunks), ""
    reader.join(timeout=5)
    try:
        proc.stdout.close()
    except OSError:
        pass
    return code, "".join(chunks), ""


def _default_echo(text):
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def describe_exit(code, table=None):
    """把退出码翻译成人话。返回 (说明, 建议)。"""
    table = table or {}
    hit = table.get(code)
    if hit:
        return hit
    return ("导出命令退出码 %d。" % code,
            "常见原因：微信没运行/没登录、密钥没取到、路径不对。")
