<h1 align="center">
  <img src="assets/logo-icon.jpg" alt="" width="100" valign="middle">
  &nbsp;
  memsearch
</h1>

<p align="center">
  <strong><a href="https://github.com/openclaw/openclaw">OpenClaw</a> 风格的 Markdown 记忆引擎</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/memsearch/"><img src="https://img.shields.io/pypi/v/memsearch?style=flat-square&color=blue" alt="PyPI"></a>
  <a href="https://zilliztech.github.io/memsearch/claude-plugin/"><img src="https://img.shields.io/badge/Claude_Code-plugin-c97539?style=flat-square&logo=claude&logoColor=white" alt="Claude Code Plugin"></a>
  <a href="https://pypi.org/project/memsearch/"><img src="https://img.shields.io/badge/python-%3E%3D3.10-blue?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://github.com/zilliztech/memsearch/blob/main/LICENSE"><img src="https://img.shields.io/github/license/zilliztech/memsearch?style=flat-square" alt="License"></a>
  <a href="https://zilliztech.github.io/memsearch/"><img src="https://img.shields.io/badge/docs-memsearch-blue?style=flat-square" alt="Docs"></a>
  <a href="https://github.com/zilliztech/memsearch/stargazers"><img src="https://img.shields.io/github/stars/zilliztech/memsearch?style=flat-square" alt="Stars"></a>
  <a href="https://discord.com/invite/FG6hMJStWu"><img src="https://img.shields.io/badge/Discord-chat-7289da?style=flat-square&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://x.com/zilliz_universe"><img src="https://img.shields.io/badge/follow-%40zilliz__universe-000000?style=flat-square&logo=x&logoColor=white" alt="X (Twitter)"></a>
</p>

https://github.com/user-attachments/assets/31de76cc-81a8-4462-a47d-bd9c394d33e3

`memsearch` 是一个面向 Agent 的语义记忆系统：你把记忆写成 Markdown，
系统负责切分、去重、向量化、检索与长期沉淀。你可以把它接到任何 Python
Agent 框架里，也可以直接通过 CLI 使用。

## 为什么用 memsearch

- **Markdown 是事实源**：记忆文件可读、可版本管理、可迁移，不绑定厂商。
- **短期记忆 + 长期记忆**：支持按天记录短期记忆，并用 LLM 提炼主题化长期记忆。
- **多触发机制**：支持手动触发、关键词触发、定时触发。
- **双层去重策略**：
  - 短期记忆：turn 锚点去重 + 内容哈希去重。
  - 长期记忆：时间水位去重 + LLM 语义合并去重。
- **生产级用户隔离**：
  - 文件层：`memory/<user_id>/...`
  - 检索层：Milvus `user_id` 元数据过滤
- **混合检索 + 可选 reranker**：向量检索 + BM25 + RRF，可叠加 cross-encoder / API rerank。
- **跨平台插件 hooks**：`ccplugin/hooks/` 同时提供 `.sh` 和 `.ps1`。

## 本次新增能力（重点）

- 新增 `memory` 子系统，目录结构默认如下：
  - `memory/<user_id>/short-memory/YYYY-MM-DD.md`
  - `memory/<user_id>/long-memory/<主题>.md`
- 新增 `MemSearch.search(..., user_id=..., filter_expr=...)`。
- CLI `search` 新增 `--user`、`--filter`、`--reranker`、`--rerank-model`、`--no-rerank`。
- 新增 `memsearch memory` 命令组（`write/list/read/consolidate/topics/read-topic/write-topic/check-triggers`）。
- 新增 API reranker（配置式，兼容 SiliconFlow / Jina / Cohere 等 `/v1/rerank` 风格接口）。
- 新增本地 cross-encoder reranker（`sentence-transformers`）。
- 新增单文件全功能演示：`examples/full_feature_demo.py`。
- 新增记忆文档：`memory/memory.md`。

## 安装

```bash
pip install memsearch
```

按需安装扩展依赖：

```bash
pip install "memsearch[google]"       # Google Gemini Embedding
pip install "memsearch[voyage]"       # Voyage Embedding
pip install "memsearch[ollama]"       # Ollama Embedding
pip install "memsearch[local]"        # 本地 embedding（sentence-transformers）
pip install "memsearch[rerank]"       # API reranker（httpx）
pip install "memsearch[rerank-local]" # 本地 reranker（sentence-transformers）
pip install "memsearch[all]"          # 全量依赖
```

## 快速上手（Python API）

下面是最小可运行示例：

```python
from memsearch import MemSearch

mem = MemSearch(paths=["./memory"])

await mem.index()
results = await mem.search("Redis 配置", top_k=3)
print(results[0]["content"], results[0]["score"])
```

## 如何构建 MemSearch（建议封装成 `build_mem`）

在真实 Agent 系统中，建议把 `MemSearch(...)` 的初始化集中到一个工厂函数，
这样更容易做多用户隔离、统一环境变量、后续扩展 reranker 和触发策略。

```python
import os
from memsearch import MemSearch
from memsearch.config import MemoryConfig, RerankConfig


def build_mem(user_id: str) -> MemSearch:
    return MemSearch(
        # 1) 数据范围与隔离（核心）
        paths=[f"./memory/{user_id}"],   # 仅索引当前用户目录
        user_id=user_id,
        memory_base_dir="./memory",
        collection="agent_memory_prod",

        # 2) Embedding
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "openai"),
        embedding_model=os.getenv("EMBEDDING_MODEL") or None,

        # 3) Milvus / Zilliz
        milvus_uri=os.environ["MILVUS_URI"],
        milvus_token=os.environ["MILVUS_TOKEN"],

        # 4) 长期记忆提炼用的 LLM
        compact_llm_provider="openai",
        compact_llm_model=os.getenv("OPENAI_MODEL") or None,

        # 5) 检索重排（可选）
        reranker="api",  # 不需要可设为 None
        rerank_model=os.getenv("RERANK_MODEL") or "BAAI/bge-reranker-v2-m3",
        rerank_config=RerankConfig(
            enabled=True,
            provider="api",
            api_base=os.getenv("RERANK_API_BASE", ""),
            api_key_env="RERANK_API_KEY",
        ),

        # 6) 记忆策略（触发/周期/沉淀）
        memory_config=MemoryConfig(
            base_dir="memory",
            keywords=["记住", "remember", "备忘"],
            short_interval_seconds=300,
            long_interval_seconds=3600,
            auto_consolidate=True,
            consolidation_days=7,
        ),
    )
```

### `build_mem` 参数怎么选

- **必须先定的 4 项**
  - `paths`：本实例要索引的目录范围。
  - `user_id`：用户隔离主键，强烈建议每请求明确传入。
  - `milvus_uri`：向量库地址（本地或云端）。
  - `milvus_token`：远程 Milvus/Zilliz 鉴权令牌（本地 Lite 可不填）。
- **建议开启的 3 项**
  - `memory_base_dir`：统一记忆根目录，便于运维和排查。
  - `memory_config`：控制关键词触发、短/长期周期、自动沉淀。
  - `compact_llm_model`：指定长期记忆提炼模型，避免默认模型漂移。
- **按需开启的 1 项**
  - `reranker` + `rerank_config`：检索精度要求高时开启，先评估延迟与成本。

### 构建后的最小运行顺序

拿到 `mem = build_mem(user_id)` 后，建议流程是：

1. 首次启动先 `await mem.index()`（初始化索引）。
2. 每轮对话里：写入后 `await mem.index_file(path)`（增量索引）。
3. 召回时：`await mem.search(query, user_id=user_id, top_k=...)`。
4. 周期任务里：`await mem.memory.on_tick()` 或 `await mem.memory.consolidate(...)`。

## 与 Agent 框架集成最佳实践

推荐按“两层模型”接入，避免代码风格混乱：

1. **底层统一主循环（必须）**：`Recall -> Think -> Remember -> Consolidate`
2. **上层框架适配（可选）**：LangGraph、OpenAI Agents SDK 只是调用底层能力

### 标准主循环（最小可用，所有框架共用）

下面这段是建议作为你业务代码的“唯一真相”。后续接任何框架，都只包装它。

```python
import asyncio
from memsearch import MemSearch


async def call_llm(user_input: str, recalls: list[dict]) -> str:
    # 这里替换成你自己的模型调用逻辑
    context = "\n".join(r["content"][:200] for r in recalls)
    return f"基于记忆回答：{context[:200]}"


async def handle_turn(
    mem: MemSearch,
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    user_input: str,
) -> str:
    # 1) 输入触发器（关键词/定时）检查
    trigger_result = await mem.memory.on_input(user_input)
    if trigger_result.short_memory_path:
        await mem.index_file(trigger_result.short_memory_path)

    # 2) Recall：检索历史记忆
    recalls = await mem.search(user_input, top_k=5, user_id=user_id)

    # 3) Think：调用 LLM
    answer = await call_llm(user_input, recalls)

    # 4) Remember：写短期记忆并增量索引
    written = await mem.memory.write_short(
        f"用户: {user_input}\n助手: {answer}",
        source="agent",
        session_id=session_id,
        turn_id=turn_id,
    )
    if written:
        await mem.index_file(written)

    # 5) 心跳触发（可能生成长期记忆）
    tick = await mem.memory.on_tick()
    for _, p in (tick.long_memory_paths or {}).items():
        await mem.index_file(p)

    return answer
```

### 记忆写入、检索与读取（一定要区分）

这一部分最容易混淆：`写入`、`检索`、`读取` 分别走的是不同路径。

#### 1）写入（把内容落到 Markdown）

写入只负责把内容保存到文件系统，不等于“已经可检索”。

- **短期记忆写入（API）**
  - `await mem.memory.write_short(content, source=..., session_id=..., turn_id=...)`
  - 返回值是写入文件路径（可能为 `None`，表示被去重跳过）
- **长期记忆写入（API）**
  - `await mem.memory.write_long(topic, content)`
  - 或 `await mem.memory.consolidate(days=7, force=False)` 自动提炼主题
- **CLI 对应命令**
  - `memsearch memory write ... --user alice`
  - `memsearch memory write-topic ... --user alice`
  - `memsearch memory consolidate --user alice`

#### 2）索引（让新写入内容进入向量检索）

写完后需要索引，新内容才会被 `search` 命中。

- **推荐增量索引**
  - `await mem.index_file(written_path)`
- **全量索引（初始化或批量修复）**
  - `await mem.index(force=False)`
- **CLI 对应命令**
  - `memsearch index ./memory/`

#### 3）检索（语义召回）

检索是从 Milvus 返回相关 chunk，不是直接读整份 Markdown 原文。

- **API**
  - `await mem.search(query, top_k=5, user_id="alice", filter_expr="...")`
- **CLI**
  - `memsearch search "你的问题" --user alice --top-k 5`
  - 可选：`--filter`、`--reranker`、`--rerank-model`、`--no-rerank`

#### 4）读取（直接读记忆文件内容）

读取是文件级访问，用于查看完整短期/长期记忆文本，不做相似度排序。

- **短期记忆读取**
  - API：`mem.memory.short.read(day="2026-02-26")`
  - CLI：`memsearch memory read --date 2026-02-26 --user alice`
- **长期记忆读取**
  - API：`mem.memory.long.read("认证策略")`
  - CLI：`memsearch memory read-topic "认证策略" --user alice`
- **查看检索结果的完整上下文（按 chunk 展开）**
  - CLI：`memsearch expand <chunk_hash> --user alice`

#### 5）一句话时序

推荐顺序：`写入 -> 增量索引 -> 检索 -> 读取/展开验证`。

如果你只写入不索引，`search` 通常不会立即看到新内容；如果只读取文件，
又无法按语义相关性召回。

### 通用落地建议

1. **按用户隔离实例**：每次请求明确传 `user_id`，避免跨用户召回。
2. **优先增量索引**：写入后 `index_file(path)`，不要每轮全量 `index()`。
3. **检索支持过滤**：按需使用 `filter_expr` 缩小检索范围。
4. **触发器前置**：在 LLM 前执行 `memory.on_input(text)`。
5. **写回后置**：在 LLM 后执行 `memory.write_short(...)`。
6. **心跳做沉淀**：定时执行 `memory.on_tick()` 或 `memory.consolidate(...)`。

### 与 LangGraph 集成（上层适配示例）

在 LangGraph 中，建议把底层主循环包装为一个 Tool，Agent 只负责调 Tool。

```python
import asyncio
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from memsearch import MemSearch

mem = MemSearch(paths=["./memory/alice"], user_id="alice")
asyncio.run(mem.index())


@tool
def ask_with_memory(user_input: str) -> str:
    return asyncio.run(
        handle_turn(
            mem,
            user_id="alice",
            session_id="langgraph-session",
            turn_id="turn-001",
            user_input=user_input,
        )
    )


agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [ask_with_memory])
result = agent.invoke({"messages": [("user", "我们之前定的鉴权策略是什么？")]})
```

### 与 OpenAI Agents SDK 集成（上层适配示例）

在 OpenAI Agents SDK 中，同样建议让 Tool 调用底层主循环，不要分散记忆逻辑。

```python
import asyncio
from agents import Agent, Runner, function_tool
from memsearch import MemSearch

mem = MemSearch(paths=["./memory/alice"], user_id="alice")
asyncio.run(mem.index())


@function_tool
def ask_with_memory(user_input: str) -> str:
    return asyncio.run(
        handle_turn(
            mem,
            user_id="alice",
            session_id="openai-agents-session",
            turn_id="turn-001",
            user_input=user_input,
        )
    )


assistant = Agent(
    name="MemoryAssistant",
    instructions="优先调用 ask_with_memory 来结合历史记忆回答问题。",
    tools=[ask_with_memory],
)

result = Runner.run_sync(assistant, "我们限流策略最终定的是哪版？")
print(result.final_output)
```

> 上述 OpenAI Agents SDK 示例使用常见版本接口（`function_tool`、
> `Agent`、`Runner`）。如果你的 SDK 版本不同，请按官方 API 名称做等价替换。

## 全功能 Demo（LLM + Embedding + Reranker + 用户隔离）

仓库内置全链路示例：`examples/full_feature_demo.py`，展示以下流程：

- 短期记忆写入与去重
- 关键词与定时触发
- 长期记忆提炼（主题化沉淀）
- 多用户隔离索引与检索
- `filter_expr` 条件检索
- 混合检索与 API reranker 对比

### 1）准备环境变量

请先基于示例文件创建你自己的本地配置文件（不要提交真实密钥）：

```bash
cp examples/.env.example examples/.env
```

在 Windows PowerShell 中可用：

```powershell
Copy-Item examples/.env.example examples/.env
```

然后编辑 `examples/.env`，填入你的真实密钥；或者直接在命令行设置环境变量。

**PowerShell 示例**

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_BASE_URL="https://api.siliconflow.cn/v1"
$env:OPENAI_MODEL="Qwen/Qwen3-235B-A22B-Instruct-2507"

$env:MILVUS_URI="https://in03-xxx.serverless.aws-eu-central-1.cloud.zilliz.com"
$env:MILVUS_TOKEN="..."

$env:RERANK_API_BASE="https://api.siliconflow.cn/v1/rerank"
$env:RERANK_API_KEY="sk-..."
$env:RERANK_MODEL="Qwen/Qwen3-Reranker-4B"

$env:EMBEDDING_PROVIDER="openai"
$env:EMBEDDING_MODEL="Qwen/Qwen3-Embedding-4B"
```

### 2）运行 Demo

```bash
uv run python examples/full_feature_demo.py
```

> Demo 会优先读取本地 `examples/.env`，并自动兼容 `EMBEDDING_BASE_URL` 到
> `OPENAI_BASE_URL`（用于 OpenAI 兼容接口）。

## CLI 常用命令

### 初始化配置

```bash
memsearch config init
memsearch config init --project
memsearch config list --resolved
```

### 索引

```bash
memsearch index ./memory/
memsearch index ./memory/ --force
```

### 检索（含用户隔离、过滤和 reranker）

```bash
memsearch search "认证和限流策略" --user alice
memsearch search "认证和限流策略" --user alice --filter 'source == "memory/alice/short-memory/2026-02-26.md"'
memsearch search "认证和限流策略" --user alice --reranker api --rerank-model "Qwen/Qwen3-Reranker-4B"
memsearch search "认证和限流策略" --user alice --no-rerank
```

### 记忆管理（新增）

```bash
memsearch memory write "这条请记住：数据库版本固定为 PostgreSQL 15。" --user alice
memsearch memory write --stdin --source manual --user alice

memsearch memory list --days 7 --user alice
memsearch memory read --date 2026-02-26 --user alice

memsearch memory consolidate --days 7 --force --user alice
memsearch memory topics --user alice
memsearch memory read-topic "认证策略" --user alice
memsearch memory write-topic "认证策略" "新的长期结论内容" --user alice

memsearch memory check-triggers "请记住：接口要做幂等保护" --user alice
```

### 监听与压缩

```bash
memsearch watch ./memory/
memsearch compact --llm-provider openai --llm-model gpt-4o-mini
```

### 统计与重置

```bash
memsearch stats --user alice
memsearch reset
```

## 工作原理

`memsearch` 的核心流程如下：

1. 扫描 Markdown 文件并按标题/段落切分 chunk。
2. 对 chunk 计算内容哈希并执行去重。
3. 对新增 chunk 进行 embedding 并写入 Milvus。
4. 查询时进行混合检索（Dense + BM25 + RRF）。
5. 如果启用 reranker，再进行二阶段重排。
6. 记忆系统按触发策略写入短期记忆，并在合适时机提炼为长期主题记忆。

## Claude Code 插件

仓库自带 `ccplugin`，用于在 Claude Code 中提供自动持久化记忆能力。

- hooks 已同时支持 Bash 与 PowerShell：
  - `ccplugin/hooks/*.sh`
  - `ccplugin/hooks/*.ps1`
- 在 Windows 环境会自动使用 PowerShell 版本。

更多说明见：`ccplugin/README.md`

## 配置与环境变量

配置优先级（低到高）：

1. 内置默认值
2. 全局配置：`~/.memsearch/config.toml`
3. 项目配置：`.memsearch.toml`
4. CLI 参数

常用环境变量：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `MILVUS_URI`
- `MILVUS_TOKEN`
- `RERANK_API_BASE`
- `RERANK_API_KEY`
- `MEMSEARCH_USER`

## 相关文档

- 文档主页：https://zilliztech.github.io/memsearch/
- Python API：https://zilliztech.github.io/memsearch/python-api/
- CLI 参考：https://zilliztech.github.io/memsearch/cli/
- 集成示例：https://zilliztech.github.io/memsearch/integrations/
- Claude 插件文档：https://zilliztech.github.io/memsearch/claude-plugin/
- FAQ：https://zilliztech.github.io/memsearch/faq/
- 记忆设计文档：`memory/memory.md`

## 贡献

欢迎提交 Issue、功能建议和 Pull Request。开发与测试说明见
`CONTRIBUTING.md`。

## 许可证

`MIT`，见 `LICENSE`。