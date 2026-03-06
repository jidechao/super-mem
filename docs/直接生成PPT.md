下面是一整套「可直接照着念」的详细讲稿，以之前 26 页 PPT 结构为基础。你可以按需要删减内容、调整用语风格。

---

### Slide 1：memsearch：让 Agent 真正拥有记忆

**开场语**

各位好，今天这场分享，我们主要聊一个在日常用大模型、用 Claude Code 开发时，非常实用但又容易被忽视的能力：**记忆系统**。  
我们会以 `memsearch` 这个项目为主线，带大家从使用体验一路走到内部实现，最后看它怎么跟你们现有的 Agent、服务、工具链集成在一起。

**要点讲解**

- `memsearch` 可以理解成：**一个专门服务于 Markdown 知识库的语义记忆系统**。
- 它帮你解决的问题：  
  - 团队到处都是 `.md` 文件：ADR、会议纪要、日报、开发笔记；  
  - Agent 每次开新会话，都像刚入职，完全不知道之前发生了什么。
- 今天的目标：
  - 从「用户视角」理解：我怎么用它来搜索、来给 Agent 加记忆；
  - 从「实现视角」理解：为何设计成现在这样，你们改代码时要注意什么；
  - 最后从「集成 + 运维视角」看如何落地到你们项目里。

**过渡语**

我们先从最直观的：**为什么需要 memsearch**、它到底帮我们解决哪些具体痛点开始。

---

### Slide 2：为什么我们需要 memsearch

**讲解**

先想象两个场景：

1. 你和 Claude 连续几天迭代同一个功能。  
   - 周一：一起做了 Redis 缓存。  
   - 周三：你说“订单接口有点慢”，Claude 完全不知道两天前已经加过缓存，又从零开始分析。
2. 团队所有的架构决策、Meeting Notes、Bug Insight 都写在 `.md` 里。  
   - 过几周再找一条信息，只能靠模糊印象 + `grep`，要么搜不到，要么结果一大堆。

这些问题的共性：

- LLM 天生「失忆」：上下文窗口一关就没了；
- 传统方案要么：
  - 自己搭个数据库 + 向量库，路由、索引、运维都要自己搞；
  - 要么用黑盒 SaaS，数据格式不透明、迁移成本高、存在锁定风险。

**总结句**

memsearch 想解决的，就是：**既让 Agent 有持续的「长期记忆」，又不增加太多运维负担，还保证数据是你看得懂的文本**。

**过渡语**

那 memsearch 是怎么做的呢？它有两个非常核心的理念，我们先把这两个理念说清楚。

---

### Slide 3：核心理念：Markdown 是事实源

**讲解**

第一条核心设计哲学：**Markdown 是唯一的事实源（Source of Truth）**。

- 所有知识、记忆，最终都以 `.md` 文件形式落在磁盘上：
  - `MEMORY.md` 放长期不变的事实和关键决策；
  - `memory/YYYY-MM-DD.md` 按天滚动记录日常事件、会话总结。
- 向量数据库 Milvus/Zilliz 在这里，只是一个**可重建索引层**：
  - 真正的知识在 `.md`；
  - Milvus 可以随时删掉，重新 `memsearch index` 一遍就能恢复。

为什么这样做？

- **人类可读**：你随时可以打开任何一条记忆，用肉眼看到：今天聊了什么、做了什么决策。
- **Git 友好**：.md 非常适合版本控制：diff、blame、review 都能看出变化。
- **零锁定**：哪天不用 memsearch 了，这些 Markdown 还在，你可以交给别的系统继续用。

**小提示**

你可以对开发同学强调：**不要把业务事实塞进 Milvus 的字段里当真源**，任何长期有价值的信息，都应该写回 Markdown 文件。

**过渡语**

第二条理念，是它在记忆切片、ID 设计上完全对齐了 OpenClaw 的方案，这一点对我们后续扩展非常关键。

---

### Slide 4：与 OpenClaw 对齐的记忆架构

**讲解**

`memsearch` 本质上是一个独立的库，但在记忆布局和 ID 策略上，**刻意对齐 OpenClaw**：

- 布局上：
  - 一个全局的 `MEMORY.md`；
  - 再配一组 `memory/YYYY-MM-DD.md` 的每日日志。
- Chunk ID 的构造：
  - `hash(markdown:source:startLine:endLine:contentHash:model)`
  - 里面包含：源文件路径、行号区间、内容哈希、使用的 embedding 模型名。
- 好处：
  - 我们可以无缝接 OpenClaw 的记忆目录，不需要迁移脚本；
  - 可以非常精确地知道「哪一行内容」对应 Milvus 里的哪一条记录；
  - 内容一改，哈希就变，**只对改动部分重嵌入**，成本非常可控。

**过渡语**

有了这两个理念，我们可以回头看一下整个系统的分层结构，理解它在代码层面是怎么落地的。

---

### Slide 5：整体架构总览

**讲解**

可以把整个 memsearch 想成三层：

- **顶层：应用层**
  - Claude Code 插件（我们会单独用一节讲）；
  - 你们自己的 Agent、后端服务、脚本等；
  - LangChain / LangGraph / CrewAI 等框架集成。
- **中间：服务层**
  - `memsearch` CLI：提供 `index / search / watch / compact / expand / transcript / memory.* / config.* / stats / reset` 等命令；
  - Python API：核心类 `MemSearch` 对上暴露 `index / search / compact / watch` 这些方法。
- **底层：核心库**
  - `chunker`：Markdown 切片；
  - `scanner`：递归扫描 Markdown 文件；
  - `store`：和 Milvus 打交道；
  - `embeddings`：统一管理 OpenAI / Google / Voyage / Ollama / local 等 embedding provider；
  - `compact + resilience`：LLM 压缩 + 重试；
  - `config`：TOML 配置系统；
  - `watcher`：文件变更监听；
  - `transcript`：Claude JSONL 对话解析；
  - `memory`：短期/长期记忆管理。

**过渡语**

了解了架构，我们先从「怎么用」开始，让大家对工具有直观感受，然后再回过头来拆源码。

---

### Slide 6：从 0 到 1：第一次搜索（CLI）

**讲解**

这一页我们只做一件事：**让大家看清楚，memsearch 基本能力只需要几条命令就能跑起来**。

整体步骤是三步：

1. 写两份 Markdown：
   - 一个 `MEMORY.md`，写一些长期事实：
     - 团队成员是谁、谁负责什么；
     - 几个关键架构决策。
   - 一个 `memory/2026-02-10.md`，写当天的 standup 和决策。
2. 执行 `memsearch index .`：
   - 扫描当前目录所有 `.md`；
   - 切成 chunks，嵌入，写入 Milvus。
3. 用 `memsearch search "Bob 最近做了什么？"` 查一查：
   - 返回结果带 Source 和 Heading；
   - 内容前 500 字预览。

**Demo 提示（可现场操作）**

- 现场在终端演示：
  - 快速创建 demo 目录和两个文件；
  - 设置 `OPENAI_API_KEY`；
  - 执行 `memsearch index .` 和 2–3 条 `memsearch search`；
- 刻意再执行一次 `memsearch index .`，让大家看到「Indexed 0 chunks」，**说明增量索引不会重复浪费 embedding 调用**。

**过渡语**

命令行层面搞清楚之后，我们再看一下，如果我要在 Python 里给一个 Agent 加上记忆，该怎么做？

---

### Slide 7：Python API：三步循环

**讲解**

Python 里的核心概念就是一句话：**Recall、Think、Remember 三步循环**。

- Step 1：Recall —— 回忆
  - `mem = MemSearch(paths=["./memory"])`
  - `await mem.index()` 初始化索引；
  - 每次收到用户问题：`memories = await mem.search(user_input, top_k=5)`。
- Step 2：Think —— 思考
  - 把 `memories` 里的内容做一个简要拼接，比如：
    - `context = "\n".join(f"- {m['content'][:200]}" for m in memories)`
  - 把这段 context 作为系统提示的一部分传给 LLM，让它「带着记忆思考」。
- Step 3：Remember —— 记住
  - 把这次对话的关键信息写入当天的 `memory/YYYY-MM-DD.md`；
  - 然后再 `await mem.index()`，让新的记忆也进入索引。

**Demo 提示**

- 可以展示 `docs/getting-started.md` 里提供的 OpenAI 示例：
  - 先 seed 几条记忆；
  - 再调用 `agent_chat("谁是我们的 frontend lead？")`；
  - 展示第二次问同样问题时，Agent 是如何从记忆中回答的。

**过渡语**

有了 CLI 和 Python API 这两层认知，我们可以再往上看一点：memsearch 在 Claude Code 插件里，是如何做到「装完就有记忆」的。

---

### Slide 8：Claude Code 插件：装完就有记忆

**讲解**

这里的对比非常关键：

- 没有插件的世界：
  - 周一、周二、周三，你和 Claude 做了很多事；
  - 每次 session 结束，所有上下文都丢失；
  - 周五再问「订单缓存有问题」，Claude 不知道你们之前怎么实现的。
- 安装了 memsearch 插件之后：
  - 每次 session 结束，插件会自动：
    - 读取这次的 JSONL transcript；
    - 用一个小模型（Haiku）总结；
    - 把总结写到 `.memsearch/memory/当天日期.md`。
  - 下次启动 Claude Code：
    - SessionStart hook 会加载最近几天的记忆；
    - 当你的问题和历史相关时，Claude 会自动调用 `memory-recall` skill 做检索。

用户层面的体验是：

- 不需要学习任何新命令；
- 不需要记得「什么时候该保存记忆」；
- 插件把所有记录、召回都自动做了。

**过渡语**

到这里，大家应该对「memsearch 用起来是什么感觉」有个直观印象。  
接下来，我们切换到「实现视角」，看看它在源码里是如何组织这些能力的。

---

### Slide 9：从使用到实现：我们要看什么

**讲解**

接下来这部分会稍微技术一些，主要面向会改代码、会二次开发的同学。

我们想回答的问题是：

- 为什么索引是增量的，而不是每次全量重算？
- 混合检索具体怎么实现的？
- 配置为什么要搞这么多层？
- Claude 插件是怎么和 CLI 与核心库拼在一起的？

今天不会把每一行代码都展开，而是聚焦 3–4 个最关键的模块，让你们之后看代码不至于「迷路」。

**过渡语**

我们先从最核心的类 `MemSearch` 开始，它几乎串起了所有重要逻辑。

---

### Slide 10：核心模块划分

**讲解**

这一页是一个「地图」，给大家一个整体导航：

- `core.MemSearch`：
  - 高层入口类，**绝大多数调用方只需要跟它打交道**。
- `store.MilvusStore`：
  - 所有和 Milvus 相关的操作都集中在这里；
  - 包括 collection schema 的创建、混合检索、统计、删除等。
- `chunker / scanner`：
  - 负责从 Markdown 原文到 Chunk 的切片；
  - 这里的策略和参数（max_chunk_size、overlap）比较关键。
- `config`：
  - 负责把默认值、全局配置、项目配置、CLI 参数合并在一起。
- `compact + resilience`：
  - 统一做 LLM 调用和重试；
  - 避免因为网络抖动或临时限流导致 compact 失败。
- `watcher`：
  - 基于 watchdog，监听文件的创建、修改、删除事件。
- `transcript`：
  - 针对 Claude Code 生成的 JSONL transcript，做解析与格式化。

**过渡语**

有了这个地图，接下来我们详细拆一下：一个「索引」请求从 `mem.index()` 开始到底做了什么。

---

### Slide 11：索引流程详细拆解

**讲解**

我们从 `MemSearch.index()` 函数入手，把内部步骤拆开讲：

1. **扫描文件**
   - 调用 `scan_paths(self._paths)`：
     - 支持多个路径；
     - 支持文件和目录；
     - 可以忽略隐藏文件。
2. **对每个文件调用 `_index_file`**
   - `_index_file` 内部会：
     - 读文件文本；
     - 调用 `chunk_markdown` 生成若干 `Chunk`。
3. **计算 chunk id 与去重**
   - 每个 chunk 会有：
     - `content_hash`：对内容做 SHA-256 截断；
     - `chunk_id`：用 `compute_chunk_id` 组合 source、行号、content_hash、模型名。
   - 还会加上 `user_id` 前缀，完成用户隔离。
4. **删除 stale chunks**
   - 查询本 source 在 Milvus 中已有的 `chunk_hash` 集合；
   - 对比当前文件生成的集合差集；
   - 对不再存在的 id 做删除。
5. **嵌入 & 写入**
   - 对需要新增的 chunk 内容调用 embedding provider；
   - 构造记录字典，写入 `MilvusStore.upsert`。
6. **统计与日志**
   - 返回这次新增的 chunk 数量；
   - 打日志：files / chunks / duration_ms，方便后续排查性能问题。

**强调点**

- 晋级开发要理解：**`index()` 是幂等而且增量的**，你们在 CI 或定时任务里可以频繁调用，不用担心成本爆炸。

**过渡语**

刚才提到的 Chunk 是整个系统的基本粒度，我们再单独看一下 Chunk 的设计。

---

### Slide 12：Chunk 与 Chunk ID 的设计

**讲解**

一个 `Chunk` 长什么样？

- 字段：
  - `content`：片段文本；
  - `source`：文件绝对路径；
  - `heading / heading_level`：最近的 Markdown 标题及层级；
  - `start_line / end_line`：在源文件中的行号（1-based）；
  - `content_hash`：对内容做 SHA-256 截断的 16 位哈希。

Chunk ID 的构造为：

- `hash(markdown:source:startLine:endLine:contentHash:model)`

这里每个字段都有用意：

- `source + 行号`：方便回溯和调试，知道某一条结果来自哪一行；
- `content_hash`：确保内容变化一定会改变整体 ID；
- `model`：保证更换 embedding 模型时，不会和旧索引混淆。

好处：

- 去重：不需要额外的 cache 表或 Redis；  
  主键本身就是「是否见过」的判定。
- 兼容性：完全对齐 OpenClaw 的方案，可以共享记忆目录。

**过渡语**

有了 Chunk，我们再看一下数据在 Milvus 里是怎么存的，以及混合检索是怎么做的。

---

### Slide 13：存储层：MilvusStore

**讲解**

`MilvusStore` 是对 `pymilvus.MilvusClient` 的一层薄封装，却非常关键：

- 创建 collection 时：
  - 字段：
    - `chunk_hash`：VARCHAR(64) 主键；
    - `embedding`：FLOAT_VECTOR(dim)；
    - `content`：VARCHAR(65535)，启用 analyzer；
    - `sparse_vector`：SPARSE_FLOAT_VECTOR；
    - 以及 `source / heading / heading_level / start_line / end_line / user_id / memory_type`。
  - Function：
    - 定义了一个 BM25 Function：输入 content，输出 sparse_vector；
  - 索引：
    - `embedding` 上建 FLAT + COSINE；
    - `sparse_vector` 上建 SPARSE_INVERTED_INDEX + BM25。
- 对外接口：
  - `upsert(chunks)`：插入或更新 chunk；
  - `search(query_embedding, query_text, top_k, filter_expr, user_id)`：
    - 会自动加上 user_id 的过滤；
    - 返回 entity + 距离（score）；
  - `query / delete_by_source / delete_by_hashes / count / drop` 等。

**强调**

团队里**不要直接从业务代码里拿 MilvusClient 去改 schema**，统一通过 `MilvusStore` 来操作，避免破坏混合检索逻辑。

**过渡语**

接下来，我们具体看一下混合检索的流程，它是如何把 dense 和 BM25 结合的。

---

### Slide 14：混合检索与 RRF

**讲解**

memsearch 的检索不是单一向量搜索，而是**Hybrid Search + RRF**：

- Dense 路径：
  - 使用 embedding 向量，在 `embedding` 字段上做 COSINE 相似度搜索。
  - 擅长「语义相似」：同义表达、描述性问题。
- Sparse 路径（BM25）：
  - 对 `query_text` 使用 BM25，在 `sparse_vector` 字段上做全文检索。
  - 擅长「关键字精确匹配」：错误码、配置项、函数名。
- RRF（Reciprocal Rank Fusion）：
  - 对两路结果按排名做融合；
  - 避免一方的信号完全压制另一方。

实现细节：

- `MilvusStore.search` 里：
  - 构造两个 `AnnSearchRequest`；
  - `self._client.hybrid_search(reqs=[dense_req, bm25_req], ranker=RRFRanker(k=60))`；
  - 返回融合后的 top_k 结果。

**过渡语**

索引和检索的核心逻辑差不多了，下面我们看看配置系统是如何支撑「多环境 + 多 provider」的。

---

### Slide 15：配置系统与多环境支持

**讲解**

配置系统的目标是：**同一套代码，可以在本地、测试、生产三种环境下平滑运行**。

- 配置结构 `MemSearchConfig`：
  - `milvus`：uri / token / collection；
  - `embedding`：provider / model；
  - `compact`：LLM provider/model + 超时/重试；
  - `chunking`：max_chunk_size / overlap_lines；
  - `watch`：debounce_ms；
  - `memory`：base_dir / user_id / short/long 目录 / 关键字触发等；
  - `rerank`：是否启用、provider、model、API base 等。
- 优先级链：
  - 默认 dataclass 值；
  - 全局 `~/.memsearch/config.toml`；
  - 项目内 `.memsearch.toml`；
  - CLI 参数传入。
- 使用方式：
  - CLI 层通过 `_build_cli_overrides` 构造覆盖；
  - 应用层可以直接调用 `resolve_config()`，拿到最终合成的配置。

**实战建议**

- 每个项目**固定维护**一个 `.memsearch.toml`；
- 统一在里面约定：
  - 这个项目用哪个 Milvus；
  - 用哪个 embedding provider 和 model；
  - memory 存在哪里。

**过渡语**

再往外看一层，我们还需要考虑调用外部 LLM / embedding API 的「韧性」，这部分在 compact 和 resilience 模块里。

---

### Slide 16：Resilience 与 Compact

**讲解**

当我们调用 OpenAI / Anthropic / Gemini 这类外部服务时，网络抖动、限流是常态。  
memsearch 把这部分逻辑抽象成了：

- `async_retry`：
  - 接受：
    - 要执行的异步函数；
    - 如何判断异常是可重试的；
    - 最大重试次数、基础延迟、最大延迟；
  - 内部实现指数退避，并打印统一的日志。
- `is_retryable_external_exception`：
  - 根据 HTTP status code（429/5xx 等）和异常类名，判断是否需要重试。
- `compact_chunks`：
  - 聚合所有 chunk 的内容；
  - 用传入的 prompt 模版拼出一个 Prompt；
  - 根据 `llm_provider` 调用：
    - OpenAI / Anthropic / Gemini 对应 SDK；
  - 整个调用包裹在 `async_retry` 中。

**小提示**

如果将来你们要换成自建 LLM 或代理，只需要：

- 在 `compact.py` 增加一个 provider 分支；
- 利用已有的 `async_retry`，保持重试与日志逻辑一致。

**过渡语**

到此为止，我们大致看完了「核心实现层」。接下来回到「怎么跟你们现有系统集成」这个问题上。

---

### Slide 17：memsearch 与 Agent 框架的关系

**讲解**

定位一定要讲清楚：

- memsearch **不是** 一个 Agent 框架，它不负责 chain、graph、planner 这些东西；
- 它是一个**标准化的记忆后端**：
  - 提供「语义检索」（search）；
  - 提供「记忆写入」（memory.* + Markdown）；
  - 提供「压缩与长期记忆」（compact / consolidate）。

和 LangChain、LangGraph、CrewAI 这类框架的关系是：

- 框架负责：如何规划调用顺序、如何把工具结果整合成回答。
- memsearch 负责：当框架需要「问过去的事情」时，给出高质量的记忆片段。

**过渡语**

具体到框架，我们先看大家用得比较多的 LangChain。

---

### Slide 18：与 LangChain 的集成模式

**讲解**

在 LangChain 中，我们最自然的集成方式是：实现一个 `BaseRetriever`。

- 核心代码模式：
  - 在 `_get_relevant_documents` 里：
    - 调用 `asyncio.run(self.mem.search(query, top_k=self.top_k))`；
    - 把每个结果包装成 `Document(page_content, metadata)`。
- 用法：
  - `retriever = MemSearchRetriever(mem=mem, top_k=3)`；
  - `docs = retriever.invoke("Redis caching")`；
  - 再配合 `ChatPromptTemplate` 和 LLM 组成 RAG chain。

**强调**

- 对框架来说，它就是一个普通的 retriever；
- 对我们来说，memsearch 保证了检索质量和记忆回溯能力。

**过渡语**

同样的思想也适用于 LangGraph、CrewAI 之类的框架，我们快速扫一下。

---

### Slide 19：LangGraph / CrewAI 等其它集成

**讲解**

在 LangGraph、CrewAI 等框架中，最常见的做法是：

- 定义一个工具函数，例如：

  ```python
  @tool
  def search_memory(query: str) -> str:
      results = asyncio.run(mem.search(query, top_k=3))
      ...
  ```

- 再把这个工具交给 Agent：
  - LangGraph：`create_react_agent(llm, [search_memory])`
  - CrewAI：在 Agent 的 `tools` 列表中注入。

整体模式不变：

- 业务框架决定「什么时候需要查记忆」；
- 工具函数里只做两件事：
  - 调 `mem.search`；
  - 把结果整理为对 LLM 友好的文本或结构。

**过渡语**

上面讲的是「与任意 Agent 框架的集成」。接下来我们重点看一个**深度集成用例**：Claude Code 插件。

---

### Slide 20：Claude Code 插件架构

**讲解**

Claude Code 插件可以看成是**memsearch 在 IDE 场景下的旗舰集成**。

三个核心组件：

1. **Hooks（ccplugin/hooks）**
   - `SessionStart`：会话开始时触发；
   - `UserPromptSubmit`：每次用户输入前触发；
   - `Stop`：每轮 assistant 回复后触发（异步）；
   - `SessionEnd`：会话结束时触发。
2. **Skill（ccplugin/skills/memory-recall）**
   - 一个 `context: fork` 的子 Agent；
   - 专门负责记忆检索，使用 `memsearch search / expand / transcript`。
3. **memsearch CLI**
   - 所有 Hook 最终都是在 shell 里调用 `memsearch` 命令；
   - 插件本身几乎不直接调用 Python，只是 orchestrate CLI。

**过渡语**

这套架构的精髓在于：**渐进式披露（Progressive Disclosure）**。我们用一张图把它讲清楚。

---

### Slide 21：Progressive Disclosure 三层模型

**讲解**

当 Claude 判断「需要用到历史记忆」时，它会触发 `memory-recall` skill，这个 skill 在一个 forked 子 Agent 中执行三层动作：

- L1：`memsearch search`
  - 输入用户当前问题；
  - 返回若干 chunk 片段（带 source/heading/score）。
  - 这些片段会被浓缩后作为 context 传回主对话。
- L2：`memsearch expand CHUNK_HASH`
  - 如果某个片段特别关键，需要更多上下文；
  - expand 会读取原 Markdown 文件，返回完整的 section。
- L3：`memsearch transcript path.jsonl --turn UUID`
  - 如果需要看到「完整原始对话」：
  - transcript 命令会解析 JSONL，将指定 turn 前后 N 条对话恢复出来。

整个过程有两个关键点：

1. Skill 在子 Agent 里跑，**中间产生的搜索结果、日志不会污染主对话上下文**；
2. 主对话只拿到一份最终的、经过筛选和融合的「记忆摘要」。

**过渡语**

Agent 侧的逻辑我们就说到这。最后一部分，我们聊聊实际落地时的部署、配置和排障。

---

### Slide 22：部署模式与环境规划

**讲解**

从运维角度看，memsearch 支持三种典型部署模式：

- 本地开发：
  - Milvus Lite：`~/.memsearch/milvus.db` 单文件；
  - Embedding 可以用 OpenAI，或者 local/sentence-transformers；
  - 零运维，非常适合个人或小团队开发。
- 团队测试 / 内部环境：
  - 部署一个 Milvus Server（Docker/K8s）；
  - 各项目用不同 collection 或 user_id 隔离；
  - Embedding provider 可以统一配置，便于成本控制。
- 生产环境：
  - Zilliz Cloud 提供完全托管的 Milvus；
  - 可按业务维度规划多个 cluster / collection；
  - 通过 `.memsearch.toml` 控制项目级配置。

**过渡语**

部署完之后，接下来就是「如何合理管理配置」的问题。

---

### Slide 23：配置管理最佳实践

**讲解**

建议团队在每个项目里做以下约定：

- 在项目根目录维护一个 `.memsearch.toml`，里面至少包括：
  - `milvus.uri / collection`：这个项目的索引存在哪里；
  - `embedding.provider / model`：统一该项目使用的嵌入模型；
  - `memory.base_dir`：记忆文件相对于仓库的路径（如 `memory` 或 `.memsearch/memory`）。
- API Key 坚持走环境变量：
  - `OPENAI_API_KEY / GOOGLE_API_KEY / VOYAGE_API_KEY / ANTHROPIC_API_KEY / OLLAMA_HOST` 等；
  - 不要把密钥写入 config 或代码仓库。
- 如需为某位用户/Agent 自定义 user_id：
  - 在 CLI 或 MemSearch 构造时传入 `user` / `user_id` 参数；
  - 统一使用配置或调用层控制，避免硬编码。

**过渡语**

即便配置合理，也难免会遇到各种问题。我们来过一遍常见故障和排查流程。

---

### Slide 24：常见问题与排障流程

**讲解**

几类最常见的问题：

1. **搜索没有结果**
   - 首先跑：`memsearch stats` 看当前 collection 里有多少 chunk；
   - 如果是 0，检查是否对正确的路径执行了 `memsearch index`；
   - 如果有 chunk，再用 CLI 跑：`memsearch search "某个明确存在的内容"`；
     - CLI 有结果但程序没有，多半是应用层集成的问题；
     - 两边都没结果，检查 embedding.provider 是否与索引时一致。
2. **配置不生效**
   - 使用：`memsearch config list --resolved`；
   - 确认最终生效的是不是你想要的 URI/Provider/Model；
   - 注意 CLI 参数会覆盖 `.memsearch.toml`，不要误以为是 config 被忽略。
3. **Milvus 报 embedding 维度错误**
   - 通常是：
     - 你之前用 `text-embedding-3-small` 索引了一个 collection；
     - 后来改成了另一种模型但仍在用同一个 collection。
   - 解决：
     - 要么换一个新的 collection 名字；
     - 要么 `memsearch reset --yes` 后重建索引。

**过渡语**

此外，在 Claude Code 插件场景下，还有一套专门的排障路径。

---

### Slide 25：Claude Code 插件排障

**讲解**

在 Claude Code 里，memsearch 插件的状态主要体现在三处：

1. **SessionStart 状态行**
   - 每次开 session 时，会在上方打印类似：
     - `[memsearch v0.1.11] embedding: openai/text-embedding-3-small | milvus: ~/.memsearch/milvus.db`
   - 如果看到：
     - `ERROR: OPENAI_API_KEY not set` 之类的提示；
     - 说明 embedding provider 对应的 key 没配置好。
2. **Debug 日志**
   - 用 `claude --debug` 启动；
   - 在 `~/.claude/logs/` 里可以看到每个 hook 返回的 JSON；
   - 重点看 `systemMessage` 和 `additionalContext` 字段。
3. **Memory 文件**
   - 查看 `.memsearch/memory/YYYY-MM-DD.md`：
     - 是否在每个 session 结束后都有新增的 `## Session HH:MM` 和若干 bullet；
   - 如果只有 Session 标题没有内容：
     - 多半是 `stop` hook 内部的 `claude -p` 调用失败；
     - 需要检查 `claude` CLI 是否可用、模型和 key 是否正确。

**过渡语**

到这里，关于 memsearch 的功能、架构、集成与运维，我们已经完整走了一圈。最后一页做个简短总结。

---

### Slide 26：总结与下一步

**讲解**

收个尾，我们回顾一下今天的三个关键点：

1. **架构理念**  
   - Markdown 是唯一事实源，Milvus 只是可重建的索引；
   - 与 OpenClaw 保持高度兼容，可以复用现有记忆资产。
2. **工程实现**  
   - 核心类 `MemSearch` 串联 scanner → chunker → embeddings → MilvusStore；
   - Hybrid Search + RRF 确保检索质量；
   - config / compact / resilience / watcher 等模块支撑多环境稳定运行。
3. **落地实践**
   - CLI + Python API 让你们很容易把 memsearch 接入现有服务；
   - Claude Code 插件是一个完整的端到端示范；
   - 与 LangChain / LangGraph / CrewAI 等框架的集成模式统一简单。

**下一步建议**

- 选一个你们正在维护的项目，真实跑一遍：
  - 在仓库里加 `memory` 目录；
  - 把关键 ADR、会议纪要迁过去；
  - 配好 `.memsearch.toml` 和 Claude 插件；
  - 让 Agent 在这个项目上「带着记忆」工作一段时间。
- 后续如果你们在接入或二次开发中遇到具体问题，可以基于今天讲的模块划分快速定位到对应文件和逻辑。

**结尾语**

今天的内容稍微有点多，尤其是实现细节这块。  
大家可以先从「CLI + Python API + Claude 插件」这三块动手试一试，遇到具体问题再回到对应源码模块查一查。  
下面我们预留一段时间做 Q&A，欢迎大家就「如何集成到你现在的项目」或者「内部实现细节」提问。