#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整条本机链路的集成自测：python3 tests/test_pipeline.py

采集（合成数据） → 归一化 → 推假中转 → 本机假模型出日报 → 推日报成品 → 平台侧拉日报
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COLLECTOR = os.path.join(ROOT, "collector")

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def wait_ready(port, tries=50):
    for _ in range(tries):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/requests" % port, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.1)
    return False


def test_full_pipeline():
    print("\n[全链路：采集 → 归一化 → 推中转 → 摘要 → 推日报 → 平台拉取]")
    port = 8793
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py"),
                             "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready(port):
            check("mock 服务启动", False)
            return
        check("mock 服务启动", True)

        tmp = tempfile.mkdtemp()
        try:
            env = dict(os.environ)
            env.update({
                "LLM_BASE_URL": "http://127.0.0.1:%d/v1" % port,
                "LLM_API_KEY": "mock-key",
                "WEIXIN_BRIDGE": "http",
                "BRIDGE_URL": "http://127.0.0.1:%d/wechat" % port,
                "BRIDGE_REPORT_URL": "http://127.0.0.1:%d/wechat/report" % port,
            })
            # 别让宿主环境里的真实配置干扰
            for k in ("GH_TOKEN", "GH_REPO", "FS_APP_ID", "FS_APP_SECRET", "FS_TABLE_ID", "FS_APP_TOKEN"):
                env.pop(k, None)

            # 1) 造一份 chatlog 风格的导出
            src = os.path.join(tmp, "export", "2026-11-18")
            os.makedirs(os.path.join(src, "张三"))
            with open(os.path.join(src, "张三", "2026-11-18.json"), "w", encoding="utf-8") as f:
                json.dump([{"type": 1, "content": "方案我看了", "sender": "张三",
                            "timestamp": 1794963600}], f, ensure_ascii=False)

            # 2) 采集 + 归一化 + 推送 + 摘要 + 推日报（一条命令）
            r = subprocess.run(
                [sys.executable, os.path.join(COLLECTOR, "run_daily.py"),
                 "--source", src, "--date", "2026-11-18", "--summarize", "--scope", "all"],
                capture_output=True, text=True, env=env, cwd=COLLECTOR, timeout=180)
            check("run_daily 退出码 0", r.returncode == 0, (r.stdout + r.stderr)[-400:])

            data_file = os.path.join(ROOT, "data", "2026-11-18.json")
            report_file = os.path.join(ROOT, "reports", "2026-11-18.md")
            check("本机有 daily json", os.path.isfile(data_file))
            check("本机有日报 md", os.path.isfile(report_file))
            if os.path.isfile(data_file):
                d = json.load(open(data_file, encoding="utf-8"))
                check("daily json 有消息", d["stats"]["message_count"] == 1,
                      str(d["stats"]["message_count"]))
                check("daily json 有会话名", d["chats"][0]["chat_name"] == "张三")

            # 3) 平台侧按日报成品拉取（只读 report 通道）
            pulled = os.path.join(tmp, "pulled.md")
            r2 = subprocess.run(
                [sys.executable, os.path.join(COLLECTOR, "bridge.py"),
                 "pull-report", "--date", "2026-11-18", "--out", pulled],
                capture_output=True, text=True, env=env, cwd=COLLECTOR, timeout=60)
            check("平台侧拉日报成功", r2.returncode == 0, r2.stderr[-300:])
            check("拉到的日报非空", os.path.isfile(pulled) and os.path.getsize(pulled) > 0)

            # 4) 平台侧不该拿到原始聊天记录：pull 原始数据在设计上仍可用，但日报通道独立
            reqs = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:%d/requests" % port, timeout=5).read().decode())
            paths = [r["path"] for r in reqs]
            check("推过原始记录", any(p.endswith("/wechat") for p in paths), str(paths))
            check("推过日报成品", any(p.endswith("/wechat/report") for p in paths), str(paths))
            check("调用过模型", any("chat/completions" in p for p in paths), str(paths))
        finally:
            shutil.rmtree(tmp)
            # 清掉测试产物，别污染真实目录
            for p in (os.path.join(ROOT, "data", "2026-11-18.json"),
                      os.path.join(ROOT, "reports", "2026-11-18.md")):
                if os.path.isfile(p):
                    os.remove(p)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_config_env():
    print("\n[config.env 加载]")
    sys.path.insert(0, COLLECTOR)
    import bridge
    tmp = tempfile.mkdtemp()
    try:
        env_file = os.path.join(tmp, "config.env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("# 注释\nWEIXIN_BRIDGE=feishu\nFS_APP_ID='cli_xxx'\nexport FS_APP_SECRET=\"sec_yyy\"\n")
        os.environ.pop("FS_APP_ID", None)
        os.environ.pop("FS_APP_SECRET", None)
        bridge.load_env(env_file)
        check("读出 WEIXIN_BRIDGE", os.environ.get("WEIXIN_BRIDGE") == "feishu")
        check("去掉引号", os.environ.get("FS_APP_ID") == "cli_xxx", os.environ.get("FS_APP_ID"))
        check("去掉引号与 export", os.environ.get("FS_APP_SECRET") == "sec_yyy",
              os.environ.get("FS_APP_SECRET"))
        os.environ["FS_APP_ID"] = "already_set"
        bridge.load_env(env_file)
        check("不覆盖已有环境变量", os.environ.get("FS_APP_ID") == "already_set")
    finally:
        shutil.rmtree(tmp)
        for k in ("WEIXIN_BRIDGE", "FS_APP_ID", "FS_APP_SECRET"):
            os.environ.pop(k, None)


if __name__ == "__main__":
    test_full_pipeline()
    test_config_env()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
