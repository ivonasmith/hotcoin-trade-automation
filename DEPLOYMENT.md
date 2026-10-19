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
