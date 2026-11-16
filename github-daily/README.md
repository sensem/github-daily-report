# GitHub 趋势日报 · 每日任务

把 `github-trending-daily` 这个 skill 变成每个工作日 08:00 自动跑一次的日报任务，并把「与上一次的差异」放进日报和对话里。

skill 本身保持原样（`../github-trending-daily/`，4 个文件未改动一行），所有适配都放在本目录。

---

## 目录

```
github-daily/
├── scripts/
│   └── run_daily.py        抓取 → 修 url → 渲染 → 差异对比（唯一入口）
├── vendor/bs4/             内置最小 BeautifulSoup（本机装不了 bs4，仅供 skill 脚本解析用）
├── data/
│   ├── annotations.json    注解缓存（owner/name → intro/tags），跨天累积、只增不减
│   ├── blacklist.json      黑名单（被屏蔽的仓库不再显示卡片）
│   ├── watchlist.json      关注名单（关注的项目上榜时给出更新说明 + 建议）
│   ├── watch_facts.json    关注项目的更新事实缓存（当天复用，避免重复打 API）
│   ├── last_raw.json       最近一次抓取结果，同时是下一次的对比基线
│   ├── snapshots/          每次运行的存档（YYYYMMDD-HHMMSS.json）
│   ├── diff/               差异：<日期>.md、latest.md、latest.html、latest.json
│   ├── reports/            每日日报（<日期>.html）
│   ├── stats.csv           累计进榜统计（仓库 × 日/周/月榜次数）
│   └── latest.html         最新日报固定路径，直接打开就能看
└── tests/
    ├── selftest_diff.py    差异算法自测（构造数据，验证四类变化都能识别）
    └── selftest_output.py  交付自检（链接、注解完整性、写作规范、差异注入）
```

## 三条命令

```bash
# 1. 完整跑一次：抓取 + 渲染 + 与上次对比（每日任务用的就是这条）
python github-daily\scripts\run_daily.py

# 2. 补完注解后重渲染，不重新抓取
python github-daily\scripts\run_daily.py --no-fetch

# 3. 只看最近一次的差异，不抓取不渲染
python github-daily\scripts\run_daily.py --only-diff

# 4. 黑名单：加 / 移除 / 查看（加完会自动重渲染，不用再抓一次）
python github-daily\scripts\run_daily.py --block owner/name
python github-daily\scripts\run_daily.py --unblock owner/name
python github-daily\scripts\run_daily.py --list-blocked

# 5. 关注名单：加 / 移除 / 查看
python github-daily\scripts\run_daily.py --watch owner/name
python github-daily\scripts\run_daily.py --unwatch owner/name
python github-daily\scripts\run_daily.py --list-watched
```

## 排序规则

每个板块（今日/本周/本月）里卡片顺序是：

```
新进榜（历史上首次出现） → 重回榜（掉过又回来） → 其余（保持原榜名次）
```

所以当天真正的新项目永远在板块最前面，往下才是常客。组内仍按原榜单名次排，不会打乱相对顺序。

卡片版式：**第一行只有项目名**；第二行是徽标（新进榜/重回榜/已关注）、语言标签、☆ / × 按钮；再往下依次是简介、技术标签、星数与累计上榜次数。

## 黑名单

三种加法：

1. **卡片右上角的圆形 ×**：优先走本地按钮服务（见下节），**一键直接写入**；服务没在运行时自动退回「本地隐藏 + 复制命令」模式。
2. `python github-daily\scripts\run_daily.py --block owner/name`：写进 `data/blacklist.json` 并立即重渲染。
3. 直接告诉我「屏蔽 xxx」，我用上面两条命令之一处理。

被屏蔽的仓库：

- 不出现在任何板块的卡片里（也就不再占版面）
- 只在所属板块**末尾**以折叠列表保留名字（默认收起，和差异区块一样的交互），点开仍可跳 GitHub
- 差异区块里也不再提它（掉榜/排名/星数噪声一并过滤）
- `进榜统计.csv` 仍保留历史计数，只是多一列「已屏蔽」标记为「是」——统计是事实，不该被屏蔽改掉
- `--unblock` 解除后下次渲染即恢复卡片

同一台浏览器反复打开同一份报告时，点过的 × 会通过 localStorage 记住，卡片保持隐藏；跨天则依赖 `blacklist.json`。

## 本地按钮服务（让 × / ☆ 真正一键生效）

静态 HTML 不能直接写磁盘，这是浏览器沙箱的硬限制，所以另起了一个只监听本机的小服务：

```bash
python github-daily\scripts\local_server.py            # 默认 127.0.0.1:8765
python github-daily\scripts\local_server.py --port 8790
```

- 报告里的 × / ☆ 先 `POST /add` 到这个服务（处理了 `file://` 的 `Origin: null` CORS）
- 服务写入 `blacklist.json` / `watchlist.json`，再自动跑一次 `--no-fetch` 重渲染，桌面副本同步更新
- **每次生成日报时自动检查**：服务没在跑就后台拉起（独立进程，不阻塞本次日报），日志里会打印「本地按钮服务：已在运行 / 已自动启动」
- 不想让它自动起：`python github-daily\scripts\run_daily.py --no-server`
- 服务输出写到 `data/local_server.log`；换端口用 `--port` 或环境变量 `GITHUB_DAILY_SERVER_PORT`
- 服务不在（App/电脑重启后尚未重跑日报）时，按钮自动退回「隐藏 + 复制命令」，不会静默失败：× 复制 `屏蔽 owner/name`，☆ 复制 `关注 owner/name`
- 改动只落在 `github-daily/data/` 两个 json 里，服务挂了也不影响第二天 08:00 的定时任务
- 只监听 `127.0.0.1`，只接受 block / watch 两个动作，仓库名做格式校验；不要改成监听 `0.0.0.0`

## 关注名单

和黑名单相反：关注的项目**照常显示卡片**，但每次它重新上榜时，会在该板块末尾（**已屏蔽列表之前**）单独给一块「关注项目动态」，回答一个问题——**这段时间它有没有重大更新**。

三种加法（与黑名单对称）：

1. 卡片上的 **☆**（在 × 左边）：同 ×，优先走本地服务一键写入；服务不在时退回复制命令。
2. `python github-daily\scripts\run_daily.py --watch owner/name`
3. 直接告诉我「关注 xxx」。

### 更新事实从哪来

脚本只对**当天在榜**的关注项目取两类事实：

| 来源 | 指标 |
| --- | --- |
| 本地历史快照（不联网） | 上次上榜日期、距今天数、期间星数变化 |
| GitHub API（失败可降级） | 最近推送时间、自上次上榜以来的提交数、最新 release、是否归档、issue 数 |

API 用公开接口（无 token 时 60 次/小时），支持环境变量 `GITHUB_TOKEN` / `GH_TOKEN` 提额；同一份数据当天重渲染直接读 `watch_facts.json` 缓存，不重复调用。取不到时区块里标「更新详情获取失败」，仍显示本地事实。

### 建议说明

事实由脚本给，**判断由 Agent 写**：`watchlist.json` 里每个仓库有一个 `note` 字段，每天依据上面的事实写 1–2 句「有没有重大更新、值不值得看」，例如：

```json
{"repos": {"anthropics/skills": {
  "since": "2026-11-16",
  "note": "3 天里只有 1 次提交、没发新版本，星数涨 1,680 更像是榜单回流；除非你在等它的技能模板，否则不用专门看。"
}}}
```

未填写时区块显示「建议待补」并打印待补清单，补完跑 `--no-fetch` 即可刷新。

卡片上带蓝色「已关注」徽标；板块内顺序是：**卡片 → 关注动态 → 已屏蔽列表**。

## 名单同步（跨设备）

黑名单和关注名单存在 `github-daily/data/*.json` 里，是**这台机器**的事实来源；换台设备打开同一份日报时，名单不一定一致。为此日报末尾有一个「名单同步 · 跨设备」区块：

- 生成时把当前名单**嵌入到 HTML** 里（`window.__GD_LISTS__`），文件本身自带一份基线
- 点「一键复制同步」把 **嵌入名单 + 本机临时点过的改动** 合成一段文本复制到剪贴板：

```text
github-daily 名单同步（2026-11-16）
屏蔽：nodejs/node、sgl-project/sglang
关注：anthropics/skills、tradermonty/claude-trading-skills
```

- 把这段贴到对话里，我会逐条执行 `--block` / `--watch`，把这台机器的名单对齐到清单
- 服务不可用时，× / ☆ 的复制模式仍保留：× 复制 `屏蔽 owner/name`、☆ 复制 `关注 owner/name`

同步是**手动的一次性动作**（静态文件不可能自动联网同步）；第一行带日期，便于确认是哪天拍的快照。

## 差异是怎么算的

对比对象是 `data/last_raw.json`（上一次运行），不是「昨天」——所以同一天跑两次也能看出盘中变化。

按 今日 / 本周 / 本月 三张榜分别输出四类变化：

| 类型 | 判定 |
| --- | --- |
| 首次进榜 | 本次有、上次没有，且**历史上从未上过任何一张榜** |
| 重回榜 | 本次有、上次没有，但以前上过（掉过又回来） |
| 掉榜 | 上次有、本次没有 |
| 排名变化 | 两期都在榜，名次差值 ≠ 0（↑ 上升 / ↓ 下降） |
| 星数变化 | 两期都在榜，总星数差值 ≠ 0 |

另存「待补注解」清单。差异会写进两处：`data/diff/<日期>.md`（纯文本，适合贴消息）和日报 HTML 顶部的「与上次的差异」区块。

**差异区块是可折叠的**：今日/本周/本月各占一行摘要（仓库数 + 首次/重回/掉榜/排名变动计数），点一下展开缩进的明细列表。用原生 `<details>` 实现，不依赖 JS，离线可用；默认全部收起，避免一进页面就是一长条。

## 累计进榜统计

`data/stats.csv`（对外同步到输出目录的 `进榜统计.csv`）记录每个仓库累计进过多少次榜：

| 列 | 含义 |
| --- | --- |
| 仓库 | `owner/name` |
| 日榜次数 / 周榜次数 / 月榜次数 | 分别在三张榜里出现过多少**天** |
| 合计 | 三项之和 |
| 首见日期 / 最近日期 | 第一次、最后一次上榜的日期 |
| 语言 / 链接 | 最近一次抓到的语言和仓库地址 |
| 已屏蔽 | 该仓库是否在黑名单里（仅标记，历史计数照常保留） |

口径说明：

- **按天去重**：同一天跑多次（比如补注解后重渲染）只算一天；统计源是 `data/snapshots/*.json` 与 `data/last_raw.json` 的全部历史。
- **含本次**：当天这次抓取已经计入，所以「日榜次数」至少为 1 的仓库就是今天在榜的。
- 编码 `utf-8-sig`，Excel 双击打开不乱码；按「合计」降序排列。

每张卡片会显示 `上榜 日N · 周N · 月N`；本次进榜的卡片另加标记——**历史上从未出现过**才标绿色「新进榜」，以前上过、这次回来的标琥珀色「重回榜」。所以「掉榜几天后又回来」不会被误报成新项目。

## 日报输出位置

每次运行会把成品 HTML 与统计表复制一份到输出目录（默认 `~/Desktop/日报/github`，可用 `--outdir` 或环境变量 `GITHUB_DAILY_OUTDIR` 覆盖）：

```
<输出目录>/<日期>.html         # 例：2026-11-16.html
<输出目录>/进榜统计.csv         # 累计统计表（每次覆盖为最新）
```

- 文件名就是抓取当天日期（`YYYY-MM-DD`），按名排序即时间顺序
- 工作区里同时保留 `data/reports/<日期>.html`、`data/latest.html` 和 `data/stats.csv`
- 写入该目录失败时不会中断任务，只降级为「仅工作区副本」并打印警告
- 想换目录：改 `scripts/run_daily.py` 顶部 `DEFAULT_OUTDIR`，或用环境变量 `GITHUB_DAILY_OUTDIR`，或加 `--outdir <路径>`

## 定时任务

- 位置：会话级定时提醒（DSH 的 schedule 机制）
- 规则：`0 8 * * 1-5`，`Asia/Shanghai`，即**工作日 08:00**
- 行为：到点后自动执行上面的第 1 条命令；若有待补注解，按 SKILL.md 的规矩补写后跑第 2 条；然后把差异、日报路径、注解处理情况回复到对话里

查看 / 改时间 / 暂停：

```text
schedule_list                                  # 看当前提醒
schedule_update(id=..., cron={expression, time_zone})   # 改时间
schedule_delete(id=...)                        # 取消
```

注意：这是宿主侧的定时器，DSH 没运行时会漏掉当次，下次启动补跑最近一次（不会连补多次）。如果希望完全脱离 GUI 由 Windows 计划任务驱动，可以把 `run_daily.py` 包一层 `schtasks`，但注解那一步需要 Agent 参与，纯脚本只能复用已有注解、新仓库会落在「待补」。

## 已知取舍

- **注解缓存只增不减**：历史上写过的仓库永远命中，不会重复花钱写。仓库改名/换了定位时需要手动改 `data/annotations.json`。
- **新进榜仓库当天会先显示「待补注解」**：定时任务会现场补齐，然后再重建一次；手工跑时需要自己补。
- **抓取失败不动基线**：网络失败直接报错退出，`last_raw.json` 和上次日报保持原样，不会用空数据污染对比基线。
- **url 修正在本层做**：skill 的 `fetch_trending.py` 仍会产出 `https://github.com/trending/x/y` 这类错误链接，`run_daily.py` 抓到后统一改写成 `https://github.com/x/y`，不改 skill 源文件。

## 测试

```bash
python github-daily\tests\selftest_diff.py      # 差异算法（8 项）
python github-daily\tests\selftest_output.py    # 交付自检（35 项，含排序/黑名单/关注名单/同步区块断言）
```
