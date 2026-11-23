#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装 / 卸 Windows 计划任务：本机每天定时生成日报。

    python collector/install_task.py install       # 装（默认 22:55，无窗口）
    python collector/install_task.py install --time 23:10
    python collector/install_task.py install --visible   # 用 .bat 跑（会弹黑窗口）
    python collector/install_task.py install --s4u       # 额外要求「没登录也能跑」（需管理员）
    python collector/install_task.py status
    python collector/install_task.py uninstall

为什么要有这个：
  平台侧每晚 23:00 去飞书拉日报成品，所以本机必须**先**把日报生成好。
  本机这边就是靠这个计划任务（22:55）跑 daily.bat：
  解密导出 -> 归一化 -> 本机摘要 -> 写飞书。

**默认无窗口**：任务跑的是 pythonw.exe（没有控制台），所以 22:55 不会弹黑窗口。
这一点很关键 —— 弹出来的黑窗口最容易被人误当成卡死，一按 Ctrl+C 就把导出杀了，
2026-11-23 那天的日报就是这么丢的（导出进程退出码 0xC000013A）。

**--s4u**：额外要求「电脑没登录（注销/锁屏）也能跑」。
S4U 任务需要「作为批处理作业登录」权限，**普通用户注册会报 Access is denied**，
所以要用**管理员身份**的 PowerShell 装一次。不加这个参数也能正常出日报，
只是必须处于登录状态。

三种模式：
  默认（pythonw）  不弹窗口，普通权限就能装，需要已登录
  --visible (.bat)  会弹黑窗口，普通权限就能装，需要已登录
  --s4u            不弹窗口，**需要管理员**装，不用登录也行
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
LOG_SCRIPT = os.path.join(HERE, "run_daily_logged.py")
PYTHONW = "pythonw.exe"
LOG = os.path.join(PROJECT, "wechat_daily.log")
TASK_NAME = "WeChatDailyReport"


def out(msg=""):
    print(msg)
    try:
        sys.stdout.flush()
    except Exception:
        pass


def find_pythonw():
    """找 pythonw.exe（和 python.exe 在同一个目录）。

    pythonw 跑起来**没有控制台窗口** —— 这正是我们要的：
    弹出来的黑窗口最容易被人误触 Ctrl+C，一按导出就被打断，
    整条流水线半路死掉（2026-11-23 那天的日报就是这么丢的）。

    返回空字符串表示找不到，调用方退回 .bat 方式。
    """
    try:
        exe = os.path.abspath(sys.executable)
    except Exception:
        return ""
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        return exe
    cand = os.path.join(os.path.dirname(exe), PYTHONW)
    if os.path.isfile(cand):
        return cand
    return ""


def resolve_action(prefer_hidden=True):
    """决定计划任务到底执行什么。

    返回 (command, arguments, 说明)。
    """
    if prefer_hidden:
        pyw = find_pythonw()
        if pyw:
            return (pyw, '"%s"' % LOG_SCRIPT,
                    "pythonw 无窗口运行 run_daily_logged.py（推荐）")
    return (WRAPPER, "", "run_daily_logged.bat（会弹控制台窗口，容易被误按 Ctrl+C）")


def build_task_command(time_str="22:55", task_name=TASK_NAME,
                       command=None, arguments=None):
    """拼出 schtasks 的参数（用列表传参，避开 Windows 引号地狱）。

    这是**兜底**方案。优先用 build_task_xml() —— 走 XML 才能打开
    「错过后补跑」和「电池上也能跑」这两个开关，命令行方式做不到。
    """
    if command is None:
        command, arguments, _ = resolve_action()
    # 参数为空时别留尾随空格（schtasks 对 /tr 的尾部空格比较敏感）
    task_run = '"%s"' % command
    if arguments:
        task_run += " " + arguments
    return [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", task_run,               # 路径带空格也安全
        "/sc", "daily",
        "/st", time_str,             # 24 小时制 HH:MM
        "/f",                        # 已存在就覆盖
    ]


def build_task_xml(time_str="22:55", task_name=TASK_NAME,
                   command=None, arguments=None, logon_type="InteractiveToken"):
    """生成任务计划程序的 XML。

    三个关键开关（schtasks 命令行方式默认是关的）：
      StartWhenAvailable = true  -> 22:55 电脑没开，之后开机也会补跑一次
      DisallowStartIfOnBatteries / StopIfGoingOnBatteries = false
                                 -> 笔记本插不插电都能跑
      LogonType = S4U            -> 额外要求「没登录也能跑」
                                 （注册 S4U 需要管理员权限，普通用户会 Access denied）

    另外 RunOnlyIfIdle = false（不等「空闲」）、WakeToRun = false（不强行唤醒睡眠的电脑）。
    """
    import xml.sax.saxutils as sx
    esc = sx.escape
    today = dt.date.today().isoformat()
    start = "%sT%s:00" % (today, time_str)
    if command is None:
        command, arguments, _ = resolve_action()
    xml = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/2004/02/mit/task">
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
      <Arguments>%s</Arguments>
      <WorkingDirectory>%s</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""" % (esc(start), esc(logon_type), esc(command), esc(arguments or ""), esc(HERE))
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


def install(time_str, prefer_hidden=True, s4u=False):
    if os.name != "nt":
        out("[X] 只能装在 Windows 上")
        return 1
    command, arguments, desc = resolve_action(prefer_hidden)
    if not os.path.isfile(command) and not command.lower().endswith(".exe"):
        out("[X] 找不到 %s" % command)
        return 1
    if s4u and not prefer_hidden:
        out("[!] --s4u 和 --visible 一起用没意义，S4U 本来就为了不弹窗。")
        return 1
    logon_type = "S4U" if s4u else "InteractiveToken"
    out("装计划任务：每天 %s 跑一次" % time_str)
    out("  任务名：%s" % TASK_NAME)
    out("  执行：%s" % desc)
    out("  登录方式：%s" % ("S4U（没登录也能跑）" if s4u else "InteractiveToken（需要已登录）"))
    out("  日志：%s" % LOG)
    out("")

    # 优先走 XML：能打开「错过后补跑」和「电池上也跑」
    code = 1
    xml = build_task_xml(time_str, command=command, arguments=arguments,
                         logon_type=logon_type)
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(xml.encode("utf-16"))
        code = _run(["schtasks", "/create", "/tn", TASK_NAME, "/xml", xml_path, "/f"])
        if code == 0:
            out("")
            out("  已开启：错过后补跑（%s 没开机，之后开机也会补一次）" % time_str)
            out("  已开启：笔记本用电池时也能跑")
    except Exception as exc:
        out("  (XML 方式失败：%s，改用普通方式)" % exc)
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass

    degraded = False
    if code != 0:
        degraded = True
        out("  改用 schtasks 命令行方式 ...")
        code = _run(build_task_command(time_str, command=command, arguments=arguments))
        out("")
        out("  注意：这种方式**错过不补跑**，笔记本在电池上也不会启动。")
        if s4u:
            out("  注意：S4U 没装上，**没登录时跑不了**。")
            out("        要装 S4U 请用**管理员身份**打开 PowerShell 再跑一次。")
    if code == 0:
        out("")
        out("[OK] 装好了。计划任务在「任务计划程序 → 任务计划程序库」里能看到。")
        out("     想立刻试一次，在那个列表里点「运行」。")
        out("")
        # 下面这段必须如实反映**实际生效的方式**，别和降级情况说反
        no_window = prefer_hidden
        if degraded:
            out("  运行前提：电脑开着 + 你已登录（锁屏没关系，注销/睡眠/关机就不行）。")
            if no_window:
                out("  这次是**无窗口**跑的：22:55 不会弹黑窗口。")
            else:
                out("  ⚠️ 这次会**弹黑窗口**：别在窗口上按 Ctrl+C，否则导出会被杀掉。")
        elif s4u:
            out("  运行前提：**电脑开着就行**（不用登录）。睡眠/关机时不会当场跑，")
            out("            但之后开机任务计划程序会补跑一次。")
            out("  这次是**无窗口**跑的：22:55 不会弹黑窗口。")
        else:
            out("  运行前提：电脑开着 + 你已登录（锁屏没关系，注销/睡眠/关机就不行）。")
            if no_window:
                out("  这次是**无窗口**跑的：22:55 不会弹黑窗口，")
                out("  也就没机会误按 Ctrl+C 把导出杀掉。")
            else:
                out("  ⚠️ 这次会**弹黑窗口**：别在窗口上按 Ctrl+C，否则导出会被杀掉。")
        out("  微信不用开着 —— 密钥早就注入好了（除非微信升级过）。")
        out("  网络要通（要连 MiniMax 生成摘要、飞书推结果）。")
    return code


def status():
    out("查询计划任务 %s ..." % TASK_NAME)
    code = _run(["schtasks", "/query", "/tn", TASK_NAME, "/v", "/fo", "list"])
    if code == 0:
        out("")
        command, arguments, desc = resolve_action(True)
        out("现在装的应该是：%s" % desc)
    return code


def uninstall():
    out("删除计划任务 %s ..." % TASK_NAME)
    return _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="装/卸本机的日报计划任务")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_install = sub.add_parser("install", help="安装")
    p_install.add_argument("--time", default="22:55", help="每天几点跑（默认 22:55）")
    p_install.add_argument("--visible", action="store_true",
                            help="用 .bat 方式跑（会弹控制台窗口，容易被误按 Ctrl+C）")
    p_install.add_argument("--s4u", action="store_true",
                            help="额外要求「没登录也能跑」（需管理员权限装）")
    sub.add_parser("status", help="查看状态")
    sub.add_parser("uninstall", help="卸载")
    args = ap.parse_args(argv)

    if args.cmd == "install":
        return install(args.time, prefer_hidden=not args.visible, s4u=args.s4u)
    if args.cmd == "status":
        return status()
    return uninstall()


if __name__ == "__main__":
    sys.exit(main())
