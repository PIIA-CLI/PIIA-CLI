# Model providers

`piia` speaks two wire formats, which between them cover essentially every
endpoint you might point it at. There are no provider SDKs — one HTTP client —
so adding a provider is a configuration change, not a code change.

| `LLM_API_STYLE` | Request | Covers |
| :--- | :--- | :--- |
| `openai` (default) | `POST {LLM_BASE_URL}/chat/completions` | OpenAI, Azure OpenAI, Gemini's OpenAI-compatible endpoint, Ollama, vLLM, llama.cpp, LM Studio, LiteLLM, OpenRouter, Together, Fireworks, Groq, DeepSeek, Mistral, and most gateways |
| `anthropic` | `POST {LLM_BASE_URL}/v1/messages` with `x-api-key` + `anthropic-version` | Anthropic's native Messages API |

`piia doctor` sends a one-token probe and reports latency, so you can confirm a
configuration before spending a real run on it.

## Verified configurations

### Local, no API key

```bash
# Ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL_NAME=qwen2.5-coder:14b

# vLLM
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL_NAME=Qwen/Qwen2.5-14B-Instruct

# llama.cpp server
LLM_BASE_URL=http://localhost:8080/v1
LLM_MODEL_NAME=local-model

# LM Studio
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL_NAME=lmstudio-community/Qwen2.5-14B-Instruct-GGUF
```

Most local servers ignore the key. Leave `LLM_API_KEY` empty and `piia` sends no
`Authorization` header at all.

### Hosted

```bash
# OpenAI
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4.1
LLM_API_KEY=sk-...

# Anthropic (native API)
LLM_BASE_URL=https://api.anthropic.com
LLM_MODEL_NAME=claude-sonnet-4-5
LLM_API_KEY=sk-ant-...
LLM_API_STYLE=anthropic

# Google Gemini, via its OpenAI-compatible endpoint
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL_NAME=gemini-2.5-pro
LLM_API_KEY=...

# OpenRouter
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL_NAME=anthropic/claude-sonnet-4.5
LLM_API_KEY=sk-or-...

# Groq
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL_NAME=llama-3.3-70b-versatile
LLM_API_KEY=gsk_...

# Together
LLM_BASE_URL=https://api.together.xyz/v1
LLM_MODEL_NAME=meta-llama/Llama-3.3-70B-Instruct-Turbo
LLM_API_KEY=...

# DeepSeek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-chat
LLM_API_KEY=sk-...

# Mistral
LLM_BASE_URL=https://api.mistral.ai/v1
LLM_MODEL_NAME=mistral-large-latest
LLM_API_KEY=...
```

### Gateways and proxies

```bash
# LiteLLM proxy — one endpoint in front of many providers
LLM_BASE_URL=http://localhost:4000/v1
LLM_MODEL_NAME=whatever-alias-your-proxy-serves

# Azure OpenAI — the deployment name is the model name
LLM_BASE_URL=https://<resource>.openai.azure.com/openai/deployments/<deployment>
LLM_MODEL_NAME=<deployment>
LLM_API_KEY=...
LLM_EXTRA_HEADERS={"api-key":"..."}
```

`LLM_EXTRA_HEADERS` takes either JSON (`{"X-Org":"acme"}`) or `K=V,K=V` pairs,
and is merged into every request — use it for org routing, cost tags, or a
gateway's own auth header.

## Every setting

| Variable | Default | Notes |
| :--- | :--- | :--- |
| `LLM_BASE_URL` | — | **Required.** Usually ends in `/v1`. A URL already ending in `/chat/completions` is used verbatim. |
| `LLM_MODEL_NAME` | — | **Required.** |
| `LLM_API_KEY` | empty | Omitted entirely when empty. |
| `LLM_TEMPERATURE` | `0.1` | Low on purpose: legal drafting wants boring, repeatable prose. |
| `LLM_MAX_TOKENS` | `8192` | Also the required `max_tokens` for the Anthropic style. |
| `LLM_TOP_P` | unset | Drop it if an endpoint rejects it. |
| `LLM_SEED` | unset | Sent when set; some servers make sampling reproducible with it. |
| `LLM_TIMEOUT` | `180` | Seconds per request. Raise it for large local models. |
| `LLM_MAX_RETRIES` | `3` | Retries `408/409/425/429/5xx` and transport errors with jittered backoff, honouring `Retry-After`. |
| `LLM_API_STYLE` | `openai` | Or `anthropic`. |
| `LLM_API_VERSION` | `2023-06-01` | Anthropic style only. |
| `LLM_VERIFY_SSL` | `true` | Set `false` only for an internal endpoint with a self-signed certificate. |
| `LLM_SYSTEM_PROMPT` | built-in | Replaces the drafting system prompt. |
| `LLM_SYSTEM_PROMPT_FILE` | unset | Same, read from a file. Wins over `LLM_SYSTEM_PROMPT`. |
| `LLM_EXTRA_HEADERS` | `{}` | JSON object or `K=V,K=V`. |

## How `piia` copes with small and awkward models

The drafting layer assumes models misbehave, because they do:

- **Structured output is attempted, then abandoned.** `piia` asks for
  `response_format: {"type":"json_object"}`; if the endpoint returns HTTP 400
  mentioning `response_format`, it silently retries without it and relies on the
  prompt instead. Both paths work.
- **JSON is extracted, not assumed.** Replies wrapped in prose or ```` ```json ````
  fences are handled by finding the first balanced object.
- **Malformed replies get two repair turns**, with the specific problem quoted
  back to the model ("these required keys were missing: …").
- **Work is batched.** Prior works are drafted `--batch-size` at a time (default
  4), so a 16k-context model copes with a 40-repository run. Lower it if the
  model truncates; raise it if you have a large context and want more
  cross-repository awareness in each call.
- **Every failure has a floor.** A failed task falls back to a deterministic
  template, is recorded in `provenance.degraded`, and surfaces as a warning. The
  run still produces a complete document.

## Cost

Roughly `2 + ceil(prior_works / batch_size)` requests per `generate`. Fourteen
prior works at the default batch size is six calls. `piia generate --dry-run`
reports the exact count before you spend anything, and `piia analyze` costs
nothing at all.
