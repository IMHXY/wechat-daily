#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`yara` 模块的纯 Python 替身（只覆盖 key_v4.py 实际用到的那一小撮 API）。

为什么需要它：
  yara-python 只发布到 cp313 的 wheel。Python 3.14 上 pip 只能去编译源码，
  没有 Visual Studio Build Tools 就会失败。而 key_v4.py 的内存扫描**强依赖
  yara**（`if pymem is None or yara is None: raise`），装不上就整条链路断掉。

实际只需要一条规则、一个十六进制模式：

    rule GetKeyAddrStub
    {
        strings:
            $a = { ?? ?? ?? ?? ?? ?? 00 00 ... 2f 00 ... }
        condition:
            all of them
    }

这里的 `??` 是「任意一个字节」。所以整条规则等价于：
  在内存里找一段固定的 26 字节尾巴，前面 6 字节任意 —— 纯 Python 十几行就够。

安装方式：setup 时如果 import yara 失败，就把本文件复制成 recovery/tools/yara.py。
Python 会优先从脚本所在目录 import，所以 key_v4.py 的 `import yara` 会拿到本文件。

（如果以后真的装上了官方 yara-python，setup 会跳过这一步；本文件只作为兜底，
 行为对它用到的那条规则是等价的。）
"""

import re

__all__ = ["compile", "Match", "StringMatch", "Instance", "Rules"]


class Instance(object):
    """对应 yara 的 instance：一次匹配的位置和字节内容。"""

    __slots__ = ("offset", "matched_data")

    def __init__(self, offset, matched_data):
        self.offset = offset
        self.matched_data = matched_data

    def __repr__(self):
        return "Instance(offset=%d)" % self.offset


class StringMatch(object):
    """对应 yara 的 string match：一批 instance。"""

    __slots__ = ("instances",)

    def __init__(self, instances):
        self.instances = instances

    def __repr__(self):
        return "StringMatch(instances=%d)" % len(self.instances)


class Match(object):
    """对应 yara 的 match：rule 名 + 命中的 strings。"""

    __slots__ = ("rule", "strings")

    def __init__(self, rule, strings):
        self.rule = rule
        self.strings = strings

    def __repr__(self):
        return "Match(rule=%r)" % self.rule


_RULE_RE = re.compile(r"rule\s+([A-Za-z_][A-Za-z0-9_]*)")
_STRINGS_BLOCK_RE = re.compile(r"strings\s*:\s*(.*?)condition\s*:", re.S)
_VAR_RE = re.compile(r"\$([A-Za-z0-9_]*)\s*=\s*\{([^}]*)\}")


def _parse_rule(source):
    """把 yara 规则源码解析成 [(rule_name, [(var_name, [token,...])])]。"""
    rules = []
    for rm in _RULE_RE.finditer(source):
        name = rm.group(1)
        seg = source[rm.end():]
        sm = _STRINGS_BLOCK_RE.search(seg)
        if not sm:
            continue
        patterns = []
        for pm in _VAR_RE.finditer(sm.group(1)):
            tokens = []
            for tok in pm.group(2).split():
                tokens.append(None if tok == "??" else int(tok, 16))
            patterns.append((pm.group(1), tokens))
        rules.append((name, patterns))
    return rules


def _find_all(data, pattern):
    """找出 pattern 在 data 里的全部匹配偏移（None 表示通配字节）。"""
    size = len(pattern)
    if size == 0 or len(data) < size:
        return []

    # 找最长的固定字节段做锚点，避免全量逐字节比对
    anchor_start, anchor_len = -1, 0
    cur_start, cur_len = -1, 0
    for idx, value in enumerate(pattern):
        if value is None:
            cur_start, cur_len = -1, 0
            continue
        if cur_start < 0:
            cur_start, cur_len = idx, 1
        else:
            cur_len += 1
        if cur_len > anchor_len:
            anchor_start, anchor_len = cur_start, cur_len
    if anchor_len == 0:
        return []

    needle = bytes(pattern[anchor_start:anchor_start + anchor_len])
    hits = []
    pos = data.find(needle)
    while pos != -1:
        base = pos - anchor_start
        if base >= 0 and base + size <= len(data):
            ok = True
            for i, value in enumerate(pattern):
                if value is not None and data[base + i] != value:
                    ok = False
                    break
            if ok:
                hits.append(base)
        pos = data.find(needle, pos + 1)
    return hits


class Rules(object):
    """对应 yara.compile() 的返回值。"""

    def __init__(self, source):
        self.source = source
        self.rules = _parse_rule(source)

    def match(self, data=None, **kwargs):
        results = []
        for name, patterns in self.rules:
            instances = []
            for _var, pattern in patterns:
                for offset in _find_all(data, pattern):
                    instances.append(Instance(offset, data[offset:offset + len(pattern)]))
            if instances:
                results.append(Match(name, [StringMatch(instances)]))
        return results


def compile(source=None, **kwargs):  # noqa: A001 - 与 yara 同名
    """对应 yara.compile(source=...)。"""
    return Rules(source or "")


if __name__ == "__main__":
    # 自检：拿真实规则跑一遍
    SRC = r'''
    rule GetKeyAddrStub
    {
        strings:
            $a = { ?? ?? ?? ?? ?? ?? 00 00 00 00 00 00 00 00 00 00 20 00 00 00 00 00 00 00 2f 00 00 00 00 00 00 00 }
        condition:
            all of them
    }
    '''
    rules = compile(source=SRC)
    sample = b"\x01\x02\x03\x04\x05\x06" + b"\x00" * 10 + b"\x20\x00" + b"\x00" * 6 + b"\x2f" + b"\x00" * 7
    out = rules.match(data=sample)
    assert len(out) == 1 and out[0].rule == "GetKeyAddrStub"
    assert out[0].strings[0].instances[0].offset == 0
    print("yara shim self-test ok")
