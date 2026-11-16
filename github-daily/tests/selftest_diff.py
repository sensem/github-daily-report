#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""差异算法的自测：用构造数据验证新进榜/掉榜/排名/星数四类变化都能识别。

用法: python tests/selftest_diff.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_daily as rd  # noqa: E402


def snap(daily, weekly, at):
    def repos(items):
        out = []
        for owner, name, stars in items:
            out.append({"owner": owner, "name": name, "lang": "Python",
                        "total_stars": stars, "period_stars": 10, "forks": 1,
                        "url": "https://github.com/%s/%s" % (owner, name)})
        return out
    return {"fetched_at": at,
            "periods": {"daily": {"label": "今日", "repos": repos(daily)},
                        "weekly": {"label": "本周", "repos": repos(weekly)}}}


prev = snap(
    [("a", "one", 1000), ("b", "two", 2000), ("c", "three", 3000), ("d", "four", 4000)],
    [("e", "five", 5000), ("f", "six", 6000)],
    "2026-11-08T08:00:00+08:00",
)
cur = snap(
    [("b", "two", 2100), ("d", "four", 4000), ("a", "one", 1050), ("x", "new", 900)],
    [("e", "five", 5000)],
    "2026-11-09T08:00:00+08:00",
)

diff = rd.compute_diff(prev, cur)
text = rd.diff_text(prev["fetched_at"], cur["fetched_at"], diff, ["x/new"])
frag = rd.diff_html(prev["fetched_at"], cur["fetched_at"], diff, ["x/new"])

checks = []
d = diff["daily"]
checks.append(("新进榜识别", d["added"] == ["x/new"]))
checks.append(("掉榜识别", d["dropped"] == ["c/three"]))
checks.append(("排名变化识别", len(d["moved"]) == 3))
checks.append(("星数变化识别", any(k == "a/one" and delta == 50 for k, delta, _, _ in d["star_jumps"])))
w = diff["weekly"]
checks.append(("周榜掉榜识别", w["dropped"] == ["f/six"]))
checks.append(("差异文本含基线", "对比基线" in text))
checks.append(("差异 HTML 可注入", 'id="diff"' in frag and "x/new" in frag))
checks.append(("待补注解进文本", "待补注解 1 个" in text))

print(text)
print("-" * 60)
bad = 0
for name, ok in checks:
    print(("PASS  " if ok else "FAIL  ") + name)
    bad += 0 if ok else 1
print("-" * 60)
print("全部通过" if bad == 0 else "%d 项失败" % bad)
sys.exit(0 if bad == 0 else 1)
