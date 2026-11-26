# 待测智能体：校园智能助手

这是一个独立的、可接入真实大模型的校园助手。它不依赖测试工具运行。

## 配置 API Key 和 URL

复制模板：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少填写：

```dotenv
MODEL_API_URL=https://api.deepseek.com/v1/chat/completions
MODEL_API_KEY=sk-你的真实密钥
MODEL_NAME=deepseek-chat
```

`MODEL_API_URL` 要填写完整的 OpenAI-compatible Chat Completions 地址，不是服务商控制台首页地址。常见示例：

```text
DeepSeek: https://api.deepseek.com/v1/chat/completions
通义千问兼容接口: https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
智谱 GLM: https://open.bigmodel.cn/api/paas/v4/chat/completions
本地 Ollama: http://127.0.0.1:11434/v1/chat/completions
```

`MODEL_NAME` 以对应服务商控制台提供的模型名为准，例如 `deepseek-chat`、`qwen-plus` 或 `glm-4-flash`。本地 Ollama 没有 Key 时，可以把 `MODEL_API_KEY` 留空。

不要把真实 API Key 发到聊天中，也不要提交 `.env` 到 GitHub。

## 启动和聊天

```powershell
python agent_server.py
```

然后打开浏览器访问 <http://127.0.0.1:8000/>。页面支持多轮聊天。

接口：

- `GET /`：浏览器聊天页面
- `GET /health`：查看模型是否配置
- `POST /chat`：对话接口，传入 `message` 和可选的 `session_id`

如果未配置 `.env`，服务仍可启动，但 `/chat` 会返回明确的配置错误，不会伪造模型回答。
