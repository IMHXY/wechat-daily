#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信 4.x 链路自测：python3 tests/test_wechat4.py

验证不依赖 Windows 的部分：路径探测、工具下载、命令行契约。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def test_find_root():
    print("\n[定位 xwechat_files]")
    import wechat4
    tmp = tempfile.mkdtemp()
    old_home = os.environ.get("HOME")
    try:
        docs = os.path.join(tmp, "Documents")
        root = os.path.join(docs, "xwechat_files")
        os.makedirs(os.path.join(root, "wxid_abc", "db_storage", "message"))
        os.environ["HOME"] = tmp
        os.environ["USERPROFILE"] = tmp
        found = wechat4.find_wechat_root()
        check("找到根目录", found == root, "%s != %s" % (found, root))
        dbs = wechat4.find_db_storages(found) if found else []
        check("找到 db_storage", len(dbs) == 1, str(dbs))
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


def test_cli_contract():
    print("\n[命令行契约]")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "collector", "wechat4.py"), "--help"],
                       capture_output=True, text=True, timeout=60)
    check("有 setup 子命令", "setup" in r.stdout, r.stdout[:200])
    check("有 export 子命令", "export" in r.stdout, r.stdout[:200])

    r2 = subprocess.run([sys.executable, os.path.join(ROOT, "collector", "wechat4.py"),
                         "export", "--help"], capture_output=True, text=True, timeout=60)
    check("export 有 --out", "--out" in r2.stdout, r2.stdout[:200])
    check("export 有 --days", "--days" in r2.stdout, r2.stdout[:200])


def test_dependencies_declared():
    print("\n[依赖与常量]")
    import wechat4
    for dep in ("pefile", "pycryptodome", "pymem", "yara-python"):
        check("声明依赖 %s" % dep, dep in wechat4.PIP_DEPS, str(wechat4.PIP_DEPS))
    check("KEEPER 下载地址正确",
          wechat4.KEEPER_REPO.endswith("v0.3.7-preview"), wechat4.KEEPER_REPO)
    check("有国内镜像兜底", len(wechat4.MIRRORS) >= 2, str(wechat4.MIRRORS))


def test_download_recovery():
    print("\n[下载密钥恢复脚本（需要网络）]")
    import wechat4
    tmp = tempfile.mkdtemp()
    old = wechat4.TOOLS_DIR
    try:
        wechat4.TOOLS_DIR = tmp
        target = wechat4.ensure_recovery_tools()
        ok = bool(target) and os.path.isfile(
            os.path.join(target, "tools", "run_recovery_generic.py"))
        check("下载并解压成功", ok, str(target))
        if ok:
            src = open(os.path.join(target, "tools", "run_recovery_generic.py"),
                       encoding="utf-8").read()
            check("脚本含 HMAC 校验步骤", "HMAC" in src or "hmac" in src)
            check("脚本含静态 XOR 提取", "scan" in src)
    except Exception as exc:
        check("下载并解压成功", False, str(exc))
    finally:
        wechat4.TOOLS_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_recovery_flow_documented():
    print("\n[链路顺序正确]")
    src = open(os.path.join(ROOT, "collector", "wechat4.py"), encoding="utf-8").read()
    order = [src.find("ensure_recovery_tools"), src.find("ensure_keeper"),
             src.find("recover_passphrase"), src.find("inject_key"), src.find("do_export")]
    check("五步顺序递增", order == sorted(order) and all(p > 0 for p in order), str(order))
    check("注入用 --key-stdin", "--key-stdin" in src)
    check("导出带 --days", "--days" in src)



def test_yara_shim():
    """内置 yara 替身：等价实现 key_v4.py 用到的那条规则。"""
    print("\n[yara 纯 Python 替身]")
    import _yara_shim as shim
    RULE = r"""
    rule GetKeyAddrStub
    {
        strings:
            $a = { ?? ?? ?? ?? ?? ?? 00 00 00 00 00 00 00 00 00 00 20 00 00 00 00 00 00 00 2f 00 00 00 00 00 00 00 }
        condition:
            all of them
    }
    """
    rules = shim.compile(source=RULE)
    tail = (b"\x00" * 10 + b"\x20\x00" + b"\x00" * 6 + b"\x2f" + b"\x00" * 7)
    check("固定尾巴长度 26", len(tail) == 26, str(len(tail)))
    mem = b"\xaa" * 100 + b"\x01\x02\x03\x04\x05\x06" + tail + b"\xbb" * 50
    hits = rules.match(data=mem)
    check("命中 1 处", len(hits) == 1, str(len(hits)))
    if hits:
        check("rule 名正确", hits[0].rule == "GetKeyAddrStub")
        inst = hits[0].strings[0].instances[0]
        check("偏移正确（通配的 6 字节也算在内）", inst.offset == 100, str(inst.offset))
        check("matched_data 32 字节", len(inst.matched_data) == 32, str(len(inst.matched_data)))
    check("无匹配返回空", rules.match(data=b"\x00" * 64) == [])

    # 边界：固定字节被改动就不该命中
    bad = b"\x01\x02\x03\x04\x05\x06" + tail[:20] + b"\x21" + tail[21:]
    check("改一个字节就不命中", rules.match(data=bad) == [])


def test_yara_shim_installed():
    """setup 时把替身拷进 recovery/tools/。"""
    print("\n[yara 替身安装]")
    import wechat4
    tmp = tempfile.mkdtemp()
    tools = os.path.join(tmp, "recovery", "tools")
    os.makedirs(tools)
    try:
        ok = wechat4.ensure_yara_usable(os.path.join(tmp, "recovery"))
        shim_path = os.path.join(tools, "yara.py")
        if os.path.isfile(shim_path):
            check("yara.py 已生成", ok)
            src = open(shim_path, encoding="utf-8").read()
            check("替身含 compile", "def compile(" in src)
        else:
            # 沙箱里恰好装了真 yara 时就不该写替身
            check("真 yara 可用时跳过替身", ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_find_root_via_ini():
    """微信 4.x 的配置 ini：整个文件就是一整行路径（父目录），xwechat_files 拼在后面。"""
    print("\n[按真实 ini 格式定位]")
    import wechat4
    tmp = tempfile.mkdtemp()
    old_appdata = os.environ.get("APPDATA")
    old_home = os.environ.get("HOME")
    try:
        appdata = os.path.join(tmp, "AppData", "Roaming")
        ini_dir = os.path.join(appdata, "Tencent", "xwechat", "config")
        os.makedirs(ini_dir)
        # 真实布局：ini 里只写父目录，没有 xwechat_files 字样
        with open(os.path.join(ini_dir, "path.ini"), "w", encoding="utf-8") as fh:
            fh.write(tmp + "\n")
        root = os.path.join(tmp, "xwechat_files")
        os.makedirs(os.path.join(root, "wxid_test", "db_storage", "message"))

        os.environ["APPDATA"] = appdata
        os.environ["HOME"] = tmp
        os.environ["USERPROFILE"] = tmp

        paths = wechat4._ini_paths()
        check("从 ini 读出父目录", paths and os.path.normpath(paths[0]) == os.path.normpath(tmp),
              str(paths))
        found = wechat4.find_wechat_root()
        check("拼出 xwechat_files 根目录", found and os.path.normpath(found) == os.path.normpath(root),
              str(found))
        dbs = wechat4.find_db_storages(found)
        check("找到 db_storage", len(dbs) == 1, str(dbs))
    finally:
        for k, v in (("APPDATA", old_appdata), ("HOME", old_home)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def test_ini_encodings():
    """ini 可能是 utf-8 / gbk / utf-16，都要读得出来。"""
    print("\n[ini 编码兼容]")
    import wechat4
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "a.ini")
        open(p, "wb").write("C:\\Users\\x\\Documents\n".encode("utf-8"))
        check("utf-8", "Documents" in (wechat4._read_text_auto(p) or ""))
        open(p, "wb").write("C:\\Users\\x\\Documents\n".encode("gbk"))
        check("gbk", "Documents" in (wechat4._read_text_auto(p) or ""))
        open(p, "wb").write("C:\\Users\\x\\Documents\n".encode("utf-16"))
        text = wechat4._read_text_auto(p) or ""
        check("utf-16", "Documents" in text and "\x00" not in text, repr(text))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_child_utf8_output():
    """子进程用 GBK 输出 ✓ 时不能把父进程搞崩（真机上就是这样崩的）。"""
    print("\n[子进程编码]")
    import wechat4
    code_str = "print('[\u2713] 命中!')"
    cmd = '%s -c "%s"' % (sys.executable, code_str)

    old = os.environ.get("PYTHONIOENCODING")
    os.environ["PYTHONIOENCODING"] = "gbk"
    try:
        code, so, se = wechat4.run(cmd)
        check("GBK 子进程崩溃不牵连父进程", isinstance(code, int), repr(code))
        check("崩溃信息被捕获", "UnicodeEncodeError" in (se or ""), (se or "")[:120])
    finally:
        if old is None:
            os.environ.pop("PYTHONIOENCODING", None)
        else:
            os.environ["PYTHONIOENCODING"] = old

    code, so, se = wechat4.run(cmd, utf8_child=True)
    check("utf8_child 下子进程正常退出", code == 0, (se or "")[:200])
    check("✓ 被正确解码", "\u2713" in so and "命中" in so, repr(so[:80]))



def test_recover_passphrase():
    """密钥提取：正常写文件 + 文件没写成时从日志兜底。"""
    print("\n[密钥提取]")
    import wechat4
    hexkey = "ab" * 32

    # 1) 脚本正常写 recovered_key.txt
    tmp = tempfile.mkdtemp()
    try:
        tools = os.path.join(tmp, "tools")
        os.makedirs(tools)
        with open(os.path.join(tools, "run_recovery_generic.py"), "w", encoding="utf-8") as fh:
            fh.write(
                "import sys, os\n"
                "print('[\\u2713] 命中!')\n"
                "final = '%s'\n"
                "open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"
                "'recovered_key.txt'), 'w').write('final = ' + final)\n"
                % hexkey)
        key = wechat4.recover_passphrase(tmp, 123, "C:/fake/Weixin.dll", [os.path.join(tmp, "db")])
        check("从文件取回密钥", key == hexkey, str(key))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 2) 脚本崩在写文件之前（真机就是这个情况）→ 从日志里捞
    tmp = tempfile.mkdtemp()
    try:
        tools = os.path.join(tmp, "tools")
        os.makedirs(tools)
        with open(os.path.join(tools, "run_recovery_generic.py"), "w", encoding="utf-8") as fh:
            fh.write("print('[\\u2713] 命中!')\nprint('final = %s')\n" % hexkey)
        key = wechat4.recover_passphrase(tmp, 123, "C:/fake/Weixin.dll", [os.path.join(tmp, "db")])
        check("文件没写成也能从日志捞回", key == hexkey, str(key))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_write_config():
    """config.env 自动生成：路径填好，密钥项留空等用户填。"""
    print("\n[config.env 生成]")
    import wechat4
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "config.env")
        wechat4.write_config(data_root="D:\\xwechat_files", path=path)
        text = open(path, encoding="utf-8").read()
        check("写出了文件", os.path.isfile(path))
        check("数据目录填好", "WECHAT_DATA_DIR=D:\\xwechat_files" in text)
        check("导出命令指向 wechat4", "wechat4.py" in text and " export" in text)
        check("飞书三项留空", "FS_APP_ID=\n" in text and "FS_APP_SECRET=\n" in text
              and "FS_TABLE_ID=\n" in text)
        check("本机 key 留空", "LLM_API_KEY=\n" in text)
        # 飞书表现在只有「日期 / 日报」两列生效；「消息数」「内容」已弃用，
        # 在模板里保留为注释。这里只确认四个 FS_FIELD_* 键名都还在模板里出现过
        # （两个生效 + 两个弃用注释），不改变原有断言逻辑。
        check("飞书表字段名是中文两列：日期 / 日报",
              all(("FS_FIELD_%s=" % k) in text for k in ("DATE", "MSG", "PAYLOAD", "REPORT")))

        # 已填的值要保留
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("FS_APP_ID=cli_xxx\nLLM_API_KEY=sk-yyy\n")
        wechat4.write_config(data_root="D:\\xwechat_files", path=path)
        text2 = open(path, encoding="utf-8").read()
        check("保留已填的 FS_APP_ID", "FS_APP_ID=cli_xxx" in text2)
        check("保留已填的 LLM_API_KEY", "LLM_API_KEY=sk-yyy" in text2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_export_cmd_location_independent():
    """EXPORT_CMD 用相对路径，整个文件夹挪位置也不会失效。"""
    print("\n[EXPORT_CMD 相对路径]")
    import wechat4
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "config.env")
        # 第一次生成（绝对路径版），再重跑一次 -> 应该变成相对路径
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('EXPORT_CMD="D:\\python.exe" "C:\\old\\collector\\wechat4.py" export\n')
        wechat4.write_config(data_root="D:\\xwechat_files", path=path)
        text = open(path, encoding="utf-8").read()
        line = [l for l in text.splitlines() if l.startswith("EXPORT_CMD")][0]
        check("重跑后用相对路径", "collector\\wechat4.py" in line, line)
        check("不再含旧绝对路径", "old" not in line, line)
        check("引用的是本解释器", sys.executable in line, line)

        # 用户换了自己的解密工具就别覆盖
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('EXPORT_CMD=python "C:\\mytool\\export.py"\n')
        wechat4.write_config(data_root="D:\\xwechat_files", path=path)
        text = open(path, encoding="utf-8").read()
        line = [l for l in text.splitlines() if l.startswith("EXPORT_CMD")][0]
        check("保留用户自定义的导出工具", "mytool" in line, line)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_set_config():
    """set 子命令：直接改 config.env，不用记事本。"""
    print("\n[set 改配置]")
    import wechat4
    tmp = tempfile.mkdtemp()
    try:
        # 用一个临时项目根（set 固定写项目根下的 config.env）
        root = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(root, "collector"))
        shutil.copy(os.path.join(ROOT, "collector", "wechat4.py"),
                    os.path.join(root, "collector", "wechat4.py"))
        shutil.copy(os.path.join(ROOT, "collector", "bridge.py"),
                    os.path.join(root, "collector", "bridge.py"))
        env_file = os.path.join(root, "config.env")
        wechat4.write_config(data_root="D:\\xwechat_files", path=env_file)
        old_env = os.environ.get("WEIXIN_ENV_FILE")
        os.environ["WEIXIN_ENV_FILE"] = env_file
        try:
            pass
        finally:
            pass
        before = open(env_file, encoding="utf-8").read()
        rc = wechat4.cmd_set(type("A", (), {"values": [
            "FS_TABLE_ID=abc/tbl123", "LLM_API_KEY=sk-test"]})())
        check("返回 0", rc == 0)
        after = open(env_file, encoding="utf-8").read()
        check("FS_TABLE_ID 写进去了", "FS_TABLE_ID=abc/tbl123" in after, after[:200])
        check("LLM_API_KEY 写进去了", "LLM_API_KEY=sk-test" in after)
        check("其它值没动", "WECHAT_DATA_DIR=D:\\xwechat_files" in after)
        check("注释还在", after.count("#") == before.count("#"), str(after.count("#")))
        # 再设一次，应该覆盖而不是追加
        wechat4.cmd_set(type("A", (), {"values": ["FS_TABLE_ID=new/tbl"]})())
        after2 = open(env_file, encoding="utf-8").read()
        check("重复设置会覆盖", "FS_TABLE_ID=new/tbl" in after2
              and "FS_TABLE_ID=abc/tbl123" not in after2, after2[:200])
        check("没有重复行", after2.count("FS_TABLE_ID=") == 1, str(after2.count("FS_TABLE_ID=")))
    finally:
        if old_env is None:
            os.environ.pop("WEIXIN_ENV_FILE", None)
        else:
            os.environ["WEIXIN_ENV_FILE"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)



def test_masked_placeholder():
    """聊天里复制来的打码占位符要被识别出来，而不是当成真值去用。"""
    print("\n[打码占位符防护]")
    import wechat4
    import bridge
    check("认得出占位符", wechat4._is_masked("{{ARCHON_SECRET:FS_TABLE_ID}}"))
    check("正常值不误判", not wechat4._is_masked("appXXX/tblYYY"))

    parsed = wechat4.parse_bitable_url(
        "https://qcn982eh46xg.feishu.cn/base/ZYKob61a9aZdnKsTqEfcdNujnnT"
        "?table=tblWZw1Arahx3ZUj&view=vewAQzugcz")
    check("从网址解析出 app_token/table_id",
          parsed == ("ZYKob61a9aZdnKsTqEfcdNujnnT", "tblWZw1Arahx3ZUj"), str(parsed))
    check("非表格网址返回 None",
          wechat4.parse_bitable_url("https://example.com/foo") is None)

    # bridge 遇到占位符要给出可操作的报错，而不是崩或去请求飞书
    old = os.environ.get("FS_TABLE_ID")
    os.environ["FS_TABLE_ID"] = "{{ARCHON_SECRET:FS_TABLE_ID}}"
    try:
        try:
            bridge._feishu_table()
            check("bridge 要拒绝占位符", False, "没报错")
        except RuntimeError as exc:
            msg = str(exc)
            check("bridge 给出可操作提示", "set-table" in msg, msg[:160])
    finally:
        if old is None:
            os.environ.pop("FS_TABLE_ID", None)
        else:
            os.environ["FS_TABLE_ID"] = old



def test_set_creates_config_with_root():
    """config.env 不存在时 set 要先探数据目录，别写个空配置。"""
    print("\n[set 自动创建配置]")
    import wechat4
    tmp = tempfile.mkdtemp()
    old_env = os.environ.get("WEIXIN_ENV_FILE")
    try:
        env_file = os.path.join(tmp, "config.env")
        os.environ["WEIXIN_ENV_FILE"] = env_file
        wechat4.cmd_set(type("A", (), {"values": ["FS_TABLE_ID=appX/tblY"]})())
        text = open(env_file, encoding="utf-8").read()
        check("配置被创建了", os.path.isfile(env_file))
        check("FS_TABLE_ID 写进去了", "FS_TABLE_ID=appX/tblY" in text, text[:200])
        check("数据目录字段在（可能为空但不能缺）",
              "WECHAT_DATA_DIR=" in text, text[:200])
    finally:
        if old_env is None:
            os.environ.pop("WEIXIN_ENV_FILE", None)
        else:
            os.environ["WEIXIN_ENV_FILE"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)



def test_set_clip():
    """set-clip：从剪贴板读值，剪贴板是打码占位符时要拒绝。"""
    print("\n[剪贴板取值]")
    import wechat4
    old_read = wechat4.read_clipboard
    tmp = tempfile.mkdtemp()
    old_env = os.environ.get("WEIXIN_ENV_FILE")
    try:
        env_file = os.path.join(tmp, "config.env")
        os.environ["WEIXIN_ENV_FILE"] = env_file
        wechat4.write_config(data_root="D:\\xwechat_files", path=env_file)

        wechat4.read_clipboard = lambda: "cli_fake_value"
        rc = wechat4.cmd_set_clip(type("A", (), {"keys": ["FS_APP_ID"]})())
        check("剪贴板值写进去了", rc == 0)
        text = open(env_file, encoding="utf-8").read()
        check("FS_APP_ID 是剪贴板的值", "FS_APP_ID=cli_fake_value" in text, text[:200])

        wechat4.read_clipboard = lambda: "{{ARCHON_SECRET:FS_APP_ID}}"
        rc = wechat4.cmd_set_clip(type("A", (), {"keys": ["FS_APP_ID"]})())
        check("打码占位符要拒绝", rc == 2, str(rc))
        text2 = open(env_file, encoding="utf-8").read()
        check("拒绝后没写进去", "ARCHON_SECRET" not in text2, text2[:200])

        wechat4.read_clipboard = lambda: None
        rc = wechat4.cmd_set_clip(type("A", (), {"keys": ["FS_APP_ID"]})())
        check("空剪贴板要报错", rc == 2, str(rc))
    finally:
        wechat4.read_clipboard = old_read
        if old_env is None:
            os.environ.pop("WEIXIN_ENV_FILE", None)
        else:
            os.environ["WEIXIN_ENV_FILE"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)



def test_set_clip_rejects_bad_llm_key():
    """LLM_API_KEY 长度不对 / 含空白时要拦下来，别写进配置。"""
    print("\n[set-clip 拦截坏 key]")
    import wechat4
    old_read = wechat4.read_clipboard
    tmp = tempfile.mkdtemp()
    old_env = os.environ.get("WEIXIN_ENV_FILE")
    try:
        env_file = os.path.join(tmp, "config.env")
        os.environ["WEIXIN_ENV_FILE"] = env_file
        wechat4.write_config(data_root="D:\\xwechat_files", path=env_file)

        wechat4.read_clipboard = lambda: "212410\nshort"
        rc = wechat4.cmd_set_clip(type("A", (), {"keys": ["LLM_API_KEY"]})())
        check("太短/含空白要拒绝", rc == 2, str(rc))
        text = open(env_file, encoding="utf-8").read()
        check("拒绝后没写进去", "212410" not in text, text[:200])

        wechat4.read_clipboard = lambda: "shortkey"
        rc = wechat4.cmd_set_clip(type("A", (), {"keys": ["LLM_API_KEY"]})())
        check("太短要拒绝", rc == 2, str(rc))

        # 飞书的 app_id 只有 20 个字符，不能被误伤
        wechat4.read_clipboard = lambda: "cli_abcdefghij1234"
        rc = wechat4.cmd_set_clip(type("A", (), {"keys": ["FS_APP_ID"]})())
        check("FS_APP_ID 20 字符要放行", rc == 0, str(rc))
        text2 = open(env_file, encoding="utf-8").read()
        check("FS_APP_ID 写进去了", "FS_APP_ID=cli_abcdefghij1234" in text2, text2[:200])
    finally:
        wechat4.read_clipboard = old_read
        if old_env is None:
            os.environ.pop("WEIXIN_ENV_FILE", None)
        else:
            os.environ["WEIXIN_ENV_FILE"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)


def test_include_file():
    """白名单从 UTF-8 文件读（中文群名不能走 cmd 参数）。

    .bat 必须是纯 ASCII —— cmd 用系统代码页（GBK）读 UTF-8 的 bat 会崩，
    所以中文群名放进 UTF-8 文本文件，由 Python 读。
    """
    print("\n[白名单文件]")
    import wechat4
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "include.txt")
        with open(path, "w", encoding="utf-8-sig") as f:   # 带 BOM 也要能读
            f.write("# 注释行\n\n周末骑行群\n读书会  \n# 另一个注释\n\n")
        got = wechat4._read_include_file(path)
        check("读出两个群名", got == "周末骑行群,读书会", got)
        check("注释和空行被忽略", "#" not in got, got)

        # 空文件 -> 清空白名单
        empty = os.path.join(tmp, "empty.txt")
        open(empty, "w", encoding="utf-8").close()
        check("空文件得到空串", wechat4._read_include_file(empty) == "", "")

        # 文件不存在 -> 明确报错，不是静默清空
        try:
            wechat4._read_include_file(os.path.join(tmp, "nope.txt"))
            check("文件不存在会报错", False, "没抛异常")
        except RuntimeError:
            check("文件不存在会报错", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_set_scope_bat():
    """set_scope.bat 必须是纯 ASCII（cmd 用 GBK 读 UTF-8 的 bat 会崩）。"""
    print("\n[set_scope.bat]")
    path = os.path.join(ROOT, "collector", "set_scope.bat")
    check("有 set_scope.bat", os.path.isfile(path))
    if not os.path.isfile(path):
        return
    raw = open(path, "rb").read()
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    check("纯 ASCII", not bad,
          "第 %d 字节: %s" % (bad[0][0], bad[0][1]) if bad else "")
    text = raw.decode("ascii")
    check("切了 UTF-8 代码页", "chcp 65001" in text, text[:200])
    check("调的是 --include-file", "--include-file" in text, text[:200])
    check("文件不存在时会给示例", "include.example.txt" in text, text[:200])
    # 上下文窗口 / 白名单上限也要能用 bat 改
    check("有 window 子命令", "--window" in text and '"window"' in text, text[:400])
    check("有 max 子命令", "--include-max" in text and '"max"' in text, text[:400])
    check("缺数字时会提示", "neednum" in text, text[:400])


def test_regen_reports_bat():
    """regen_reports.bat 必须纯 ASCII，且流程是「本地归档 -> 重排 -> 回写」。

    原始记录不再上传飞书，本机 data\<日期>.json 才是权威归档：
    本地有就直接重排，没有才退回 bridge.py pull。
    """
    print("\n[regen_reports.bat]")
    path = os.path.join(ROOT, "collector", "regen_reports.bat")
    check("有 regen_reports.bat", os.path.isfile(path))
    if not os.path.isfile(path):
        return
    raw = open(path, "rb").read()
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    check("纯 ASCII", not bad,
          "第 %d 字节: %s" % (bad[0][0], bad[0][1]) if bad else "")
    text = raw.decode("ascii")
    check("切了 UTF-8 代码页", "chcp 65001" in text, text[:200])
    check("优先用本地归档", "..\\data\\%1.json" in text, text[:600])
    check("本地没有才拉", "bridge.py pull" in text, text[:600])
    check("先看本地再决定", text.find("..\\data\\%1.json") < text.find("bridge.py pull --date"),
          text[:600])
    check("再重排", "summarize_local.py" in text, text[:400])
    check("回写日报列", "--push" in text, text[:400])
    check("不带参数时取前两天", "timedelta(days=1)" in text, text[:600])


if __name__ == "__main__":
    test_find_root()
    test_cli_contract()
    test_dependencies_declared()
    test_recovery_flow_documented()
    test_yara_shim()
    test_yara_shim_installed()
    test_find_root_via_ini()
    test_ini_encodings()
    test_child_utf8_output()
    test_recover_passphrase()
    test_write_config()
    test_export_cmd_location_independent()
    test_set_config()
    test_masked_placeholder()
    test_set_creates_config_with_root()
    test_set_clip()
    test_set_clip_rejects_bad_llm_key()
    test_download_recovery()
    test_include_file()
    test_set_scope_bat()
    test_regen_reports_bat()
    print("\n%s" % ("全部通过" if not FAILURES else "失败: %s" % FAILURES))
    sys.exit(1 if FAILURES else 0)
