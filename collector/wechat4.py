#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信 4.1.10.31+ / 4.2 的解密导出链路（Windows）。

    python wechat4.py setup    # 一次性：装工具 + 恢复密钥 + 验证导出
    python wechat4.py export   # 每天：导出最近两天的聊天记录成 JSON

为什么要这么绕：微信 4.2 的数据库密钥不再以明文形式留在内存里，
工具自带的那套"扫明文"的路子必然 0 命中。正确链路是：

    静态扫 Weixin.dll 拿到 XOR 常量  →  只读扫内存拿混淆态候选
    →  XOR 还原成 passphrase  →  HMAC 逐库验真  →  注入给 chatlog-keeper 导出

本文件负责把这条链路串起来，具体算法来自开源实现（MIT 许可）：
    密钥恢复：anpingjing/wechat-chatlog-decrypt-export（tools/scan.py、key_v4.py）
    解密导出：labazhou2024/chatlog-keeper（官方 release 的 exe）
"""

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bridge  # noqa: E402

VERSION = "v27"
TOOLS_DIR = os.path.join(os.path.expanduser("~"), ".wechat_daily_tools")
RECOVERY_REPO = "https://github.com/anpingjing/wechat-chatlog-decrypt-export/archive/refs/heads/main.zip"
KEEPER_REPO = "https://github.com/labazhou2024/chatlog-keeper/releases/download/v0.3.7-preview"
KEEPER_EXE = "chatlog-keeper.exe"
PIP_DEPS = ["pefile", "pycryptodome", "pymem", "yara-python"]
MIRRORS = ["https://pypi.tuna.tsinghua.edu.cn/simple",
           "https://mirrors.aliyun.com/pypi/simple"]


def out(msg=""):
    print(msg)
    try:
        sys.stdout.flush()
    except Exception:
        pass


def run(cmd, cwd=None, timeout=None, stdin_text=None, utf8_child=False):
    """跑子进程。

    utf8_child=True：强制子进程用 UTF-8 输出，并把 stdout/stderr 按 UTF-8 解码。
    必须开这个的原因：子进程的 stdout 被管道接走时，Python 用的是系统 ANSI 代码页
    （中文 Windows = GBK）。恢复脚本里有一句 print('[✓] 命中!')，U+2713 在 GBK 里
    不存在，直接抛 UnicodeEncodeError —— 密钥都找到了，却在写文件前崩掉。
    """
    out("  > %s" % cmd)
    kwargs = {"errors": "replace"}
    env = None
    if utf8_child:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        kwargs["encoding"] = "utf-8"
    else:
        kwargs["text"] = True
    if stdin_text is not None:
        kwargs["input"] = stdin_text
    try:
        proc = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
                              capture_output=True, timeout=timeout, env=env, **kwargs)
    except subprocess.TimeoutExpired:
        out("  [X] 超时")
        return 1, "", ""
    except UnicodeDecodeError:
        out("  [X] 输出解码失败")
        return 1, "", ""
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def download(url, dest, timeout=600, retries=3):
    """下载文件，失败重试（release 资产的签名 URL 会过期，重试时重新拿）。"""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            with open(dest, "wb") as f:
                f.write(data)
            return len(data)
        except Exception as exc:
            last = exc
            out("    下载失败（第 %d/%d 次）：%s" % (attempt, retries, exc))
            if attempt < retries:
                import time
                time.sleep(3 * attempt)
    raise RuntimeError(str(last))


# ---------------------------------------------------------------- 工具准备

def ensure_recovery_tools():
    """anpingjing 的密钥恢复脚本。"""
    target = os.path.join(TOOLS_DIR, "recovery")
    if os.path.isfile(os.path.join(target, "tools", "run_recovery_generic.py")):
        out("    密钥恢复脚本已就位")
        return target
    out("    下载密钥恢复脚本 ...")
    tmp = os.path.join(tempfile.gettempdir(), "wechat4_recovery.zip")
    try:
        size = download(RECOVERY_REPO, tmp)
    except Exception as exc:
        out("    [X] 下载失败：%s" % exc)
        return None
    if size < 5000:
        out("    [X] 下载文件异常")
        return None
    parent = tempfile.mkdtemp(prefix="wechat4_rec_")
    try:
        with zipfile.ZipFile(tmp) as z:
            z.extractall(parent)
        inner = None
        for name in os.listdir(parent):
            cand = os.path.join(parent, name)
            if os.path.isfile(os.path.join(cand, "tools", "run_recovery_generic.py")):
                inner = cand
                break
        if not inner:
            out("    [X] 压缩包结构不对")
            return None
        if os.path.exists(target):
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(inner, target)
        out("    已放到 %s" % target)
        return target
    except Exception as exc:
        out("    [X] 解压失败：%s" % exc)
        return None
    finally:
        shutil.rmtree(parent, ignore_errors=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


def ensure_keeper():
    """chatlog-keeper.exe（带 sha256 校验）。"""
    exe = os.path.join(TOOLS_DIR, KEEPER_EXE)
    if os.path.isfile(exe):
        out("    chatlog-keeper 已就位")
        return exe
    out("    下载 chatlog-keeper.exe（27 MB，稍等）...")
    try:
        size = download("%s/%s" % (KEEPER_REPO, KEEPER_EXE), exe)
    except Exception as exc:
        out("    [X] 下载失败：%s" % exc)
        out("")
        out("    手动办法（网络下载不动时用这个）：")
        out("      浏览器打开 %s/%s" % (KEEPER_REPO, KEEPER_EXE))
        out("      下载后把文件改名成 chatlog-keeper.exe，放到：")
        out("      %s" % TOOLS_DIR)
        out("      然后重新运行本脚本。")
        return None
    out("    大小 %.1f MB，校验 sha256 ..." % (size / 1024.0 / 1024.0))
    try:
        sha_text = urllib.request.urlopen(
            "%s/%s.sha256" % (KEEPER_REPO, KEEPER_EXE), timeout=60).read().decode("utf-8", "replace")
        expected = sha_text.split()[0].strip().lower()
        actual = hashlib.sha256(open(exe, "rb").read()).hexdigest().lower()
        if expected and actual != expected:
            out("    [X] 校验不通过，可能下载不完整，删掉 %s 重试" % exe)
            try:
                os.remove(exe)
            except OSError:
                pass
            return None
        out("    校验通过")
    except Exception as exc:
        out("    [warn] 拿不到校验文件（%s），继续" % exc)
    return exe


def _pip_install(pkgs, quiet=False):
    """依次尝试官方源和国内镜像。返回 (成功?, 最后一次的错误输出)。"""
    flag = "-q " if quiet else ""
    sources = [""] + MIRRORS
    last_err = ""
    for src in sources:
        if src:
            host = src.split("/")[2]
            cmd = ('%s -m pip install %s%s -i %s --trusted-host %s'
                   % (sys.executable, flag, " ".join(pkgs), src, host))
        else:
            cmd = "%s -m pip install %s%s" % (sys.executable, flag, " ".join(pkgs))
        code, so, se = run(cmd, timeout=1200)
        if code == 0:
            return True, ""
        last_err = (se or so or "").strip()
        # 官方源超时是常态，直接切镜像
        if not src:
            out("    官方源失败，换国内镜像 ...")
    return False, last_err


def ensure_pip_deps():
    """装依赖。核心包必须成功；yara-python 装不上不算失败（用纯 Python 替身）。"""
    out("    安装 Python 依赖 ...")
    core = [p for p in PIP_DEPS if p != "yara-python"]
    ok, err = _pip_install(core)
    if not ok:
        out("    [X] 核心依赖安装失败：")
        for line in (err or "").splitlines()[-12:]:
            out("      %s" % line)
        return False
    out("    核心依赖 OK（%s）" % ", ".join(core))

    out("    安装 yara-python（可选）...")
    ok, err = _pip_install(["yara-python"], quiet=True)
    if ok:
        out("    yara-python OK")
    else:
        out("    yara-python 装不上（多半是 Python %d.%d 没有对应 wheel，"
            % sys.version_info[:2])
        out("    需要编译源码）。不影响：改用内置的纯 Python 匹配器。")
    return True


def ensure_yara_usable(recovery_dir):
    """保证 key_v4.py 的 `import yara` 能成功。

    真 yara-python 装不上时（Python 3.14 无 wheel），把内置替身拷成
    recovery/tools/yara.py —— 脚本目录优先于 site-packages，import yara 就落到替身上。
    """
    try:
        import yara  # noqa: F401
        out("    yara 可用（官方 yara-python）")
        return True
    except Exception:
        pass
    target_dir = os.path.join(recovery_dir, "tools")
    shim_src = os.path.join(HERE, "_yara_shim.py")
    if not os.path.isfile(shim_src):
        out("    [X] 找不到内置替身 %s" % shim_src)
        return False
    if not os.path.isdir(target_dir):
        out("    [X] 找不到恢复脚本目录 %s" % target_dir)
        return False
    dst = os.path.join(target_dir, "yara.py")
    shutil.copyfile(shim_src, dst)
    out("    已启用内置纯 Python yara 替身：%s" % dst)
    return True


# ---------------------------------------------------------------- 定位微信

def _read_text_auto(path, limit=8192):
    """ini 可能是 utf-8 / gbk / utf-16，按 BOM 和解码成功率猜编码。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _is_wechat_root(path):
    """xwechat_files 根目录：里面应该能翻出 db_storage。"""
    if not path or not os.path.isdir(path):
        return False
    if os.path.isdir(os.path.join(path, "db_storage")):
        return True
    try:
        for name in os.listdir(path):
            if os.path.isdir(os.path.join(path, name, "db_storage")):
                return True
    except OSError:
        pass
    return False


def _ini_paths():
    r"""微信 4.x 的配置 ini：文件内容本身通常就是一整行路径。

    这是最容易踩的坑——ini 里存的是**父目录**（例如 C:\Users\xxx\Documents），
    xwechat_files 是拼在它后面的。之前按「路径里含 xwechat_files」去正则匹配，
    一个都匹配不到，所以明明微信登录着也报「找不到 xwechat_files」。
    """
    found = []
    ini_dir = os.path.join(os.environ.get("APPDATA", ""), "Tencent", "xwechat", "config")
    if not os.path.isdir(ini_dir):
        return found
    for fn in sorted(os.listdir(ini_dir)):
        if not fn.lower().endswith(".ini"):
            continue
        text = _read_text_auto(os.path.join(ini_dir, fn))
        if not text:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip().strip('"')
            if not line or line.startswith(("[", ";", "#")):
                continue
            if re.match(r"^(?:[A-Za-z]:[\\/]|/)", line):
                found.append(line)          # 单行绝对路径（真实格式）
                continue
            m = re.search(r"=\s*\"?((?:[A-Za-z]:[\\/]|/)[^\"\r\n]*)", raw_line)
            if m:
                found.append(m.group(1).strip())
    return found


def _drive_roots():
    roots = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        d = "%s:\\" % letter
        if os.path.isdir(d):
            roots.append(d)
    return roots


def _candidate_roots(slow=False):
    """按可能性排序的候选路径。slow=True 时额外扫盘（慢，但兜底）。"""
    cands = []

    # 1) 配置 ini 里的路径
    for p in _ini_paths():
        cands.append(os.path.join(p, "xwechat_files"))
        cands.append(p)

    # 2) 常见位置
    home = os.path.expanduser("~")
    for base in (os.path.join(home, "Documents"),
                 os.path.join(home, "OneDrive", "Documents"),
                 os.path.join(home, "OneDrive"),
                 os.path.join(home, "Desktop"),
                 os.path.join(home, "Downloads"),
                 os.environ.get("APPDATA", ""),
                 os.environ.get("LOCALAPPDATA", ""),
                 "C:\\Users\\Public\\Documents"):
        if base:
            cands.append(os.path.join(base, "xwechat_files"))
            cands.append(base)

    # 3) 各盘常见名字
    for d in _drive_roots():
        for name in ("xwechat_files", "WeChat Files", "WeChatData", "Weixin"):
            cands.append(os.path.join(d, name))

    if slow:
        for d in _drive_roots():
            try:
                entries = os.listdir(d)
            except OSError:
                continue
            for name in entries:
                if name.lower() in ("windows", "$recycle.bin", "system volume information",
                                    "program files", "program files (x86)", "users",
                                    "程序文件", "windows.old", "$windows.~bt"):
                    continue
                sub = os.path.join(d, name)
                if not os.path.isdir(sub):
                    continue
                cands.append(os.path.join(sub, "xwechat_files"))
                cands.append(os.path.join(sub, "WeChat Files"))
                try:
                    for name2 in os.listdir(sub):
                        sub2 = os.path.join(sub, name2)
                        if os.path.isdir(sub2):
                            cands.append(os.path.join(sub2, "xwechat_files"))
                except OSError:
                    pass
    return cands


def find_wechat_root(data_root=None):
    """找 xwechat_files 根目录。

    顺序：显式指定 -> 配置 ini -> 常见位置 -> 扫盘（慢，最后兜底）。
    """
    if data_root:
        p = os.path.normpath(os.path.expandvars(os.path.expanduser(data_root)))
        if os.path.isdir(p):
            return p
        out("    指定的路径不存在：%s" % p)

    tried = []
    for c in _candidate_roots(slow=False):
        if _is_wechat_root(c):
            return os.path.normpath(c)
        tried.append(c)
    for c in _candidate_roots(slow=True):
        if _is_wechat_root(c):
            out("    （扫盘找到）%s" % c)
            return os.path.normpath(c)
        tried.append(c)

    out("    试过这些位置都没找到：")
    for t in tried[:12]:
        out("      %s" % t)
    out("    如果微信确实登录着，请用 --data-root 指定实际路径。")
    return None


def find_db_storages(root):
    """root 下每个 <账号>/db_storage。"""
    out_dirs = []
    try:
        for name in sorted(os.listdir(root)):
            cand = os.path.join(root, name, "db_storage")
            if os.path.isdir(cand):
                out_dirs.append(cand)
    except OSError:
        pass
    return out_dirs


def find_weixin_dll():
    """注册表里的安装目录 -> 版本号子目录 -> Weixin.dll（取版本号最大的）。"""
    install = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Tencent\Weixin") as key:
            install, _ = winreg.QueryValueEx(key, "InstallPath")
    except Exception:
        pass
    if not install or not os.path.isdir(install):
        return None, None
    best = None
    for name in os.listdir(install):
        if not re.match(r"^\d+\.\d+\.\d+\.\d+$", name):
            continue
        dll = os.path.join(install, name, "Weixin.dll")
        if os.path.isfile(dll):
            if best is None or name > best[0]:
                best = (name, dll)
    if best:
        return best[1], best[0]
    # 没有版本子目录时，退回直接找
    for root, _dirs, files in os.walk(install):
        if "Weixin.dll" in files:
            return os.path.join(root, "Weixin.dll"), None
    return None, None


def find_weixin_pid():
    """找微信主进程 PID（窗口标题含「微信」的窗口所属进程）。"""
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    found = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if "微信" in title or title.lower() == "wechat":
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.setdefault(pid.value, title)
        return True

    user32.EnumWindows(callback, 0)
    if found:
        return sorted(found.keys())[0]
    # 退路：tasklist 里所有 Weixin.exe，取最早启动的那个（子进程后起）
    try:
        res = subprocess.run("tasklist /FI \"IMAGENAME eq Weixin.exe\" /FO CSV /NH",
                             capture_output=True, text=True, timeout=30).stdout
        pids = []
        for line in res.splitlines():
            parts = [p.strip('" ') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == "weixin.exe":
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
        if pids:
            return pids[0]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 恢复密钥

def recover_passphrase(recovery_dir, pid, dll, db_storages):
    out("    恢复 passphrase（扫内存 + HMAC 验真，可能要几分钟）...")
    script = os.path.join(recovery_dir, "tools", "run_recovery_generic.py")
    cmd = "%s %s %d \"%s\"" % (sys.executable, script, pid, dll)
    for d in db_storages:
        cmd += " \"%s\"" % d
    code, stdout, stderr = run(cmd, cwd=os.path.join(recovery_dir, "tools"), timeout=1800,
                               utf8_child=True)
    out((stdout or "")[-1500:])
    if stderr.strip():
        out((stderr or "")[-800:])
    key_file = os.path.join(recovery_dir, "tools", "recovered_key.txt")
    blob = ""
    if os.path.isfile(key_file):
        blob = open(key_file, encoding="utf-8", errors="replace").read()
    else:
        out("    [warn] 没生成 recovered_key.txt，先从日志里找密钥")
        blob = (stdout or "") + "\n" + (stderr or "")

    m = re.search(r"final\s*=\s*([0-9A-Fa-f]{32,128})", blob)
    if not m:
        m = re.search(r"([0-9A-Fa-f]{64})", blob)
    if not m:
        out("    [X] 输出里也没找到密钥")
        out(blob[-800:])
        return None
    if not os.path.isfile(key_file):
        out("    （文件没写成，但密钥已从日志中取回）")
    return m.group(1)


def inject_key(keeper_exe, passphrase, root):
    out("    把密钥注入 chatlog-keeper ...")
    code, stdout, stderr = run('"%s" set-key --source wechat --data-root "%s" --key-stdin'
                               % (keeper_exe, root), timeout=600,
                               stdin_text=passphrase + "\n", utf8_child=True)
    if stdout.strip():
        out((stdout or "").strip()[-800:])
    if code != 0 and stderr.strip():
        out((stderr or "").strip()[-800:])
    return code == 0


DIRECTORY_FILE = "wechat_directory.json"


def do_export(keeper_exe, root, out_dir, days=2):
    out("    导出最近 %d 天的聊天记录 ..." % days)
    os.makedirs(out_dir, exist_ok=True)
    code, stdout, stderr = run('"%s" wechat --days %d --out "%s" --data-root "%s"'
                               % (keeper_exe, days, out_dir, root), timeout=3600,
                               utf8_child=True)
    if stdout.strip():
        out((stdout or "").strip()[-1200:])
    if code != 0:
        if stderr.strip():
            out((stderr or "").strip()[-800:])
        return False
    _export_directory(keeper_exe, root, out_dir)
    return True


def _export_directory(keeper_exe, root, out_dir):
    """顺便列一份「全部会话名单」。

    消息导出里只有最近几天有消息的会话；白名单群如果今天没人说话，
    光看消息是发现不了它的。directory 子命令只列会话、不读消息内容，
    正好用来补上这一块。
    """
    path = os.path.join(out_dir, DIRECTORY_FILE)
    code, stdout, stderr = run('"%s" directory --source wechat --data-root "%s"'
                               % (keeper_exe, root), timeout=900, utf8_child=True)
    if code != 0 or not stdout.strip():
        out("    (会话名单没列出来，白名单里今天没人说话的群会看不到)")
        return False
    try:
        data = json.loads(stdout)
    except Exception as exc:
        out("    (会话名单解析失败：%s)" % exc)
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    n = len(data.get("conversations") or [])
    out("    会话名单：%d 个（含今天没人说话的）" % n)
    return True


# ---------------------------------------------------------------- 子命令


# ---------------------------------------------------------------- config.env

CONFIG_TEMPLATE = """# 微信日报配置（本文件由 setup 自动生成，改完保存即可）
# 已经存在的环境变量优先于这里的值

# ---------- 微信 4.x 数据位置 ----------
# xwechat_files 根目录（setup 自动探测）
WECHAT_DATA_DIR={data_root}

# ---------- 解密导出工具 ----------
# 微信 4.1.10.31+ / 4.2 的库是 SQLCipher 4 加密的，必须用 wechat4 这条链路
EXPORT_CMD={export_cmd}

# 导出文件放哪（导出和归一化都读这个目录）
EXPORT_DIR={export_dir}

# ---------- 飞书中转（下面三项要你自己填） ----------
WEIXIN_BRIDGE=feishu
FS_APP_ID={fs_app_id}
FS_APP_SECRET={fs_app_secret}
# 表格 URL 里的 app_token 与 table_id
FS_TABLE_ID={fs_table_id}
FS_FIELD_DATE=日期
FS_FIELD_MSG=消息数
FS_FIELD_PAYLOAD=内容
FS_FIELD_REPORT=日报

# ---------- 本机摘要（用你自己的 API key，聊天记录不出本机） ----------
LLM_API_KEY={llm_api_key}
LLM_BASE_URL={llm_base_url}
LLM_MODEL={llm_model}
LLM_EFFORT={llm_effort}

# ---------- 日报范围 ----------
# mine = 只保留「我发过言」的会话（默认）；all = 全部会话都进日报
DAILY_SCOPE={daily_scope}
# 以我为中心裁剪上下文：保留我发言前后 N 条。0 = 只留我自己的；-1 = 不裁剪
DAILY_CONTEXT_WINDOW={daily_context_window}
# 白名单群名（逗号分隔）：这些群不管我有没有说话都进日报；群名包含关键字即命中
DAILY_INCLUDE_CHATS={daily_include_chats}
# 白名单群我不在里面说话时，最多保留最近多少条
DAILY_INCLUDE_MAX={daily_include_max}
"""


def _read_env_file(path):
    data = {}
    if not os.path.isfile(path):
        return data
    with open(path, "rb") as fh:
        raw = fh.read()
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
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        data[key.strip()] = val.strip()
    return data


def write_config(data_root=None, path=None):
    """写 config.env：已经填好的值保留，缺的用探测结果补上。"""
    project_root = os.path.dirname(HERE)
    path = path or os.path.join(project_root, "config.env")
    cur = _read_env_file(path)

    root = data_root or cur.get("WECHAT_DATA_DIR") or ""
    export_dir = cur.get("EXPORT_DIR") or os.path.join(os.path.expanduser("~"), "wx_export")
    # EXPORT_CMD 用**相对路径**（daily.py 会以项目根为工作目录执行它），
    # 这样整个项目文件夹挪位置也不用改配置。
    # 用户换了解密工具（命令里不是 wechat4.py）就保留用户自己的写法。
    old_cmd = cur.get("EXPORT_CMD", "")
    if old_cmd and "wechat4.py" not in old_cmd:
        export_cmd = old_cmd
    else:
        export_cmd = '"%s" "collector\\wechat4.py" export' % sys.executable

    content = CONFIG_TEMPLATE.format(
        data_root=root,
        export_cmd=export_cmd,
        export_dir=export_dir,
        fs_app_id=cur.get("FS_APP_ID", ""),
        fs_app_secret=cur.get("FS_APP_SECRET", ""),
        fs_table_id=cur.get("FS_TABLE_ID", ""),
        llm_api_key=cur.get("LLM_API_KEY", ""),
        llm_base_url=cur.get("LLM_BASE_URL", "https://api.minimax.cn/v1"),
        llm_model=cur.get("LLM_MODEL", "MiniMax-M3.1-Flash"),
        llm_effort=cur.get("LLM_EFFORT", "medium"),
        daily_scope=cur.get("DAILY_SCOPE", "mine"),
        daily_context_window=cur.get("DAILY_CONTEXT_WINDOW", "5"),
        daily_include_chats=cur.get("DAILY_INCLUDE_CHATS", ""),
        daily_include_max=cur.get("DAILY_INCLUDE_MAX", "50"),
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def cmd_init_config(args):
    out("=" * 56)
    out(" 生成 config.env")
    out("=" * 56)
    out("")
    root = find_wechat_root(getattr(args, "data_root", None))
    if not root:
        out("    [warn] 没探测到 xwechat_files，WECHAT_DATA_DIR 先留空")
    path = write_config(root)
    out("")
    out("    已写好：%s" % path)
    out("    已自动填好：WECHAT_DATA_DIR / EXPORT_DIR / EXPORT_CMD")
    out("    还需要你填三项（见文件里 FS_ 开头的）：")
    out("      FS_APP_ID / FS_APP_SECRET / FS_TABLE_ID")
    out("    以及本机摘要用的：LLM_API_KEY")
    return 0


def cmd_setup(args):
    out("=" * 56)
    out(" 微信 4.2 解密链路 · 一次性设置")
    out("=" * 56)
    out("")
    os.makedirs(TOOLS_DIR, exist_ok=True)

    out("[1/6] 准备工具")
    recovery = ensure_recovery_tools()
    if not recovery:
        return 1
    keeper = ensure_keeper()
    if not keeper:
        return 1

    out("[2/6] 安装 Python 依赖")
    if not ensure_pip_deps():
        return 1
    if not ensure_yara_usable(recovery):
        return 1

    out("[3/6] 定位微信")
    root = find_wechat_root(getattr(args, "data_root", None))
    if not root:
        out("    [X] 找不到 xwechat_files 目录。微信登录过吗？")
        out("")
        out("    微信的配置 ini 在这里，内容我打印一下，你贴给我就能直接定位：")
        ini_dir = os.path.join(os.environ.get("APPDATA", ""), "Tencent", "xwechat", "config")
        if os.path.isdir(ini_dir):
            for fn in sorted(os.listdir(ini_dir)):
                if not fn.lower().endswith(".ini"):
                    continue
                text = _read_text_auto(os.path.join(ini_dir, fn))
                out("      %s -> %s" % (fn, (text or "").strip()[:200]))
        else:
            out("      （没有 %s 这个目录）" % ini_dir)
        out("")
        out("    也可以手动指定：setup.bat --data-root \"D:\\你的路径\\xwechat_files\"")
        return 1
    out("    数据根目录: %s" % root)
    db_storages = find_db_storages(root)
    if not db_storages:
        out("    [X] 下面没有 db_storage 目录")
        return 1
    for d in db_storages:
        out("    账号库: %s" % d)

    dll, version = find_weixin_dll()
    if not dll:
        out("    [X] 找不到 Weixin.dll")
        return 1
    out("    Weixin.dll: %s" % dll)

    pid = find_weixin_pid()
    if not pid:
        out("    [X] 找不到微信进程。微信开着并登录了吗？")
        return 1
    out("    微信主进程 PID: %d" % pid)

    out("[4/6] 恢复密钥")
    passphrase = recover_passphrase(recovery, pid, dll, db_storages)
    if not passphrase:
        out("")
        out("    [X] 没拿到密钥。常见原因：")
        out("      - 不是管理员身份运行（右键 -> 以管理员身份运行）")
        out("      - 微信没登录，或 PID 取到了子进程")
        out("      - db_storage 指向了没登录的账号目录")
        out("      - 微信版本又变了（scan.py 的特征码要更新）")
        out("      把上面的日志发我。")
        return 1
    out("    拿到 passphrase ✓")

    out("[5/6] 注入密钥")
    if not inject_key(keeper, passphrase, root):
        out("    [X] 注入失败")
        return 1

    out("[6/6] 验证导出")
    export_dir = os.path.join(TOOLS_DIR, "export_test")
    if not do_export(keeper, root, export_dir, days=2):
        out("    [X] 导出失败")
        return 1
    jsons = []
    for root_dir, _d, files in os.walk(export_dir):
        for f in files:
            if f.lower().endswith(".json"):
                jsons.append(os.path.join(root_dir, f))
    if not jsons:
        out("    [X] 没导出 JSON 文件")
        return 1
    out("    导出成功: %s" % ", ".join(os.path.basename(j) for j in jsons[:3]))

    out("")
    out("=" * 56)
    out(" 全部搞定！")
    out("=" * 56)
    out("接下来填 config.env：")
    out("  WECHAT_DATA_DIR=%s" % root)
    out("  EXPORT_DIR=%%USERPROFILE%%\\wx_export")
    out("  EXPORT_CMD=python \"%s\"" % os.path.join(HERE, "wechat4.py") + " export")
    out("")
    cfg_path = write_config(root)
    out("  config.env 已自动写好：%s" % cfg_path)
    out("")
    out("然后跑一次：python \"%s\" export" % os.path.join(HERE, "wechat4.py"))
    return 0


def cmd_export(args):
    bridge.load_env()
    root = (os.environ.get("WECHAT_DATA_DIR", "").strip()
            or find_wechat_root(getattr(args, "data_root", None)))
    if not root or not os.path.isdir(root):
        sys.stderr.write("[error] 找不到 xwechat_files 根目录，先跑 setup\n")
        return 1
    keeper = os.path.join(TOOLS_DIR, KEEPER_EXE)
    if not os.path.isfile(keeper):
        sys.stderr.write("[error] chatlog-keeper 没装好，先跑 setup\n")
        return 1
    export_dir = args.out or os.environ.get("EXPORT_DIR", "").strip()
    if not export_dir:
        sys.stderr.write("[error] 没指定导出目录：加 --out 或设 EXPORT_DIR\n")
        return 2
    days = args.days or int(os.environ.get("EXPORT_DAYS", "2"))
    if not do_export(keeper, root, export_dir, days):
        return 1
    out("[ok] 导出完成: %s" % export_dir)
    return 0



def parse_bitable_url(url):
    """从飞书表格网址里取出 app_token 和 table_id。

    为什么要这个：表格网址里不含密钥，复制粘贴不会被平台打码；
    而 app_token/table_id 本身在聊天里会被替换成 {{ARCHON_SECRET:...}} 占位符。
    所以最稳的做法是让用户贴网址，由程序自己解析。
    """
    url = (url or "").strip()
    m = re.search(r"/base/([A-Za-z0-9]+)", url)
    t = re.search(r"[?&]table=([A-Za-z0-9]+)", url)
    if not m or not t:
        return None
    return m.group(1), t.group(1)


def _is_masked(value):
    """平台会把聊天里的密钥串换成 {{ARCHON_SECRET:NAME}} 占位符。"""
    v = value or ""
    return "{{" in v or "ARCHON_SECRET" in v


def cmd_set(args):
    """在 config.env 里改值：wechat4.py set KEY=VALUE [KEY=VALUE ...]

    比开记事本手改稳：不会存成 .txt，不会漏等号，值里的 = 和空格都能正确处理。
    """
    project_root = os.path.dirname(HERE)
    # 和 bridge.load_env 保持一致：可以用 WEIXIN_ENV_FILE 指向别的配置文件
    path = os.environ.get("WEIXIN_ENV_FILE") or os.path.join(project_root, "config.env")
    if not os.path.isfile(path):
        # 文件不存在就先探一次数据目录，别生成一个空配置的 config.env
        root = find_wechat_root()
        path = write_config(root, path)
        out("    已创建 %s（数据目录：%s）" % (path, root or "没探测到，之后用 set 补"))

    pairs = []
    for item in args.values:
        if "=" not in item:
            out("[X] 格式不对：%s（要写成 KEY=VALUE）" % item)
            return 2
        key, val = item.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if _is_masked(val):
            out("[X] %s 的值看起来是平台打码后的占位符，不是真值。" % key)
            out("    聊天里的密钥串会被自动替换成 {{ARCHON_SECRET:...}}，别从聊天里复制。")
            out("    表格的话，直接贴网址，让程序自己解析：")
            out("        python collector\\wechat4.py set-table \"https://xxx.feishu.cn/base/appXXX?table=tblYYY\"")
            return 2
        if key.upper() == "FS_TABLE_ID" and ("/base/" in val or "feishu.cn" in val):
            parsed = parse_bitable_url(val)
            if parsed:
                val = "%s/%s" % parsed
                out("    （从网址里解析出表 ID）")
        pairs.append((key, val))
    if not pairs:
        out("[X] 至少要写一个 KEY=VALUE")
        return 2

    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.read().split("\n")

    for key, val in pairs:
        replaced = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.split("=", 1)[0].strip() == key:
                lines[i] = "%s=%s" % (key, val)
                replaced = True
                break
        if not replaced:
            lines.append("%s=%s" % (key, val))
        out("    %s = %s" % (key, ("（已填，值不显示）" if _is_secret(key) else val)))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    out("")
    out("    已保存：%s" % path)
    return 0


def _is_secret(key):
    k = key.upper()
    return "SECRET" in k or "KEY" in k or "TOKEN" in k




def cmd_scope(args):
    """查看和调整日报范围：哪些会话进日报、上下文怎么裁。"""
    bridge.load_env()
    out("=" * 60)
    out(" 日报范围")
    out("=" * 60)
    out("")

    path = os.environ.get("WEIXIN_ENV_FILE") or os.path.join(ROOT, "config.env")
    cur = _read_env_file(path)
    scope = (cur.get("DAILY_SCOPE") or "mine").strip().lower()
    try:
        window = int((cur.get("DAILY_CONTEXT_WINDOW") or "5").strip())
    except (TypeError, ValueError):
        window = 5
    include_chats = (cur.get("DAILY_INCLUDE_CHATS") or "").strip()
    try:
        include_max = int((cur.get("DAILY_INCLUDE_MAX") or "50").strip())
    except (TypeError, ValueError):
        include_max = 50

    # 只改不显示具体值，避免把敏感配置打到屏幕上
    changed = False
    if getattr(args, "scope", None):
        scope = args.scope.strip().lower()
        changed = True
    if getattr(args, "window", None) is not None:
        window = int(args.window)
        changed = True
    if getattr(args, "include", None) is not None:
        include_chats = args.include.strip()
        changed = True
    if getattr(args, "clear_include", False):
        include_chats = ""
        changed = True
    if getattr(args, "include_max", None) is not None:
        include_max = int(args.include_max)
        changed = True
    if getattr(args, "reset", False):
        scope, window, include_chats = "mine", 5, ""
        changed = True

    if changed:
        text = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        lines = text.splitlines() if text else []
        for key, val in (("DAILY_SCOPE", scope),
                         ("DAILY_CONTEXT_WINDOW", str(window)),
                         ("DAILY_INCLUDE_CHATS", include_chats),
                         ("DAILY_INCLUDE_MAX", str(include_max))):
            found = False
            for i, line in enumerate(lines):
                raw = line.strip()
                if raw.startswith("export "):
                    raw = raw[7:].strip()
                if raw.startswith("#") or "=" not in raw:
                    continue
                if raw.split("=", 1)[0].strip() == key:
                    lines[i] = "%s=%s" % (key, val)
                    found = True
                    break
            if not found:
                lines.append("%s=%s" % (key, val))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")
        out("  已保存到 %s" % path)
        out("")

    scope_desc = ("只保留「我发过言」的会话" if scope != "all"
                  else "全部会话都进日报")
    win_desc = ("不裁剪" if window < 0 else
                ("只留我自己的消息" if window == 0 else
                 "保留我发言前后 %d 条" % window))
    out("  当前设置")
    out("    DAILY_SCOPE = %s        （%s）" % (scope, scope_desc))
    out("    DAILY_CONTEXT_WINDOW = %-3s（%s）" % (window, win_desc))
    out("    DAILY_INCLUDE_CHATS = %s" % (include_chats or "(空)"))
    if include_chats:
        out("      ↑ 白名单：这些群不管你说不说话都进日报（群名包含关键字即命中）")
        out("      ↑ 我不在里面说话时，最多保留最近 %d 条" % include_max)
    out("")

    if getattr(args, "preview", False):
        return _scope_preview(args, scope, window, include_chats, include_max)
    return 0


def _scope_preview(args, scope, window, include_chats="", include_max=50):
    """跑一次导出，看看今天到底哪些会话会进日报。"""
    import datetime as _dt
    import normalize

    date_str = getattr(args, "date", None)
    tz = normalize.get_timezone(os.environ.get("WEIXIN_TZ", "Asia/Shanghai"))
    if not date_str:
        date_str = _dt.datetime.now(tz).strftime("%Y-%m-%d")

    export_dir = os.environ.get("EXPORT_DIR", "").strip()
    cmd = os.environ.get("EXPORT_CMD", "").strip()
    if cmd:
        out("  正在跑一次导出以便预览（十几秒）...")
        try:
            proc = subprocess.run(cmd, shell=True, cwd=ROOT)
            if proc.returncode != 0:
                out("  [X] 导出失败（退出码 %d），下面用已有的导出目录预览" % proc.returncode)
        except Exception as exc:
            out("  [X] 导出起不来：%s" % exc)
    if not export_dir or not os.path.isdir(export_dir):
        out("  [X] 找不到导出目录：%s" % (export_dir or "(没配 EXPORT_DIR)"))
        return 1

    msgs = normalize.collect_sources([export_dir], tz)
    aliases = set(a.strip() for a in
                  (os.environ.get("WEIXIN_SELF_ALIASES") or "我,自己,Self").split(",")
                  if a.strip())
    known_chats = normalize.collect_directory([export_dir])
    rows = normalize.preview_scope(msgs, date_str, tz, "Asia/Shanghai", aliases,
                                   only_mine=(scope != "all"), context_window=window,
                                   include_chats=include_chats, include_max=include_max,
                                   known_chats=known_chats)
    out("")
    out("  %s 的会话筛选预览" % date_str)
    out("  " + "-" * 56)
    if not rows:
        out("    没有解析到消息")
        return 1
    kept_chats = 0
    for r in rows:
        if r["included"]:
            kept_chats += 1
            if r.get("silent"):
                mark = "[静]"
                detail = "今天一条消息都没有（白名单群，今天没人发言）"
            elif r.get("by_whitelist"):
                mark = "[白]"
                detail = "共 %d 条 → 保留最近 %d 条（白名单强制纳入，我没说话）" % (
                    r["total"], r["kept"])
            else:
                mark = "[留]"
                detail = "共 %d 条 → 保留 %d 条（我说了 %d 条）" % (
                    r["total"], r["kept"], r["mine_count"])
        else:
            mark, detail = "[跳]", "共 %d 条，我没在里面说过话" % r["total"]
        out("    %s %-14s %s   %s" % (
            mark, r["chat_name"][:14],
            "群聊" if r["chat_type"] == "group" else "单聊", detail))
    out("  " + "-" * 56)
    out("    %d 个会话进日报，%d 个跳过" % (kept_chats, len(rows) - kept_chats))
    out("")
    return 0


def cmd_set_table(args):
    """wechat4.py set-table <表格网址>"""
    out("=" * 56)
    out(" 从网址解析表格 ID")
    out("=" * 56)
    out("")
    parsed = parse_bitable_url(getattr(args, "url", ""))
    if not parsed:
        out("[X] 这个网址里没解析出 app_token / table_id")
        out("    要长这样：https://xxx.feishu.cn/base/appXXXXXXXX?table=tblYYYYYYYY")
        return 2
    app_token, table_id = parsed
    out("    app_token = %s" % app_token)
    out("    table_id  = %s" % table_id)
    rc = cmd_set(type("A", (), {"values": ["FS_TABLE_ID=%s/%s" % (app_token, table_id)]})())
    return rc



def read_clipboard():
    """读 Windows 剪贴板文本。"""
    try:
        ps = "Get-Clipboard -Raw"
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def cmd_set_clip(args):
    """从剪贴板读值写进配置：wechat4.py set-clip FS_APP_ID

    为什么要这个：从飞书页面复制 App Secret 时，容易少复制一个字符、
    或者不小心把聊天里的打码占位符也复制进去。直接读剪贴板就没有这类意外。
    """
    out("=" * 56)
    out(" 从剪贴板读取配置值")
    out("=" * 56)
    out("")
    keys = list(getattr(args, "keys", []) or [])
    if not keys:
        out("[X] 要指定键名，例如：set-clip FS_APP_ID")
        return 2
    value = read_clipboard()
    if not value:
        out("[X] 读不到剪贴板内容（剪贴板是空的？）")
        out("    先在飞书页面选中要复制的内容，再运行这个命令。")
        return 2
    if _is_masked(value):
        out("[X] 剪贴板里是打码占位符，不是真值。")
        out("    请到飞书开放平台「凭证与基础信息」页面重新复制。")
        return 2
    # 有些 key 有明确格式要求，贴错时直接拦下来
    for key in keys:
        k = key.strip().upper()
        if k == "LLM_API_KEY":
            if any(ch.isspace() for ch in value):
                out("[X] 这个值里含空白字符（换行/空格），不可能是 API key。")
                out("    剪贴板里多半是好几段内容，重新只复制 key 本身。")
                return 2
            if len(value) < 32:
                out("[X] 只有 %d 个字符，太短了 —— MiniMax 的 API key 是 32 位或 64 位。" % len(value))
                out("    你复制到的大概不是 key（可能是编号、验证码或别的文本）。")
                return 2
    pairs = []
    for key in keys:
        pairs.append("%s=%s" % (key.strip(), value))
    return cmd_set(type("A", (), {"values": pairs})())


def _force_utf8_console():
    """把 Windows 控制台代码页切成 UTF-8，这样 ✓、→ 这些字符才显示得出来。"""
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


def cmd_version(args):
    """打印版本，顺便告诉用户从哪查配置。"""
    out("wechat-daily %s" % VERSION)
    out("")
    out("有这些命令就说明是新版本：")
    out("  setup / export / init-config / set / set-table / set-clip / scope")
    out("")
    cfg = os.environ.get("WEIXIN_ENV_FILE") or os.path.join(ROOT, "config.env")
    out("配置文件：%s%s" % (cfg, "" if os.path.isfile(cfg) else "  （还没生成）"))
    return 0


def main(argv=None):
    _force_utf8_console()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="微信 4.x 解密导出（%s）" % VERSION)
    ap.add_argument("--version", action="store_true", help="打印版本号")
    sub = ap.add_subparsers(dest="cmd")
    p_setup = sub.add_parser("setup", help="一次性设置（装工具、恢复密钥、验证）")
    p_setup.add_argument("--data-root", dest="data_root",
                          help="手动指定 xwechat_files 目录（自动找不到时才需要）")
    p_setup.set_defaults(func=cmd_setup)
    p_export = sub.add_parser("export", help="导出最近几天的聊天记录")
    p_export.add_argument("--out", help="导出目录")
    p_export.add_argument("--days", type=int, default=None, help="导出最近几天（默认 2）")
    p_export.add_argument("--data-root", dest="data_root",
                          help="手动指定 xwechat_files 目录")
    p_export.set_defaults(func=cmd_export)
    p_cfg = sub.add_parser("init-config", help="自动生成 config.env（不用手抄路径）")
    p_cfg.add_argument("--data-root", dest="data_root",
                       help="手动指定 xwechat_files 目录")
    p_cfg.set_defaults(func=cmd_init_config)
    p_set = sub.add_parser("set", help="改 config.env 里的值（KEY=VALUE ...）")
    p_set.add_argument("values", nargs="+", help="要写入的键值对，例如 FS_TABLE_ID=appX/tblY")
    p_set.set_defaults(func=cmd_set)
    p_st = sub.add_parser("set-table", help="从飞书表格网址里取 app_token/table_id 填进配置")
    p_st.add_argument("url", help="表格网址，形如 https://xxx.feishu.cn/base/appXXX?table=tblYYY")
    p_st.set_defaults(func=cmd_set_table)
    p_sc = sub.add_parser("set-clip", help="从剪贴板读值写进配置（不会复制错）")
    p_sc.add_argument("keys", nargs="+", help="键名，例如 FS_APP_ID")
    p_sc.set_defaults(func=cmd_set_clip)
    p_scope = sub.add_parser("scope", help="查看/调整日报范围（哪些会话进日报）")
    p_scope.add_argument("--scope", choices=["mine", "all"], help="mine=只留我参与过的会话；all=全部")
    p_scope.add_argument("--window", type=int, help="上下文窗口 N；0=只留我自己的；-1=不裁剪")
    p_scope.add_argument("--reset", action="store_true", help="恢复默认（mine / 5）")
    p_scope.add_argument("--include", default=None,
                         help="白名单群名（逗号分隔），不管我有没有说话都进日报")
    p_scope.add_argument("--clear-include", action="store_true", help="清空白名单")
    p_scope.add_argument("--include-max", type=int, default=None,
                         help="白名单群我不在里面说话时，最多保留最近多少条（默认 50）")
    p_scope.add_argument("--preview", action="store_true", help="预览今天哪些会话会进日报")
    p_scope.add_argument("--date", help="预览哪一天，默认今天")
    p_scope.set_defaults(func=cmd_scope)

    args = ap.parse_args(argv)
    if getattr(args, "version", False):
        return cmd_version(args)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
