#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥接层：把归一化后的每日 JSON 在「你的电脑」和「平台云端」之间搬运。

两个方向：
  push  在你自己的电脑上跑，把当日数据推到中转存储
  pull  在平台云端跑（定时任务里），把当日数据拉回工作区

已弃用：原始记录**不再上传飞书**。几百条消息的 JSON 塞进飞书单元格会撑爆
单格上限（报 1254130 TooLargeCell），而且原始聊天记录留在本机更安全。
现在飞书里只有日报成品，原始记录的权威归档是本机 data/<日期>.json。
github / http 两条路的原始通道仍按原样工作。

支持三种中转（通过环境变量 WEIXIN_BRIDGE 选择，缺省自动探测）：
  github  私有仓库，文件即数据。最省事，适合有 GitHub 账号的人。
  feishu  飞书多维表格，一行一天。国内网络友好，适合公司环境。
  http    任意 HTTP 端点（云函数、Worker、自建服务），你给 URL 和 Token。

只用标准库，Python 3.7+ 可跑。
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 30


# ---------------------------------------------------------------- HTTP 工具

# 飞书返回的错误码 -> 人话。这些是实测踩出来的，每晚定时任务失败时
# 日志里要能一眼看出该改哪儿，而不是只丢一个 HTTPError。
FEISHU_ERROR_HINTS = {
    91403: ("Forbidden：应用没有这张表的写入权限。",
            "两个都要做：①开放平台里把权限改成 bitable:app（读写），"
            "然后「版本管理与发布」里创建版本并发布；"
            "②表格右上角「协作者」里把应用加为「可管理」或「可编辑」。"),
    99991672: ("Access denied：应用缺权限 scope。",
               "开放平台「权限管理」里开通 bitable:app，"
               "然后「版本管理与发布」里创建版本并发布（不发版权限不生效）。"),
    1254045: ("table not found：app_token 或 table_id 不对。",
              "从表格地址栏重新取：.../base/appXXXXXXXX?table=tblYYYYYYYY"),
    1254065: ("Table Not Exist：这张表不存在或已被删除。",
              "检查表格 URL 里的 app_token / table_id。"),
    91402: ("Not Found：找不到这条记录。", "记录可能已被删掉，重新跑一次即可。"),
    1254130: ("TooLargeCell：单元格内容太长写不进去。",
              "这是把原始聊天记录整包塞进一列导致的（几百条消息就超上限）。"
              "已弃用上传原始记录：原始记录只留在本机 data/ 目录，飞书里只放日报成品。"),
}


def _request(method, url, headers=None, body=None):
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # 飞书的错误码在响应体里，urlopen 会抛异常把体丢掉 —— 这里接住
        text = ""
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        code, msg = exc.code, text[:200]
        try:
            payload = json.loads(text)
            code = payload.get("code", exc.code)
            msg = payload.get("msg", msg)
        except Exception:
            pass
        hint = FEISHU_ERROR_HINTS.get(code)
        detail = "飞书接口 %s 失败：HTTP %s code=%s %s" % (method, exc.code, code, msg)
        if hint:
            detail += "\n  → %s\n  → %s" % hint
        raise RuntimeError(detail)
    except urllib.error.URLError as exc:
        raise RuntimeError("飞书接口连不上（网络问题？）：%s" % exc)
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


def _auth_headers():
    headers = {"Content-Type": "application/json; charset=utf-8"}
    token = os.environ.get("BRIDGE_TOKEN") or os.environ.get("WEIXIN_BRIDGE_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def data_home():
    """data/ 和 reports/ 所在目录（默认为项目根）。

    设 WEIXIN_HOME 可以把产物写到别处——测试用它指向临时目录，
    免得测试去动用户真实的日报文件。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("WEIXIN_HOME", "").strip() or root


def load_env(path=None):
    """读取项目根目录的 config.env（KEY=VALUE），已存在的环境变量优先，不覆盖。

    Windows 用户不用去系统设置里配环境变量，把配置写进 config.env 即可。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = path or os.environ.get("WEIXIN_ENV_FILE") or os.path.join(root, "config.env")
    if not os.path.isfile(path):
        return
    with open(path, "rb") as f:
        raw = f.read()
    text = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val



def save_env(key, value, path=None):
    """就地改 config.env 里的一个键，保留注释和其它值。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = path or os.environ.get("WEIXIN_ENV_FILE") or os.path.join(root, "config.env")
    key = key.strip()
    if not key:
        return False
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    replaced = False
    for i, line in enumerate(lines):
        raw = line.strip()
        if raw.startswith("export "):
            raw = raw[7:].strip()
        if raw.startswith("#") or "=" not in raw:
            continue
        if raw.split("=", 1)[0].strip() == key:
            lines[i] = "%s=%s" % (key, value)
            replaced = True
            break
    if not replaced:
        lines.append("%s=%s" % (key, value))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    return True



def detect_bridge():
    explicit = _env("WEIXIN_BRIDGE").lower()
    if explicit:
        return explicit
    if _env("GH_TOKEN") and _env("GH_REPO"):
        return "github"
    if _env("FS_APP_ID") and _env("FS_APP_SECRET"):
        return "feishu"
    if _env("BRIDGE_URL"):
        return "http"
    return ""


# ---------------------------------------------------------------- GitHub

def _gh_api(path):
    token = _env("GH_TOKEN")
    repo = _env("GH_REPO")
    branch = _env("GH_BRANCH", "main")
    if not token or not repo:
        raise RuntimeError("GH_TOKEN / GH_REPO 未设置")
    url = "https://api.github.com/repos/%s%s" % (repo, path)
    return url, {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wechat-daily",
        "Content-Type": "application/json; charset=utf-8",
    }, branch


def _gh_write(path, text, message):
    url, headers, branch = _gh_api("/contents/" + urllib.parse.quote(path))
    sha = None
    try:
        existing = _request("GET", url, headers)
        if isinstance(existing, dict):
            sha = existing.get("sha")
    except urllib.error.HTTPError:
        pass  # 文件不存在，正常
    body = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    return _request("PUT", url, headers, json.dumps(body).encode("utf-8"))


def github_push(path, payload):
    return _gh_write(path, json.dumps(payload, ensure_ascii=False),
                     "chore: wechat daily %s" % payload.get("date", ""))


def github_pull(path):
    url, headers, _ = _gh_api("/contents/" + urllib.parse.quote(path))
    res = _request("GET", url, headers)
    if not isinstance(res, dict) or "content" not in res:
        raise RuntimeError("GitHub 上没有 %s" % path)
    return json.loads(base64.b64decode(res["content"]).decode("utf-8"))


def github_push_report(date_str, text):
    path = _env("BRIDGE_REPORT_PATH", "wechat-daily/reports/{date}.md").replace("{date}", date_str)
    return _gh_write(path, text, "docs: wechat daily report %s" % date_str)


def github_pull_report(date_str):
    path = _env("BRIDGE_REPORT_PATH", "wechat-daily/reports/{date}.md").replace("{date}", date_str)
    url, headers, _ = _gh_api("/contents/" + urllib.parse.quote(path))
    res = _request("GET", url, headers)
    if not isinstance(res, dict) or "content" not in res:
        raise RuntimeError("GitHub 上没有 %s" % path)
    return base64.b64decode(res["content"]).decode("utf-8")


# ---------------------------------------------------------------- 飞书

def _feishu_token():
    app_id = _env("FS_APP_ID")
    app_secret = _env("FS_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("FS_APP_ID / FS_APP_SECRET 未设置")
    res = _request("POST", "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                   {"Content-Type": "application/json; charset=utf-8"},
                   json.dumps({"app_id": app_id, "app_secret": app_secret}).encode())
    token = res.get("tenant_access_token")
    if not token:
        raise RuntimeError("飞书鉴权失败: %s" % res.get("msg"))
    return token


def _feishu_table():
    """支持 FS_TABLE_ID='appXXX/tblYYY' 或 FS_APP_TOKEN + FS_TABLE_ID 两种写法。"""
    combined = _env("FS_TABLE_ID")
    if "{{" in combined or "ARCHON_SECRET" in combined:
        raise RuntimeError(
            "FS_TABLE_ID 是打码占位符，不是真值。\n"
            "  → 聊天里的密钥串会被平台自动替换成 {{ARCHON_SECRET:...}}，别从聊天里复制。\n"
            "  → 表格的话贴网址让程序解析：python collector\\wechat4.py set-table "
            "\"https://xxx.feishu.cn/base/appXXX?table=tblYYY\"")
    if "/" in combined:
        app_token, table_id = combined.split("/", 1)
    else:
        app_token = _env("FS_APP_TOKEN")
        table_id = combined
    if not app_token or not table_id:
        raise RuntimeError("FS_TABLE_ID 或 FS_APP_TOKEN 未设置")
    return app_token, table_id


def _feishu_headers():
    return {"Authorization": "Bearer " + _feishu_token(),
            "Content-Type": "application/json; charset=utf-8"}


def _feishu_search(date_str):
    app_token, table_id = _feishu_table()
    url = ("https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records/search"
           % (app_token, table_id))
    body = {
        "filter": {"conjunction": "and", "conditions": [
            {"field_name": _env("FS_FIELD_DATE", "日期"), "operator": "is", "value": [date_str]},
        ]},
        "page_size": 10,
    }
    res = _request("POST", url, _feishu_headers(), json.dumps(body, ensure_ascii=False).encode())
    if res.get("code"):
        raise RuntimeError("飞书查询失败: %s %s" % (res.get("code"), res.get("msg")))
    return res.get("data", {}).get("items", [])


def feishu_push(path, payload):
    """已弃用：原始记录不上传飞书。

    飞书表现在只有「日期 / 日报」两列。这里**只更新已存在的那一行的日期列**，
    不会新建「只有日期、没有日报」的半成品行 —— 那一行由日报成品自己建
    （日期 + 日报一起写，见 feishu_push_report）。

    原始记录的权威归档是本机 data/<日期>.json。
    """
    date_str = payload.get("date", "")
    items = _feishu_search(date_str)
    fields = {
        _env("FS_FIELD_DATE", "日期"): date_str,
    }
    if not items:
        print("[warn] 原始记录不上传飞书（已弃用）——原始记录留在本机 data/%s.json" % date_str)
        print("       飞书里的那一行由日报成品自己建：日期 + 日报一起写。")
        return None
    app_token, table_id = _feishu_table()
    base = "https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records" % (app_token, table_id)
    record_id = items[0].get("record_id")
    res = _request("PUT", base + "/" + record_id, _feishu_headers(),
                   json.dumps({"fields": fields}, ensure_ascii=False).encode())
    if res.get("code"):
        raise RuntimeError("飞书写入失败: %s %s" % (res.get("code"), res.get("msg")))
    return res


def feishu_pull(path):
    """已弃用：原始记录不上传飞书。

    飞书表现在只有「日期 / 日报」两列，**没有「内容」列**。
    这里保持容错：表里没有「内容」列、该列为空、或者压根没有这一天的
    记录时，都返回 None 而不抛异常 —— 用户可能已经把那一列删了。
    原始记录只留在本机 data/ 目录。
    """
    date_str = os.path.basename(path).replace(".json", "")
    items = _feishu_search(date_str)
    if not items:
        return None
    fields = items[0].get("fields", {})
    raw = fields.get(_env("FS_FIELD_PAYLOAD", "内容"))
    if isinstance(raw, list):  # 飞书长文本字段有时返回数组
        raw = "".join(str(x.get("text", "")) for x in raw if isinstance(x, dict))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def feishu_push_report(date_str, text):
    """写「日报」列，按日期 upsert。

    表里只有「日期 / 日报」两列，所以**一次写全**：
      找到同日期的行 → 只更新「日报」列
      没找到        → 新建一行，「日期」和「日报」一起写
    绝不留「只有日期、没有日报」的半成品行。
    """
    items = _feishu_search(date_str)
    fields = {
        _env("FS_FIELD_DATE", "日期"): date_str,
        _env("FS_FIELD_REPORT", "日报"): text,
    }
    app_token, table_id = _feishu_table()
    base = "https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records" % (app_token, table_id)
    if items:
        record_id = items[0].get("record_id")
        res = _request("PUT", base + "/" + record_id, _feishu_headers(),
                       json.dumps({"fields": fields}, ensure_ascii=False).encode())
    else:
        res = _request("POST", base, _feishu_headers(),
                       json.dumps({"fields": fields}, ensure_ascii=False).encode())
    if res.get("code"):
        raise RuntimeError("飞书日报写入失败: %s %s" % (res.get("code"), res.get("msg")))
    return res


def feishu_pull_report(date_str):
    """只读「日报」列——平台侧拿不到原始聊天内容，隐私边界在代码里划死。"""
    items = _feishu_search(date_str)
    if not items:
        raise RuntimeError("飞书里没有 %s 的记录" % date_str)
    fields = items[0].get("fields", {})
    raw = fields.get(_env("FS_FIELD_REPORT", "日报"))
    if isinstance(raw, list):
        raw = "".join(str(x.get("text", "")) for x in raw if isinstance(x, dict))
    if not raw:
        raise RuntimeError("飞书里 %s 这条记录还没有日报内容" % date_str)
    return raw


# ---------------------------------------------------------------- 通用 HTTP

def http_push(path, payload):
    url = _env("BRIDGE_URL")
    if not url:
        raise RuntimeError("BRIDGE_URL 未设置")
    body = {"date": payload.get("date", ""), "payload": payload}
    res = _request("POST", url, _auth_headers(), json.dumps(body, ensure_ascii=False).encode())
    return res


def http_pull(path):
    url = _env("BRIDGE_URL")
    if not url:
        raise RuntimeError("BRIDGE_URL 未设置")
    sep = "&" if "?" in url else "?"
    res = _request("GET", url + sep + urllib.parse.urlencode({"date": os.path.basename(path)[:-5]}),
                   _auth_headers())
    if isinstance(res, dict) and "payload" in res:
        return res["payload"]
    return res


def _report_url():
    return _env("BRIDGE_REPORT_URL", _env("BRIDGE_URL", "").rstrip("/") + "/report")


def http_push_report(date_str, text):
    url = _report_url()
    if not url:
        raise RuntimeError("BRIDGE_REPORT_URL / BRIDGE_URL 未设置")
    return _request("POST", url, _auth_headers(),
                    json.dumps({"date": date_str, "report": text}, ensure_ascii=False).encode())


def http_pull_report(date_str):
    url = _report_url()
    if not url:
        raise RuntimeError("BRIDGE_REPORT_URL / BRIDGE_URL 未设置")
    sep = "&" if "?" in url else "?"
    res = _request("GET", url + sep + urllib.parse.urlencode({"date": date_str}), _auth_headers())
    if isinstance(res, dict) and "report" in res:
        return res["report"]
    if isinstance(res, dict) and "_raw" in res:
        return res["_raw"]
    raise RuntimeError("HTTP 端点返回的不是日报格式: %s" % str(res)[:120])


# ---------------------------------------------------------------- 调度

PUSHERS = {"github": github_push, "feishu": feishu_push, "http": http_push}
PULLERS = {"github": github_pull, "feishu": feishu_pull, "http": http_pull}
REPORT_PUSHERS = {"github": github_push_report, "feishu": feishu_push_report, "http": http_push_report}
REPORT_PULLERS = {"github": github_pull_report, "feishu": feishu_pull_report, "http": http_pull_report}


def remote_path(date_str):
    tmpl = _env("BRIDGE_PATH", "wechat-daily/data/{date}.json")
    return tmpl.replace("{date}", date_str)


def do_push(date_str, payload, bridge):
    fn = PUSHERS.get(bridge)
    if not fn:
        raise RuntimeError("未知的中转方式: %r（可选 github / feishu / http）" % bridge)
    return fn(remote_path(date_str), payload)


def do_pull(date_str, bridge):
    fn = PULLERS.get(bridge)
    if not fn:
        raise RuntimeError("未知的中转方式: %r（可选 github / feishu / http）" % bridge)
    return fn(remote_path(date_str))


def do_push_report(date_str, text, bridge):
    fn = REPORT_PUSHERS.get(bridge)
    if not fn:
        raise RuntimeError("未知的中转方式: %r（可选 github / feishu / http）" % bridge)
    return fn(date_str, text)


def do_pull_report(date_str, bridge):
    fn = REPORT_PULLERS.get(bridge)
    if not fn:
        raise RuntimeError("未知的中转方式: %r（可选 github / feishu / http）" % bridge)
    return fn(date_str)


def main(argv=None):
    load_env()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="微信日报数据桥接")
    ap.add_argument("action", choices=["push", "pull", "push-report", "pull-report"])
    ap.add_argument("--date", required=True)
    ap.add_argument("--file", help="push 时指定本地文件；缺省用 data/<date>.json")
    ap.add_argument("--out", help="pull 时的落盘路径")
    ap.add_argument("--bridge", default="", help="github / feishu / http，缺省自动探测")
    args = ap.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.action == "pull-report":
        # 日报成品：不需要原始记录凭据也能取，平台侧只走这一条路
        bridge = (args.bridge or detect_bridge()).lower()
        if not bridge:
            sys.stderr.write("[error] 没有可用中转：请设置 WEIXIN_BRIDGE，或配置对应环境变量\n")
            return 2
        try:
            text = do_pull_report(args.date, bridge)
        except Exception as exc:
            sys.stderr.write("[error] %s\n" % exc)
            return 1
        dest = args.out or os.path.join(root, "reports", args.date + ".md")
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("[ok] 已通过 %s 拉取日报 %s -> %s\n" % (bridge, args.date, dest))
        return 0

    bridge = (args.bridge or detect_bridge()).lower()
    if not bridge:
        sys.stderr.write("[error] 没有可用中转：请设置 WEIXIN_BRIDGE，或配置对应环境变量\n")
        return 2

    try:
        if args.action == "push":
            src = args.file or os.path.join(root, "data", args.date + ".json")
            with open(src, "r", encoding="utf-8") as f:
                payload = json.load(f)
            do_push(args.date, payload, bridge)
            sys.stderr.write("[ok] 已通过 %s 推送 %s\n" % (bridge, args.date))
        elif args.action == "push-report":
            src = args.file or os.path.join(root, "reports", args.date + ".md")
            with open(src, "r", encoding="utf-8") as f:
                text = f.read()
            do_push_report(args.date, text, bridge)
            sys.stderr.write("[ok] 已通过 %s 推送日报 %s\n" % (bridge, args.date))
        else:
            payload = do_pull(args.date, bridge)
            if payload is None:
                sys.stderr.write(
                    "[error] %s 里没有 %s 的原始记录。\n"
                    "        已弃用上传原始记录（原始记录只留在本机 data/ 目录），"
                    "飞书里只有日报成品。\n" % (bridge, args.date))
                return 1
            dest = args.out or os.path.join(root, "data", args.date + ".json")
            os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            sys.stderr.write("[ok] 已通过 %s 拉取 %s -> %s\n" % (bridge, args.date, dest))
    except Exception as exc:
        sys.stderr.write("[error] %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
