# API 额度面板 (api-quota-panel)

一个轻量、密码保护的 Web 面板，集中查看多个 AI 渠道的 API 调用额度/余额。
Python + FastAPI 后端 + 原生 JS 前端（无构建），可打包成 Docker 部署。

## 设计目标

- **隐私优先**：所有 API key / OAuth token 仅存在于后端 `.env`，`/api/quota` 只返回数字与状态
- **适配器模式**：每个 AI 渠道对应一个适配器类，`TYPE` 在 `.env` 中配置，新增渠道只需添加适配器文件
- **错误隔离**：单个渠道超时或报错不影响其他渠道的额度展示
- **热更新**：OAuth token 刷新后自动写回 `.env` 并 in-memory 热更新，不用重启面板
- **轻量**：零前端构建、零数据库、单进程可部署

## 渠道与适配器类型

| 类型 | 渠道 | 鉴权方式 | 接口 | 额度字段 |
|---|---|---|---|---|
| `siliconflow` | 硅基流动 | API key (Bearer) | `GET /v1/user/info` | `data.totalBalance` (CNY) |
| `openai-relay` | 任意 OpenAI 兼容中转 | API key (Bearer) | `GET /v1/dashboard/billing/subscription` + `/usage` | `hard_limit_usd - total_usage/100` (USD) |
| `codex-web` | Codex (ChatGPT Web) | OAuth access_token (Bearer) | `GET /backend-api/wham/usage` | `rate_limit.primary_window.used_percent` → 剩余 % |
| `google-oauth` | Antigravity CLI | Google OAuth access_token (Bearer) | `POST /v1internal:fetchAvailableModels` | `models[].quotaInfo.remainingFraction` → 剩余 % |
| `pioneer` | Pioneer.ai | API key (X-API-Key) | `GET /billing/plan-info`（备 `/billing/billing-status`） | `remaining_credits` / `total_usage` / `credit_limit`（USD，接口返回「分」自动 /100） |
| `unsupported` | — | — | — | 显示「暂不支持查询」 |

> **现实结论**：官方直连能直接查额度的只有 **硅基流动** 和 **Pioneer.ai**。其余渠道官方端无公开额度 API，需要通过 OAuth token 或中转 relay 才能查到。Google 侧只有 Antigravity CLI 的 client_id 仍可用——旧 Gemini CLI client 已被 Google 弃用，会返回 `UNSUPPORTED_CLIENT`。

## 快速开始

```bash
cd ~/projects/api-quota-panel
cp example.env .env
# 编辑 .env：设 PANEL_PASSWORD、PANEL_SESSION_SECRET，填需要的 API key / token
python3 -c "import secrets;print(secrets.token_hex(32))"   # 生成 session secret
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 单测适配器（不启动服务）
python check.py            # 列出渠道
python check.py all        # 并行拉取全部已启用渠道
python check.py SILICONFLOW

# 启动
uvicorn app.main:app --reload --port 8000
# 浏览器打开 http://localhost:8000 ，输入密码
```

## 配置（.env）

完整模板见 `example.env`。每渠道支持以下字段：

| 字段 | 说明 | 适用类型 |
|---|---|---|
| `<NAME>_ENABLED` | 是否启用 (`true`/`false`) | 全部 |
| `<NAME>_TYPE` | 适配器类型 | 全部 |
| `<NAME>_API_KEY` | API key 或 OAuth access_token | 全部 |
| `<NAME>_BASE_URL` | 自定义 base URL（可选） | 全部 |
| `<NAME>_LABEL` | 显示名称 | 全部 |
| `<NAME>_REFRESH_TOKEN` | OAuth refresh_token（可选，自动刷新） | `codex-web`, `google-oauth` |
| `<NAME>_ACCOUNT_ID` | ChatGPT Account ID（可选） | `codex-web` |
| `<NAME>_PROJECT_ID` | GCP project ID | `google-oauth` |

`NAME` ∈ `CODEX, ANTIGRAVITY, GEMINI, GLM, SILICONFLOW, PIONEER`。

可选环境变量：

| 变量 | 说明 | 默认 |
|---|---|---|
| `PANEL_PASSWORD` / `PANEL_PASSWORD_HASH` | 登录密码（hash 用 `sha256(password)`） | — |
| `PANEL_SESSION_SECRET` | 64 字符 session 签名密钥 | 临时随机（重启失效） |
| `PANEL_COOKIE_SECURE` | HTTPS 后端设为 `true` | `false` |
| `PORT` | HTTP 端口 | `8000` |
| `CACHE_TTL_SECONDS` | 单渠道缓存 TTL | `120` |
| `ADAPTER_TIMEOUT_SECONDS` | 单 HTTP 请求超时 | `10` |
| `PANEL_ENV_FILE` | `.env` 路径（用于 token 持久化） | `.env` |

## Token 管理

### 获取来源

- **Codex**：`~/.codex/auth.json` 提取 `access_token` 和 `refresh_token`
- **Antigravity**：走浏览器 OAuth，拿到 `access_token` / `refresh_token` / `cloudaicompanionProject` 填到 `ANTIGRAVITY_*`

### 自动刷新 + 持久化 + 热更新

`codex-web` 和 `google-oauth` 在 `.env` 配置 `REFRESH_TOKEN` 后：

1. 适配器遇 401 → 自动调 OAuth token 端点换新 access_token（必要时 refresh_token 也轮换）
2. **新 access_token / refresh_token 原子写回 `.env`**（`app/config.py::write_env_updates`，temp + rename 防半写）
3. **`settings.reload()` 立即重建 in-memory `channels` 列表**，后续请求用新 token，无需重启
4. 仅该渠道的缓存项失效，其他渠道保留仍 fresh 的缓存

### 手动刷新

- **前端**：每张卡片的 ↻ 按钮 → `GET /api/quota/{channel}`（拉单个渠道）
- **后端**：`POST /api/refresh/{channel}`（OAuth 轮换 + 写 .env + 热更新）

### 凭证轮换 / 泄露响应

如发现 token 在日志、对话、截图、仓库中泄露，立刻按下列步骤处理：

1. **撤销旧 token**：去对应授权方撤销
   - Codex / ChatGPT：https://chatgpt.com/#settings/Security → Disconnect
   - Google：https://myaccount.google.com/permissions 移除 Antigravity 应用
2. **重新走 OAuth**：拿到新的 `access_token` / `refresh_token` / `project_id`
3. **更新 `.env` 里的 `<NAME>_API_KEY` / `<NAME>_REFRESH_TOKEN`**：
   - 面板已运行：可借 `settings.reload()` 热更新（修改 `.env` 后 `docker compose restart panel` 触发；原生部署直接 `kill -HUP <pid>` 不生效，需要重启进程）
   - 没运行：直接改 `.env`，启动时自动读取
4. **验证**：`python check.py <NAME>` 应返回 `status=ok`
5. **回查日志**：确认旧 token 没被滥用（提供方一般会发邮件通知异地登录）

## Docker

```bash
cp example.env .env   # 填好密钥
docker compose up --build -d
# 打开 http://localhost:8000
curl http://localhost:8000/api/health   # {"status":"ok"}
docker compose down
```

`.env` 经 `env_file` 运行时注入，**不打包进镜像**（`.dockerignore` 排除）。

**修改 `.env` 后必须重启容器**才能生效（`env_file` 是启动时读取，不热更新）：

```bash
docker compose restart panel
```

Docker 容器内 `settings.env_path` 默认指向容器内的 `.env`（由 `env_file:` 挂载）；修改宿主 `.env` 后 `docker compose restart panel` 即可让面板看到新值。

## 项目结构

```
api-quota-panel/
├── app/
│   ├── main.py              FastAPI 路由 + 中间件 + 静态文件
│   ├── config.py            .env 加载 + Settings（reload / write_env_updates）
│   ├── models.py            ChannelConfig / QuotaResult（Pydantic）
│   ├── auth.py              密码验证 + 登录限流 + 会话依赖
│   ├── cache.py             内存 TTL 缓存（is_stale / invalidate / set_one）
│   ├── aggregator.py        并行拉取 + 超时隔离（fetch_all / fetch_one）
│   ├── token_refresh.py     OAuth token 刷新（codex + Antigravity google）
│   └── adapters/
│       ├── base.py          适配器基类 + 结果构建函数
│       ├── siliconflow.py   硅基流动
│       ├── openai_relay.py  OpenAI 兼容中转
│       ├── codex_web.py     Codex (ChatGPT Web)
│       ├── google_oauth.py  Google OAuth (Antigravity)
│       ├── pioneer.py       Pioneer.ai（/billing/plan-info，分→美元）
│       ├── unsupported.py   无接口渠道
│       └── __init__.py      注册表 + get_adapter()
├── static/
│   ├── index.html           单页 HTML（登录 + 面板）
│   ├── app.js               前端逻辑 + 每张卡片独立 ↻ 刷新按钮
│   └── styles.css           响应式网格 + 状态配色
├── check.py                 适配器 CLI 预检工具
├── example.env              配置模板
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .gitignore
├── .dockerignore
├── README.md
└── SKILL.md
```

## API 端点

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| `GET` | `/api/health` | 无 | 健康检查 |
| `POST` | `/api/login` | 无 | `{password}` → 设置会话 cookie |
| `POST` | `/api/logout` | 有 | 清除会话 |
| `GET` | `/api/quota` | 有 | 返回全部渠道额度，**只增量拉取**过期或缺失的渠道；`?refresh=1` 强制全量刷新 |
| `GET` | `/api/quota/{channel}` | 有 | 拉取单个渠道并替换其缓存项（前端每张卡片 ↻ 按钮用） |
| `POST` | `/api/refresh/{channel}` | 有 | 手动触发 OAuth token 刷新：新 token 原子写 `.env` + `settings.reload()`，仅该渠道缓存失效 |

## 开发指南

### 添加新渠道

1. 在 `app/adapters/` 下创建适配器文件（继承 `QuotaAdapter`，实现 `fetch_quota`）
2. 在 `app/adapters/__init__.py` 注册表中添加 `type_name → AdapterClass`
3. 在 `config.py` 中 `_CHANNEL_NAMES` 列表添加渠道名
4. 在 `example.env` 添加模板配置
5. 用 `python check.py <NAME>` 测试

### 适配器接口

```python
class MyAdapter(QuotaAdapter):
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        # 使用 self._ok(ch, ...) / self._err(ch, msg) / self._unsupported(ch) 返回结果
```

所有异常必须在适配器内部捕获并转为 `QuotaResult(status="error")`——聚合器会兜底，但适配器自身捕获能提供更精确的错误信息。

## License

MIT License. See [LICENSE](LICENSE) for details.