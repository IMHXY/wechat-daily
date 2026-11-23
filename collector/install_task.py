#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装 / 卸 Windows 计划任务：本机每天定时生成日报。

    python collector/install_task.py install      # 装（默认 22:55，后台静默跑，不弹窗）
    python collector/install_task.py install --time 23:10
    python collector/install_task.py install --interactive   # 旧模式：弹黑窗口 + 必须已登录
    python collector/install_task.py status
    python collector/install_task.py uninstall

为什么要有这个：
  平台侧每晚 23:00 去飞书拉日报成品，所以本机必须**先**把日报生成好。
  本机这边就是靠这个计划任务（22:55）跑 daily.bat：
  解密导出 -> 归一化 -> 本机摘要 -> 写飞书。

默认用 S4U 方式注册：任务在后台静默会话里跑，**不会弹黑窗口**，
而且**电脑没登录（注销/锁屏）也能跑**。想退回旧行为加 --interactive。
"""

import argparse
import datetime as dt
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
WRAPPER = os.path.join(HERE, "run_daily_logged.bat")
LOG = os.path.join(PROJECT, "wechat_daily.log")
TASK_NAME = "WeChatDailyReport"


def out(msg=""):
    print(msg)
    try:
        sys.stdout.flush()
    except Exception:
        pass


def build_task_command(time_str="22:55", task_name=TASK_NAME):
    """拼出 schtasks 的参数（用列表传参，避开 Windows 引号地狱）。

    这是**兜底**方案。优先用 build_task_xml() —— 走 XML 才能打开
    「错过后补跑」和「电池上也能跑」这两个开关，命令行方式做不到。
    """
    return [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", '"%s"' % WRAPPER,     # 路径带空格也安全
        "/sc", "daily",
        "/st", time_str,             # 24 小时制 HH:MM
        "/f",                        # 已存在就覆盖
    ]


def build_task_xml(time_str="22:55", task_name=TASK_NAME, logon_type="S4U"):
    """生成任务计划程序的 XML。

    三个关键开关（schtasks 命令行方式默认是关的）：
      StartWhenAvailable = true  -> 22:55 电脑没开，之后开机也会补跑一次
      DisallowStartIfOnBatteries / StopIfGoingOnBatteries = false
                                 -> 笔记本插不插电都能跑
      LogonType = S4U            -> 在后台静默会话里跑：**不弹黑窗口**，
                                    而且**没登录也能跑**

    另外 RunOnlyIfIdle = false（不等「空闲」）、WakeToRun = false（不强行唤醒睡眠的电脑）。

    logon_type 传 "InteractiveToken" 就退回旧行为（弹窗 + 必须已登录）。
    """
    import xml.sax.saxutils as sx
    esc = sx.escape
    today = dt.date.today().isoformat()
    start = "%sT%s:00" % (today, time_str)
    xml = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>%s</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>%s</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Actions Context="Author">
    <Exec>
      <Command>"%s"</Command>
      <Arguments></Arguments>
      <WorkingDirectory>%s</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""" % (esc(start), esc(logon_type), esc(WRAPPER), esc(HERE))
    return xml


def _run(cmd):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        out("[X] 找不到 schtasks（这不是 Windows？）")
        return 1
    out((proc.stdout or "").strip())
    if proc.stderr.strip():
        out((proc.stderr or "").strip())
    return proc.returncode


def install(time_str, interactive=False):
    if os.name != "nt":
        out("[X] 只能装在 Windows 上")
        return 1
    if not os.path.isfile(WRAPPER):
        out("[X] 找不到 %s" % WRAPPER)
        return 1
    logon_type = "InteractiveToken" if interactive else "S4U"
    out("装计划任务：每天 %s 跑一次" % time_str)
    out("  任务名：%s" % TASK_NAME)
    out("  执行：%s" % WRAPPER)
    out("  日志：%s" % LOG)
    out("")

    # 优先走 XML：能打开「错过后补跑」「电池上也跑」「不弹窗」这三个开关
    code = 1
    xml = build_task_xml(time_str, logon_type=logon_type)
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(xml.encode("utf-16"))
        code = _run(["schtasks", "/create", "/tn", TASK_NAME, "/xml", xml_path, "/f"])
        if code == 0:
            out("")
            out("  已开启：错过后补跑（%s 没开机，之后开机也会补一次）" % time_str)
            out("  已开启：笔记本用电池时也能跑")
            if interactive:
                out("  旧模式：运行时**会弹一个黑窗口**，且**必须已登录**才会跑")
            else:
                out("  已开启：**不弹黑窗口**，后台静默跑")
                out("  已开启：**没登录也能跑**（锁屏、注销都不影响）")
    except Exception as exc:
        out("  (XML 方式失败：%s，改用普通方式)" % exc)
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass

    if code != 0:
        out("  改用 schtasks 命令行方式 ...")
        code = _run(build_task_command(time_str))
        out("")
        out("  注意：这种方式**错过不补跑**，笔记本在电池上也不会启动。")
        out("  注意：这种方式**会弹黑窗口**，而且**必须已登录**才会跑。")
    if code == 0:
        out("")
        out("[OK] 装好了。计划任务在「任务计划程序 → 任务计划程序库」里能看到。")
        out("     想立刻试一次，在那个列表里点「运行」。")
        out("")
        if interactive:
            out("  运行前提：电脑开着 + 你已登录（锁屏没关系，注销/睡眠/关机就不行）。")
        else:
            out("  运行前提：**电脑开着就行**（不用登录）。睡眠/关机时不会跑，")
            out("            但之后开机任务计划程序会补跑一次。")
        out("  微信不用开着 —— 密钥早就注入好了（除非微信升级过）。")
        out("  网络要通（要连 MiniMax 生成摘要、飞书推结果）。")
    return code


def status():
    out("查询计划任务 %s ..." % TASK_NAME)
    return _run(["schtasks", "/query", "/tn", TASK_NAME, "/v", "/fo", "list"])


def uninstall():
    out("删除计划任务 %s ..." % TASK_NAME)
    return _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="装/卸本机的日报计划任务")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_install = sub.add_parser("install", help="安装")
    p_install.add_argument("--time", default="22:55", help="每天几点跑（默认 22:55）")
    p_install.add_argument("--interactive", action="store_true",
                            help="退回旧模式：运行时弹黑窗口，且必须已登录才会跑")
    sub.add_parser("status", help="查看状态")
    sub.add_parser("uninstall", help="卸载")
    args = ap.parse_args(argv)

    if args.cmd == "install":
        return install(args.time, interactive=args.interactive)
    if args.cmd == "status":
        return status()
    return uninstall()


if __name__ == "__main__":
    sys.exit(main())
