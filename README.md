# 热币交易自动发布：实施记录

更新：2026-10-19，北京时间。当前仅完成审核队列核心和离线测试，未接入真实账号、未启动定时服务、未发推。

## 最新约束（优先于后面的早期测算）

- 用户要求今天直接部署云端，不使用电脑长期运行。
- 中文20:00、英文21:00，仅周一至周五；排除中国公布的放假日期及美国联邦节假日（含observed补休日，此美国口径为已说明的暂定理解）。周末即使中国调休上班也不发。
- `calendar.mjs` 已实现2026日历，4项测试通过；未知年份暂停发布，需补充已核验的日历。
- 必须带海报，必须展示日志/网站链接，不能用删除链接降低预算。
- 日志目前在Notion公开，未来迁至网站；保留原始计划和追加更正记录，历史交易参数不被新判断覆盖。
- 降本首选：用户手动问已有Grok会员，将结果发送给Telegram Bot；程序不调用付费X Search。收到热点不等于批准发布。
- Telegram接收须支持长文本分段或文件、明确提交完成、记录截止时间与接收时间、校验审核人、拒绝把过期热点用于当天稿件。该接收适配器待实现。
- 现有Claude订阅可用于人工翻译后回传；自动程序使用官方API，不能假定网页订阅无限抵扣API。当前官方说明Max/Team可能有月度API额度，需查看本人Console。
- 翻译API费用远小于检索：Haiku 4.5按$1/百万输入、$5/百万输出，每次3000输入+1500输出约$0.0105；22次约$0.23。给重写与海报文字预留后，单翻译预算$0.2—1/月。

### 当前预算：20个发布日、40条均带URL的推文

| 项目 | 月预算（美元） |
|---|---:|
| 手动Grok热点 | 0新增API费，不含已有会员费 |
| 自动撰稿 | 1—5，假设先压缩数据、每个发布日约2万输入+3000输出，选用约$3/$15每百万token级别模型并预留修改 |
| 自动翻译 | 0.2—1 |
| X带URL发帖 | 40 × 0.20 = 8 |
| 图片处理、存储和相关请求预留 | 0—2 |
| Cloudflare | 0—5，低流量假设；其他资源超额另计 |
| GitHub Actions | 额度内0，须核对已有任务用量 |
| 合计 | 约10—21，初期按25美元/月预留 |

免费翻译API可选择有免费层的Gemini型号，但以账号地区/配额为准；部分免费层内容可用于产品改进。免费额度耗尽时提醒，不自动切换到未经配置的付费服务。

API费用来源补充：
- https://platform.claude.com/docs/en/about-claude/pricing
- https://support.claude.com/en/articles/9876003-i-have-a-paid-claude-subscription-pro-max-team-or-enterprise-plans-why-is-claude-api-usage-billed-separately-from-my-paid-claude-plan
- https://ai.google.dev/gemini-api/docs/pricing

部署状态：Wrangler 4.151.2已下载成功，尚未登录Cloudflare。一次后续进程排查的越权命令被自动审批系统因用量限制阻断；不是安全否决。未执行该命令，未绕过审批。

## 已确认输入

- 网站仓库：https://github.com/ivonasmith/uncleonchain.git
- 已找到现有脚本：`/Users/hi/Documents/ChatGPT/热币交易/热币交易脚本/每日取数.py`
- 已找到海报模板：`/Users/hi/Documents/ChatGPT/热币交易/第35期/poster_gen.py`
- `L1L4日更.py` 已读取网站公开的四层数据，不需要重新建设四层计算。
- 网站已有 GitHub Actions 数据任务，Cloudflare 承载网站。
- 原始热点提示词保存在 `grok_prompt.txt`，未改写用户要求。
- 用户希望单条约200字，已有X会员。API长帖能力仍需账号级验证，不擅自压缩正文。

## 已实现

`review_queue.py` 是不依赖部署环境的 SQLite 审核与发送状态核心：

- 只允许指定审核人、文字先审、图片后审。
- 正文/图片/快照变更产生新版本，旧审核按钮失效。
- 中文北京时间20点、英文21点；各自独立状态。
- 发送前必须有时效内的复核通过记录。实际事件与来源核验适配器尚未实现。
- 原子领取防止两个进程重复发送；发送结果不明时禁止自动重发。
- 超期不补发；持久化审计日志。

验证命令：`python3 -m unittest discover -s tests -v`

## 待接通

1. X开发者应用及用户授权，实际测试会员账号长帖+图片发布能力。
2. Telegram Bot及审核人ID，按钮回调和稿件/图片推送。
3. xAI API检索、用量记录和预算限制；模型撰稿。
4. 完整交易规则/写稿规则/当前台账，不能以第35期历史数据替代今日事实。
5. 现有脚本独立工作目录运行、输出数据新鲜度验证。
6. 海报模板改为接收结构化输入，截图路径跨环境配置。
7. 事件日历、发布前复核和异常提醒。
8. 按用户选定环境配置长期调度，端到端验收后启用。

## 费用预算（美元/月，30天，60条帖子）

这是预算模型，不是实际账单或供应商保证。价格核查于2026-10-19。

| 项目 | 假设 | 预算 |
|---|---|---:|
| X Search | 每日100—200条计费抓取记录，$0.005/条；重复抓取也计费 | $15—30 |
| 模型文字 | 热点总结和双语撰稿复用xAI；每日合计3—6万输入token、0.8—2万输出token；按$2/$6每百万token | $3.24—7.20 |
| X创建帖子 | 每月60次、无URL，$0.015/次 | $0.90 |
| 其他预留 | 图片上传相关请求、少量复核、重写 | $1—6 |
| 本地运行 | 不含已有电脑折旧、电费和网络 | $0租金 |
| 合计 | 现有免费数据源、不新增付费数据套餐 | 约$20—45 |

若60条都带URL，当前发布费按$0.20/条计，为$12/月，较上表增加$11.10。媒体上传不等于正文附带URL，最终按控制台账单核对。已有X/Grok会员费、税费不包含在上述新增运行费中。若单独使用Claude撰稿，另按所选模型报价。

扩大样本的量级：每天500条=$75/月搜索费；每天1000条=$150/月搜索费，尚未含模型与发帖。低成本方案是覆盖广泛话题的样本观察，不能称为全量X普查。

云端推荐复用GitHub Actions执行Python/浏览器截图，Cloudflare负责审核与发布。新任务每天合计20—40分钟时，约600—1200分钟/月；私有仓库是否免费取决于账号剩余额度。不要为了免费额度公开草稿或凭据。Workers基础预算$0—5/月；含存储等预留总计$0—10，额度超限另计。Actions定时可能延迟，因此提前生成材料，发帖由Cloudflare调度；同样不承诺秒级准点。

## 来源

- https://docs.x.ai/developers/tools/x-search
- https://docs.x.ai/developers/models
- https://docs.x.com/x-api/getting-started/pricing
- https://docs.x.com/fundamentals/counting-characters
- https://developers.cloudflare.com/workers/platform/pricing/
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- https://learn.chatgpt.com/docs/automations?surface=app
