---
name: skill-wechat-chat-exporter-member
slug: skill-wechat-chat-exporter-member
displayName: 微信聊天记录导出助手 (社群会员版)
version: 1.0.0
summary: 面向社群会员的微信 4.x 聊天记录导出助手，基于 ServiceHub 远程密码机鉴权，提取指定私聊或群聊并按自然月导出为标准化 Markdown 归档
license: Proprietary
description: 当付费社群会员需要从正在运行的微信 4.x 中导出、归档私聊或群聊聊天记录为标准 Markdown 文档时使用。本技能通过 ServiceHub 远程密码机鉴权，保证仅有效社群会员可用；具备按群名/联系人自动解析、纯文本过滤、按自然月分块与规范化三行式排版。
disable-model-invocation: true
user-invocable: true
argument-hint: [target-chat-or-name]
---

# skill-wechat-chat-exporter-member: 微信聊天记录导出助手 (社群会员版)

## Goal
在微信已登录状态下，通过 ServiceHub 远程密码机安全鉴权，将社群会员指定的群聊或私聊会话中的纯文本聊天记录，按自然月精准拆分并清洗为标准化的 Markdown 归档文档。

## Required Inputs
1. **target**：目标群聊名称、好友昵称/备注或微信 ID（例如：`社群答疑交流群`、`张三`、`46112508908@chatroom`）。
2. **output-dir**（可选）：导出的 Markdown 文件保存目录。若不指定，默认输出至当前目录下的 `output/<目标名称>/`。
3. **user / token**（可选）：ServiceHub 会员用户名与授权码（若已通过 `--login` 保存凭据则无需重复指定）。
4. **self-name**（可选）：账号本人在记录中的统一显示名称，默认为 `CC`。
5. **since / until**（可选）：时间过滤区间（格式：`YYYY-MM-DD`）。

## Workflow
1. **会员身份探测与配置校验**：
   - 自动检测本地 `~/.servicehub/config.json` 或环境变量；
   - 若未配置，引导会员通过 `python scripts/export_chat.py --login` 完成一次性绑定。
2. **微信进程与本地存储探测**：
   - 自动探测运行中的微信 4.x 进程及当前账号本地 `db_storage` 存储根目录。
3. **远程密码机解算（安全防盗版核心）**：
   - 客户端仅在内存中扫描捕获未经解密的特征块（Blobs）；
   - 通过 HTTPS 请求发送至 ServiceHub 远程密码机接口 `/api/auth/resolve-wechat-key`；
   - ServiceHub 在服务端内存中完成解算，下发该会员设备当前专用的数据库 Raw Key；
   - 客户端执行本地第一页 HMAC-SHA512 双重校验，确保 100% 真实有效。
4. **目标会话定位**：
   - 打开 `contact.db`，依据输入的关键词模糊/精确匹配对应的目标 `username`（单聊或 `@chatroom` 群聊）。
5. **消息提取与流式解密**：
   - 跨所有 `message_x.db` 分库，检索表名 `Msg_<MD5(username)>`，仅拉取 `local_type = 1` 的纯文本行。
6. **内容清洗与格式化**：
   - 自动检测并解压 zstandard 压缩帧；
   - 剔除群聊报头前缀 `wxid_xxxx:\n`；
   - 关联 `Name2Id` 与通讯录，按“备注名 > 微信昵称 > 微信号”三级回退确定发言人；本人统一标注为 `CC`。
7. **按月切片与文档输出**：
   - 按自然月分块，自动跳过无纯文本发言的月份；
   - 提取首末条消息时间戳生成文件名（方案 A 规范）；
   - 按三行式排版写入 Markdown，并自动在目标目录生成/更新 `README.md` 归档规范。

## Decision Rules
1. **会员专属原则**：非 ServiceHub 会员或会员已过期，坚决拒绝提供解密服务。
2. **纯文本独占规则**：只提取 `local_type = 1`。所有图片（3）、语音视频（43）、文件表格（6）、表情包（47）、系统提示（10000）一律丢弃，不生成空占位符。
3. **空月跳过规则**：若某月纯文本消息数为 0，坚决不创建空文档。
4. **命名格式铁律**：文件名必须严格遵循 `<目标名称>_<该月首条YYYYMMDDHHMM>-<该月末条YYYYMMDDHHMM>.md`。
5. **三行式排版铁律**：
   ```markdown
   发言人
   YYYY年MM月DD日  H:MM
   消息正文内容

   ```
   两条消息之间必须严格保留一个空行。

## Output Requirements
1. **归档文档**：在目标目录下输出一个或多个按月命名的 `.md` 文件。
2. **索引文件**：在目标目录根下输出包含会话元信息的 `README.md`。
3. **控制台总结**：输出包含月份、纯文本消息条数、文件大小及文件名的汇总清单。

## Execution Modes
- **模式 A（用户桌面端推荐）**：无需 Python 环境与命令行，直接双击运行根目录的 `微信聊天记录导出助手.exe`（或 macOS 运行 `微信聊天记录导出助手-macOS.zip`）。
- **模式 B（AI 智能体 / CLI 驱动）**：由 AI 助手在终端调用 `python scripts/export_chat.py` 执行，需预先安装 `requirements.txt`。

## Examples

### 示例 1：社群会员首次登录绑定
```powershell
python scripts/export_chat.py --login
```

### 示例 2：查看活跃会话
```powershell
python scripts/export_chat.py --list --limit 10
```

### 示例 3：导出指定社群聊天记录
```powershell
python scripts/export_chat.py -t "CC付费答疑群" -o "./output/CC付费答疑群"
```

### 示例 4：导出指定联系人私聊记录（限定时间）
```powershell
python scripts/export_chat.py -t "张三" -o "./output/张三" --since "2026-06-01"
```
