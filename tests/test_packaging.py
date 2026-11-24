#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包层自测：python3 tests/test_packaging.py

防回归：.bat 文件一旦含非 ASCII 字符，Windows cmd（GBK 代码页）就会把
中文读成乱码，乱码里混进的字符还会被 cmd 当成命令分隔符，整段脚本直接崩。
所以这里强制所有 .bat 保持纯 ASCII，中文提示一律放 Python 里。
"""

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def test_bat_ascii():
    print("\n[.bat 必须是纯 ASCII]")
    bat_dir = os.path.join(ROOT, "collector")
    bats = [f for f in os.listdir(bat_dir) if f.lower().endswith(".bat")]
    check("有 .bat 文件", bool(bats), str(bats))
    for name in bats:
        path = os.path.join(bat_dir, name)
        with open(path, "rb") as f:
            raw = f.read()
        non_ascii = [(i, b) for i, b in enumerate(raw) if b > 127]
        check("%s 纯 ASCII" % name, not non_ascii,
              "第 %d 字节有非 ASCII: %s" % (non_ascii[0][0], non_ascii[0][1]) if non_ascii else "")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            continue
        check("%s 设置了 UTF-8 代码页" % name, "chcp 65001" in text,
              "cmd 窗口不切 UTF-8 的话 Python 输出的中文会变乱码")


def test_python_compiles():
    print("\n[Python 文件可编译]")
    bad = []
    tmpdir = tempfile.mkdtemp()
    try:
        for sub in ("collector", "tests"):
            d = os.path.join(ROOT, sub)
            for name in sorted(os.listdir(d)):
                if not name.endswith(".py"):
                    continue
                try:
                    py_compile.compile(os.path.join(d, name), doraise=True,
                                       cfile=os.path.join(tmpdir, name + "c"))
                except py_compile.PyCompileError as exc:
                    bad.append("%s/%s: %s" % (sub, name, exc))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    check("全部编译通过", not bad, "; ".join(bad))


def test_utf8_output():
    print("\n[Python 入口都设了 UTF-8 输出]")
    entries = ["daily.py", "run_daily.py", "summarize_local.py", "export_via_wxq.py",
               "doctor.py", "setup_wxq.py", "bridge.py", "normalize.py"]
    for name in entries:
        path = os.path.join(ROOT, "collector", name)
        if not os.path.isfile(path):
            check("%s 存在" % name, False)
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        check("%s 设置了 reconfigure" % name, "reconfigure" in text)


def test_no_chinese_in_bat():
    print("\n[回归检查：确认旧版问题不复现]")
    # 模拟 cmd 用 GBK 读 UTF-8 bat 的情况：非 ASCII 字节被解释成 GBK 字符
    bat_dir = os.path.join(ROOT, "collector")
    for name in sorted(os.listdir(bat_dir)):
        if not name.lower().endswith(".bat"):
            continue
        with open(os.path.join(bat_dir, name), "rb") as f:
            raw = f.read()
        try:
            raw.decode("ascii")
            ok = True
        except UnicodeDecodeError:
            ok = False
        check("%s 在 GBK 下不会乱码" % name, ok)


def test_zip_contents():
    print("\n[交付包内容]")
    for required in ("README.md", "LICENSE", "config.env.example",
                     "collector/setup_wxq.bat", "collector/setup_wxq.py",
                     "collector/doctor.py", "collector/daily.py",
                     "collector/export_via_wxq.py", "collector/normalize.py",
                     "collector/wechat4.py", "collector/_yara_shim.py",
                     "collector/setup.bat", "collector/install_task.py",
                     "collector/install_task.bat", "collector/run_daily_logged.bat",
                     "collector/run_daily_logged.py", "collector/procutil.py",
                     "collector/set_scope.bat", "collector/include.example.txt",
                     "collector/regen_reports.bat",
                     "tests/test_procutil.py", "tests/test_run_daily_logged.py",
                     "prompts/daily_report.md"):
        check("包含 %s" % required, os.path.isfile(os.path.join(ROOT, required)))
    # data/ 和 reports/ 里放的是**你本机真实产生的**日报和原始记录，
    # 机器上本来就不该是空的 —— 要保证的是它们**不会进仓库**，
    # 而不是目录必须为空。空目录检查放到打包脚本里做。
    ignore_text = ""
    gi = os.path.join(ROOT, ".gitignore")
    if os.path.isfile(gi):
        ignore_text = open(gi, encoding="utf-8").read()
    for d, needle in (("data", "data/"), ("reports", "reports/")):
        check("%s/ 已在 .gitignore 里" % d, needle in ignore_text, ignore_text[:200])


def test_tests_dont_touch_real_reports():
    """测试不能写/删项目里的 data/ 与 reports/。

    以前 test_scope.py 会删掉 reports/<今天的日期>.md，
    用户在真机上跑一次测试，当天真日报就没了。
    现在测试一律用 WEIXIN_HOME 指到临时目录，这里防回退。
    """
    print("\n[测试不碰真实产物]")
    tests_dir = os.path.join(ROOT, "tests")
    bad = []
    needles = ('ROOT, "data"', 'ROOT, "reports"', 'ROOT + "/data"', 'ROOT + "/reports"')
    for name in sorted(os.listdir(tests_dir)):
        if not name.endswith(".py") or name == os.path.basename(__file__):
            continue  # 这个文件自己就带着这些字符串
        path = os.path.join(tests_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        for needle in needles:
            if needle in text:
                bad.append("%s: %s" % (name, needle))
    check("测试不引用项目里的 data//reports/", not bad, "; ".join(bad))

    # 真正跑一次，确认产物落在 WEIXIN_HOME 里而不是项目目录
    import bridge
    tmp = tempfile.mkdtemp()
    try:
        old = os.environ.get("WEIXIN_HOME")
        os.environ["WEIXIN_HOME"] = tmp
        try:
            home = bridge.data_home()
            check("WEIXIN_HOME 生效", home == tmp, home)
        finally:
            if old is None:
                os.environ.pop("WEIXIN_HOME", None)
            else:
                os.environ["WEIXIN_HOME"] = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_license():
    """LICENSE 必须是标准 MIT 全文（只有版权行可以改）。"""
    print("\n[LICENSE]")
    path = os.path.join(ROOT, "LICENSE")
    check("有 LICENSE 文件", os.path.isfile(path))
    if not os.path.isfile(path):
        return
    text = open(path, encoding="utf-8").read()
    for needle, desc in (
        ("MIT License", "标题"),
        ("Permission is hereby granted, free of charge", "授权段"),
        ("without restriction", "使用限制说明"),
        ("The above copyright notice and this permission notice shall be included",
         "保留声明条款"),
        ('THE SOFTWARE IS PROVIDED "AS IS"', "免责条款"),
        ("WITHOUT WARRANTY OF ANY KIND", "无担保条款"),
        ("AUTHORS OR COPYRIGHT HOLDERS BE LIABLE", "责任限制"),
    ):
        check("含%s" % desc, needle in text, text[:120])
    check("有版权行", any(l.startswith("Copyright (c)") for l in text.splitlines()),
          text[:120])


def test_no_leaked_identifiers():
    """隐私守卫：非测试文件里不许出现「像真的」的标识。

    背景：这个项目要公开，仓库里一旦出现真实的 wxid / Windows 用户名 /
    飞书表 id / API key，就等于把个人身份信息发出去。
    这里用「形态」来判定（真实值长度和随机度都和占位符不同），
    检查器本身不包含任何真实值。
    """
    print("\n[隐私守卫]")
    import re as _re
    rules = [
        (r"C:\\Users\\\d", "真实 Windows 用户名（数字目录）"),
        (r"wxid_[a-z0-9]{12,}", "疑似真实微信 wxid"),
        (r"tbl[A-Za-z0-9]{13,}", "疑似真实飞书 table_id"),
        (r"app[A-Za-z0-9]{18,}", "疑似真实飞书 app_token"),
        (r"sk-(?:api|cp)-[A-Za-z0-9]{16,}", "疑似真实 API key"),
        (r"cli_[A-Za-z0-9]{12,}", "疑似真实飞书 app_id"),
        (r"https?://(?!open|xxx|your-tenant)[a-z0-9]{8,}\.feishu\.cn",
         "疑似真实飞书租户域名"),
    ]
    hits = []
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        rel = os.path.relpath(dirpath, ROOT).replace("\\", "/")
        if rel == "tests" or rel.startswith("tests/"):
            continue  # 测试夹具里允许出现短假 id
        for fn in files:
            if not fn.endswith((".py", ".md", ".json", ".example", ".bat", ".sh", ".txt")):
                continue
            path = os.path.join(dirpath, fn)
            try:
                text = open(path, encoding="utf-8").read()
            except Exception:
                continue
            for pat, desc in rules:
                for m in _re.finditer(pat, text):
                    hits.append("%s: %s (%s)" % (os.path.relpath(path, ROOT), desc, m.group(0)))
    check("非测试文件里没有疑似真实标识", not hits, "; ".join(hits[:5]))


def test_gitignore():
    """.gitignore 必须挡住真实配置、日志和运行时产物。"""
    print("\n[.gitignore]")
    path = os.path.join(ROOT, ".gitignore")
    check("有 .gitignore", os.path.isfile(path))
    if not os.path.isfile(path):
        return
    text = open(path, encoding="utf-8").read()
    for needle, desc in (
        ("config.env", "真实配置"),
        ("*.log", "运行日志"),
        ("__pycache__", "Python 缓存"),
        ("data/", "归一化结果"),
        ("reports/", "日报成品"),
    ):
        check("忽略%s" % desc, needle in text, text[:200])


def test_task_command():
    """Windows 计划任务的命令拼装（引号、路径带空格、时间格式）。"""
    print("\n[计划任务命令]")
    sys.path.insert(0, os.path.join(ROOT, "collector"))
    import install_task
    cmd = install_task.build_task_command("22:55")
    check("用 schtasks", cmd[0] == "schtasks", cmd[0])
    check("任务名正确", "/tn" in cmd and install_task.TASK_NAME in cmd, str(cmd))
    check("每天一次", "daily" in cmd, str(cmd))
    check("时间 24 小时制", "22:55" in cmd, str(cmd))
    check("强制覆盖", "/f" in cmd, str(cmd))
    tr = cmd[cmd.index("/tr") + 1]
    check("执行路径带引号（路径有空格也安全）",
          tr.startswith('"') and tr.endswith('"'), tr)
    # 默认走 pythonw（无窗口）；找不到 pythonw 时退回 bat
    check("执行的是无窗口入口（pythonw -> run_daily_logged.py）或 bat 兜底",
          ("run_daily_logged.py" in tr) or ("run_daily_logged.bat" in tr), tr)
    check("wrapper 存在", os.path.isfile(install_task.WRAPPER))
    check("日志路径在项目根", install_task.LOG.endswith("wechat_daily.log"),
          install_task.LOG)


def test_readme_sample_in_sync():
    """README 里展示的日报样例必须和 sample/ 里的逐字一致。

    日报格式改过好几轮，每次都靠手动同步 README 和样例，很容易漏 ——
    这里钉死，省得 README 展示的是旧格式。
    """
    print("\n[README 与样例一致]")
    readme_path = os.path.join(ROOT, "README.md")
    sample_path = os.path.join(ROOT, "sample", "2026-11-18.report.md")
    check("有样例文件", os.path.isfile(sample_path))
    if not os.path.isfile(sample_path):
        return
    readme = open(readme_path, encoding="utf-8").read()
    sample = open(sample_path, encoding="utf-8").read().rstrip("\n")
    i = readme.find("### 5.3")
    check("README 有 5.3 节", i >= 0)
    if i < 0:
        return
    start = readme.find("```markdown", i)
    end = readme.find("\n```", start + 3)
    check("5.3 里有 markdown 代码块", start >= 0 and end > start)
    if start < 0 or end <= start:
        return
    block = readme[start + len("```markdown\n"):end]
    check("样例逐字一致", block == sample,
          "README 和 sample/2026-11-18.report.md 不一致了")


if __name__ == "__main__":
    test_bat_ascii()
    test_python_compiles()
    test_utf8_output()
    test_no_chinese_in_bat()
    test_task_command()
    test_zip_contents()
    test_tests_dont_touch_real_reports()
    test_license()
    test_gitignore()
    test_no_leaked_identifiers()
    test_readme_sample_in_sync()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
