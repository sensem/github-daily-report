#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Trending 每日任务执行器。

做四件事：
  1. 调用 skill 原始脚本 fetch_trending.py 抓取日/周/月榜（不改动 skill 文件）
  2. 修正 skill 已知的 url 拼接缺陷（https://github.com/trending/x/y -> https://github.com/x/y）
  3. 用累积的注解缓存 + skill 原始模板渲染日报 HTML（缺注解自动降级为「待补」）
  4. 与「上一次运行」的抓取结果做差异对比，输出 md + 注入日报 HTML

用法:
    python run_daily.py                    # 抓取 + 渲染 + 差异（每日任务用这个）
    python run_daily.py --no-fetch         # 用上一次抓取结果重渲染（补完注解后用）
    python run_daily.py --only-diff        # 只打印最近一次差异，不抓取不渲染

产物:
    data/last_raw.json            最近一次抓取的原始数据（下一次的对比基线）
    data/snapshots/*.json         每次运行的存档
    data/annotations.json         注解缓存（owner/name -> intro/tags），跨天累积
    data/diff/<日期>.md           当日差异（人读）
    data/diff/latest.html         差异 HTML 片段（注入日报用）
    data/reports/<日期>.html      当日日报
    data/latest.html              最新日报（固定路径，方便打开）
"""

import argparse
import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # github-daily/
WORKSPACE = ROOT.parent                             # 工作区
SKILL = WORKSPACE / "github-trending-daily"         # 被调用的 skill（保持原样）
VENDOR = ROOT / "vendor"                            # 内置最小 bs4（本机装不了 bs4）
DATA = ROOT / "data"

# 每日 HTML 的对外输出目录（可用 --outdir 或环境变量 GITHUB_DAILY_OUTDIR 覆盖）
DEFAULT_OUTDIR = Path(os.environ.get("GITHUB_DAILY_OUTDIR")
                      or (Path.home() / "Desktop" / "日报" / "github"))

PERIOD_CN = {"daily": "今日", "weekly": "本周", "monthly": "本月"}
PERIODS = ("daily", "weekly", "monthly")
TRENDING_PREFIX = "https://github.com/trending/"


def _stdout_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def fix_urls(snap):
    """补齐 skill 的 url 拼接缺陷：BASE 用了 trending 列表页而不是站点根。"""
    n = 0
    for period in snap.get("periods", {}).values():
        for repo in period.get("repos", []):
            u = repo.get("url") or ""
            if u.startswith(TRENDING_PREFIX):
                repo["url"] = "https://github.com/" + u[len(TRENDING_PREFIX):]
                n += 1
    return n


def fetch_snapshot(timeout_note=""):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / "last_raw.tmp.json"
    if tmp.exists():
        tmp.unlink()
    cmd = [
        sys.executable,
        str(SKILL / "scripts" / "fetch_trending.py"),
        "--out", str(tmp),
        "--periods", "daily,weekly,monthly",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(VENDOR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not tmp.exists():
        raise RuntimeError(
            "抓取失败：\n<stdout>\n%s\n<stderr>\n%s" % (proc.stdout, proc.stderr))
    snap = _load(tmp, {})
    tmp.unlink()
    fixed = fix_urls(snap)
    snap["_url_fixed"] = fixed
    return snap


# --------------------------------------------------------------------------
# 差异计算
# --------------------------------------------------------------------------

def _period_index(snap, period):
    out = {}
    repos = (snap.get("periods", {}).get(period) or {}).get("repos") or []
    for i, r in enumerate(repos):
        key = "%s/%s" % (r.get("owner"), r.get("name"))
        out[key] = {
            "rank": i + 1,
            "total_stars": r.get("total_stars"),
            "period_stars": r.get("period_stars"),
            "lang": r.get("lang"),
        }
    return out


def compute_diff(prev, cur):
    """返回结构化差异：{period: {added, dropped, moved, star_jumps}}"""
    result = {}
    for period in PERIODS:
        cur_idx = _period_index(cur, period)
        if not cur_idx:
            continue
        prev_idx = _period_index(prev, period) if prev else {}
        added = [k for k in cur_idx if k not in prev_idx]
        dropped = [k for k in prev_idx if k not in cur_idx]
        moved = []
        star_jumps = []
        for k, c in cur_idx.items():
            p = prev_idx.get(k)
            if not p:
                continue
            rank_delta = p["rank"] - c["rank"]          # >0 = 排名上升
            if rank_delta:
                moved.append((k, rank_delta, p["rank"], c["rank"]))
            ps, cs = p.get("total_stars"), c.get("total_stars")
            if isinstance(ps, int) and isinstance(cs, int) and cs != ps:
                star_jumps.append((k, cs - ps, ps, cs))
        moved.sort(key=lambda x: -x[1])
        star_jumps.sort(key=lambda x: -x[1])
        result[period] = {
            "added": added,
            "dropped": dropped,
            "moved": moved,
            "star_jumps": star_jumps,
            "count": len(cur_idx),
        }
    return result


def diff_text(prev_at, cur_at, diff, missing_annotations, first_time=None):
    lines = []
    if not prev_at:
        lines.append("首次运行，没有上一份结果可对比。")
    else:
        lines.append("对比基线：%s → 本次 %s" % (prev_at, cur_at))
    lines.append("")

    if prev_at:
        for period in PERIODS:
            d = diff.get(period)
            if not d:
                continue
            head = "【%s榜】%d 个仓库" % (PERIOD_CN[period], d["count"])
            ft = first_time or set()
            n_first = sum(1 for k in d["added"] if k in ft)
            n_back = len(d["added"]) - n_first
            changes = []
            changes.append("首次进榜 %d" % n_first)
            changes.append("重回榜 %d" % n_back)
            changes.append("掉榜 %d" % len(d["dropped"]))
            changes.append("排名变动 %d" % len(d["moved"]))
            lines.append(head + "：" + "，".join(changes))
            for k in d["added"][:8]:
                lines.append(("  + 首次进榜  %s" if k in ft else "  ↩ 重回榜    %s") % k)
            for k in d["dropped"][:8]:
                lines.append("  - 掉榜    %s" % k)
            for k, delta, old, new in d["moved"][:5]:
                arrow = "↑" if delta > 0 else "↓"
                lines.append("  %s 排名 %s  #%d → #%d" % (arrow, k, old, new))
            for k, delta, old, new in d["star_jumps"][:5]:
                lines.append("  ★ 星数 %s  %s → %s（%+d）"
                             % (k, format(old, ","), format(new, ","), delta))
            if len(d["added"]) + len(d["dropped"]) + len(d["moved"]) == 0:
                lines.append("  榜单与上次一致，无进出与排名变化")
            lines.append("")

    if missing_annotations:
        lines.append("待补注解 %d 个：%s"
                     % (len(missing_annotations), "、".join(missing_annotations[:10])
                        + ("…" if len(missing_annotations) > 10 else "")))
    else:
        lines.append("注解齐全，无待补。")
    return "\n".join(lines).rstrip() + "\n"


def diff_html(prev_at, cur_at, diff, missing_annotations, first_time=None):
    # 折叠样式内联，保证单文件离线可用；<details> 原生展开，不需要 JS
    style = (
        "<style>"
        "#diff details{margin:0;padding:5px 0;border-top:1px dashed #d5e5dc}"
        "#diff details:first-of-type{border-top:0;padding-top:0}"
        "#diff summary{cursor:pointer;list-style:none;display:flex;align-items:baseline;"
        "gap:8px;flex-wrap:wrap;font-size:14px;font-weight:700;color:#1c1a17;outline:none}"
        "#diff summary::-webkit-details-marker{display:none}"
        "#diff summary .tri{font-size:10px;color:#0f6b5f;transition:transform .15s;display:inline-block}"
        "#diff details[open] summary .tri{transform:rotate(90deg)}"
        "#diff summary .hint{margin-left:auto;font-weight:400;font-size:12px;color:#0f6b5f}"
        "#diff details[open] summary .hint{visibility:hidden}"
        "#diff ul{margin:6px 0 2px;padding-left:44px;font-size:13.5px;color:#35322d;line-height:1.75}"
        "</style>"
    )
    if not prev_at:
        body = "<p style='margin:0;color:#6b675f'>首次运行，本次结果作为后续对比基线。</p>"
    else:
        blocks = []
        for period in PERIODS:
            d = diff.get(period)
            if not d:
                continue
            rows = []
            ft = first_time or set()
            for k in d["added"]:
                if k in ft:
                    rows.append("<li><b style='color:#0f6b5f'>+ 首次进榜</b> %s</li>"
                                % html.escape(k))
                else:
                    rows.append("<li><b style='color:#8a6d1f'>↩ 重回榜</b> %s</li>"
                                % html.escape(k))
            for k in d["dropped"]:
                rows.append("<li><b style='color:#b04a2f'>− 掉榜</b> %s</li>" % html.escape(k))
            for k, delta, old, new in d["moved"]:
                arrow = "↑" if delta > 0 else "↓"
                rows.append("<li>%s 排名 <code>%s</code> #%d → #%d</li>"
                            % (arrow, html.escape(k), old, new))
            for k, delta, old, new in d["star_jumps"]:
                rows.append("<li>★ 星数 <code>%s</code> %s → %s（%+d）</li>"
                            % (html.escape(k), format(old, ","), format(new, ","), delta))
            if not rows:
                rows.append("<li>榜单与上次一致</li>")
            n_first = sum(1 for k in d["added"] if k in ft)
            n_back = len(d["added"]) - n_first
            blocks.append(
                "<details><summary><span class='tri'>▶</span><span>%s榜</span>"
                "<span style='font-weight:400;font-size:13px;color:#6b675f'>"
                "%d 个仓库 · 首次 %d / 重回 %d / 掉榜 %d / 排名变动 %d</span>"
                "<span class='hint'>点击展开</span></summary><ul>%s</ul></details>"
                % (PERIOD_CN[period], d["count"], n_first, n_back,
                   len(d["dropped"]), len(d["moved"]), "".join(rows)))
        body = ("<p style='margin:0 0 4px;font-size:13px;color:#6b675f'>对比基线：%s → 本次 %s</p>"
                % (html.escape(str(prev_at)), html.escape(str(cur_at)))) + "".join(blocks)

    note = ""
    if missing_annotations:
        note = ("<p style='margin:8px 0 0;font-size:13px;color:#b04a2f'>待补注解 %d 个：%s</p>"
                % (len(missing_annotations),
                   html.escape("、".join(missing_annotations[:10])
                               + ("…" if len(missing_annotations) > 10 else ""))))
    return (
        "<section class=\"sec\" id=\"diff\" style=\"margin:0 0 34px;border:1px solid #cfe0d8;"
        "background:#f2f8f5;border-radius:10px;padding:16px 18px\">"
        + style +
        "<div style='border-bottom:1px solid #cfe0d8;padding-bottom:8px;margin-bottom:8px'>"
        "<span style='font-family:Georgia,serif;font-size:19px;font-weight:700'>与上次的差异</span>"
        "<span style='font-size:12.5px;color:#6b675f;margin-left:8px'>GitHub 趋势日报</span></div>"
        + body + note + "</section>"
    )


def missing_annotations(snap, annotations):
    have = annotations.get("repos", {}) if annotations else {}
    missing = []
    for period in snap.get("periods", {}).values():
        for r in period.get("repos", []):
            key = "%s/%s" % (r.get("owner"), r.get("name"))
            ann = have.get(key) or {}
            if not ann.get("intro"):
                missing.append(key)
    # 去重保序
    seen, out = set(), []
    for k in missing:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def build_report(snap_path, annotations_path, out_path):
    cmd = [
        sys.executable,
        str(SKILL / "scripts" / "build_report.py"),
        "--raw", str(snap_path),
        "--annotations", str(annotations_path),
        "--template", str(SKILL / "assets" / "report_template.html"),
        "--out", str(out_path),
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError("渲染失败：\n<stdout>\n%s\n<stderr>\n%s"
                           % (proc.stdout, proc.stderr))
    return proc.stdout.strip()


BADGE_NEW = (
    '<span style="flex:none;font-size:11px;font-weight:600;color:#0f6b5f;'
    'background:#dff0e8;border:1px solid #a9d3c3;border-radius:20px;'
    'padding:1px 8px;white-space:nowrap">新进榜</span>'
)
BADGE_BACK = (
    '<span style="flex:none;font-size:11px;font-weight:600;color:#8a6d1f;'
    'background:#f7f0dc;border:1px solid #ddd0a3;border-radius:20px;'
    'padding:1px 8px;white-space:nowrap">重回榜</span>'
)

CARD_RE = re.compile(r'<li class="card">.*?</li>', re.S)

# 本地按钮服务（报告里的 ☆ / × 通过它直接写名单）
SERVER_FILE = ROOT / "scripts" / "local_server.py"
SERVER_PORT = int(os.environ.get("GITHUB_DAILY_SERVER_PORT") or 8765)


SERVER_LABEL = {
    "running": "已在运行",
    "started": "已自动启动",
    "port-busy": "端口被其他程序占用",
    "missing": "服务脚本缺失",
    "failed": "启动失败，可用 python github-daily\\scripts\\local_server.py 手动起",
    "skipped": "已跳过（--no-server）",
}


def _server_ping(port=SERVER_PORT, timeout=0.8):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ping" % port, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def _port_open(port=SERVER_PORT, timeout=0.5):
    import socket
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def ensure_local_server(port=SERVER_PORT):
    """生成日报时顺手确认按钮服务在跑；没跑就后台拉起（不阻塞本次日报）。"""
    if _server_ping(port):
        return "running"
    if _port_open(port):
        return "port-busy"
    if not SERVER_FILE.exists():
        return "missing"
    log = DATA / "local_server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    with open(log, "ab") as fh:
        try:
            subprocess.Popen(
                [sys.executable, str(SERVER_FILE), "--port", str(port)],
                cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
                close_fds=True, creationflags=flags)
        except Exception as exc:                  # noqa: BLE001
            print("本地按钮服务启动异常：%s" % exc, file=sys.stderr)
            return "failed"
    for _ in range(20):
        time.sleep(0.15)
        if _server_ping(port):
            return "started"
    return "failed"


def _snapshot_date(snap):
    return (snap.get("fetched_at") or "")[:10]


def load_history():
    """把 data/snapshots/*.json 与 data/last_raw.json 读成 {日期: 快照}。

    同一天跑过多次只保留最后一次（统计口径是「进过多少天」，不是「跑过多少次」）。
    """
    by_date = {}
    files = sorted((DATA / "snapshots").glob("*.json"))
    last = DATA / "last_raw.json"
    if last.exists():
        files.append(last)
    for path in files:
        snap = _load(path)
        d = _snapshot_date(snap or {})
        if not snap or not d:
            continue
        prev = by_date.get(d)
        if prev is None or (snap.get("fetched_at") or "") >= (prev.get("fetched_at") or ""):
            by_date[d] = snap
    return by_date


def _iter_keys(snap):
    for period in PERIODS:
        for r in ((snap.get("periods", {}).get(period) or {}).get("repos") or []):
            yield period, "%s/%s" % (r.get("owner"), r.get("name")), r


def compute_history_stats(history):
    """{repo: {daily, weekly, monthly, total, first, last, lang, url}}，按天累加。"""
    stats = {}
    for date in sorted(history):
        for period, key, repo in _iter_keys(history[date]):
            s = stats.setdefault(key, {
                "daily": 0, "weekly": 0, "monthly": 0,
                "first": date, "last": date,
                "lang": repo.get("lang"), "url": repo.get("url"),
            })
            s[period] += 1
            s["first"] = min(s["first"], date)
            s["last"] = max(s["last"], date)
            if repo.get("lang"):
                s["lang"] = repo["lang"]
            if repo.get("url"):
                s["url"] = repo["url"]
    for s in stats.values():
        s["total"] = s["daily"] + s["weekly"] + s["monthly"]
    return stats


def write_stats_csv(stats, path, blacklist=None):
    """累计进榜统计表（utf-8-sig，Excel 直接打开不乱码）。"""
    blocked = set((blacklist or {}).get("repos", {}))
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(stats.items(), key=lambda kv: (-kv[1]["total"], -kv[1]["daily"], kv[0]))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["仓库", "日榜次数", "周榜次数", "月榜次数", "合计",
                    "首见日期", "最近日期", "语言", "链接", "已屏蔽"])
        for key, s in rows:
            w.writerow([key, s["daily"], s["weekly"], s["monthly"], s["total"],
                        s["first"], s["last"], s["lang"] or "", s["url"] or "",
                        "是" if key in blocked else "否"])
    return len(rows)


def filter_diff(diff, blacklist):
    """黑名单项目不参与差异展示（它们本来就不该再打扰阅读）。"""
    blocked = set((blacklist or {}).get("repos", {}))
    if not blocked or not diff:
        return diff
    out = {}
    for period, d in diff.items():
        nd = dict(d)
        nd["added"] = [k for k in d.get("added") or [] if k not in blocked]
        nd["dropped"] = [k for k in d.get("dropped") or [] if k not in blocked]
        nd["moved"] = [m for m in d.get("moved") or [] if m[0] not in blocked]
        nd["star_jumps"] = [s for s in d.get("star_jumps") or [] if s[0] not in blocked]
        out[period] = nd
    return out


def _card_key(card):
    m = re.search(r'<a class="repo" href="https://github\.com/([^"/]+)/([^"]+)"', card)
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def _stat_span(s):
    return ('<span class="dim" style="color:#6b675f">上榜 '
            '<b style="color:#0f6b5f">日%d · 周%d · 月%d</b></span>'
            % (s["daily"], s["weekly"], s["monthly"]))


EXT_STYLE = """<style id="daily-ext">
.blk{flex:none;width:18px;height:18px;line-height:15px;border-radius:50%;border:1px solid #dcd6c8;
background:#fff;color:#a39c8e;font-size:12px;cursor:pointer;padding:0;text-align:center;font-family:inherit}
.blk:hover{border-color:#b04a2f;color:#b04a2f;background:#fdf3f0}
.blocked{margin:12px 0 0;padding:6px 10px;border:1px dashed #ddd6c9;border-radius:8px;background:#faf8f3}
.blocked summary{cursor:pointer;font-size:12.5px;color:#6b675f;list-style:none;outline:none}
.blocked summary::-webkit-details-marker{display:none}
.blocked summary:before{content:"\\25B8  ";color:#a39c8e}
.blocked[open] summary:before{content:"\\25BE  "}
.blocked ul{margin:6px 0 2px;padding-left:22px;font-size:12.5px;color:#6b675f;line-height:1.9;columns:2;column-gap:24px}
.blocked a{color:#6b675f;text-decoration:none;border-bottom:1px dotted #cfc8b8}
.blocked a:hover{color:#0f6b5f}
@media(max-width:720px){.blocked ul{columns:1}}
#dl-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(10px);background:#1c1a17;
color:#f7f4ee;font-size:13px;padding:10px 16px;border-radius:22px;opacity:0;transition:.2s;pointer-events:none;
max-width:80vw;z-index:99;box-shadow:0 6px 20px rgba(0,0,0,.18)}
#dl-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.wt{flex:none;width:18px;height:18px;line-height:15px;border-radius:50%;border:1px solid #dcd6c8;background:#fff;
color:#b9a35f;font-size:12px;cursor:pointer;padding:0;text-align:center;font-family:inherit}
.wt:hover{border-color:#c9a227;color:#a8860b;background:#fdf9ec}
.watch{margin:12px 0 0;padding:10px 12px;border:1px solid #d9e2ef;border-radius:8px;background:#f5f8fd}
.watch .wh{font-size:12.5px;font-weight:700;color:#2f4a6d;margin-bottom:6px}
.watch ul{margin:0;padding-left:18px;font-size:13px;color:#35322d;line-height:1.7}
.watch li{margin:0 0 8px}
.watch li:last-child{margin-bottom:0}
.watch a{color:#1c1a17;font-weight:600;text-decoration:none;border-bottom:1px dotted #c3cfdf}
.watch a:hover{color:#0f6b5f}
.watch .chips{color:#6b675f;font-size:12.5px;margin-left:6px}
.watch .advice{margin:3px 0 0;color:#3f4a5a;font-size:12.5px}
.watch .advice b{color:#2f4a6d}
.sync{margin:28px 0 0;padding:12px 14px;border:1px dashed #d9cfb8;border-radius:8px;background:#fbf8f0}
.sync-h{font-size:13px;font-weight:700;color:#6b5b2f;margin-bottom:4px}
.sync-p{margin:0 0 9px;font-size:12.5px;color:#6b675f;line-height:1.7}
.sync-btn{font-size:12.5px;padding:5px 14px;border-radius:20px;border:1px solid #c9a227;background:#fff;
color:#8a6d1f;cursor:pointer;font-family:inherit}
.sync-btn:hover{background:#fdf9ec}
.sync-tip{margin-left:10px;font-size:12px;color:#8a8578}
.card-head{display:block;margin:0}
.card-sub{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0}
.card-sub .sub-gap{flex:1}
</style>"""

EXT_SCRIPT = """<script id="daily-ext-js">
(function(){
  var KEY='github-daily-blacklist';
  function load(){try{return JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){return[]}}
  function save(a){try{localStorage.setItem(KEY,JSON.stringify(a))}catch(e){}}
  var WKEY='github-daily-watch';
  function loadW(){try{return JSON.parse(localStorage.getItem(WKEY)||'[]')}catch(e){return[]}}
  function saveW(a){try{localStorage.setItem(WKEY,JSON.stringify(a))}catch(e){}}
  function toast(m){var t=document.getElementById('dl-toast');if(!t){t=document.createElement('div');t.id='dl-toast';document.body.appendChild(t);}
    t.textContent=m;t.className='show';clearTimeout(toast._t);toast._t=setTimeout(function(){t.className=''},4000);}
  function copy(s){function fb(){var ta=document.createElement('textarea');ta.value=s;ta.style.position='fixed';ta.style.opacity='0';
      document.body.appendChild(ta);ta.select();try{document.execCommand('copy')}catch(e){}ta.remove();}
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(s).then(function(){},fb)}else{fb()}}
  function secOf(el){while(el&&el.tagName!=='SECTION'){el=el.parentElement}return el}
  function box(sec){var d=sec.querySelector('details.blocked');if(!d){d=document.createElement('details');
      d.className='blocked';d.innerHTML='<summary></summary><ul></ul>';sec.appendChild(d)}return d}
  function markBlocked(sec,repo){var d=box(sec),ul=d.querySelector('ul'),li=document.createElement('li'),
      a=document.createElement('a');a.href='https://github.com/'+repo;a.target='_blank';a.rel='noopener';
      a.textContent=repo;li.appendChild(a);ul.appendChild(li);
      d.querySelector('summary').textContent='已屏蔽 '+ul.children.length+' 个仓库（点击展开，可在对话里解除）';}
  function hide(card,repo){var sec=secOf(card);if(card.parentNode){card.parentNode.removeChild(card)}if(sec){markBlocked(sec,repo)}}
  var SA='http://127.0.0.1:8765';
  function send(type,repo){return fetch(SA+'/add',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type:type,repo:repo})}).then(function(r){return r.ok}).catch(function(){return false});}
  load().forEach(function(repo){Array.prototype.forEach.call(document.querySelectorAll('li.card'),function(card){
      var a=card.querySelector('a.repo');if(!a)return;
      if(a.getAttribute('href').replace('https://github.com/','')===repo){hide(card,repo)}})});
  document.addEventListener('click',function(e){
    var b=e.target.closest?e.target.closest('button.blk'):null;if(!b)return;e.preventDefault();
    var card=b.closest('li.card');if(!card)return;var repo=b.getAttribute('data-repo');if(!repo)return;
    hide(card,repo);
    send('block',repo).then(function(ok){
      if(ok){toast('已加入黑名单并写入名单，明天起不再出现（刷新本页可见最新）');}
      else{
        /* 服务不在：只做本机临时隐藏 + 复制命令，不假装已经落盘 */
        var arr=load();if(arr.indexOf(repo)<0){arr.push(repo);save(arr)}
        copy('屏蔽 '+repo);
        toast('已在页面隐藏：'+repo+'；本地服务未运行，已复制命令，粘贴到对话可永久屏蔽');}});});
  document.addEventListener('click',function(e){
    var b=e.target.closest?e.target.closest('button.wt'):null;if(!b)return;e.preventDefault();
    var repo=b.getAttribute('data-repo');if(!repo)return;b.textContent='★';
    send('watch',repo).then(function(ok){
      if(ok){b.title='已加入关注名单';toast('已加入关注名单，下次上榜会附更新说明');}
      else{var wa=loadW();if(wa.indexOf(repo)<0){wa.push(repo);saveW(wa)}
        copy('关注 '+repo);b.title='已复制关注命令';
        toast('已复制：关注 '+repo+'；本地服务未运行，粘贴到对话即可加入');}});});
  // ---- 名单同步（跨设备）：嵌入名单 + 本机临时改动合在一起，一键复制 ----
  function allLists(){
    var bl=((window.__GD_LISTS__||{}).blacklist||[]).slice();
    load().forEach(function(r){if(bl.indexOf(r)<0)bl.push(r)});
    var wt=((window.__GD_LISTS__||{}).watch||[]).slice();
    loadW().forEach(function(r){if(wt.indexOf(r)<0)wt.push(r)});
    return {bl:bl,wt:wt};}
  function refreshSync(){
    var el=document.getElementById('gd-sync-info');if(!el)return;
    var a=allLists();
    el.textContent='当前名单：屏蔽 '+a.bl.length+' 个 · 关注 '+a.wt.length+' 个'
      +'（服务不可用时本机点过的也会算进来）';}
  var sb=document.getElementById('gd-sync-btn');
  if(sb){sb.addEventListener('click',function(){
    var a=allLists(),d=((window.__GD_LISTS__||{}).date)||'';
    var txt='github-daily 名单同步'+(d?'（'+d+'）':'')+'\\n'
      +'屏蔽：'+(a.bl.length?a.bl.join('、'):'无')+'\\n'
      +'关注：'+(a.wt.length?a.wt.join('、'):'无');
    copy(txt);toast('已复制名单同步清单，粘贴到对话即可在其他设备/日报上对齐');});}
  refreshSync();
})();
</script>"""


def inject_assets(content):
    if 'id="daily-ext"' not in content:
        content = content.replace("</head>", EXT_STYLE + "\n</head>", 1)
    if 'id="daily-ext-js"' not in content:
        content = content.replace("</body>", EXT_SCRIPT + "\n</body>", 1)
    return content


def inject_sync_block(content, blacklist, watchlist, day):
    """页脚后面加「名单同步」区块：嵌入当前名单 + 一键复制，用于跨设备对齐。"""
    if 'id="gd-sync"' in content:
        return content
    lists = {
        "date": day,
        "blacklist": sorted((blacklist or {}).get("repos", {})),
        "watch": sorted((watchlist or {}).get("repos", {})),
    }
    payload = json.dumps(lists, ensure_ascii=False).replace("</", "<\\/")
    block = (
        '<script id="gd-lists">window.__GD_LISTS__ = ' + payload + ';</script>'
        '<div class="sync" id="gd-sync">'
        '<div class="sync-h">名单同步 · 跨设备</div>'
        '<p class="sync-p" id="gd-sync-info">正在统计当前名单…</p>'
        '<button type="button" class="sync-btn" id="gd-sync-btn">一键复制同步</button>'
        '<span class="sync-tip">复制后粘贴到对话，我按清单在另一台设备/另一份日报上对齐</span>'
        '</div>'
    )
    marker = "</footer>"
    pos = content.find(marker)
    if pos == -1:
        return content
    pos += len(marker)
    return content[:pos] + "\n" + block + content[pos:]


def _block_button(key):
    owner, _, name = key.partition("/")
    return ('<button class="blk" type="button" data-repo="%s" title="不想再看到它，加入黑名单">×</button>'
            % html.escape(key))


def _blocked_html(keys):
    items = "".join(
        '<li><a href="https://github.com/%s" target="_blank" rel="noopener">%s</a></li>'
        % (html.escape(k), html.escape(k)) for k in keys)
    return ('<details class="blocked"><summary>已屏蔽 %d 个仓库（点击展开，可在对话里解除）</summary>'
            '<ul>%s</ul></details>' % (len(keys), items))


# --------------------------------------------------------------------------
# 关注名单：关注的项目再次上榜时，给出「这段时间有没有重大更新」的事实与建议
# --------------------------------------------------------------------------

BADGE_WATCH = (
    '<span style="flex:none;font-size:11px;font-weight:600;color:#2f4a6d;'
    'background:#e8eefa;border:1px solid #c3d2ec;border-radius:20px;'
    'padding:1px 8px;white-space:nowrap">已关注</span>'
)

WATCH_FACTS_FILE = DATA / "watch_facts.json"
GITHUB_API = "https://api.github.com"


def _watch_button(key):
    return ('<button class="wt" type="button" data-repo="%s" '
            'title="加入关注名单：下次上榜时给出更新说明">☆</button>'
            % html.escape(key))


def _api_get(path, params=None, timeout=15):
    import requests
    headers = {"User-Agent": "github-daily-report", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    resp = requests.get(GITHUB_API + path, headers=headers, params=params, timeout=timeout)
    if resp.status_code == 404:
        return None
    if resp.status_code in (403, 429):
        raise RuntimeError("API 限流 HTTP %d" % resp.status_code)
    resp.raise_for_status()
    return resp.json()


def _local_watch_facts(repo, history, today, snap):
    """本地历史能给出的事实：上次上榜日期、间隔天数、星数变化。"""
    last_seen, stars_then = None, None
    for d in sorted(history):
        if d >= today:
            continue
        hit = None
        for _p, key, r in _iter_keys(history[d]):
            if key == repo:
                hit = r
                break
        if hit is not None:
            last_seen = d
            stars_then = hit.get("total_stars")
    stars_now = None
    for _p, key, r in _iter_keys(snap):
        if key == repo:
            stars_now = r.get("total_stars")
            break
    gap = None
    if last_seen:
        gap = (datetime.strptime(today, "%Y-%m-%d")
               - datetime.strptime(last_seen, "%Y-%m-%d")).days
    delta = None
    if isinstance(stars_then, int) and isinstance(stars_now, int):
        delta = stars_now - stars_then
    return {"last_seen": last_seen, "gap": gap, "star_delta": delta, "stars": stars_now}


def collect_watch_facts(watchlist, snap, history, today):
    """只为「今天在榜」的关注项目取更新事实；同一天重渲染复用缓存，避免重复打 API。"""
    repos = list((watchlist or {}).get("repos", {}))
    if not repos:
        return {}
    today_keys = {k for _p, k, _r in _iter_keys(snap)}
    cache = (_load(WATCH_FACTS_FILE, {"repos": {}}) or {}).get("repos", {})
    facts = {}
    for repo in repos:
        if repo not in today_keys:
            continue
        cached = cache.get(repo)
        if cached and cached.get("date") == today:
            facts[repo] = cached
            continue
        f = {"date": today}
        f.update(_local_watch_facts(repo, history, today, snap))
        # 提交数统计窗口：上次上榜那天起；从没上过榜则取最近 7 天
        since = f.get("last_seen") or (
            datetime.strptime(today, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        f["commits_since"] = since
        try:
            meta = _api_get("/repos/" + repo) or {}
            f["pushed_at"] = meta.get("pushed_at")
            f["stars"] = meta.get("stargazers_count") or f.get("stars")
            f["open_issues"] = meta.get("open_issues_count")
            f["archived"] = bool(meta.get("archived"))
            try:
                rel = _api_get("/repos/%s/releases/latest" % repo)
                if rel and rel.get("tag_name"):
                    f["release"] = {"tag": rel.get("tag_name"),
                                    "at": rel.get("published_at")}
            except Exception:
                pass
            try:
                commits = _api_get("/repos/%s/commits" % repo,
                                   {"since": since + "T00:00:00Z", "per_page": 100})
                if isinstance(commits, list):
                    f["commits"] = len(commits)
            except Exception:
                pass
        except Exception as exc:
            f["api_error"] = str(exc)[:120]
        facts[repo] = f
    _dump(WATCH_FACTS_FILE, {"date": today, "repos": facts})
    return facts


def _watch_html(facts, watchlist):
    if not facts:
        return ""
    repos_cfg = (watchlist or {}).get("repos", {})
    items = []
    for repo, f in facts.items():
        chips = []
        if f.get("last_seen"):
            chips.append("上次上榜 %s（%d 天前）" % (f["last_seen"], f.get("gap") or 0))
        else:
            chips.append("首次上榜")
        if isinstance(f.get("star_delta"), int):
            chips.append("星 %+d" % f["star_delta"])
        if f.get("commits") is not None:
            span = ("自 %s " % f["commits_since"]) if f.get("commits_since") else ""
            chips.append("%s提交 %s 次"
                         % (span, "100+" if f["commits"] >= 100 else f["commits"]))
        if f.get("pushed_at"):
            chips.append("最近推送 %s" % f["pushed_at"][:10])
        rel = f.get("release") or {}
        if rel.get("tag"):
            chips.append("最新版本 %s（%s）" % (rel["tag"], (rel.get("at") or "")[:10]))
        if f.get("archived"):
            chips.append("仓库已归档")
        if f.get("api_error"):
            chips.append("更新详情获取失败")
        note = (repos_cfg.get(repo) or {}).get("note") or ""
        advice = (html.escape(note) if note else
                  "<i style='color:#8a6d1f'>建议待补：说明这段时间有哪些重大更新、值不值得看</i>")
        items.append(
            '<li><a href="https://github.com/%s" target="_blank" rel="noopener">%s</a>'
            '<span class="chips">%s</span>'
            '<p class="advice"><b>建议：</b>%s</p></li>'
            % (html.escape(repo), html.escape(repo),
               html.escape(" · ".join(chips)), advice))
    return ('<div class="watch"><div class="wh">关注项目动态 · %d 个'
            '（这段时间有没有值得注意的更新）</div><ul>%s</ul></div>'
            % (len(facts), "".join(items)))


def _decorate_card(card, key, added, first_time, stats, watched):
    """项目名独占一行；徽标、语言、☆ / × 按钮放到下一行；meta 里补累计上榜。"""
    head_start = card.find('<div class="card-head">')
    head_close = card.find("</div>", head_start) if head_start != -1 else -1
    if head_start != -1 and head_close != -1:
        inner = card[head_start + len('<div class="card-head">'):head_close]
        m = re.search(r'(<a class="repo".*?</a>)\s*(<span class="lang">.*?</span>)?',
                      inner, re.S)
        if m:
            anchor, lang = m.group(1), (m.group(2) or "")
            badges = ""
            if key in added:
                badges += BADGE_NEW if key in first_time else BADGE_BACK
            if key in watched:
                badges += BADGE_WATCH
            sub = ('<div class="card-sub">' + badges + lang
                   + '<span class="sub-gap"></span>'
                   + _watch_button(key) + _block_button(key) + "</div>")
            card = (card[:head_start] + '<div class="card-head">' + anchor + "</div>"
                    + sub + card[head_close + len("</div>"):])

    s = stats.get(key)
    if s:
        pos = card.find('<div class="meta">')
        if pos != -1:
            close = card.find("</div>", pos)
            if close != -1:
                card = card[:close] + _stat_span(s) + card[close:]
    return card


def decorate_sections(content, diff, prev_at, stats, first_time, blacklist,
                      watchlist=None, watch_facts=None):
    """按板块重排卡片、处理黑名单与关注名单。

    - 新进榜排最前，其次是重回榜，其余保持原有名次顺序
    - 黑名单里的仓库不出现卡片，只在板块末尾的折叠列表里留个名字
    - 关注项目在榜时，板块末尾（已屏蔽列表之前）给出更新说明
    """
    blocked_keys = set((blacklist or {}).get("repos", {}))
    watched_keys = set((watchlist or {}).get("repos", {}))
    added_by_period = {}
    if prev_at and diff:
        for period in PERIODS:
            added_by_period[period] = set((diff.get(period) or {}).get("added") or [])

    for period in PERIODS:
        added = added_by_period.get(period) or set()
        start_marker = '<section class="sec" id="%s">' % period
        start = content.find(start_marker)
        if start == -1:
            continue
        end = content.find("</section>", start)
        if end == -1:
            continue
        end += len("</section>")
        chunk = content[start:end]
        ul_start = chunk.find('<ul class="grid">')
        if ul_start == -1:
            continue
        ul_end = chunk.find("</ul>", ul_start)
        if ul_end == -1:
            continue

        inner = chunk[ul_start + len('<ul class="grid">'):ul_end]
        kept, blocked, section_keys = [], [], set()
        for card in CARD_RE.findall(inner):
            key = _card_key(card)
            if not key:
                continue
            section_keys.add(key)
            if key in blocked_keys:
                if key not in blocked:
                    blocked.append(key)
                continue
            card = _decorate_card(card, key, added, first_time, stats, watched_keys)
            order = 0 if key in first_time else (1 if key in added else 2)
            kept.append((order, card))
        kept.sort(key=lambda t: t[0])      # 稳定排序：组内保持原名次

        watch_html = ""
        if watch_facts:
            mine = {k: v for k, v in watch_facts.items() if k in section_keys}
            watch_html = _watch_html(mine, watchlist)

        new_chunk = (chunk[:ul_start + len('<ul class="grid">')]
                     + "\n" + "\n".join(c for _, c in kept) + "\n            "
                     + "</ul>"
                     + watch_html
                     + (_blocked_html(blocked) if blocked else "")
                     + chunk[ul_end + len("</ul>"):])
        content = content[:start] + new_chunk + content[end:]
    return content


def inject_diff(report_path, diff_fragment):
    content = report_path.read_text(encoding="utf-8")
    if 'id="diff"' in content:      # 幂等：重渲染后不会叠加
        start = content.index("<section class=\"sec\" id=\"diff\"")
        end = content.index("</section>", start) + len("</section>")
        content = content[:start] + diff_fragment + content[end:]
    else:
        marker = "<main>"
        pos = content.find(marker)
        if pos == -1:
            raise RuntimeError("模板里没找到 <main>，无法注入差异区块")
        pos += len(marker)
        content = content[:pos] + "\n" + diff_fragment + content[pos:]
    report_path.write_text(content, encoding="utf-8")


def main():
    _stdout_utf8()
    ap = argparse.ArgumentParser(description="GitHub Trending 每日任务")
    ap.add_argument("--no-fetch", action="store_true", help="用上次抓取结果重渲染")
    ap.add_argument("--only-diff", action="store_true", help="只打印最近一次差异")
    ap.add_argument("--outdir", default=str(DEFAULT_OUTDIR),
                    help="每日 HTML 与统计 CSV 的对外输出目录")
    ap.add_argument("--block", metavar="OWNER/NAME", help="加入黑名单并立即重渲染")
    ap.add_argument("--unblock", metavar="OWNER/NAME", help="移出黑名单并立即重渲染")
    ap.add_argument("--list-blocked", action="store_true", help="打印当前黑名单")
    ap.add_argument("--watch", metavar="OWNER/NAME", help="加入关注名单并立即重渲染")
    ap.add_argument("--unwatch", metavar="OWNER/NAME", help="移出关注名单并立即重渲染")
    ap.add_argument("--list-watched", action="store_true", help="打印关注名单")
    ap.add_argument("--no-server", action="store_true",
                    help="不检查/不自动启动本地按钮服务")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    ann_path = DATA / "annotations.json"
    annotations = _load(ann_path, {"summary": {}, "repos": {}})

    bl_path = DATA / "blacklist.json"
    blacklist = _load(bl_path, {"repos": {}})
    if not isinstance(blacklist, dict) or "repos" not in blacklist:
        blacklist = {"repos": {}}

    if args.list_blocked:
        keys = sorted(blacklist["repos"])
        print("黑名单共 %d 个：" % len(keys))
        for k in keys:
            print("  " + k)
        return 0

    changed = False
    for value, action in ((args.block, "add"), (args.unblock, "remove")):
        if not value:
            continue
        repo = value.strip()
        if "/" not in repo:
            print("仓库要写成 owner/name：%s" % repo, file=sys.stderr)
            return 2
        if action == "add":
            if repo not in blacklist["repos"]:
                blacklist["repos"][repo] = {"since": datetime.now().strftime("%Y-%m-%d")}
                changed = True
            print("已加入黑名单：%s" % repo)
        else:
            if blacklist["repos"].pop(repo, None) is not None:
                changed = True
            print("已移出黑名单：%s" % repo)
    if changed:
        _dump(bl_path, blacklist)
        args.no_fetch = True          # 改完直接重渲染，不必再抓一次

    wl_path = DATA / "watchlist.json"
    watchlist = _load(wl_path, {"repos": {}})
    if not isinstance(watchlist, dict) or "repos" not in watchlist:
        watchlist = {"repos": {}}

    if args.list_watched:
        keys = sorted(watchlist["repos"])
        print("关注名单共 %d 个：" % len(keys))
        for k in keys:
            note = (watchlist["repos"][k] or {}).get("note") or "（建议待补）"
            print("  %-42s %s" % (k, note))
        return 0

    wchanged = False
    for value, action in ((args.watch, "add"), (args.unwatch, "remove")):
        if not value:
            continue
        repo = value.strip()
        if "/" not in repo:
            print("仓库要写成 owner/name：%s" % repo, file=sys.stderr)
            return 2
        if action == "add":
            if repo not in watchlist["repos"]:
                watchlist["repos"][repo] = {
                    "since": datetime.now().strftime("%Y-%m-%d"), "note": ""}
                wchanged = True
            print("已加入关注名单：%s" % repo)
        else:
            if watchlist["repos"].pop(repo, None) is not None:
                wchanged = True
            print("已移出关注名单：%s" % repo)
    if wchanged:
        _dump(wl_path, watchlist)
        args.no_fetch = True

    if args.only_diff:
        p = DATA / "diff" / "latest.md"
        print(p.read_text(encoding="utf-8") if p.exists() else "还没有差异记录，先跑一次 run_daily.py")
        return 0

    now = datetime.now().astimezone()
    day = now.strftime("%Y-%m-%d")
    last_path = DATA / "last_raw.json"
    state_path = DATA / "diff" / "latest.json"

    if args.no_fetch:
        snap = _load(last_path)
        state = _load(state_path)
        if not snap or not state:
            print("没有可复用的抓取结果，请先完整跑一次（去掉 --no-fetch）", file=sys.stderr)
            return 2
        prev_at, cur_at = state.get("prev_at"), state.get("cur_at")
        diff = state.get("diff") or {}
    else:
        prev = _load(last_path)
        prev_at = prev.get("fetched_at") if prev else None
        snap = fetch_snapshot()
        cur_at = snap.get("fetched_at")
        diff = compute_diff(prev, snap)
        _dump(last_path, snap)
        _dump(DATA / "snapshots" / ("%s.json" % now.strftime("%Y%m%d-%H%M%S")), snap)

    # 黑名单项目不参与差异展示
    raw_diff = diff
    diff = filter_diff(raw_diff, blacklist)

    # 历史统计（含本次）：用于「累计上榜次数」与「新进榜/重回榜」判定
    history = load_history()
    stats = compute_history_stats(history)
    cur_date = _snapshot_date(snap) or day
    prior_keys = set()
    for d, old in history.items():
        if d >= cur_date:
            continue
        for _period, key, _repo in _iter_keys(old):
            prior_keys.add(key)
    added_all = set()
    if prev_at:
        for period in PERIODS:
            added_all |= set((diff.get(period) or {}).get("added") or [])
    first_time = {k for k in added_all if k not in prior_keys}

    # 关注名单：只对今天在榜的关注项目取更新事实（有缓存则复用）
    watch_facts = collect_watch_facts(watchlist, snap, history, cur_date)

    miss = missing_annotations(snap, annotations)
    diff_md = diff_text(prev_at, cur_at, diff, miss, first_time)
    fragment = diff_html(prev_at, cur_at, diff, miss, first_time)

    (DATA / "diff").mkdir(parents=True, exist_ok=True)
    (DATA / "diff" / "latest.md").write_text(diff_md, encoding="utf-8")
    (DATA / "diff" / "latest.html").write_text(fragment, encoding="utf-8")
    (DATA / "diff" / ("%s.md" % day)).write_text(diff_md, encoding="utf-8")
    _dump(state_path, {"prev_at": prev_at, "cur_at": cur_at, "diff": raw_diff,
                       "missing": miss, "first_time": sorted(first_time)})

    # 渲染
    ann_path.parent.mkdir(parents=True, exist_ok=True)
    if not ann_path.exists():
        _dump(ann_path, {"summary": {}, "repos": {}})
    report = DATA / "reports" / ("%s.html" % day)
    report.parent.mkdir(parents=True, exist_ok=True)
    build_report(last_path, ann_path, report)

    content = report.read_text(encoding="utf-8")
    decorated = decorate_sections(content, diff, prev_at, stats, first_time, blacklist,
                                 watchlist, watch_facts)
    decorated = inject_assets(decorated)
    decorated = inject_sync_block(decorated, blacklist, watchlist, day)
    if decorated != content:
        report.write_text(decorated, encoding="utf-8")

    inject_diff(report, fragment)
    shutil.copyfile(report, DATA / "latest.html")

    # 累计进榜统计表
    stats_path = DATA / "stats.csv"
    write_stats_csv(stats, stats_path, blacklist)

    # 对外输出：<输出目录>/<日期>.html 与 进榜统计.csv
    outdir = Path(args.outdir)
    published = stats_published = None
    publish_note = ""
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        published = outdir / ("%s.html" % day)
        shutil.copyfile(report, published)
        stats_published = outdir / "进榜统计.csv"
        shutil.copyfile(stats_path, stats_published)
    except OSError as exc:
        publish_note = "警告：写入输出目录失败（%s），本次只保留工作区副本。" % exc
        published = stats_published = None

    # 生成完顺手确认按钮服务在跑（不在就后台拉起，不影响本次产物）
    server_state = "skipped" if args.no_server else ensure_local_server()

    print("=" * 68)
    print("GitHub 趋势日报 · %s" % day)
    print("=" * 68)
    print(diff_md.strip())
    print("-" * 68)
    if stats:
        print("进榜统计：%s ~ %s 共 %d 天记录，%d 个仓库；本次首次进榜 %d 个"
              % (min(history), max(history), len(history), len(stats), len(first_time)))
        for key, s in sorted(stats.items(),
                             key=lambda kv: (-kv[1]["total"], kv[0]))[:3]:
            print("  上榜最多：%-38s 日%d 周%d 月%d"
                  % (key, s["daily"], s["weekly"], s["monthly"]))
    if published:
        print("日报（对外）：%s" % published)
    else:
        print("日报（工作区）：%s" % report)
    print("日报（工作区）：%s" % report)
    print("统计表：%s%s" % (stats_path,
                          "" if not stats_published else "  |  对外：%s" % stats_published))
    print("本地按钮服务：%s（127.0.0.1:%d）"
          % (SERVER_LABEL.get(server_state, server_state), SERVER_PORT))
    if watch_facts:
        print("关注项目在榜 %d 个：%s" % (len(watch_facts), "、".join(watch_facts)))
        pending = [r for r in watch_facts
                   if not ((watchlist["repos"].get(r) or {}).get("note"))]
        if pending:
            print("关注项目建议待补 %d 个（补 watchlist.json 的 note 后重跑 --no-fetch）：%s"
                  % (len(pending), "、".join(pending)))
    if blacklist["repos"]:
        print("黑名单：%d 个（不显示卡片，只在各板块末尾折叠列出）" % len(blacklist["repos"]))
    print("最新：%s" % (DATA / "latest.html"))
    if publish_note:
        print(publish_note)
    if miss:
        print("待补注解 %d 个，补完 data/annotations.json 后执行："
              "python scripts/run_daily.py --no-fetch" % len(miss))
    return 0


if __name__ == "__main__":
    sys.exit(main())
