#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第一步自检：一条命令看清本机还差什么（微信 4.x / Windows）。

    python collector/doctor.py

只读不写，不会碰你的微信数据，也不会调用任何外部接口。
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bridge  # noqa: E402

OK = "[OK]  "
BAD = "[!!]  "
WARN = "[~]  "

RESULTS = []


def record(ok, name, detail="", fix=""):
    RESULTS.append((ok, name, detail, fix))
    print("%s %s%s" % (OK if ok else BAD, name, ("  — " + detail) if detail else ""))
    if not ok and fix:
        print("     → %s" % fix)


def warn(name, detail="", fix=""):
    RESULTS.append((None, name, detail, fix))
    print("%s %s%s" % (WARN, name, ("  — " + detail) if detail else ""))
    if fix:
        print("     → %s" % fix)


def home():
    return os.environ.get("USERPROFILE") or os.path.expanduser("~")


def appdata():
    return os.environ.get("APPDATA") or os.path.join(home(), "AppData", "Roaming")


# ---------------------------------------------------------------- 各项检查

def check_python():
    ok = sys.version_info >= (3, 7)
    record(ok, "Python 解释器", "%d.%d.%d" % sys.version_info[:3],
           "" if ok else "需要 Python 3.7 及以上（部分解密工具要 3.10+）")


def check_config():
    cfg = os.path.join(ROOT, "config.env")
    if os.path.isfile(cfg):
        bridge.load_env(cfg)
        record(True, "config.env", cfg)
    else:
        # 没有 config.env 也要能用环境变量配（平台侧就是这么跑的）
        record(False, "config.env", "不存在",
               "跑一次 python collector\\wechat4.py init-config 自动生成，"
               "或者把 config.env.example 复制成 config.env 再填")
    return {k: os.environ.get(k, "") for k in
            ("EXPORT_DIR", "EXPORT_CMD", "WECHAT_DATA_DIR", "WEIXIN_BRIDGE",
             "FS_APP_ID", "FS_APP_SECRET", "FS_TABLE_ID", "LLM_API_KEY")}


def find_wechat_root(cfg):
    """候选的 xwechat_files 根目录（按可能性排序）。

    探测逻辑统一放在 wechat4.py 里 —— 之前两边各写一套正则，结果不一致：
    微信 4.x 的配置 ini 里存的是**父目录**（整个文件就一行路径），
    xwechat_files 是拼在它后面的，按「路径含 xwechat_files」去正则匹配必然落空。
    """
    candidates = []
    d = (cfg.get("WECHAT_DATA_DIR") or "").strip()
    if d:
        candidates.append(os.path.expandvars(d))
    try:
        import wechat4
        candidates.extend(wechat4._candidate_roots(slow=False))
    except ImportError:
        docs = os.path.join(home(), "Documents")
        if os.path.isdir(docs):
            for name in os.listdir(docs):
                if name.startswith("xwechat_files"):
                    candidates.append(os.path.join(docs, name))

    seen, roots = set(), []
    for c in candidates:
        if not c:
            continue
        n = os.path.normpath(c)
        if n not in seen:
            seen.add(n)
            roots.append(n)
    return roots


def check_wechat_root(cfg):
    roots = find_wechat_root(cfg)
    if not roots:
        record(False, "微信数据根目录", "没找到 xwechat_files",
               "微信 4.x 的数据在「文档\\xwechat_files」。如果改过存储位置，"
               "看 %APPDATA%\\Tencent\\xwechat\\config\\*.ini 里的路径，"
               "填进 config.env 的 WECHAT_DATA_DIR")
        return None
    for root in roots:
        root = os.path.normpath(root)
        if not os.path.isdir(root):
            continue
        dbdir = None
        for name in os.listdir(root):
            candidate = os.path.join(root, name, "db_storage")
            if os.path.isdir(candidate):
                dbdir = candidate
                break
        if dbdir:
            msgdir = os.path.join(dbdir, "message")
            dbs = []
            if os.path.isdir(msgdir):
                dbs = [f for f in os.listdir(msgdir)
                       if f.lower().endswith(".db") and "fts" not in f.lower()]
            record(True, "微信数据根目录", "%s（db_storage 在 %s）" % (root, dbdir))
            if dbs:
                record(True, "消息库", "%d 个 message_*.db（最大的 %s）"
                       % (len(dbs), max(dbs, key=lambda f: os.path.getsize(
                           os.path.join(msgdir, f)))))
            else:
                warn("消息库", "%s 里没找到 message_*.db" % msgdir,
                     "确认微信已登录过、这台电脑上有聊天记录")
            return root
    record(False, "微信数据根目录", "找到目录但里面没有 db_storage：" + " / ".join(roots),
           "确认这是微信 4.x 的数据目录（3.x 是 WeChat Files，没有 db_storage）")
    return None


def check_wechat4_tools():
    """微信 4.x 的解密工具链：chatlog-keeper.exe + 密钥恢复脚本。"""
    tools_dir = os.path.join(home(), ".wechat_daily_tools")
    keeper = os.path.join(tools_dir, "chatlog-keeper.exe")
    recovery = os.path.join(tools_dir, "recovery", "tools", "run_recovery_generic.py")
    if os.path.isfile(keeper):
        record(True, "chatlog-keeper（解密导出）", keeper)
    else:
        record(False, "chatlog-keeper（解密导出）", "还没装",
               "跑一次 collector\\setup.bat，它会自动下载")
    if os.path.isfile(recovery):
        record(True, "密钥恢复脚本", recovery)
    else:
        # 密钥恢复脚本只在「需要重新取密钥」时才用得上（微信升级后）。
        # 密钥一旦注入过 chatlog-keeper，删掉它也不影响每天的导出。
        warn("密钥恢复脚本", "不在了（不影响每天跑）",
             "只有微信升级导致密钥失效时，才需要重跑 collector\\setup.bat 重新取一次")


def check_export_tool(cfg):
    cmd = (cfg.get("EXPORT_CMD") or "").strip()
    if not cmd:
        record(False, "导出命令 EXPORT_CMD", "未配置",
               "微信 4.x 的库是 SQLCipher 4 加密的，密钥是 XOR 混淆态，"
               "必须走 wechat4 这条链路。跑完 setup.bat 后填：")
        print("       EXPORT_CMD=python \"%~dp0wechat4.py\" export")
        return
    exe = cmd.split()[0]
    found = shutil.which(exe)
    if found:
        record(True, "导出命令 EXPORT_CMD", "可执行文件在 PATH：%s" % found)
    elif os.path.isfile(exe):
        record(True, "导出命令 EXPORT_CMD", exe + "（相对路径，记得配 PATH）")
    else:
        record(True, "导出命令 EXPORT_CMD", cmd)


def check_export_dir(cfg):
    d = (cfg.get("EXPORT_DIR") or "").strip()
    if not d:
        record(False, "导出目录 EXPORT_DIR", "未配置",
               "填成 C:\\wx_export（导出工具写出的 JSON 放这儿）")
        return
    d = os.path.expandvars(d)
    if not os.path.isdir(d):
        # 导出工具第一次跑会自己建目录，不是错误
        warn("导出目录 EXPORT_DIR", d + " 还不存在（第一次导出时会自动创建）",
             "跑一次 collector\\daily.bat 就会生成")
        return
    files = []
    for root, _dirs, names in os.walk(d):
        for n in names:
            if n.lower().endswith((".json", ".jsonl", ".csv", ".md")):
                files.append(os.path.join(root, n))
    if not files:
        record(False, "导出目录 EXPORT_DIR", d + " 里没有导出文件",
               "先跑一次导出命令")
        return
    newest = max(files, key=os.path.getmtime)
    age_h = (time.time() - os.path.getmtime(newest)) / 3600.0
    record(True, "导出目录 EXPORT_DIR", "%d 个文件，最新 %s（%.1f 小时前）"
           % (len(files), os.path.relpath(newest, d), age_h))
    if age_h > 30:
        warn("导出数据新鲜度", "最新文件已经 %.0f 小时没更新了" % age_h,
             "先手动跑一次导出命令，确认工具能出数据")

    # 真跑一次解析，看看字段认不认
    try:
        import normalize
        tz = normalize.get_timezone("Asia/Shanghai")
        msgs = normalize.collect_sources([d], tz)
        if msgs:
            record(True, "导出数据可解析", "识别出 %d 条消息" % len(msgs))
        else:
            probe = normalize._probe_json(newest) if newest.endswith(".json") else None
            record(False, "导出数据可解析",
                   "一条都没解析出来" + ("；文件结构：%s" % probe if probe else ""),
                   "字段名不认？把上面的结构发给开发者，加个别名即可")
    except Exception as exc:
        record(False, "导出数据可解析", str(exc))


def _value_report(key, value):
    """不泄露密钥内容，只说清楚「这个值有没有问题」。"""
    v = (value or "").strip()
    if not v:
        return "空"
    if "{{" in v or "ARCHON_SECRET" in v:
        return "打码占位符（聊天里复制的，不是真值）"
    if key.upper() == "FS_APP_ID" and not v.startswith("cli_"):
        return "长度 %d，但不是 cli_ 开头（可能多复制了东西）" % len(v)
    return "长度 %d" % len(v)


def _feishu_token(cfg):
    """拿 tenant_access_token。返回 (token, 错误信息)。"""
    import urllib.request
    import json as _json
    body = _json.dumps({"app_id": cfg.get("FS_APP_ID"),
                         "app_secret": cfg.get("FS_APP_SECRET")}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=body, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode())
    except Exception as exc:
        return None, str(exc)
    if data.get("code") != 0:
        return None, data.get("msg") or ("code=%s" % data.get("code"))
    return data.get("tenant_access_token"), None


def check_feishu(cfg):
    if (cfg.get("WEIXIN_BRIDGE") or "").lower() != "feishu":
        record(False, "飞书中转配置", "WEIXIN_BRIDGE 不是 feishu",
               "本方案用飞书，把 WEIXIN_BRIDGE 设成 feishu")
        return
    missing = [k for k in ("FS_APP_ID", "FS_APP_SECRET", "FS_TABLE_ID") if not cfg.get(k)]
    if missing:
        record(False, "飞书凭据", "缺少 " + " / ".join(missing),
               "自建应用的 app_id / app_secret，以及表格 URL 里的 app_token 与 table_id")
        return
    table_id_raw = (cfg.get("FS_TABLE_ID") or "").strip()
    if "{{" in table_id_raw or "ARCHON_SECRET" in table_id_raw:
        record(False, "飞书凭据", "FS_TABLE_ID 是打码占位符，不是真值",
               "聊天里的密钥串会被平台自动替换成 {{ARCHON_SECRET:...}}，别从聊天里复制。"
               "表格的话直接贴网址，让程序自己解析："
               "python collector\\wechat4.py set-table \"https://xxx.feishu.cn/base/appXXX?table=tblYYY\"")
        return
    if "/" not in table_id_raw:
        record(False, "飞书凭据", "FS_TABLE_ID 格式不对",
               "要写成 appXXXXXXXX/tblYYYYYYYY；"
               "或者用 set-table 命令从表格网址自动填")
        return
    record(True, "飞书凭据", "已配置（表: %s）" % table_id_raw)
    app_id_v = (cfg.get("FS_APP_ID") or "").strip()
    app_secret_v = (cfg.get("FS_APP_SECRET") or "").strip()
    for key, val in (("FS_APP_ID", app_id_v), ("FS_APP_SECRET", app_secret_v)):
        rep = _value_report(key, val)
        if rep.startswith("打码") or rep == "空" or "不是 cli_" in rep:
            record(False, "  %s" % key, rep,
                   "从飞书开放平台「凭证与基础信息」页重新复制（别从聊天里复制）")
        else:
            record(True, "  %s" % key, rep)

    # 真连一次：拿 token，再读表格字段
    token, err = _feishu_token(cfg)
    if not token:
        record(False, "飞书连通性", "拿不到 token：%s" % err,
               "FS_APP_ID=%s；FS_APP_SECRET=%s。两者都要从飞书「凭证与基础信息」"
               "页重新复制，别从聊天里复制。"
               % (_value_report("FS_APP_ID", cfg.get("FS_APP_ID")),
                  _value_report("FS_APP_SECRET", cfg.get("FS_APP_SECRET"))))
        return
    record(True, "飞书连通性", "app_id / app_secret 有效")

    import urllib.request
    import json as _json
    app_token, table_id = cfg.get("FS_TABLE_ID", "").split("/", 1)
    url = ("https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/fields?page_size=50"
           % (app_token, table_id))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode())
    except Exception as exc:
        record(False, "飞书表格访问", str(exc),
               "多半是表格没分享给应用：表格右上角「分享」-> 搜索你的应用名加为协作者")
        return
    if data.get("code") != 0:
        record(False, "飞书表格访问", data.get("msg") or ("code=%s" % data.get("code")),
               "检查 app_token / table_id 有没有复制对；以及应用有没有开通 bitable 权限")
        return
    fields = [f.get("field_name") for f in (data.get("data") or {}).get("items") or []]
    record(True, "飞书表格访问", "读到 %d 个字段：%s" % (len(fields), "、".join(fields)))
    expect = [cfg.get("FS_FIELD_DATE", "日期"), cfg.get("FS_FIELD_MSG", "消息数"),
              cfg.get("FS_FIELD_PAYLOAD", "内容"), cfg.get("FS_FIELD_REPORT", "日报")]
    lack = [e for e in expect if e not in fields]
    if lack:
        record(False, "飞书表格字段", "缺列：" + "、".join(lack),
               "四列名字必须一字不差：" + "、".join(expect))
    else:
        record(True, "飞书表格字段", "四列都在 ✓")



def check_feishu_write(cfg):
    """真写一行测试日报再删掉，确认应用有写权限（只读协作者会在这里失败）。"""
    print("\n--- 写权限自检 ---")
    parts = (cfg.get("FS_TABLE_ID") or "").strip().split("/", 1)
    if len(parts) != 2:
        record(False, "飞书写入", "FS_TABLE_ID 格式不对，跳过写测试",
               "要写成 appXXXXXXXX/tblYYYYYYYY")
        return False
    app_token, table_id = parts
    date_str = dt.date.today().isoformat()
    marker = "写入自检 %s" % dt.datetime.now().strftime("%H:%M:%S")
    text = "# 写入自检\n\n%s" % marker
    try:
        bridge.feishu_push_report(date_str, text)
    except Exception as exc:
        record(False, "飞书写入", str(exc)[:300],
               "权限或协作者角色不对：表格右上角「···」→「···更多」→「添加文档应用」，"
               "权限选「可管理」")
        return False
    record(True, "飞书写入", "已写入一行测试日报")
    try:
        back = bridge.feishu_pull_report(date_str)
        ok = marker in back
    except Exception as exc:
        ok = False
        record(False, "飞书回读", str(exc)[:200])
    if ok:
        record(True, "飞书回读", "内容一致 ✓")
    # 清理
    try:
        for item in bridge._feishu_search(date_str):
            bridge._request("DELETE",
                            "https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records/%s"
                            % (app_token, table_id, item["record_id"]),
                            bridge._feishu_headers())
        record(True, "清理测试行", "已删除，表里不留东西")
    except Exception as exc:
        record(False, "清理测试行", str(exc)[:200],
               "表里可能残留一行 %s 的测试数据，手动删掉即可" % date_str)
    return ok


def _key_kind(key):
    """看 key 前缀判断它是「按量付费」还是「订阅」凭据。"""
    v = (key or "").strip()
    if v.startswith("sk-cp-"):
        return "订阅 key（sk-cp-，吃订阅额度）"
    if v.startswith("sk-api-"):
        return "按量付费 key（sk-api-，扣账户余额）"
    return "未知格式"


def _key_shape(key):
    """不显示 key 本身，只说清楚它长什么样、有没有可疑字符。"""
    v = (key or "").strip()
    if not v:
        return "空"
    problems = []
    if any(ch.isspace() for ch in v):
        problems.append("含空白字符（贴错了？key 不会有换行）")
    if len(v) < 32 and v.startswith("eyJ"):
        problems.append("长度不对")
    if any(ord(ch) > 127 for ch in v):
        problems.append("含非 ASCII 字符")
    if v.startswith("{{") or "ARCHON_SECRET" in v:
        problems.append("是打码占位符")
    if "&GroupId=" in v or "?" in v or v.endswith("="):
        problems.append("混进了 GroupId 等参数")
    return "长度 %d%s" % (len(v), ("；" + "、".join(problems)) if problems else "")


def check_llm(cfg, probe=False):
    key = (cfg.get("LLM_API_KEY") or "").strip()
    if not key:
        record(False, "本机摘要 API key", "未配置 LLM_API_KEY",
               "开放平台拿一个 key；默认用 https://api.minimax.cn/v1 + MiniMax-M3.1-Flash")
        return
    model = os.environ.get("LLM_MODEL", "MiniMax-M3.1-Flash")
    base = os.environ.get("LLM_BASE_URL", "https://api.minimax.cn/v1")
    shape = _key_shape(key)
    suspicious = ("空白字符" in shape) or (len(key) < 32)
    if not probe:
        if suspicious:
            record(False, "本机摘要 API key", shape,
                   "这不是一个像样的 API key（MiniMax 的 key 是 32 位或 64 位）。"
                   "重新去控制台「接口密钥」页复制，并用 set-clip 写入。")
        else:
            record(True, "本机摘要 API key", "%s；%s" % (shape, _key_kind(key)))
        return

    record(True, "本机摘要 API key", "%s；%s" % (shape, _key_kind(key)))
    print("      配置地址：%s" % base)
    print("      正在试各个官方域名和模型 ...")
    try:
        import summarize_local
        b, m, tok_key, eff, errors = summarize_local.probe_llm()
    except Exception as exc:
        record(False, "模型接口", "探测失败：%s" % str(exc)[:200],
               "key 不对？或者网络不通（国内 api.minimax.cn，国际 api.minimax.io）")
        return
    if not b:
        record(False, "模型接口", "所有 域名 x 模型 组合都调不通")
        print("      逐条错误：")
        for e in errors[:8]:
            print("        - %s" % e)
        print("")
        print("      怎么看这些错误：")
        print("        HTTP 401/403 -> key 和这个域名不匹配（国内 key 只能用国内域名，反之亦然）")
        print("        HTTP 402     -> 余额不足：按量付费 key 要充值；订阅 key 要等额度窗口重置")
        print("        HTTP 404     -> 这个域名上没有这个模型")
        print("        HTTP 400     -> 参数名不对，程序会自动换 max_completion_tokens / max_tokens 再试")
        print("        余额不足     -> 去 MiniMax 控制台充值")
        return
    record(True, "模型接口", "可用：%s / %s / %s" % (b, m, tok_key),
           "以后就固定用这组参数")


def check_wechat_running():
    """微信 4.x 进程名是 Weixin.exe。取密钥时它必须已登录并在跑。"""
    if os.name != "nt":
        return
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe"],
                             capture_output=True, text=True, timeout=10).stdout
        if "Weixin.exe" in out:
            record(True, "微信进程", "Weixin.exe 正在运行")
        else:
            warn("微信进程", "没找到 Weixin.exe",
                 "取密钥需要微信已登录并运行")
    except Exception:
        pass


def next_steps(cfg):
    print("\n" + "=" * 62)
    failed = [r for r in RESULTS if r[0] is False]
    if not failed:
        print("全部就绪。下一步：跑一次真实采集")
        print("    cd %s" % os.path.join(ROOT, "collector"))
        print("    daily.bat")
        return
    print("还差 %d 项，按这个顺序补：" % len(failed))
    for i, (_ok, name, _detail, fix) in enumerate(failed, 1):
        print("  %d) %s" % (i, name))
        if fix:
            for line in fix.splitlines():
                print("     %s" % line)
    print("=" * 62)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("微信日报 · 本机自检")
    print("项目目录：%s\n" % ROOT)
    check_python()
    cfg = check_config()
    check_wechat_root(cfg)
    check_wechat_running()
    check_wechat4_tools()
    check_export_tool(cfg)
    check_export_dir(cfg)
    check_feishu(cfg)
    if "--write-test" in argv:
        check_feishu_write(cfg)
    check_llm(cfg, probe=("--llm-test" in argv))
    next_steps(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
