# Feishu Relay v1：Win Hermes ↔ iPhone Minis

## Why

iOS Minis/iSH 的 iCloud FUSE view 在 2026-09-12 真机测试中会冻结：Files App 能看到 Mac 写入，iSH 的 `readdir/stat/open` 却持续返回旧目录或 `ENOENT`。因此，飞书 API 是默认传输层；iCloud 文件队列仅保留为 legacy 实验路径。

## Architecture

```text
Win Hermes
  │  feishu_relay.py --send
  ▼
Feishu chat（[MINIS_REQ] 协议消息）
  │  feishu_relay.py --pull
  ▼
iPhone Minis session 处理 body
  │  feishu_relay.py --reply
  ▼
Feishu chat（[MINIS_REPLY] 协议消息）
  │  feishu_relay.py --read
  ▼
Win Hermes
```

现有 Mac `feishu-hermes-bridge` 只消费 `sender_type=user`；relay 请求由 bot 发出，`sender_type=app`，并且带 `[MINIS_REQ]` 前缀，因此不会被贾维斯桥抢单。

## One-time setup on Minis

1. 安装/更新完整仓库到：

   `/var/minis/skills/minis-bridge/`

2. 在 Minis Settings → Environment Variables 添加（值从本地安全配置复制，不要发进聊天、邮件或 GitHub）：

   ```text
   FEISHU_APP_ID
   FEISHU_APP_SECRET
   FEISHU_MINIS_CHAT_ID
   ```

   第一版允许 `FEISHU_MINIS_CHAT_ID` 暂时复用现有 `FEISHU_CHAT_ID`；长期建议建 Minis 专用 chat。

3. 验证脚本：

   ```sh
   python3 /var/minis/skills/minis-bridge/scripts/feishu_relay.py --help
   python3 /var/minis/skills/minis-bridge/scripts/feishu_relay.py --pull
   ```

   没任务时输出 `null`，退出码为 3；有任务时输出一条 JSON：

   ```json
   {"id":"req-...","body":"任务内容"}
   ```

## Process command inside a Minis session

用户说 **`process feishu bridge`** 时：

1. 执行：

   ```sh
   python3 /var/minis/skills/minis-bridge/scripts/feishu_relay.py --pull
   ```

2. 若输出 `null`，报告没有待处理任务，不要编造回复。
3. 若输出 JSON，把 `body` 当作用户请求，正常使用 Minis 的 skills/tools 处理。
4. 得到最终答案后执行（答案较长时先写本地临时文件，再安全读入；不要把 shell 特殊字符裸拼进命令）：

   ```sh
   python3 /var/minis/skills/minis-bridge/scripts/feishu_relay.py \
     --reply '<request-id>' \
     --text '<final-answer>'
   ```

5. 再跑一次 `--pull`；直到输出 `null`，或本轮最多处理 5 条，防止无限循环。

## Win usage

```bash
# 发任务
python scripts/feishu_relay.py --send "请总结今天的 Apple 新闻"
# stdout 会返回 request id

# 读回复
python scripts/feishu_relay.py --read <request-id>
```

配置从环境变量、`~/.feishu.env` 或 `~/.minis-feishu.env` 读取：

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_MINIS_CHAT_ID  # 优先
FEISHU_CHAT_ID        # fallback
CHAT_ID               # fallback
```

## Protocol

Request：

```text
[MINIS_REQ]
{"id":"req-123"}

用户任务
```

Reply：

```text
[MINIS_REPLY]
{"id":"req-123"}

Minis 最终答案
```

- 只消费 `sender_type=app` 且前缀精确匹配的消息。
- 同 request id 已出现 reply 时，不再作为 pending 返回。
- `--pull` 返回最早一条未回复请求。
- `--read` 只返回匹配 request id 的最新回复。

## Verified evidence

### Complete Win ↔ iPhone Minis E2E — passed

```text
request id: iphone-minis-smoke-1789192695
Win request:  2026-09-12T05:58:19.780Z
first reply:  2026-09-12T14:30:12.373Z
latency:      30,712.593 s (includes waiting for manual setup)
result:       MINIS_FEISHU_E2E_OK
```

The active iPhone Minis session pulled the original request, processed it,
sent a reply with the same request ID, and Windows read a non-empty matching
reply. This meets the full E2E acceptance criteria.

The first device run accidentally sent two replies 15.342 seconds apart.
v1.2.1 prevents recurrence by checking for an existing reply before sending.
The guard passed seven unit tests and a real probe that returned
`already replied` without creating another message.

### Earlier Windows transport-only loop

```text
send → pull → reply → read
request id: minis-e2e-1789191679
read result: Feishu relay transport loop OK.
```

另外，能力探测确认：

- tenant token：成功；
- bot 发消息：`code=0`；
- 同一 bot 可从 chat history 回读自己的消息；
- 回读消息 `sender_type=app`。

上面的早期回环只证明传输层；`iphone-minis-smoke-1789192695` 已进一步完成 iPhone Minis agent hop。

## Security

- `FEISHU_APP_SECRET` 只放 Minis 本地 Environment Variables；不得写入仓库、Skill、邮件或飞书消息。
- 当前应用权限会随 secret 带到 iPhone。若需要收紧权限，后续创建一个 Minis 专用飞书应用，只授予读写目标 chat 所需权限。
- 第一版可复用现有 chat，但必须使用 `[MINIS_REQ]` / `[MINIS_REPLY]` 前缀隔离。
