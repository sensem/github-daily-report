# github-daily-report

把 GitHub Trending（日 / 周 / 月榜）抓下来，渲染成一份说人话的中文 HTML 日报。

## 目录

- `github-trending-daily/` —— 上游 skill，**原样保留**
- `github-daily/` —— 本地适配层（每日任务、差异对比、累计统计、黑名单/关注名单、本地按钮服务），在后续提交中加入

## 来源

`github-trending-daily/` 来自 [zhuweijun1003-source/zhuwj-skills](https://github.com/zhuweijun1003-source/zhuwj-skills)
的 `github-trending-daily` 目录，4 个文件按原字节保留（blob SHA 与上游一致）。

上游仓库未附 LICENSE，此处仅作学习与研究引用；如需再分发，请先确认原作者授权。

## 隐私

仓库不含运行期数据与凭证：`data/`（抓取快照、注解、黑白名单）、token 文件、日志均已 gitignore。
