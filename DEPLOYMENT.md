# 云端部署记录

更新时间：2026-10-19，北京时间。

## 已部署

- Worker：`uncleonchain-trade-bot`
- 服务：https://uncleonchain-trade-bot.ivona-smith.workers.dev
- 管理页：https://uncleonchain-trade-bot.ivona-smith.workers.dev/admin
- X授权回调：https://uncleonchain-trade-bot.ivona-smith.workers.dev/oauth/x/callback
- Telegram回调：https://uncleonchain-trade-bot.ivona-smith.workers.dev/telegram/webhook
- 当前版本：`68d498c5-c666-4bfe-9bfa-b69842d427a0`
- 运行模式：`live`（正式环境）；发布总开关仍为 `false`，待取数、海报、审批和 X 发送端到端验证完成后开启。
- 存储：Cloudflare Durable Object（SQLite-backed），一个私有TradeDesk实例。
- 未配置Cron、未启用自动发布、代码中尚无发推调用。

部署使用新Worker与独立存储，未修改uncleonchain.com网站。

## 已验证

- 已完成本地审核队列测试9项，JS日历/加密/云端路由测试14项。
- 线上 `/health` 返回200，`publishing=false`。
- 未授权 `/admin` 返回401，正确管理员凭据返回200。
- 无效OAuth回调返回400。
- X OAuth使用PKCE、state、同浏览器Cookie和一次性授权状态。
- 绑定预期X账号为 `Uncle_Onchain`，令牌加密保存。
- 修复部分隐私/嵌入式浏览器的null或缺失Origin表单提交：加入管理员页面签发的隐藏HMAC表单令牌；显式异站来源仍拒绝；原始403 JSON改为可操作的中文提示。增加回归测试并部署。

## 凭据输入

用户名为 `admin`；随机管理员密码保存在本地 `private/管理员登录.txt`，不在此文档记录。

管理员通过HTTPS设置页填写：

1. X Client ID。
2. X Client Secret。
3. Telegram Bot Token。
4. 机器人 `/start` 返回的本人审核人ID。

这些字段使用应用层AES-GCM加密后写入私有Durable Object，不回显到管理页。主加密密钥、管理员密码和Webhook密钥放在Cloudflare Secrets中。

X应用凭据保存之后，点击“授权 @Uncle_Onchain”完成账号授权；Telegram Token保存之后，点击“接通Telegram机器人”注册Webhook。

Telegram文字热点流程：`/hotspots` → 粘贴一段或多段文字 → `/done`。保存素材不会触发发布。仅允许配置的审核人ID提交素材。

## 还未接入

- X账号真实授权、Telegram真实接通及审核人设置已完成，后台状态已核实。
- 当前交易台账和完整写稿/选币/风控规则。
- 云端Python取数的7个脚本副本和独立运行器已准备；GitHub Actions尚未部署，未完成云端网络验证。
- Gemini免费API已选定，管理页密钥输入与连接检查已实现；等待用户保存密钥。完整撰稿仍需用户规则。
- 图片上传、双语稿件送审和真实发布适配器。
- 发布前事件复核、日历调度及发布成功后的日志更新。

原来的 `review_queue.py` 是本地验证过的状态核心，尚未与云端Worker连接。不得宣称完整发推流程已上线。

## 已确认的发布约定

- 单条约200字、X会员账号。长帖API能力仍需实际账号验证，不能擅自压缩或拆thread。
- 发布日：周一至周五，排除中国放假日、暂按美国联邦节假日（含补休日）。2026已核验，后续年份需更新。
- 中文北京时间20:00、英文21:00；事件导致内容失效时暂停并重新审核。
- 主帖带图片，不放URL；机器人给出固定链接回复文案，由用户手动评论。
- 交易记录使用文字/表格，不长期保存海报；当前公开日志在Notion，未来可迁网站。
- 当前Notion日志：https://bottlenose-muse-07e.notion.site/HotCoinDesk-Trade-Record-3cfb72fc8cae808d9deecfdc21f4cba5
- 热点由用户手动问Grok后提交Telegram，不新增检索API费用。

## 部署授权清理

用户明确同意暂时保留Wrangler广泛OAuth权限用于本次部署。完成系统部署和验证后：

1. 执行Wrangler logout移除本机登录，并核对它是否撤销服务端授权。
2. 在Cloudflare端撤销对应Wrangler授权；不可仅把删除本机文件称为已撤销。
3. 运行时不依赖Wrangler凭据。后续更新另行临时授权。

目前仅完成接入后台阶段，尚未宣称本次系统部署全部完成，也尚未执行撤销。

## 项目目录约定

用户确认项目名称始终为“热币交易”。新后台代码放在现有“热币交易/自动发布”子目录，原有“热币交易脚本”和“第35期”保持原样。后续不再创建“每日交易”项目目录。

## Gemini与云端取数准备

- Gemini密钥输入和免费模型列表连接检查已上线；默认检查gemini-3.5-flash-lite，未接入付费搜索或自动切换其他模型。
- 目录已迁移至 `/Users/hi/Documents/ChatGPT/热币交易/自动发布`，原脚本、海报和账号授权保留。
- `pipeline/run_data.py`在干净目录运行7个脚本副本，校验主流币价格及行情源时间戳，失败不复用旧输出。新鲜度相关4项测试通过。
- `.github/workflows/collect.yml`已准备，仅允许手动验证、没有启用定时；尚未上传GitHub或完成云端网络测试。
- 完整写稿规则、海报规范、当前交易台账尚未提供，不能据旧海报自动生成新计划。

## 文字稿通过之后的全自动流程（2026-10-21 接入，发布开关仍为 false）

1. 点“文字通过”→ Worker 以已审核中文稿为唯一来源：中文正式稿 = 原文 + Notion 交易记录链接（不经模型）；英文正式稿由模型翻译，程序逐个核对数字（日期、亿/万、百分比、比例都归一化后比较），对不上最多重试3次，仍不一致就不出图并报原因。
2. Worker 排“海报任务”。GitHub Actions 工作流 `jobs.yml` 每5分钟（北京时间18:00–22:59，工作日）问 Worker 有没有任务（用已有的 `PIPELINE_SECRET`，不需要新密钥），有任务才安装 Chromium，用仓库里的 `poster/poster_gen.py`（原样，不改）出4张PNG并自检（英文无中文、订单价位与正文一致），上传给 Worker，Worker 直接发到 Telegram（文档形式，保留原分辨率）。
3. Telegram 收到：中文正式稿、英文正式稿、4张图、按钮“图片与正式稿通过 / 退回修改”。
4. 终审通过 → 排“台账任务”：同一个工作流用 `NOTION_TOKEN` 写 Notion，只追加不改原记录（新单进“当前在场”，条件进“条件单登记簿”，成交/作废/过期/止损了结记入自动创建的“六、状态变更记录”，汇总数字和“更新至”同步，旧“更新至”文字保留在“上期记录”后面）；每条都有 `desk:` 标记，重复运行不会重复写。
5. 中文北京时间20:00、英文21:00由 Durable Object 闹钟触发；目标时间后60分钟内仍自动执行，超过60分钟才停止自动补发并回 Telegram 问“现在补发 / 这条不发了”。`PUBLISH_ENABLED` 不是 `true` 时到点只发“演练”消息，不调用 X。
6. 防重复：每个按钮、任务、闹钟槽位都是 compare-and-set；Telegram 重发同一个回调只处理一次；发推采用“先占位再动作”，请求结果不确定时标记 unknown，绝不自动重发。

可选：在后台填“GitHub令牌”（Actions读写）可让海报任务立刻启动，不用等5分钟轮询。仓库 Secret 里放 `CLOUDFLARE_API_TOKEN` 后，`deploy.yml` 会在推送 main 且测试通过后自动部署 Worker；没有这个 Secret 时它只会跳过，部署仍用 `npx wrangler deploy`。

海报的涨腿起点/山寨窗口起点在 `poster/poster_state.json`（规则状态，换新一轮时才改）；天数、峰值、回撤由 `pipeline/poster_facts.py` 用日线收盘价每天算。已知限制：模板里“最近了结”手机版固定写“做多”，目前6张均为多单；X 的图片/发推调用路径只有模拟测试，没有真实调用过。
