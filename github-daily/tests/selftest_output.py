# -*- coding: utf-8 -*-
"""交付前自检：日报完整性 + 差异注入 + 链接正确性 + 定时任务配置。"""
import csv
import json
import pathlib
import re
import sys

base = pathlib.Path("github-daily")
h = (base / "data" / "latest.html").read_text(encoding="utf-8")

checks = []
checks.append(("日报含差异区块", 'id="diff"' in h))
checks.append(("差异区块在正文之前", h.find("<main>") < h.find('id="diff"') < h.find("<footer>")))
checks.append(("无「待补注解」占位", "还没写人话介绍" not in h))
checks.append(("无「待补标签」", "待补标签" not in h))
links = re.findall(r'<a class="repo" href="([^"]+)"', h)
raw_peek = json.loads((base / "data" / "last_raw.json").read_text(encoding="utf-8"))
blocked_keys = set(json.loads(
    (base / "data" / "blacklist.json").read_text(encoding="utf-8")).get("repos", {}))
all_keys = ["%s/%s" % (r["owner"], r["name"])
            for p in raw_peek["periods"].values() for r in p["repos"]]
shown_keys = [k for k in all_keys if k not in blocked_keys]
expected_cards = len(shown_keys)
checks.append(("卡片数 = %d（黑名单 %d 个不显示卡片）"
               % (expected_cards, len(all_keys) - expected_cards),
               len(links) == expected_cards))
checks.append(("每张卡片都有圆形 × 按钮", h.count('class="blk"') == expected_cards))
checks.append(("无 trending 前缀错误链接", not any(u.startswith("https://github.com/trending/") for u in links)))
checks.append(("链接均为仓库根路径", all(re.fullmatch(r"https://github\.com/[^/]+/[^/]+", u) for u in links)))
checks.append(("无模板占位符残留", not re.search(r"\{\{[A-Z_]+\}\}", h)))

raw = json.loads((base / "data" / "last_raw.json").read_text(encoding="utf-8"))
ann = json.loads((base / "data" / "annotations.json").read_text(encoding="utf-8"))
keys = {f'{r["owner"]}/{r["name"]}' for p in raw["periods"].values() for r in p["repos"]}
uncovered = [k for k in keys if not (ann["repos"].get(k) or {}).get("intro")]
checks.append(("全部仓库都有 intro", not uncovered))
tag_bad = [k for k in keys if len((ann["repos"].get(k) or {}).get("tags") or []) != 2]
checks.append(("每个仓库恰好 2 个标签", not tag_bad))

banned = ["赋能", "助力", "一站式", "生态", "闭环", "解决方案", "降本增效", "开启新时代"]
texts = [ann["repos"][k]["intro"] for k in keys if k in ann["repos"]]
checks.append(("intro 无禁用营销词", not any(b in t for b in banned for t in texts)))
checks.append(("intro 无感叹号", not any("！" in t or "!" in t for t in texts)))
checks.append(("intro 无「它是一款」模板腔", not any("它是一款" in t for t in texts)))
long_ones = [t for t in texts if len(t) > 80]
checks.append(("intro 均为一句话（<=80 字）", not long_ones))

# 历史统计：CSV + 卡片上的「上榜 日/周/月」小字 + 新进榜/重回榜标记
sys.path.insert(0, str(base / "vendor"))
sys.path.insert(0, str(base.resolve() / "scripts"))
from bs4 import BeautifulSoup  # noqa: E402
import run_daily as rd  # noqa: E402

state_file = base / "data" / "diff" / "latest.json"
state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {"diff": {}}

history = rd.load_history()
stats = rd.compute_history_stats(history)
cur_date = max(history) if history else ""
prior_keys = set()
for d, old in history.items():
    if d >= cur_date:
        continue
    prior_keys |= {k for _p, k, _r in rd._iter_keys(old)}
added = set()
added_entries = first_entries = back_entries = 0
for v in (state.get("diff") or {}).values():
    added |= set(v.get("added") or [])
    for k in v.get("added") or []:
        if k in blocked_keys:
            continue
        added_entries += 1
        if k in prior_keys:
            back_entries += 1
        else:
            first_entries += 1
expect_first = len([k for k in added if k not in prior_keys])
expect_back = len(added) - expect_first
checks.append(("统计表存在且行数 = %d" % len(stats),
               (base / "data" / "stats.csv").exists()))
csv_rows = list(csv.DictReader(open(base / "data" / "stats.csv", encoding="utf-8-sig")))
checks.append(("CSV 行数 = 仓库数", len(csv_rows) == len(stats)))
checks.append(("CSV 表头完整", list(csv_rows[0].keys()) == [
    "仓库", "日榜次数", "周榜次数", "月榜次数", "合计",
    "首见日期", "最近日期", "语言", "链接", "已屏蔽"]))
csv_bad = 0
for r in csv_rows:
    s = stats.get(r["仓库"])
    if not s or int(r["日榜次数"]) != s["daily"] or int(r["周榜次数"]) != s["weekly"] \
            or int(r["月榜次数"]) != s["monthly"] or int(r["合计"]) != s["total"]:
        csv_bad += 1
checks.append(("CSV 计数与快照一致", csv_bad == 0))

# 卡片版式：第一行只有项目名，第二行是徽标 / 语言 / 按钮
cards_html = re.findall(r'<li class="card">.*?</li>', h, re.S)
layout_bad = 0
for card in cards_html:
    mhead = re.search(r'<div class="card-head">(.*?)</div>', card, re.S)
    msub = re.search(r'<div class="card-sub">(.*?)</div>', card, re.S)
    if not mhead or not msub:
        layout_bad += 1
        continue
    if not re.fullmatch(r'\s*<a class="repo".*?</a>\s*', mhead.group(1), re.S):
        layout_bad += 1
    sub = msub.group(1)
    if sub.count('class="wt"') != 1 or sub.count('class="blk"') != 1:
        layout_bad += 1
checks.append(("项目名独占一行、徽标/语言/按钮在第二行", layout_bad == 0))

badged = h.count(">新进榜</span>") + h.count(">重回榜</span>")
back_badged = h.count(">重回榜</span>")
checks.append(("进榜标记总数 = %d（按板块计）" % added_entries, badged == added_entries))
checks.append(("新进榜 %d / 重回榜 %d（按板块计）" % (first_entries, back_entries),
               badged - back_badged == first_entries and back_badged == back_entries))

cards_html = re.findall(r'<li class="card">.*?</li>', h, re.S)
stat_bad = 0
for card in cards_html:
    mk = re.search(r'<a class="repo" href="https://github\.com/([^"/]+)/([^"]+)"', card)
    ms = re.search(r'上榜 <b[^>]*>日(\d+) · 周(\d+) · 月(\d+)</b>', card)
    if not mk or not ms:
        stat_bad += 1
        continue
    key = "%s/%s" % (mk.group(1), mk.group(2))
    s = stats.get(key)
    if not s or (int(ms.group(1)), int(ms.group(2)), int(ms.group(3))) != \
            (s["daily"], s["weekly"], s["monthly"]):
        stat_bad += 1
checks.append(("每张卡片显示累计上榜次数且数值正确", stat_bad == 0))


def _section(text, period):
    marker = '<section class="sec" id="%s">' % period
    s = text.find(marker)
    return text[s:text.find("</section>", s)] if s != -1 else ""


# 排序：新进榜 -> 重回榜 -> 其余（组内保持名次）
order_ok = True
for period in ("daily", "weekly", "monthly"):
    ranks = []
    for card in re.findall(r'<li class="card">.*?</li>', _section(h, period), re.S):
        if ">新进榜</span>" in card:
            ranks.append(0)
        elif ">重回榜</span>" in card:
            ranks.append(1)
        else:
            ranks.append(2)
    if ranks != sorted(ranks):
        order_ok = False
checks.append(("各板块新进榜/重回榜已置前", order_ok))

# 关注名单：☆ 按钮、已关注徽标、板块末尾的更新说明（含建议文字）
watchlist = json.loads((base / "data" / "watchlist.json").read_text(encoding="utf-8"))
watched = set(watchlist.get("repos", {}))
checks.append(("每张卡片都有 ☆ 关注按钮", h.count('class="wt"') == expected_cards))
if watched:
    watched_cards = len([k for k in all_keys if k in watched and k not in blocked_keys])
    checks.append(("已关注徽标数 = %d" % watched_cards,
                   h.count(">已关注</span>") == watched_cards))
    box_ok = True
    for period in ("daily", "weekly", "monthly"):
        keys_in_period = {"%s/%s" % (r["owner"], r["name"])
                          for r in raw_peek["periods"][period]["repos"]}
        expect_box = bool(keys_in_period & watched)
        got_box = '<div class="watch">' in _section(h, period)
        if expect_box != got_box:
            box_ok = False
    checks.append(("关注项目在榜的板块才渲染更新说明", box_ok))
    notes = {k: (v or {}).get("note") for k, v in watchlist.get("repos", {}).items()}
    missing_notes = [k for k in watched if k in all_keys and not notes.get(k)]
    checks.append(("关注项目的建议说明已填写", not missing_notes))
else:
    checks.append(("无关注项目时不渲染更新说明", '<div class="watch">' not in h))

# 按钮服务脚本与自动拉起逻辑存在
checks.append(("按钮服务脚本存在", (base / "scripts" / "local_server.py").exists()))

# 末尾的「名单同步」区块：嵌入名单 + 一键复制
checks.append(("末尾有名单同步区块与复制按钮",
               'id="gd-sync"' in h and 'id="gd-sync-btn"' in h
               and h.find('id="gd-sync"') > h.find("</footer>")))
_em = re.search(r'window\.__GD_LISTS__ = (\{.*?\});</script>', h, re.S)
embedded = json.loads(_em.group(1)) if _em else {}
_bl_file = json.loads((base / "data" / "blacklist.json").read_text(encoding="utf-8"))
_wl_file = json.loads((base / "data" / "watchlist.json").read_text(encoding="utf-8"))
checks.append(("同步区块嵌入名单与磁盘文件一致",
               set(embedded.get("blacklist") or []) == set(_bl_file.get("repos") or {})
               and set(embedded.get("watch") or []) == set(_wl_file.get("repos") or {})))

# 黑名单：卡片移除 + 板块末尾折叠列出（有黑名单时才校验）
if blocked_keys:
    leak = []
    for period in ("daily", "weekly", "monthly"):
        sec = _section(h, period)
        in_cards = {m.group(1) for m in re.finditer(
            r'<li class="card">.*?https://github\.com/([^"]+).*?</li>', sec, re.S)}
        leak += [k for k in blocked_keys if k in in_cards]
    checks.append(("黑名单项目已从卡片移除", not leak))
    listed = all(k in h for k in blocked_keys)
    checks.append(("黑名单项目在板块末尾折叠列出", listed and 'class="blocked"' in h))
else:
    checks.append(("无黑名单时不渲染屏蔽区块", 'class="blocked"' not in h))

# 差异模块：三张榜各自折叠，默认收起，详情缩进
diff_start = h.find('id="diff"')
seg = h[diff_start:h.find("</section>", diff_start)] if diff_start != -1 else ""
checks.append(("差异模块为折叠结构（3 个 details）", seg.count("<details>") == 3))
checks.append(("差异详情默认收起", not re.search(r"<details[^>]*\bopen", seg)))
soup = BeautifulSoup(h)
diff_secs = [s for s in soup.select("section") if s.attrs.get("id") == "diff"]
struct_ok = len(diff_secs) == 1
if struct_ok:
    dets = [d for d in diff_secs[0].iter_descendants() if d.tag == "details"]
    struct_ok = len(dets) == 3 and all(
        any(c.tag == "summary" for c in d.children if not isinstance(c, str))
        and any(c.tag == "ul" for c in d.children if not isinstance(c, str))
        for d in dets)
checks.append(("每个折叠块含摘要行与明细列表", struct_ok))

bad = 0
for name, ok in checks:
    print(("PASS  " if ok else "FAIL  ") + name)
    bad += 0 if ok else 1
print("-" * 50)
print("全部通过（%d 项）" % len(checks) if bad == 0 else "%d 项失败" % bad)
raise SystemExit(0 if bad == 0 else 1)
