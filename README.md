# github-daily-report

把 GitHub Trending（日 / 周 / 月榜）抓下来，渲染成一份说人话的中文 HTML 日报；
在此之上加了一层「每日任务」：差异对比、累计进榜统计、黑名单 / 关注名单、本地按钮服务。

## 目录

```
github-trending-daily/          上游 skill：抓取三个周期的 Trending、渲染 HTML（原样保留）
github-daily/                   适配层
├── scripts/run_daily.py        唯一入口：抓取 → 修 url → 渲染 → 差异 → 统计 → 名单 → 发布
├── scripts/local_server.py     本地小服务（让报告里的 ☆ / × 直接写名单）
├── tests/                      自检脚本（差异算法 8 项 / 交付 35 项）
└── vendor/bs4/                 极简 BeautifulSoup 替身（无外网环境也能跑）
```

## 快速开始

```bash
python -m pip install requests beautifulsoup4
python github-daily/scripts/run_daily.py                 # 抓取 + 渲染 + 差异 + 统计
python github-daily/scripts/run_daily.py --no-fetch      # 补完注解后重渲染
python github-daily/scripts/run_daily.py --only-diff     # 只看最近一次差异
```

产物：`github-daily/data/latest.html`，并复制到 `<输出目录>/<日期>.html`
（默认 `~/Desktop/日报/github`，可用 `--outdir` 或 `GITHUB_DAILY_OUTDIR` 覆盖）。

## 适配层做了什么

| 能力 | 说明 |
| --- | --- |
| 每日任务 | 工作日 08:00 自动跑；抓取失败不动基线 |
| 差异对比 | 对比上一次运行，输出首次进榜 / 重回榜 / 掉榜 / 排名变化 / 星数变化，HTML 里可折叠 |
| 累计统计 | 每个仓库进过多少次日/周/月榜（按天去重），导出 `stats.csv` |
| 排序 | 每个板块内：新进榜 → 重回榜 → 其余（保持原名次） |
| 黑名单 | 不看某个项目：卡片消失，只在板块末尾折叠列出；× 按钮一键写入 |
| 关注名单 | 关注的项目再上榜时，给出「这段时间有没有重大更新」的事实 + 建议；☆ 按钮一键写入 |
| 本地按钮服务 | 报告是静态 HTML，浏览器不能写磁盘，所以起一个只监听 127.0.0.1 的小服务处理按钮；服务不在时自动退回「复制命令」 |
| 名单同步 | 报告末尾把当前名单嵌进 HTML，一键复制清单用于跨设备对齐 |

## 修复的上游问题

- `fetch_trending.py` 把仓库链接拼成 `https://github.com/trending/<owner>/<repo>`（404），适配层统一改写为 `https://github.com/<owner>/<repo>`
- 渲染层 `intro` / `takeaway` 未转义，适配层在注入前做了处理
- 上游无 `requirements.txt`、无 LICENSE，README 里做了说明

## 隐私与安全

- 仓库**不含**运行期数据：`data/`（抓取快照、注解、黑白名单、报表）、`*.log`、token 文件均已 gitignore
- 所有凭证只从环境变量读取（`GITHUB_TOKEN` / `GH_TOKEN`），不落盘
- 本地按钮服务只绑定 `127.0.0.1`，只接受 block / watch 两个动作，仓库名做格式校验

## 来源与许可

**本仓库分两部分，许可不同：**

| 部分 | 许可 |
| --- | --- |
| `github-daily/`（适配层）与根目录文档 | **Apache License 2.0**，见 [LICENSE](LICENSE) |
| `github-trending-daily/`（上游 skill） | **不在 Apache-2.0 范围内**：上游仓库未附 LICENSE，此处按原字节保留、仅作学习与研究引用 |

`github-trending-daily/` 来自 [zhuweijun1003-source/zhuwj-skills](https://github.com/zhuweijun1003-source/zhuwj-skills)
的 `github-trending-daily` 目录，4 个文件逐字节保留（blob SHA 与上游一致）。
如需再分发该目录，请先取得原作者授权。第三方归属说明见 [NOTICE](NOTICE)。

```
Copyright 2026 sensem

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
```
