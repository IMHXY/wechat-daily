#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在你自己的电脑上生成日报——聊天记录不经过任何第三方云端模型。

调用 OpenAI 兼容接口（默认 MiniMax），只用标准库。

环境变量：
  LLM_BASE_URL   默认 https://api.minimax.cn/v1
  LLM_API_KEY    你的 API key（也认 MINIMAX_API_KEY）
  LLM_MODEL      默认 MiniMax-M3.1-Flash
  LLM_EFFORT     思考深度 low/medium/high/xhigh/max，默认 medium

用法：
    python3 summarize_local.py --date 2026-11-18
    python3 summarize_local.py --date 2026-11-18 --push   # 顺便把日报推到飞书
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bridge  # noqa: E402
import normalize  # noqa: E402


def data_home():
    """data/ 和 reports/ 所在目录。

    设 WEIXIN_HOME 可以把产物写到别处——测试用它指向临时目录，
    免得测试去动用户真实的日报文件。
    """
    return bridge.data_home()

DEFAULT_BASE_URL = "https://api.minimax.cn/v1"
DEFAULT_MODEL = "MiniMax-M3.1-Flash"
MAX_INPUT_CHARS = 30000      # 单次请求的聊天文本上限
MAX_MESSAGE_CHARS = 400      # 单条消息截断长度
TIMEOUT = 180


def env(*names, default=""):
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return default


# ---------------------------------------------------------------- 渲染

def render_chat(chat):
    kind = "群聊" if chat.get("chat_type") == "group" else "单聊"
    if chat.get("silent") or not chat.get("messages"):
        head = "## 会话：%s（%s，**今天没有发言**）" % (
            chat.get("chat_name", "未知会话"), kind)
        return head + "\n（这个会话今天一条消息都没有，不要编内容）"
    mine = sum(1 for m in chat.get("messages", []) if m.get("is_self"))
    if chat.get("trimmed"):
        how = "，已按我的发言裁剪" if mine else "，按白名单只取最近若干条"
    else:
        how = ""
    head = "## 会话：%s（%s，%d 条%s）" % (
        chat.get("chat_name", "未知会话"),
        kind,
        chat.get("message_count", len(chat.get("messages", []))),
        how,
    )
    lines = [head]
    for m in chat.get("messages", []):
        content = (m.get("content") or "").replace("\n", " ").strip()
        if len(content) > MAX_MESSAGE_CHARS:
            content = content[:MAX_MESSAGE_CHARS] + "…"
        if not content:
            content = "[%s]" % (m.get("type") or "message")
        who = "我" if m.get("is_self") else m.get("sender", "?")
        lines.append("%s %s: %s" % (m.get("time", "")[-8:-3] or "??:??", who, content))
    return "\n".join(lines)


def render_stats(daily):
    s = daily.get("stats", {})
    parts = [
        "消息 %s 条" % s.get("message_count", 0),
        "进日报 %s 个会话" % s.get("chat_count", 0),
    ]
    if s.get("silent_chat_count"):
        parts.append("（%s 个今天无人发言）" % s["silent_chat_count"])
    if s.get("skipped_chat_count"):
        parts.append("另有 %s 个未参与会话已跳过" % s["skipped_chat_count"])
    parts.append("高峰 %s 时" % s.get("peak_hour", "-"))
    parts.append("活跃时段 %s" % " ~ ".join(s.get("active_hours", []) or ["-"]))
    return " · ".join(parts)


def split_text(text, limit=MAX_INPUT_CHARS):
    """按行切块，尽量不把一条消息劈开。"""
    if len(text) <= limit:
        return [text]
    chunks, cur, size = [], [], 0
    for line in text.splitlines():
        if size + len(line) + 1 > limit and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


# ---------------------------------------------------------------- LLM

# 候选域名：MiniMax 国内版和国际版鉴权系统物理隔离，key 和域名必须一一对应。
# 国内：api.minimax.cn / api.minimaxi.com   国际：api.minimax.io / api.minimax.chat
DEFAULT_BASE_CANDIDATES = [
    "https://api.minimax.cn/v1",
    "https://api.minimaxi.com/v1",
    "https://api.minimax.io/v1",
    "https://api.minimax.chat/v1",
]

# 候选模型：按顺序试，第一个能用的就用它。
# MiniMax-M3.1-Flash 只对 M Plan / MiniMax Code 开放，
# 按量付费的账号要退到 MiniMax-M3，所以这里做了逐个回退。
DEFAULT_FALLBACK_MODELS = ["MiniMax-M3", "MiniMax-M2.1", "MiniMax-M2"]

# 已经探测成功的组合，跑第二次就不用再探
_WORKING = {}


def _candidate_bodies(model, max_tokens, effort):
    """按可能性从高到低排列的 (额度字段, 思考深度) 组合。"""
    out = []
    for tok_key in ("max_completion_tokens", "max_tokens"):
        for eff in ([effort] if effort else []) + [None]:
            out.append((tok_key, eff))
    return out


def _http_chat(base, key, body, timeout):
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _extract_text(res):
    """从响应里取正文，顺带认 finish_reason。"""
    finish = ""
    try:
        finish = res["choices"][0].get("finish_reason") or ""
    except Exception:
        pass
    try:
        msg = res["choices"][0]["message"]
    except (KeyError, IndexError):
        raise RuntimeError("响应结构异常: %s" % str(res)[:200])
    text = (msg.get("content") or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text:
        reason = len(msg.get("reasoning_content") or "")
        if finish == "length":
            raise RuntimeError("输出被截断（finish_reason=length）：思考 + 正文"
                               "超过了额度（正文 0 字，思考 %d 字）。把额度调大再试。" % reason)
        raise RuntimeError("模型没有返回正文（reasoning_content 长度 %d）" % reason)
    return text


# 常见错误码 -> 人话
ERROR_HINTS = {
    "401": "key 无效，或者这个 key 和这个域名不属于同一个平台"
            "（国内 key 只能用 api.minimax.cn / api.minimaxi.com，"
            "国际 key 只能用 api.minimax.io / api.minimax.chat）",
    "402": "账户余额不足。到 MiniMax 控制台给「这个 key 所属的那个账号」充值，"
           "或者换一个 key（订阅套餐的 key 和充值的 key 可能不在同一个账号下）",
    "403": "key 有效但没这个模型的权限，换个模型名试试",
    "404": "这个域名上没有这个模型",
    "429": "调用太频繁，等一会儿再试",
    "1008": "余额不足，去充值",
}


def key_kind(key):
    """看 key 前缀判断它是「按量付费」还是「订阅」凭据。"""
    v = (key or "").strip()
    if v.startswith("sk-cp-"):
        return "subscription"
    if v.startswith("sk-api-"):
        return "payg"
    if v.startswith("eyJ"):
        return "jwt"
    return "unknown"


def _explain(err_text, key=None):
    """把 401/402 这类错误码翻译成能直接照做的中文。"""
    for code, hint in ERROR_HINTS.items():
        if ("HTTP %s" % code) in err_text or ("(%s)" % code) in err_text:
            extra = ""
            if code == "402":
                kind = key_kind(key)
                if kind == "payg":
                    extra = ("\n       你用的是「按量付费 key」（sk-api- 开头），它只扣账户余额。"
                             "想用订阅额度，去 MiniMax 控制台"
                             "「账户管理 → 订阅管理 → Token Plan / Coding Plan」"
                             "复制 Subscription Key（sk-cp- 开头）填进来。")
                elif kind == "subscription":
                    extra = ("\n       你用的是「订阅 key」（sk-cp- 开头），"
                             "额度用完了：等下一个 5 小时窗口重置，或在客户端里"
                             "「购买积分 / 升级套餐」。")
            return "%s\n       原因：%s%s" % (err_text, hint, extra)
    return err_text


def _one_attempt(base, key, model, tok_key, eff, max_tokens, system_prompt, user_prompt):
    body = {"model": model, tok_key: max_tokens}
    if eff:
        body["reasoning_effort"] = eff
    body["messages"] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        res = _http_chat(base, key, body, TIMEOUT)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(_explain("HTTP %s: %s" % (e.code, detail), key))
    except Exception as e:
        raise RuntimeError("请求失败: %s" % e)
    if res.get("base_resp", {}).get("status_code"):
        raise RuntimeError("接口报错: %s" % res["base_resp"])
    return _extract_text(res)


def probe_llm(base=None, key=None, models=None, timeout=60, bases=None):
    """探一遍：哪个 (域名, 模型, 参数) 组合能用。

    返回 (base, model, tok_key, effort, errors)。errors 记每种组合的失败原因，
    探测失败时直接打印出来，省得猜。
    """
    key = key or env("LLM_API_KEY", "MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("没有 API key")
    configured = (base or env("LLM_BASE_URL", "MINIMAX_BASE_URL",
                               default=DEFAULT_BASE_URL)).rstrip("/")
    if bases is None:
        extra = [b.strip().rstrip("/") for b in
                 env("LLM_BASE_CANDIDATES", default="").split(",") if b.strip()]
        bases = [configured] + [b for b in DEFAULT_BASE_CANDIDATES + extra if b != configured]
    primary = env("LLM_MODEL", "MINIMAX_MODEL", default=DEFAULT_MODEL)
    fallbacks = [m.strip() for m in env("LLM_FALLBACK_MODELS",
                                        default=",".join(DEFAULT_FALLBACK_MODELS)).split(",")
                 if m.strip()]
    models = models or ([primary] + [m for m in fallbacks if m != primary])
    effort = env("LLM_EFFORT", default="medium")
    errors = []
    for b in bases:
        auth_failed = False
        for model in models:
            for tok_key, eff in _candidate_bodies(model, 64, effort):
                body = {"model": model, tok_key: 64}
                if eff:
                    body["reasoning_effort"] = eff
                try:
                    res = _http_chat(b, key, dict(body, messages=[{"role": "user", "content": "ping"}]),
                                     timeout)
                    if res.get("base_resp", {}).get("status_code"):
                        raise RuntimeError("接口报错: %s" % res["base_resp"])
                    return b, model, tok_key, eff, errors
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", "replace")[:300]
                    errors.append("%s | %s | %s | effort=%s -> HTTP %s %s"
                                  % (b, model, tok_key, eff, e.code, detail))
                    if e.code in (401, 403):
                        auth_failed = True   # 这个域名不认这个 key，整个域名换掉
                        break
                except Exception as e:
                    errors.append("%s | %s | %s | effort=%s -> %s" % (b, model, tok_key, eff, e))
            if auth_failed:
                break
    return None, None, None, None, errors


def _model_chain():
    """主模型 + 回退模型。MiniMax-M3.1-Flash 只对 M Plan 开放，按量付费要退到 M3。"""
    primary = env("LLM_MODEL", "MINIMAX_MODEL", default=DEFAULT_MODEL)
    fallbacks = [m.strip() for m in env("LLM_FALLBACK_MODELS",
                                        default=",".join(DEFAULT_FALLBACK_MODELS)).split(",")
                 if m.strip()]
    return [primary] + [m for m in fallbacks if m != primary]


def call_llm(system_prompt, user_prompt, max_tokens=4096):
    """发一次请求；域名/参数/模型被拒就按候选顺序回退。

    候选顺序：配置域名 → 其他官方域名；每个域名下依次试模型和参数组合。
    401/403 直接换域名（说明 key 和站点不匹配），400 换下一个组合。
    全部失败时把每条错误都报出来，不猜。
    """
    key = env("LLM_API_KEY", "MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("没有 API key：设置 LLM_API_KEY（或 MINIMAX_API_KEY）")
    effort = env("LLM_EFFORT", default="medium")
    models = _model_chain()
    configured = env("LLM_BASE_URL", "MINIMAX_BASE_URL", default=DEFAULT_BASE_URL).rstrip("/")
    bases = [configured] + [b for b in DEFAULT_BASE_CANDIDATES if b != configured]

    errors = []
    for b in bases:
        auth_failed = False
        for model in models:
            for tok_key, eff in _candidate_bodies(model, max_tokens, effort):
                try:
                    text = _one_attempt(b, key, model, tok_key, eff, max_tokens,
                                        system_prompt, user_prompt)
                    if b != configured or model != models[0]:
                        print("[summarize] 已自动切换：%s / %s / %s"
                              % (b, model, tok_key))
                        # 记住这次跑通的组合，省得每天重新探测
                        try:
                            if b != configured:
                                bridge.save_env("LLM_BASE_URL", b)
                            if model != models[0]:
                                bridge.save_env("LLM_MODEL", model)
                            print("[summarize] 已写回 config.env，下次直接用这套")
                        except Exception as exc:
                            print("[summarize] 写回配置失败（不影响本次）：%s" % exc)
                    return text
                except RuntimeError as e:
                    errors.append("%s | %s | %s | effort=%s -> %s" % (b, model, tok_key, eff, e))
                    if "HTTP 401" in str(e) or "HTTP 403" in str(e):
                        auth_failed = True   # 这个域名不认这个 key，整个域名换掉
                        break
            if auth_failed:
                break
    raise RuntimeError("模型接口调不通，试了这些组合都不行：\n  - %s"
                       % "\n  - ".join(e[:260] for e in errors[:10]))


# ---------------------------------------------------------------- 主流程

CHUNK_PROMPT = """你在整理一天的微信沟通记录。下面是某个会话的聊天片段。
请只提炼事实性要点，用中文简洁列表输出：
1) 要做的事：谁答应了什么、涉及什么时间点
2) 进展：发生了什么、结论是什么
3) 要留意的：风险、冲突、没落实的事
4) 日常：一句话概括这个会话聊了什么

只写片段里真实出现的内容，不推断人名、日期和承诺；信息不足就不写这一项。不要评价人。"""


def summarize(daily, spec, verbose=True):
    chats = daily.get("chats", [])
    if not chats:
        raise RuntimeError("没有聊天内容可总结")

    # 日期必须显式喂给模型：聊天正文里只有 HH:MM，模型看不到年月日，
    # 让它自己填标题日期就会凭空编一个出来。
    date_str = daily.get("date", "")
    blocks = [render_chat(c) for c in chats]
    total = sum(len(b) for b in blocks)

    if total <= MAX_INPUT_CHARS:
        transcript = "\n\n".join(blocks)
        if verbose:
            print("[summarize] 单轮总结（%d 字）" % len(transcript))
        return call_llm(spec,
                        "日报日期：%s\n\n统计：%s\n\n今天的聊天记录：\n\n%s"
                        % (date_str, render_stats(daily), transcript),
                        max_tokens=16384)

    # 分块：先按会话切，每块再按长度切，逐块要点化，最后合并
    if verbose:
        print("[summarize] 内容较长（%d 字），分块处理" % total)
    notes = []
    for chat, block in zip(chats, blocks):
        name = chat.get("chat_name", "?")
        for i, piece in enumerate(split_text(block), 1):
            if verbose:
                print("  - %s 分块 %d" % (name, i))
            notes.append("### %s\n%s" % (name, call_llm(CHUNK_PROMPT, piece, max_tokens=8192)))

    merged = "\n\n".join(notes)
    if verbose:
        print("[summarize] 合并 %d 份要点" % len(notes))
    return call_llm(spec,
                     "日报日期：%s\n\n统计：%s\n\n以下是各会话的要点，请合并成一份日报：\n\n%s"
                     % (date_str, render_stats(daily), merged),
                     max_tokens=16384)


def ensure_title(report, date_str):
    """标题里的日期由脚本定死，不让模型自己编。

    模型即使在「日报日期」之外又写错日期，这里也会覆盖成真实日期。
    """
    if not date_str:
        return report
    title = "# 微信沟通日报 · %s" % date_str
    lines = report.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            lines[i] = title
            return "\n".join(lines)
    return title + "\n\n" + report


def ensure_overview(report, daily):
    """第二行的概览数字由脚本摆正。

    概览要放在**最前面**（读者最先看到的），但模型不一定照做，
    所以落盘前统一摆正：标题 -> 空行 -> 概览 -> 空行 -> 正文。
    """
    s = daily.get("stats", {}) or {}
    hours = s.get("active_hours") or []
    span = "%s-%s" % (hours[0], hours[-1]) if hours else "-"
    line = "%s 条消息 · %s 个会话 · 高峰 %s 时 · 活跃 %s" % (
        s.get("message_count", 0), s.get("chat_count", 0),
        s.get("peak_hour", "-"), span)

    lines = report.splitlines()
    title_idx = None
    for i, l in enumerate(lines):
        if l.startswith("# "):
            title_idx = i
            break
    if title_idx is None:
        return report
    title = lines[title_idx]

    body = lines[title_idx + 1:]
    # 去掉模型自己写的概览行（短行 + 同时含两个关键词才算）
    body = [l for l in body
            if not (len(l.strip()) <= 90 and "条消息" in l and "个会话" in l)]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return "\n".join([title, "", line, ""] + body)


def fix_invalid_dates(report):
    """把日历上不存在的日期（比如「11月34」）换成「未指定」。

    模型偶尔会拼出「11月34」这种日期。写进日报就是假信息，
    而这种纯格式问题机器查得比模型准 —— 提示词说了「不编造日期」，
    但光靠提示词不够，还得有道兜底的闸。
    """
    import calendar
    import re

    def repl(m):
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= calendar.monthrange(2024, month)[1]:
            return m.group(0)
        return "未指定"

    return re.sub(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?", repl, report)


def fix_suspicious_times(report):
    """待办行里孤零零一个时钟时间（如「16:01」）换成「未指定」。

    模型偶尔把**消息的发送时间**当成截止时间填进时间点 ——
    那等于凭空造了个 deadline，比不写更糟。
    真的约定时间一般带日期（「周六 08:00 集合」）或带「前/后」，
    只有 HH:MM 一个数孤零零的，多半是消息时间戳。
    """
    import re
    pattern = re.compile(
        r"(——\s*[^，\n]*?，)\s*(\d{1,2}:\d{2})\s*(（[^）\n]*）)?\s*$", re.M)

    def repl(m):
        return m.group(1) + "未指定" + (m.group(3) or "")

    return pattern.sub(repl, report)


def main(argv=None):
    bridge.load_env()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="本机生成微信日报")
    ap.add_argument("--date", default=None, help="目标日期，缺省取今天")
    ap.add_argument("--data", help="输入的 daily json；缺省 data/<date>.json")
    ap.add_argument("--out", help="输出 md；缺省 reports/<date>.md")
    ap.add_argument("--spec", help="日报规范；缺省 prompts/daily_report.md")
    ap.add_argument("--push", action="store_true", help="把日报推到中转（飞书日报列）")
    ap.add_argument("--bridge", default="", help="github / feishu / http")
    args = ap.parse_args(argv)

    tz = normalize.get_timezone(os.environ.get("WEIXIN_TZ", "Asia/Shanghai"))
    date_str = args.date or dt.datetime.now(tz).strftime("%Y-%m-%d")

    home = data_home()
    data_path = args.data or os.path.join(home, "data", date_str + ".json")
    spec_path = args.spec or os.path.join(ROOT, "prompts", "daily_report.md")
    out_path = args.out or os.path.join(home, "reports", date_str + ".md")

    if not os.path.isfile(data_path):
        sys.stderr.write("[error] 没有数据文件：%s（先跑采集）\n" % data_path)
        return 1
    with open(data_path, "r", encoding="utf-8") as f:
        daily = json.load(f)
    if not daily.get("stats", {}).get("message_count"):
        sys.stderr.write("[error] %s 这一天没有消息\n" % date_str)
        return 1

    with open(spec_path, "r", encoding="utf-8") as f:
        spec = f.read()

    try:
        report = summarize(daily, spec)
    except Exception as exc:
        sys.stderr.write("[error] %s\n" % exc)
        return 1

    report = ensure_title(report, daily.get("date") or date_str)
    report = ensure_overview(report, daily)
    report = fix_invalid_dates(report)
    report = fix_suspicious_times(report)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report.rstrip() + "\n")
    print("[ok] 日报已写入 %s" % out_path)

    if args.push:
        kind = (args.bridge or bridge.detect_bridge()).lower()
        if not kind:
            print("[warn] 未配置中转，跳过推送")
        else:
            bridge.do_push_report(date_str, report, kind)
            print("[ok] 已通过 %s 推送日报" % kind)
    return 0


if __name__ == "__main__":
    sys.exit(main())
