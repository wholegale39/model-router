# Model Router · 多模型路由代理

OpenAI 兼容 API 代理——根据规则自动将请求路由到不同后端模型。支持 DeepSeek、OpenAI、OpenRouter，以及任意 OpenAI 兼容服务。

## 为什么做这个？

- ❌ 想用 DeepSeek 的便宜模型做日常，复杂的任务切 Claude——但 Agent 代码里得硬编码不同 API 地址
- ❌ DeepSeek 挂了不会自动 fallback 到备用模型
- ❌ 想算每个模型的 token 消耗和成本，但没有中间层做统计
- ❌ 模型多了记不清哪个任务该用哪个——Agent 调度器里配的模型名很乱

Model Router 是一个透明代理：Agent 只管向 `localhost:8771` 发请求，路由器决定发到哪个后端。

## 快速开始

```bash
git clone https://github.com/wholegale39/model-router.git
cd model-router

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 设置 API Key
export DEEPSEEK_API_KEY="sk-..."
export OPENAI_API_KEY="sk-..."
export OPENROUTER_API_KEY="sk-..."

# 启动
python3 -m uvicorn src.api:app --host 0.0.0.0 --port 8771
```

## 使用

```bash
# 完全兼容 OpenAI Chat Completions API
curl http://localhost:8771/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# 流式请求
curl http://localhost:8771/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[...],"stream":true}'

# 查看可用模型
curl http://localhost:8771/v1/models
```

### 集成到 Hermes Agent

```yaml
# hermes config.yaml
provider: openai
model: deepseek-chat
base_url: http://localhost:8771/v1
```

之后 Hermes 所有请求都走路由，你可以随时改路由规则来切换模型。

## 路由规则

默认配置：

| 规则 | 匹配 | 目标 |
|------|------|------|
| 精确匹配 | `deepseek-chat` | → deepseek/deepseek-chat |
| 前缀匹配 | `gpt-*` | → openai/gpt-4o-mini |
| 兜底 | 其他全部 | → openrouter/... |

可通过规则配置文件自定义：

```python
# 自定义规则
rules = [
    # 精确模型名匹配
    RouteRule(match_type="model", match_value="deepseek-chat", 
              target_model="deepseek/deepseek-chat"),
    # 任务类型匹配
    RouteRule(match_type="task", match_value="cheap",
              target_model="openai/gpt-4o-mini"),
    # 前缀匹配
    RouteRule(match_type="prefix", match_value="claude-",
              target_model="openrouter/anthropic/claude-sonnet-4"),
]
```

## 特性

| 特性 | 说明 |
|------|------|
| **完全兼容 OpenAI API** | 可以直接替换任何 OpenAI SDK 的 base_url |
| **多后端路由** | DeepSeek / OpenAI / OpenRouter / 任意 OpenAI 兼容服务 |
| **规则引擎** | 精确匹配 / 前缀匹配 / 任务类型匹配 / 兜底 |
| **自动 fallback** | 后端失败自动重试（指数退避） |
| **流式支持** | SSE streaming 透传 |
| **响应归一化** | 返回的 model 名带 backend 前缀，便于区分来源 |
| **零依赖 SDK** | Agent 端无需改代码，只改 base_url |

## 架构

```
Agent / 应用
     │
     ▼ POST /v1/chat/completions
┌─ Model Router (8771) ─────────────┐
│                                    │
│  Route Rule Engine ─→ 匹配规则     │
│       │                            │
│       ▼                            │
│  Backend Proxy ─→ 转发到后端       │
│       │                            │
│       ├─ deepseek (priority 1)     │
│       ├─ openai   (priority 10)    │
│       └─ openrouter (priority 20)  │
│                                    │
│  Response ← 归一化 model 名        │
└────────────────────────────────────┘
     │
     ▼ 响应（与标准 OpenAI 格式一致）
```

## 配置

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | — | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek 地址 |
| `OPENAI_API_KEY` | — | OpenAI API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI 地址 |
| `OPENROUTER_API_KEY` | — | OpenRouter API Key |

## 许可证

MIT
