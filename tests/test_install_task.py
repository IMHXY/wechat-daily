#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 计划任务的 XML 生成。

要保证四件事：
  1. 22:55 电脑没开机 -> 之后开机也会补跑一次（StartWhenAvailable）
  2. 笔记本用电池时也能跑（DisallowStartIfOnBatteries=false）
  3. 默认**无窗口**（pythonw）：不弹黑窗口，就不会被人误按 Ctrl+C 打断导出
     —— 2026-11-23 的日报就是被误按 Ctrl+C 弄丢的
  4. 想要「没登录也能跑」时能切到 S4U（但 S4U 需要管理员，普通用户会失败）
schtasks 命令行方式默认前两项是关的，所以必须走 XML。
"""
import os
import subprocess
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
        ("<LogonType>InteractiveToken</LogonType>", "默认以登录用户身份跑"),
        ("<DaysInterval>1</DaysInterval>", "每天一次"),
        ("T22:55:00", "触发时间是 22:55"),
    ):
        check(desc, needle in xml, xml[:200])
    check("工作目录是 collector", install_task.HERE in xml)


def test_xml_hidden_mode():
    """默认走 pythonw：没有控制台窗口，不会被人误按 Ctrl+C 打断导出。"""
    print("\n[无窗口模式]")
    xml = install_task.build_task_xml("22:55", command="C:\\py\\pythonw.exe",
                                      arguments='"%s"' % install_task.LOG_SCRIPT)
    check("执行的是 pythonw", "pythonw.exe" in xml, xml[:200])
    check("参数是 run_daily_logged.py", "run_daily_logged.py" in xml, xml[:200])
    check("不再执行 bat", "run_daily_logged.bat" not in xml, xml[:200])
    check("有 Arguments 节点", "<Arguments>" in xml, xml[:200])


def test_xml_visible_mode():
    """--visible 时仍然能退回 bat（老机器兜底）。"""
    print("\n[可见窗口模式（兜底）]")
    xml = install_task.build_task_xml("22:55", command=install_task.WRAPPER,
                                      arguments="")
    check("执行的是 bat", install_task.WRAPPER in xml, xml[:200])


def test_logon_type_switch():
    """--s4u：额外要求「没登录也能跑」（注册 S4U 需要管理员权限）。"""
    print("\n[S4U 开关]")
    xml = install_task.build_task_xml("22:55", logon_type="S4U")
    check("能切到 S4U（不用登录也能跑）", "<LogonType>S4U</LogonType>" in xml, xml[:200])
    check("默认不带 S4U（普通用户就能装）",
          "<LogonType>S4U</LogonType>" not in
          install_task.build_task_xml("22:55"), xml[:200])
    # CLI 开关
    r = subprocess.run([sys.executable, os.path.join(ROOT, "collector", "install_task.py"),
                        "install", "--help"],
                       capture_output=True, text=True)
    check("install 有 --s4u 开关", "--s4u" in r.stdout, r.stdout[:300])
    check("install 有 --visible 开关", "--visible" in r.stdout, r.stdout[:300])
    # --s4u 和 --visible 互斥（说清楚，别让人装出四不像）
    r2 = subprocess.run([sys.executable, os.path.join(ROOT, "collector", "install_task.py"),
                         "install", "--s4u", "--visible"],
                        capture_output=True, text=True)
    check("S4U + visible 会被拦下", r2.returncode != 0, r2.stdout[:200])


def test_resolve_action():
    print("\n[动作解析]")
    cmd, args, desc = install_task.resolve_action(prefer_hidden=False)
    check("不要无窗口时用 bat", cmd == install_task.WRAPPER, cmd)
    check("有说明", bool(desc), desc)
    cmd2, args2, desc2 = install_task.resolve_action(prefer_hidden=True)
    check("两种模式都有说明", bool(desc2), desc2)
    if cmd2 == install_task.WRAPPER:
        check("找不到 pythonw 时退回 bat", args2 == "", args2)
    else:
        check("无窗口模式指向 run_daily_logged.py",
              "run_daily_logged.py" in args2, args2)


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
    test_xml_hidden_mode()
    test_xml_visible_mode()
    test_logon_type_switch()
    test_resolve_action()
    test_xml_time()
    test_cli_fallback_exists()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
