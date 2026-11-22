#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第一步：安装 wxq（微信 4.x 的本地记录解密导出工具）并取密钥。

    python setup_wxq.py

前提：
  - 微信电脑版已登录并在运行（密钥只在登录态下才在内存里）
  - 用管理员身份运行（读进程内存需要）
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WXQ_REPO = "https://github.com/vpcoderli/wxq.git"
WXQ_DIR = os.path.join(os.path.expanduser("~"), "wxq")


def out(msg=""):
    print(msg)
    try:
        sys.stdout.flush()
    except Exception:
        pass


def run(cmd, cwd=None, timeout=None):
    out("  > %s" % cmd)
    try:
        proc = subprocess.run(cmd, cwd=cwd, shell=True, timeout=timeout)
        return proc.returncode
    except Exception as exc:
        out("  执行失败: %s" % exc)
        return 1


def check_python():
    if sys.version_info < (3, 10):
        out("[X] Python 版本太低（当前 %d.%d.%d），wxq 需要 3.10 以上。"
            % sys.version_info[:3])
        out("    去 https://www.python.org/downloads/ 装个新版本，"
            "安装时勾选 Add Python to PATH。")
        return False
    out("[1/4] Python %d.%d.%d ✓" % sys.version_info[:3])
    return True


def download_zip(dest_dir):
    """不依赖 git：直接下载仓库 ZIP 并解压。"""
    url = "https://github.com/vpcoderli/wxq/archive/refs/heads/main.zip"
    out("    正在下载 %s ..." % url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    tmp = os.path.join(tempfile.gettempdir(), "wxq_src.zip")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = resp.read()
    except Exception as exc:
        out("    下载失败：%s" % exc)
        return False
    if len(data) < 10000:
        out("    下载到的文件太小，可能被网络拦了。")
        return False
    with open(tmp, "wb") as f:
        f.write(data)
    parent = tempfile.mkdtemp(prefix="wxq_dl_")
    try:
        with zipfile.ZipFile(tmp) as z:
            z.extractall(parent)
        inner = None
        for name in os.listdir(parent):
            candidate = os.path.join(parent, name)
            if is_source_dir(candidate):
                inner = candidate
                break
        if not inner:
            out("    解压后没找到 pyproject.toml，仓库结构可能变了。")
            return False
        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir, ignore_errors=True)
        shutil.move(inner, dest_dir)
        out("    已解压到 %s" % dest_dir)
        return True
    except Exception as exc:
        out("    解压失败：%s" % exc)
        return False
    finally:
        shutil.rmtree(parent, ignore_errors=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


def is_source_dir(path):
    """有没有 pyproject.toml 或 setup.py 就算源码目录。"""
    return (os.path.isfile(os.path.join(path, "pyproject.toml"))
            or os.path.isfile(os.path.join(path, "setup.py")))


def ensure_wxq_source():
    """把 wxq 源码弄到本地。优先直接下载 ZIP，不需要 git。"""
    if is_source_dir(WXQ_DIR):
        out("[2/4] wxq 源码已存在: %s" % WXQ_DIR)
        return True

    out("[2/4] 下载 wxq 源码 ...")
    if download_zip(WXQ_DIR):
        return True

    if shutil.which("git"):
        out("    改用 git ...")
        if run("git clone --depth 1 %s \"%s\"" % (WXQ_REPO, WXQ_DIR)) == 0:
            return True

    out("    [X] 自动下载失败。手动办法：浏览器打开 %s" % WXQ_REPO)
    out("       点 Code -> Download ZIP，下载后解压到 %s" % WXQ_DIR)
    out("       （解压后要能直接看到 pyproject.toml），然后重新运行本脚本。")
    return False


def install():
    out("[3/4] 安装 wxq ...")
    if run("%s -m pip install -e ." % sys.executable, cwd=WXQ_DIR, timeout=1800) == 0:
        return True
    # 国内网络 pip 经常很慢或超时，换镜像再试一次
    out("    换个国内镜像再试一次 ...")
    for mirror in ("https://pypi.tuna.tsinghua.edu.cn/simple",
                   "https://mirrors.aliyun.com/pypi/simple"):
        out("    镜像: %s" % mirror)
        if run('%s -m pip install -e . -i %s --trusted-host %s'
               % (sys.executable, mirror, mirror.split("/")[2]), cwd=WXQ_DIR, timeout=1800) == 0:
            return True
    out("    [X] 安装失败。把报错发我；如果报的是 Python 版本问题，")
    out("        装个 3.12 或 3.13 再试（wxq 官方支持到 3.13）。")
    return False


def ensure_on_path():
    """pip install -e 装的命令可能不在当前 PATH 里。"""
    found = shutil.which("wxq")
    if found:
        return found
    # 常见位置：%APPDATA%\Python\Scripts、Python 安装目录下的 Scripts
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(appdata, "Python", "Scripts", "wxq.exe"))
    candidates.append(os.path.join(WXQ_DIR, "Scripts", "wxq.exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def init_keys():
    out("[4/4] 取密钥（wxq init）...")
    exe = ensure_on_path()
    if not exe:
        out("    [X] 找不到 wxq 命令。装完可能需要重开一个命令行窗口再试。")
        return False
    out("    微信必须已登录并在运行；这一步需要管理员权限。")
    rc = run('"%s" init' % exe, timeout=600)
    if rc != 0:
        out("")
        out("    [X] 取密钥失败。常见原因：")
        out("      - 微信没开，或没登录")
        out("      - 这个窗口不是管理员身份（右键 -> 以管理员身份运行）")
        out("      - 微信版本太新，密钥在内存里的藏法变了")
        out("      把上面那几行报错原样发我，我来判断下一步。")
        return False
    return True


def verify():
    exe = ensure_on_path()
    if not exe:
        return
    out("")
    out("验证：列出你的会话 ...")
    proc = subprocess.run('"%s" sessions' % exe, shell=True, capture_output=True, text=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and text.strip():
        lines = [l for l in text.splitlines() if l.strip()][:8]
        out("  ✓ 解密成功，能读出 %d 行会话：" % len(lines))
        for l in lines:
            out("    " + l[:100])
        out("")
        out("=" * 50)
        out(" 第一步完成！回我一声，我们做下一步。")
        out("=" * 50)
    else:
        out("  [X] 还没读到会话。报错：")
        out("    " + text.strip()[:400])
        out("    发我这段就行。")


# ------------------------------------------------------------------
# 微信版本守卫
# wxq 的 Windows 扫描器只找明文 keyspec（x'<64位hex>'）。
# 微信 4.1.10.31 起把密钥改成 XOR 混淆态，内存里没有明文 → wxq 必然 0 命中。
# 所以这里先探版本，版本太高就立刻拦下，别让用户白等十分钟。
# ------------------------------------------------------------------
NEW_KEY_API = (4, 1, 10, 31)
VER_RE = re.compile(r"(\d+(?:\.\d+)*)")


def _registry_install_paths():
    paths = []
    try:
        import winreg
    except ImportError:
        return paths
    for hive, key in (
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Tencent\Weixin"),
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Tencent\WeChat"),
    ):
        try:
            with winreg.OpenKey(hive, key) as k:
                for name in ("InstallPath", "InstallDir", "Path"):
                    try:
                        val, _ = winreg.QueryValueEx(k, name)
                        if val:
                            paths.append(str(val))
                    except OSError:
                        pass
        except OSError:
            continue
    return paths


def _exe_candidates():
    cands = []
    for base in _registry_install_paths():
        base = os.path.expandvars(base)
        cands.append(os.path.join(base, "Weixin.exe"))
        cands.append(os.path.join(base, "WeChat.exe"))
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        root = os.environ.get(env)
        if not root:
            continue
        cands.append(os.path.join(root, "Tencent", "Weixin", "Weixin.exe"))
        cands.append(os.path.join(root, "Tencent", "WeChat", "WeChat.exe"))
    seen, out_list = set(), []
    for c in cands:
        if c not in seen and os.path.isfile(c):
            seen.add(c)
            out_list.append(c)
    return out_list


def _version_of(exe):
    ps = ("$ErrorActionPreference='Stop';"
          "(Get-Item -LiteralPath '%s').VersionInfo.ProductVersion"
          % exe.replace("'", "''"))
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                              capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip().splitlines()
    if not text:
        return None
    m = VER_RE.match(text[-1].strip())
    return m.group(1) if m else None


def detect_wechat_version():
    """返回 (版本字符串, 微信主程序路径)；探测不到返回 (None, None)。"""
    for exe in _exe_candidates():
        ver = _version_of(exe)
        if ver:
            return ver, exe
    return None, None


def _as_tuple(ver):
    parts = [int(x) for x in ver.split(".")]
    parts += [0] * (4 - len(parts))
    return tuple(parts[:4])


def check_wechat_version(force=False):
    """版本太高（4.1.10.31+）直接拦下。返回 True 表示可以继续。"""
    if force:
        return True
    ver, exe = detect_wechat_version()
    if not ver:
        out("    没能读到微信版本，先按老版本流程试。")
        return True
    out("    检测到微信版本：%s" % ver)
    if _as_tuple(ver) >= NEW_KEY_API:
        out("")
        out("[X] 你的微信版本 %s 不支持 wxq。" % ver)
        out("    原因：微信 4.1.10.31 起把密钥改成 XOR 混淆态，")
        out("          而 wxq 只会找明文密钥，扫多少次都是 0 命中。")
        out("")
        out("    请改跑这个（同一个文件夹里）：")
        out("        collector\\setup.bat")
        out("    那条链路是专门给 4.1.10.31 / 4.2 写的，能真的把密钥取出来。")
        out("")
        out("    确实想强行试 wxq 的话，加参数：setup_wxq.py --force")
        return False
    return True


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    out("=" * 50)
    out(" 微信日报 第一步：安装解密导出工具 wxq")
    out("=" * 50)
    out("")
    if not check_wechat_version(force="--force" in sys.argv):
        return 2
    if not check_python():
        return 1
    if not ensure_wxq_source():
        return 1
    if not install():
        return 1
    if not init_keys():
        return 1
    verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
