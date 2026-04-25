# ![](doc/logo.png) Nekro WebChat

NekroAgent SSE 适配器的网页聊天客户端。

## 功能特性

* 通过 `nekro-agent-sse-sdk` 连接到 NekroAgent
* 基于 WebSocket 的浏览器聊天界面
* 将用户消息作为 SSE 适配器频道发送到 NekroAgent
* 实时显示 NekroAgent 返回的消息
* 使用 SQLite 存储会话与消息记录
* 提供 SSE 适配器所需的用户、频道与机器人信息处理器

## 运行方式

```bash
cd nekro-webchat
uv sync
copy .env.example .env
poe dev
```

打开浏览器访问：

```text
http://127.0.0.1:8765
```

请确保 NekroAgent 已启动，并已启用 SSE 适配器。

如果 SSE 适配器设置了访问密钥，请在 `.env` 中配置 `NEKRO_ACCESS_KEY`。

## 默认聊天 Key

此客户端默认创建的 NekroAgent 聊天 Key 为：

```text
sse-webchat-webchat_main
```

## 数据存储位置

默认数据文件存储在：

```text
data/webchat.db
```
