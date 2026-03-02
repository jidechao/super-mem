# memsearch

`memsearch` 是一个面向 Agent 场景的 Markdown 记忆检索框架，提供：

- 记忆文件管理（短期/长期）
- 向量索引与混合检索（Dense + BM25 + RRF）
- 可选二阶段重排（本地 Cross-Encoder 或 API Reranker）
- 多用户隔离（文件层 + 向量层）
- 命令行工具与 Python API 双入口

项目目标是让你可以把“可持久化、可检索、可演化”的记忆能力稳定集成到自己的 Agent 系统中。

---

## 目录

- [核心特性](#核心特性)
- [项目结构](#项目结构)
- [安装](#安装)
- [快速开始CLI](#快速开始cli)
- [配置说明](#配置说明)
- [Python 集成](#python-集成)
- [完整链路示例](#完整链路示例)
- [一键真实环境 E2E 测试](#一键真实环境-e2e-测试)
- [测试与质量](#测试与质量)
- [常见问题](#常见问题)
- [开发说明](#开发说明)
- [许可证](#许可证)

---

## 核心特性

### 1) 分层记忆体系

- 短期记忆：`memory/<user_id>/short-memory/YYYY-MM-DD.md`
- 长期记忆：`memory/<user_id>/long-memory/<topic>.md`
- 支持从短期记忆自动提炼长期主题并回写索引

### 2) 多用户隔离

- 文件层隔离：按 `user_id` 目录划分
- 向量层隔离：每条 chunk 写入 `user_id` 元数据，并在查询时自动拼接过滤条件

### 3) 混合检索 + 可选重排

- 一阶段：Dense 向量检索 + BM25 关键词检索 + RRF 融合
- 二阶段（可选）：
  - `api`：兼容 OpenAI 风格 `/v1/rerank` 接口
  - `cross-encoder`：本地模型重排

### 4) 显式记忆类型字段

索引记录包含 `memory_type`：

- `short`
- `long`
- `other`

并且检索结果会统一返回 `memory_type`。  
对于旧数据（没有该字段）会在读取阶段按 `source` 回填，保证兼容性。

### 5) 生产可用性增强

- 外部调用统一重试机制（含退避）
- 可配置超时与重试参数（compact/rerank）
- watch 回调异常隔离，避免单次异常中断整体监听
- CI 质量门禁（ruff / mypy / pytest / 覆盖率阈值 / 冒烟）

---

## 项目结构

```text
src/memsearch/
  core.py                 # 主编排器（索引、检索、压缩、watch）
  store.py                # Milvus 存储封装
  cli.py                  # CLI 命令入口
  config.py               # 配置模型与解析
  compact.py              # 记忆压缩
  resilience.py           # 外部调用重试工具
  memory/
    short_memory.py       # 短期记忆管理
    long_memory.py        # 长期记忆管理
    triggers.py           # 触发器逻辑
  rerankers/
    api.py                # API 重排
    cross_encoder.py      # 本地重排

scripts/
  verify_existing_functionality.py  # 现有功能冒烟
  e2e_real_chain.py                 # 一键真实环境 E2E
```

---

## 安装

### 基础安装

```bash
pip install memsearch
```

### 可选能力安装

```bash
pip install "memsearch[google]"       # Google embedding
pip install "memsearch[voyage]"       # Voyage embedding
pip install "memsearch[ollama]"       # Ollama embedding
pip install "memsearch[local]"        # 本地 embedding（sentence-transformers）
pip install "memsearch[rerank]"       # API reranker（httpx）
pip install "memsearch[rerank-local]" # 本地 reranker（sentence-transformers）
pip install "memsearch[all]"          # 全部可选依赖
```

---

## 快速开始（CLI）

### 1) 初始化配置

```bash
memsearch config init --project
memsearch config list --resolved
```

### 2) 索引记忆目录

```bash
memsearch index ./memory/
```

### 3) 检索

```bash
memsearch search "认证和限流策略" --user alice --top-k 5
memsearch search "认证和限流策略" --user alice --filter 'source == "memory/alice/short-memory/2026-03-02.md"'
memsearch search "认证和限流策略" --user alice --reranker api --rerank-model "Qwen/Qwen3-Reranker-4B"
memsearch search "认证和限流策略" --user alice --no-rerank
```

### 4) 记忆管理

```bash
memsearch memory write "请记住：数据库版本固定 PostgreSQL 15" --user alice
memsearch memory list --days 7 --user alice
memsearch memory read --date 2026-03-02 --user alice
memsearch memory consolidate --days 7 --force --user alice
memsearch memory topics --user alice
memsearch memory read-topic "认证策略" --user alice
```

### 5) 监听自动索引

```bash
memsearch watch ./memory/ --debounce-ms 1500 --user alice
```

### 6) 统计与清空

```bash
memsearch stats --user alice
memsearch reset --yes
```

---

## 配置说明

配置优先级（低 -> 高）：

1. 代码默认值
2. 全局配置：`~/.memsearch/config.toml`
3. 项目配置：`.memsearch.toml`
4. CLI 参数

核心配置分组：

- `milvus.*`：向量库连接
- `embedding.*`：向量模型
- `chunking.*`：切分策略
- `watch.*`：文件监听
- `memory.*`：短/长期记忆策略（含目录名）
- `compact.*`：长期提炼调用参数（含超时重试）
- `rerank.*`：重排参数（含超时重试）

---

## Python 集成

### 最小示例

```python
from memsearch import MemSearch

mem = MemSearch(paths=["./memory"], user_id="alice")

await mem.index()
hits = await mem.search("认证和限流策略", top_k=5, user_id="alice")
for h in hits:
    print(h["memory_type"], h["score"], h["source"])
```

### 推荐的 Agent 每轮流程

1. Recall：`await mem.search(...)`
2. Think：将召回上下文拼到 LLM Prompt
3. Remember：`await mem.memory.write_short(...)`
4. Re-index：`await mem.index_file(written_path)`
5. Consolidate：`await mem.memory.on_tick()` + `await mem.index_file(long_path)`

---

## 完整链路示例

可参考：

- `examples/full_feature_demo.py`

该示例包含：

- 多用户隔离
- 短期写入 + 去重
- 触发器
- 长期提炼
- 检索过滤
- 重排效果对比

---

## 一键真实环境 E2E 测试

新增脚本：

- `scripts/e2e_real_chain.py`

覆盖链路：

- 添加历史记忆文件
- 索引
- 召回
- 重排
- 长短期上下文组装
- LLM 问答
- 写入短期记忆并增量索引
- 自动触发长期提取并索引
- 最终检索验证
- 输出 JSON + Markdown 报告

### 一键执行（完成后自动清理）

```powershell
.\.venv\Scripts\python scripts\e2e_real_chain.py run --cleanup-after
```

### 手动清理模式

```powershell
.\.venv\Scripts\python scripts\e2e_real_chain.py cleanup `
  --collection memsearch_e2e_real_1772458532 `
  --memory-path .\memory\e2e-real\1772458532
```

### 报告输出

默认写入 `./reports/`，包含：

- `e2e_real_chain_*.json`
- `e2e_real_chain_*.md`

---

## 测试与质量

### 本地测试（项目虚拟环境）

```powershell
.\.venv\Scripts\python -m pytest -q
```

### 现有功能冒烟

```powershell
.\.venv\Scripts\python scripts\verify_existing_functionality.py
```

### CI 质量门禁

仓库已配置 CI 工作流（`.github/workflows/ci.yml`），包含：

- ruff
- mypy
- pytest + 覆盖率阈值
- 冒烟脚本
- 依赖审计（pip-audit）

---

## 常见问题

### 1) 为什么检索不到刚写入的记忆？

短期写入文件后，需要执行：

- `await mem.index_file(written_path)`（推荐）
- 或下一轮 `index/watch` 周期

### 2) 为什么 `memory_type` 看起来不对？

`memory_type` 由记忆目录名判断。若你自定义了目录名，请确保：

- `memory.short_memory_dir`
- `memory.long_memory_dir`

与真实目录一致。

### 3) `reset` 会删什么？

`memsearch reset` 会删除整个 collection（不是按用户删除）。

---

## 开发说明

```bash
# 安装开发依赖（示例）
pip install -e .
pip install pytest pytest-asyncio pytest-cov ruff mypy

# 运行质量检查
python -m ruff check src tests scripts
python -m mypy src/memsearch
python -m pytest -q
```

---

## 许可证

MIT
