#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关键词放量监控 — 主入口（launchd 每小时调用）。

每小时：对每个簇跑一次 grok Latest 检索 → 解析原始推文 → 按推文 ID【跨小时去重】
得每个桶「本轮新增」条数（去重绕开 grok 时间戳不准的问题）→ 跟该桶基线比做放量告警
→ 渲染 docs/index.html（放量曲线+告警+最新帖）→ git push（GitHub Pages 刷新）。

去重计数是「采样代理」：低频词准；高频词因 grok 每轮只采样 ~40 条会偏高/被截顶，已在页面标注。
手动运行： python3 monitor.py
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from keywords import CLUSTERS, ALL_BUCKETS, build_cluster_query

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "docs"
STATE_PATH = BASE_DIR / "data" / "state.json"
RAW_DIR = BASE_DIR / "data" / "raw"

SMART_SEARCH = os.environ.get("SMART_SEARCH_BIN", "/opt/homebrew/bin/smart-search")
SEARCH_TIMEOUT = 160
SUBPROC_TIMEOUT = 220
SITE_TITLE = "关键词放量监控"
CST = timezone(timedelta(hours=8))

SEEN_CAP = 3000        # 每桶保留的已见推文 ID 上限（滚动）
HIST_CAP = 168         # 每桶保留的小时数据点上限（7 天）
SPARK_N = 24           # sparkline 显示最近多少个点
SPIKE_MIN = 4          # 放量告警的最小新增数（避免低基数噪声）
SPIKE_MULT = 2.5       # 超过基线倍数即告警

TWEET_RE = re.compile(r"https://(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)")
TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")


# --------------------------------------------------------------------------
def load_state() -> Dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            print("警告：state.json 解析失败，重建。")
    return {"buckets": {}, "last_run": ""}


def save_state(state: Dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def run_search(query: str, out_path: Path) -> Optional[dict]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [SMART_SEARCH, "search", "--format", "json",
           "--output", str(out_path), "--timeout", str(SEARCH_TIMEOUT), query]
    try:
        subprocess.run(cmd, text=True, capture_output=True, timeout=SUBPROC_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"  ! 超时 {out_path.name}")
    except FileNotFoundError:
        print(f"  ! 找不到 smart-search: {SMART_SEARCH}"); return None
    if not out_path.exists():
        return None
    try:
        d = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  ! 解析失败 {out_path.name}: {exc}"); return None
    if not d.get("ok"):
        print(f"  ! ok=false {out_path.name}"); return None
    return d


def parse_posts(content: str) -> List[Dict]:
    """任何含推文 URL 的行 → 一条 post；text=该行去掉URL后的剩余。稳健不依赖严格格式。"""
    posts = []
    for line in (content or "").splitlines():
        m = TWEET_RE.search(line)
        if not m:
            continue
        url, handle, tid = m.group(0), m.group(1), m.group(2)
        tm = TIME_RE.search(line)
        text = TWEET_RE.sub("", line).strip(" |-—·*`")
        posts.append({"id": tid, "handle": handle, "url": url,
                      "time": tm.group(1) if tm else "", "text": text})
    return posts


def extract_signal(content: str) -> str:
    for line in (content or "").splitlines():
        s = line.strip(" *`-")
        if s.startswith("信号"):
            return re.sub(r"^信号[:：]?\s*", "", s)[:200]
    return ""


# --------------------------------------------------------------------------
def spark_svg(values: List[int]) -> str:
    vals = values[-SPARK_N:] or [0]
    w, h, pad = 180, 34, 3
    mx = max(vals + [1])
    n = len(vals)
    bw = (w - pad * 2) / max(n, 1)
    bars = []
    for i, v in enumerate(vals):
        bh = (h - pad * 2) * (v / mx)
        x = pad + i * bw
        y = h - pad - bh
        last = i == n - 1
        col = "var(--accent)" if not last else "var(--hot)"
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bw-1.5,1):.1f}" '
                    f'height="{max(bh,0.6):.1f}" rx="1" fill="{col}"/>')
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'preserveAspectRatio="none">{"".join(bars)}</svg>')


def detect_spike(history: List[Dict], current_new: int) -> bool:
    prev = [h.get("new", 0) for h in history]
    if len(prev) < 3:
        return False
    base = sum(prev[-24:]) / len(prev[-24:])
    return current_new >= SPIKE_MIN and current_new >= SPIKE_MULT * max(base, 1.0)


# --------------------------------------------------------------------------
def render(state: Dict, now: datetime, run_new: Dict[str, List[Dict]],
           spikes: Dict[str, bool], signals: List[str]) -> str:
    spiked = [b for b in ALL_BUCKETS if spikes.get(b)]
    banner = ""
    if spiked or any(signals):
        items = "".join(f"<li>🔺 <b>{_html.escape(b)}</b> 放量"
                        f"（本轮新增 {len(run_new.get(b, []))} 条）</li>" for b in spiked)
        sig = "".join(f"<li>📡 {_html.escape(s)}</li>" for s in signals if s)
        banner = (f'<section class="alert"><h2>本轮信号</h2><ul>{items}{sig}</ul></section>'
                  if (items or sig) else "")
    else:
        banner = '<section class="alert quiet"><p>本轮无放量告警，市场平静。</p></section>'

    cards = []
    for b in ALL_BUCKETS:
        bs = state["buckets"].get(b, {})
        hist = bs.get("history", [])
        cur = hist[-1]["new"] if hist else 0
        sampled = hist[-1].get("sampled", 0) if hist else 0
        spark = spark_svg([h.get("new", 0) for h in hist])
        badge = '<span class="badge hot">🔺放量</span>' if spikes.get(b) else ""
        capnote = ' <span class="cap">采样上限,可能偏低</span>' if sampled >= 38 else ""
        posts = run_new.get(b, [])[:15]
        plist = "".join(
            f'<li><span class="pt">{_html.escape(p["time"] or "—")}</span> '
            f'<a href="{_html.escape(p["url"])}" target="_blank" rel="noopener">@{_html.escape(p["handle"])}</a> '
            f'<span class="px">{_html.escape(p["text"][:90])}</span></li>'
            for p in posts) or "<li class='none'>本轮无新帖</li>"
        cards.append(
            f'<div class="card{" spk" if spikes.get(b) else ""}">'
            f'<div class="chead"><span class="cname">{_html.escape(b)}</span>{badge}</div>'
            f'<div class="cnum">{cur}<span class="cunit">新增/本轮</span>'
            f'<span class="csamp">采样 {sampled}{capnote}</span></div>'
            f'{spark}'
            f'<details><summary>本轮新帖 {len(posts)}</summary><ul class="posts">{plist}</ul></details>'
            f'</div>')

    return f"""<!doctype html>
<html lang="zh-Hans"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="900">
<title>{SITE_TITLE}</title>
<link rel="stylesheet" href="assets/style.css">
</head><body>
<header class="site-header"><b>{SITE_TITLE}</b>
  <span class="upd">更新 {now:%Y-%m-%d %H:%M} (北京时间) · 每小时自动刷新</span></header>
<main>
  <p class="lede">每小时检索 X 上这些关键词的最新内容，按推文 ID 去重统计「本轮新增」，
   突然放量或出现异常信号时高亮。新增数为采样代理：低频词准，高频词偏保守。</p>
  {banner}
  <div class="grid">{''.join(cards)}</div>
  <footer>grok 原生 X 检索（Latest 模式，采样去重）· 数字为指示性非精确计数 · 仅供研究</footer>
</main></body></html>"""


def publish(stamp: str) -> None:
    def git(args):
        return subprocess.run(["git", "-C", str(BASE_DIR), *args], text=True, capture_output=True)
    try:
        if git(["add", "docs"]).returncode != 0:
            print("发布失败：git add"); return
        if git(["diff", "--cached", "--quiet", "--", "docs"]).returncode == 0:
            print("docs 无变化，跳过。"); return
        if git(["commit", "-m", f"monitor {stamp}"]).returncode != 0:
            print("发布失败：git commit"); return
        if git(["push"]).returncode != 0:
            print("发布失败：git push"); return
        print("已 push。")
    except Exception as exc:
        print(f"发布异常：{exc}")


# --------------------------------------------------------------------------
def main() -> int:
    now = datetime.now(CST)
    hour_key = now.strftime("%Y-%m-%d %H:%M")
    date_str = now.strftime("%Y-%m-%d %H:%M")
    print(f"== 关键词监控 {hour_key} ==")
    state = load_state()
    state.setdefault("buckets", {})

    all_posts: Dict[str, Dict] = {}   # id -> post（本轮跨簇去重）
    signals: List[str] = []
    ok_any = False
    for ci, cluster in enumerate(CLUSTERS):
        print(f"[{ci+1}/{len(CLUSTERS)}] 簇「{cluster['name']}」...")
        d = run_search(build_cluster_query(cluster, date_str),
                       RAW_DIR / now.strftime("%Y%m%d_%H") / f"c{ci}.json")
        if not d:
            continue
        ok_any = True
        content = d.get("content", "")
        sig = extract_signal(content)
        if sig:
            signals.append(f"{cluster['name']}：{sig}")
        for p in parse_posts(content):
            all_posts.setdefault(p["id"], p)

    if not ok_any:
        print("全部簇查询失败，放弃本轮（不提交）。")
        return 1

    # 分桶 + 跨小时去重计数
    run_new: Dict[str, List[Dict]] = {b: [] for b in ALL_BUCKETS}
    spikes: Dict[str, bool] = {}
    bucket_defs = [(name, rx) for c in CLUSTERS for (name, rx) in c["buckets"]]
    for name, rx in bucket_defs:
        bs = state["buckets"].setdefault(name, {"seen": [], "history": []})
        seen = set(bs["seen"])
        sampled = 0
        for p in all_posts.values():
            if rx.search(p["text"]) or rx.search("@" + p["handle"]):
                sampled += 1
                if p["id"] not in seen:
                    seen.add(p["id"])
                    run_new[name].append(p)
        new_n = len(run_new[name])
        spikes[name] = detect_spike(bs["history"], new_n)
        bs["seen"] = list(seen)[-SEEN_CAP:]
        bs["history"] = (bs["history"] + [{"hour": hour_key, "new": new_n,
                          "sampled": sampled, "spike": spikes[name]}])[-HIST_CAP:]
        flag = " 🔺放量" if spikes[name] else ""
        print(f"   {name}: 新增 {new_n} / 采样 {sampled}{flag}")

    state["last_run"] = hour_key
    save_state(state)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(render(state, now, run_new, spikes, signals),
                                         encoding="utf-8")
    publish(hour_key)
    print("完成。" + ("　告警：" + ", ".join(b for b in ALL_BUCKETS if spikes.get(b))
                       if any(spikes.values()) else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
