# Memory Management System

## 概述

本项目将记忆系统分为两层：

- 短时记忆：记录近期会话事实，写入频率高，要求轻量和可追踪。
- 长期记忆：从短时记忆中提炼关键决策与稳定事实，按主题沉淀。

两层都以 Markdown 文件作为数据源，Milvus 仅作为可重建索引。

## 目录结构

默认目录为项目根下 `memory/`，可通过配置项 `memory.base_dir` 覆盖：

```text
memory/
├── memory.md
└── <user_id>/
    ├── short-memory/
    │   ├── 2026-02-25.md
    │   └── 2026-02-26.md
    └── long-memory/
        ├── .last_consolidation
        ├── API设计决策.md
        └── 架构模式.md
```

## 用户隔离

采用双层隔离：

- 文件层：每个用户写入独立目录 `memory/<user_id>/...`
- 检索层：Milvus 记录 `user_id` 元数据并在查询时自动过滤

用户解析优先级（高到低）：

1. API/CLI 显式 `user_id`
2. 环境变量 `MEMSEARCH_USER`
3. 配置项 `memory.user_id`
4. 系统用户名（自动回退）

## 去重策略

### 短时记忆（轻量去重）

- Turn 锚点去重：`turn_id` 已存在则跳过
- 内容哈希去重：同日文件中 `<!-- hash:... -->` 已存在则跳过

这一路径不调用 LLM，保证 hook 写入快速完成。

### 长期记忆（双层去重）

- 输入去重：`long-memory/.last_consolidation` 水位线，仅处理新日期文件
- 输出去重：主题已存在时走 LLM 语义合并，避免事实重复堆叠

## 触发机制

支持三类触发：

- 手动触发：CLI/API 直接写入
- 定时触发：`short_interval_seconds` / `long_interval_seconds`
- 关键词触发：命中 `memory.keywords` 后自动写入（可选自动 consolidate）

## 配置

`.memsearch.toml` 示例：

```toml
[memory]
base_dir = "memory"
user_id = ""
short_memory_dir = "short-memory"
long_memory_dir = "long-memory"
keywords = ["记住", "remember", "备忘"]
short_interval_seconds = 0
long_interval_seconds = 86400
auto_consolidate = false
consolidation_days = 7

[rerank]
enabled = false
provider = "api" # api | cross-encoder
model = ""
top_k_multiplier = 3
api_base = ""
api_key_env = "RERANK_API_KEY"
top_k_field = "top_n"
result_path = "results"
score_field = "relevance_score"
index_field = "index"
```

## Python API

```python
from memsearch import MemSearch

mem = MemSearch(
    paths=["./memory"],
    user_id="alice",
)

await mem.index()
results = await mem.search("API 设计决策", top_k=5, filter_expr="", user_id="alice")

# 短时写入
await mem.memory.write_short("决定将鉴权改为 JWT + refresh token", source="manual")

# 长期提炼
topics = await mem.memory.consolidate(days=7)
```

## CLI 用法

```bash
# 搜索（用户隔离 + 可选过滤）
memsearch search "API 设计" --user alice --filter 'source == "memory/alice/short-memory/2026-02-26.md"'

# 启用 reranker（临时）
memsearch search "API 设计" --reranker api --rerank-model "BAAI/bge-reranker-v2-m3"
memsearch search "API 设计" --no-rerank

# 短时记忆
memsearch memory write "记录内容" --user alice
memsearch memory write --stdin --source auto/stop-hook --user alice
memsearch memory list --days 7 --user alice
memsearch memory read --date 2026-02-26 --user alice

# 长期记忆
memsearch memory consolidate --days 7 --user alice
memsearch memory topics --user alice
memsearch memory read-topic API设计决策 --user alice
memsearch memory write-topic API设计决策 "补充事实" --user alice

# 触发检测
memsearch memory check-triggers "这条信息请记住" --user alice
```

## 与索引/搜索链路的关系

- `index/watch`：将短时和长期 Markdown 文件索引到 Milvus，写入 `user_id`
- `search`：Milvus 先做 hybrid 检索（dense + BM25 + RRF）
- `reranker`（可选）：对候选集进行 cross-encoder 精排

## Claude Code Hook 集成

`ccplugin/hooks` 提供 Bash + PowerShell 双版本：

- `session-start`：启动 watcher，注入最近短时记忆
- `user-prompt-submit`：注入轻量 memory 可用提示
- `stop`：解析 transcript，摘要后调用 `memsearch memory write --stdin`
- `session-end`：停止 watcher

在 Windows 环境会自动使用 `.ps1` 版本。

## 运行与测试

- 使用虚拟环境：`D:\project\Memory\super-mem\.venv`
- 推荐测试命令：`uv run python -m pytest`
- Zilliz Cloud 建议通过环境变量传递凭据：
  - `MILVUS_URI`
  - `MILVUS_TOKEN`

不要将 token 写入仓库文件。
