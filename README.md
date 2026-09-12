# minis-bridge

Win Hermes Desktop 与 iPhone Open Minis 的双向任务桥。

## v1.2 默认架构：Feishu Relay

```text
Win Hermes --send → Feishu [MINIS_REQ] → iPhone Minis --pull
Minis tools/skills → Feishu [MINIS_REPLY] → Win Hermes --read
```

默认传输脚本：`scripts/feishu_relay.py`

- 不经过 iCloud Drive；
- 不依赖 iSH FUSE；
- 不需要公网 callback、MCP 或 iOS Shortcut；
- 用 `[MINIS_REQ]` / `[MINIS_REPLY]` 和 request ID 做路由；
- 与现有 `feishu-hermes-bridge` 隔离：后者只消费 `sender_type=user`，relay 消息来自 `sender_type=app`。

完整安装、Minis 处理命令和安全边界：[`references/feishu-relay.md`](references/feishu-relay.md)

## Quick start

### Win 发送

```bash
python scripts/feishu_relay.py --send "请总结今天的 Apple 新闻"
# 输出 req-... ID
```

### Minis 处理

在 Minis session 里说：

```text
process feishu bridge
```

Skill 会指导 Minis：

1. `python3 .../feishu_relay.py --pull`
2. 把 JSON `body` 当作用户任务处理
3. `python3 .../feishu_relay.py --reply <id> --text <answer>`

### Win 读回复

```bash
python scripts/feishu_relay.py --read <request-id>
```

## One-time configuration

Win 与 Minis 本地环境需要：

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_MINIS_CHAT_ID
```

也兼容 `FEISHU_CHAT_ID` / `CHAT_ID` 作为 chat fallback。密钥只放本地环境或 `~/.feishu.env` / `~/.minis-feishu.env`，不得提交 GitHub。

## Verification status

### Complete real-device E2E — passed 2026-09-12

```text
request id: iphone-minis-smoke-1789192695
Win request created: 2026-09-12T05:58:19.780Z
first iPhone reply: 2026-09-12T14:30:12.373Z
first reply latency: 30,712.593 s (8 h 31 m 52.593 s; includes waiting for manual setup)
result: MINIS_FEISHU_E2E_OK
```

Verified path:

```text
Win --send → Feishu → iPhone Minis --pull
→ active Minis session processed the body → iPhone --reply
→ Win --read same request ID → non-empty matching reply
```

Manual operations in this first setup run:

1. One-time environment-variable configuration on iPhone.
2. One Minis-session instruction to install/process the bridge.

The first run produced two replies for the same ID, 15.342 seconds apart.
v1.2.1 adds reply idempotency: `--reply` checks history and returns
`<id> already replied` without sending if a reply already exists. This guard
passed seven unit tests and a real Feishu probe.

### v1.2.1 deployed on the iPhone — confirmed

```text
request id: iphone-minis-v121-1789226073
request: 2026-09-12T15:14:44.392Z
reply:   2026-09-12T15:28:00.358Z
elapsed: 795.966 s (13 m 15.966 s; includes waiting for the manual trigger)
result:  MINIS_V121_UPDATED
messages observed by Feishu API: 1 request, 1 reply
```

The Minis device reported commit `3591db1`, SKILL version `1.2.1`, the
idempotency branch present, and `--help` passing. The independent Feishu
history check found exactly one reply, so the duplicate-reply fix is active
on the iPhone.

### Earlier transport-only loop

```text
send → pull → reply → read
request id: minis-e2e-1789191679
result: Feishu relay transport loop OK.
```

This earlier test proved only API transport; the smoke test above proves the
full iPhone Minis agent hop.

## Legacy: iCloud queue（不可作为默认）

旧文件：

- `scripts/minis_bridge.py`
- `scripts/minis_bridge_mac.py`
- `scripts/bridge-dispatch.sh`
- `scripts/agent-hook.sh`

2026-09-12 真机发现：Files App 能看到 Mac 写入，但 Minis/iSH 的 FUSE view 可能冻结；`readdir/stat/open/os.access` 均看不到外部文件，目录 inode mtime 也不刷新。sentinel 测试同样失败。

因此 iCloud 队列只保留用于历史、回归测试或未来 iOS/Minis 修复后的复验，**不可再称为当前可用端到端方案**。

## Files

```text
SKILL.md
README.md
release-notes.md
references/
  feishu-relay.md
scripts/
  feishu_relay.py          # v1.2 默认
  minis_bridge.py          # legacy iCloud
  minis_bridge_mac.py      # legacy Win→Mac→iCloud
  bridge-dispatch.sh       # legacy Minis dispatcher
  agent-hook.sh            # legacy placeholder
 tests/
  test_feishu_relay.py
```

## Security

- 不在仓库、聊天、邮件中保存 `FEISHU_APP_SECRET`；
- v1 可复用现有 chat，但建议后续新建 Minis 专用 chat / 专用飞书应用并收紧权限；
- relay 只处理精确协议前缀和 `sender_type=app` 消息。

## License

Apache-2.0。变更记录见 [`release-notes.md`](release-notes.md)。
