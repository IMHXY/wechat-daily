#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编排层自测：python3 tests/test_daily.py

用假导出命令验证 daily.py 的三步：跑导出命令 -> 归一化 -> 出日报（假模型）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COLLECTOR = os.path.join(ROOT, "collector")

FAILURES = []



def _clear_feishu(env):
    """把真实飞书凭据从子进程环境里摘掉。

    平台上配了 FS_APP_ID / FS_APP_SECRET secret（注入到环境变量），
    不清掉的话，测试会走真实飞书分支而不是 mock。
    """
    for key in ("FS_APP_ID", "FS_APP_SECRET", "FS_TABLE_ID", "FS_APP_TOKEN",
                "WEIXIN_BRIDGE", "BRIDGE_URL", "GH_TOKEN", "GH_REPO"):
        env.pop(key, None)


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def test_missing_export_cmd():
    print("\n[缺 EXPORT_CMD 时报错]")
    tmp = tempfile.mkdtemp()
    try:
        env = dict(os.environ)
        env.pop("EXPORT_CMD", None)
        _clear_feishu(env)
        env["WEIXIN_ENV_FILE"] = os.path.join(tmp, "nonexistent.env")
        r = subprocess.run([sys.executable, os.path.join(COLLECTOR, "daily.py")],
                           capture_output=True, text=True, env=env, timeout=60)
        check("非零退出", r.returncode != 0, str(r.returncode))
        check("提示缺 EXPORT_CMD", "EXPORT_CMD" in r.stderr, r.stderr[-200:])
    finally:
        shutil.rmtree(tmp)


def test_export_command_failure():
    print("\n[导出命令失败时中止]")
    tmp = tempfile.mkdtemp()
    try:
        env = dict(os.environ)
        _clear_feishu(env)
        env["WEIXIN_ENV_FILE"] = os.path.join(tmp, "x.env")
        env["EXPORT_CMD"] = "exit 3"
        env["EXPORT_DIR"] = tmp
        r = subprocess.run([sys.executable, os.path.join(COLLECTOR, "daily.py")],
                           capture_output=True, text=True, env=env, timeout=60)
        check("非零退出", r.returncode != 0)
        check("提示导出失败", "导出命令退出码" in r.stderr, r.stderr[-200:])
    finally:
        shutil.rmtree(tmp)


def test_full_orchestration():
    print("\n[完整编排：导出 -> 归一化 -> 摘要]")
    port = 8799
    mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py"),
                             "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import time
        import urllib.request
        ready = False
        for _ in range(50):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/requests" % port, timeout=1).read()
                ready = True
                break
            except Exception:
                time.sleep(0.1)
        check("mock 服务启动", ready)
        if not ready:
            return

        tmp = tempfile.mkdtemp()
        try:
            outdir = os.path.join(tmp, "export")
            os.makedirs(outdir)
            env_file = os.path.join(tmp, "config.env")
            # 假导出命令：把一条消息写成 chatlog-keeper 风格的 JSON
            writer = os.path.join(tmp, "fake_export.py")
            with open(writer, "w", encoding="utf-8") as f:
                f.write(
                    "import json, sys, os\n"
                    "out = sys.argv[1]\n"
                    "os.makedirs(out, exist_ok=True)\n"
                    "json.dump([{'timestamp': '2026-11-18T09:12:00', 'sender': 'wxid_a',\n"
                    "  'sender_display_name': '我', 'content': '方案我看了',\n"
                    "  'chat_name': 'wxid_a', 'chat_display_name': '张三',\n"
                    "  'msg_type': 1, 'is_group_chat': False, 'is_self': True}],\n"
                    "  open(os.path.join(out, 'wechat_messages.json'), 'w'),\n"
                    "  ensure_ascii=False)\n")
            with open(env_file, "w", encoding="utf-8") as f:
                f.write('EXPORT_CMD="%s %s %s"\n' % (sys.executable, writer, outdir))
                f.write("EXPORT_DIR=%s\n" % outdir)
                f.write("LLM_BASE_URL=http://127.0.0.1:%d/v1\n" % port)
                f.write("LLM_API_KEY=mock-key\n")

            env = dict(os.environ)
            _clear_feishu(env)
            env["WEIXIN_ENV_FILE"] = env_file
            # 产物写进临时目录，别去动项目里（和用户）真实的 data/ 与 reports/
            env["WEIXIN_HOME"] = tmp
            r = subprocess.run([sys.executable, os.path.join(COLLECTOR, "daily.py"),
                                "--date", "2026-11-18"],
                               capture_output=True, text=True, env=env, timeout=180)
            check("daily.py 退出码 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])

            data_file = os.path.join(tmp, "data", "2026-11-18.json")
            report_file = os.path.join(tmp, "reports", "2026-11-18.md")
            check("生成 daily json", os.path.isfile(data_file))
            check("生成日报", os.path.isfile(report_file))
            if os.path.isfile(data_file):
                d = json.load(open(data_file, encoding="utf-8"))
                check("解析出 1 条", d["stats"]["message_count"] == 1,
                      str(d["stats"]["message_count"]))
                check("会话名用显示名", d["chats"][0]["chat_name"] == "张三",
                      d["chats"][0]["chat_name"])
        finally:
            shutil.rmtree(tmp)
    finally:
        mock.terminate()
        mock.wait(timeout=10)


if __name__ == "__main__":
    test_missing_export_cmd()
    test_export_command_failure()
    test_full_orchestration()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
