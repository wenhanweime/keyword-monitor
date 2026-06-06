# -*- coding: utf-8 -*-
"""关键词放量监控 — 监控对象定义。

改这里就能增删要盯的关键词 / 桶。结构：
  CLUSTERS：每个簇 = 一次 grok Latest 查询（省调用），含若干 query_terms 拼进 prompt。
  每个簇下的 buckets = 单独计数的「桶」（display 名 + 用于把推文归类的正则）。
一条推文可同时命中多个桶（如同时提 MU 和 HBM），各自计数。
"""
from __future__ import annotations
import re

CLUSTERS = [
    {
        "name": "存储 / 内存",
        "query_terms": [
            "$MU OR 美光 OR Micron",
            "$SNDK OR 闪迪 OR SanDisk",
            "海力士 OR Hynix OR \"SK Hynix\"",
            "存储 OR 内存 OR HBM OR DRAM OR NAND",
        ],
        "buckets": [
            ("美光 $MU",       re.compile(r"美光|micron|\$MU\b|(?<![A-Za-z])MU(?![A-Za-z])", re.I)),
            ("闪迪 $SNDK",     re.compile(r"闪迪|sandisk|\$?\bSNDK\b", re.I)),
            ("海力士 Hynix",   re.compile(r"海力士|hynix", re.I)),
            ("存储/HBM/DRAM",  re.compile(r"存储|内存|\bHBM\d?\b|\bDRAM\b|\bNAND\b|\bmemory\b", re.I)),
        ],
    },
    {
        "name": "光模块 / CPO",
        "query_terms": [
            "光模块 OR 光通信 OR \"optical module\"",
            "CPO OR 硅光 OR \"co-packaged optics\"",
            "$SIVE OR SIVE",
        ],
        "buckets": [
            ("光模块/光通信", re.compile(r"光模块|光通信|光互连|光互联|optical\s*(?:module|interconnect)", re.I)),
            ("CPO/硅光",      re.compile(r"\bCPO\b|硅光|co-?packaged", re.I)),
            ("SIVE $SIVE",    re.compile(r"\$?\bSIVE\b", re.I)),
        ],
    },
]

# 全部桶的有序列表（display 名），用于渲染顺序与 state 键
ALL_BUCKETS = [b[0] for c in CLUSTERS for b in c["buckets"]]


def build_cluster_query(cluster: dict, date_str: str) -> str:
    terms = "；".join(cluster["query_terms"])
    return (
        f"用 X(Twitter) 的 Latest（最新）模式检索，覆盖以下关键词：{terms}。"
        f"按发帖时间倒序，尽量多地（40 条以上）列出原始推文。"
        f"【每条严格占一行】，格式：`HH:MM | @handle | 原文(不改写,可截断到80字) | 推文URL`。"
        f"不要综述、不要按互动排序、不要合并重复。"
        f"最后另起一段，以「信号：」开头，用 1-2 句话指出这批里有没有：突发事件 / 新叙事 / "
        f"疑似 bot 模板刷屏（如大量雷同文案）。当前北京时间约 {date_str}。"
    )
