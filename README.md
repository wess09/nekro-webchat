# Nekro WebChat

Standalone QQ-style web chat client for NekroAgent's SSE adapter.

## Features

- Connects to NekroAgent through `nekro-agent-sse-sdk`
- Browser chat UI over WebSocket
- Sends user messages into NekroAgent as an SSE adapter channel
- Displays messages sent back by NekroAgent in real time
- Stores conversations and messages in SQLite
- Provides user, channel and bot info handlers required by the SSE adapter

## Run

```bash
cd nekro-webchat
uv sync
copy .env.example .env
poe dev
```

Open:

```text
http://127.0.0.1:8765
```

Make sure NekroAgent is running and the SSE adapter is enabled. If the SSE adapter has an access key, set `NEKRO_ACCESS_KEY` in `.env`.

The default NekroAgent chat key created by this client is:

```text
sse-webchat-webchat_main
```

Data is stored at `data/webchat.db` by default.
