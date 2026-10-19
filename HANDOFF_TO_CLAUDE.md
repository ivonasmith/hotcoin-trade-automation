# 交给 Claude 的 GitHub 发布任务

请把本压缩包的内容发布到一个新的 **GitHub 私有仓库**，仓库名使用 `hotcoin-trade-automation`。

要求：

1. 压缩包内容直接作为仓库根目录，不要再套一层文件夹。
2. 仓库必须设为 Private。
3. 不要添加、猜测或索取任何 API 密钥；不得把密钥写入文件、commit、issue、Actions 日志或聊天。
4. 确认 `.gitignore` 生效，并确认不存在 `private/`、`.env`、`.wrangler/`、数据库文件、运行缓存和 Python 缓存。
5. 保留现有 Python、JavaScript、规则、测试和 GitHub Actions 文件，不要改写交易逻辑。
6. 先提交为一个初始 commit，建议信息：`Initial private cloud automation`。
7. 手动运行 GitHub Actions 工作流“热币交易 · 云端取数验证”。先不要添加自动发布密钥，也不要开启 X 发帖。
8. 把以下结果回复给用户：私有仓库 URL、Actions 运行 URL、取数任务成功或失败、失败步骤和完整错误摘要。不得回显任何 secret。

当前安全状态：Cloudflare Worker 已是 `live` 正式环境，但 `PUBLISH_ENABLED=false`。保持发布总开关关闭，直到取数、稿件、海报、双重审批和 X 发送器完成端到端验证。

如果你的 GitHub连接只有读取权限，直接告诉用户“当前连接不能创建/写入仓库”，不要尝试转存到公共仓库。
