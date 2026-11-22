#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 计划任务的 XML 生成。

要保证两件事：
  1. 22:55 电脑没开机 -> 之后开机也会补跑一次（StartWhenAvailable）
  2. 笔记本用电池时也能跑（DisallowStartIfOnBatteries=false）
schtasks 命令行方式默认这两项都是关的，所以必须走 XML。
"""
import os
import sys
import xml.dom.minidom as minidom

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

import install_task  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print("  %s %s" % ("ok  " if cond else "FAIL", name))
    if not cond:
        FAILURES.append(name)


def test_xml_shape():
    print("\n[任务 XML]")
    xml = install_task.build_task_xml("22:55")
    check("能解析成合法 XML",
          minidom.parseString(xml.encode("utf-16")) is not None)
    for needle, desc in (
        ("<StartWhenAvailable>true</StartWhenAvailable>", "错过后补跑已开"),
        ("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>", "电池上也能启动"),
        ("<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>", "转电池也不打断"),
        ("<RunOnlyIfIdle>false</RunOnlyIfIdle>", "不等空闲"),
        ("<WakeToRun>false</WakeToRun>", "不强行唤醒睡眠的电脑"),
        ("<LogonType>InteractiveToken</LogonType>", "以登录用户身份跑"),
        ("<DaysInterval>1</DaysInterval>", "每天一次"),
        ("T22:55:00", "触发时间是 22:55"),
    ):
        check(desc, needle in xml, xml[:200])
    check("执行的是包装 bat", install_task.WRAPPER in xml)
    check("工作目录是 collector", install_task.HERE in xml)


def test_xml_time():
    print("\n[触发时间可改]")
    xml = install_task.build_task_xml("23:10")
    check("23:10 写进去了", "T23:10:00" in xml, xml[:200])


def test_cli_fallback_exists():
    print("\n[命令行兜底方案还在]")
    cmd = install_task.build_task_command("22:55")
    check("schtasks", cmd[0] == "schtasks", str(cmd))
    check("/create", "/create" in cmd, str(cmd))
    check("/sc daily", "/sc" in cmd and "daily" in cmd, str(cmd))
    check("/st 时间", "/st" in cmd and "22:55" in cmd, str(cmd))


if __name__ == "__main__":
    test_xml_shape()
    test_xml_time()
    test_cli_fallback_exists()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
