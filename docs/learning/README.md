# Technical-Stack Learning Notebook

Last updated: 2026-08-08

这套笔记用于让项目维护者最终能够独立解释、修改、调试和重建 Video
Course Cards 的关键纵向系统。它不是另一本产品路线图，也不是把框架文档
复制一遍。

- [roadmap.md](../roadmap.md) 记录产品先做什么；
- [project-mastery-plan.md](../project-mastery-plan.md) 记录掌握门槛；
- [productization-log.md](../productization-log.md) 记录已经完成的工程事实；
- 本目录记录“技术是什么、项目在哪里使用、为什么这样选、如何失败、如何
  测试，以及维护者是否真的会”。

## 使用规则

1. **代码是当前事实，笔记是可修订解释。** 每篇笔记必须链接真实代码、测试、
   ADR 和 commit，不能只写通用八股。
2. **Codex 写出的内容默认是 Draft。** 维护者闭卷复述、完成亲手修改并通过验收
   后，才可以把对应掌握等级从 M0/M1 提升。
3. **产品完成度和个人掌握度分开。** 已经有 681 个后端测试并不代表维护者会
   设计其事务；能背出 BFS 也不代表图谱证据链已经实现。
4. **只记录有证据的技术。** 不为了简历加入 Kubernetes、微服务、Neo4j 或云
   架构；只有真实实现或测量后才能写进技术栈。
5. **每次学习必须留下一个 ownership artifact。** 可接受形式包括数据流图、
   ER 图、状态机、测试、Bug 修复、性能记录、失败分析或用户独立 commit。

掌握等级沿用 [M0-M4](../project-mastery-plan.md#mastery-levels)：

```text
M0 未评估
M1 能解释与画图
M2 能在指导下修改并补测试
M3 能独立实现和跨层调试
M4 能比较方案、量化取舍并完成 system-design defense
```

## 项目技术栈地图

版本号以当前 lockfile 为准；表格关注责任边界，而不是把依赖名堆在简历上。

| 领域 | 当前技术 | 在项目中的责任 | 必须掌握的核心问题 | 代码入口 | 当前掌握 |
| --- | --- | --- | --- | --- | --- |
| 版本与交付 | Git, GitHub, conventional commits | 分支、阶段 commit、remote equality、release history | fast-forward/merge/rebase 区别；如何验证推送的是同一提交；如何回滚 | [release workflow](../../.github/workflows/windows-desktop-release.yml), [engineering log](../productization-log.md) | M0 |
| 后端语言 | Python 3.11 | 领域模型、服务、store、任务、实验 | 类型边界、异常翻译、依赖注入、context manager、可测试纯函数 | [main.py](../../backend/app/main.py), [pyproject.toml](../../backend/pyproject.toml) | M0 |
| HTTP/API | FastAPI, Pydantic | 本地 typed REST 边界、验证、错误码、OpenAPI | route/schema/service/store 分层；404/409/422/500；幂等请求 | [main.py](../../backend/app/main.py), [course_source.py](../../backend/app/course_source.py) | M0 |
| 数据库 | SQLite, Python `sqlite3` | 本地 source of truth | keys、FK、unique、index、query plan、transaction、`BEGIN IMMEDIATE`、CAS | [db.py](../../backend/app/db.py), [migrations.py](../../backend/app/migrations.py) | M0 |
| 数据迁移 | additive schema migrations | 旧工作区升级且不丢用户数据 | clean install vs upgrade、备份、失败原子性、兼容读取 | [migrations.py](../../backend/app/migrations.py), [migration tests](../../backend/tests/test_source_migrations.py) | M0 |
| 原始材料解析 | pypdf, python-pptx, python-docx, text decoding | PDF 页、PPT 页、DOCX 段落、文本 section 转 locatable units | parser failure、空页、编码、稳定 locator、derived data | [source_asset_parser.py](../../backend/app/source_asset_parser.py) | M0 |
| 视频与字幕 | FFmpeg/ffprobe, faster-whisper | 验证视频、抽取音频、生成时间戳字幕 | subprocess、codec、时间轴、ASR 分段、取消、资源限制 | [video_pipeline.py](../../backend/app/video_pipeline.py), [transcription.py](../../backend/app/transcription.py) | M0 |
| Canonical Sources | Pydantic + SQLite projection | 将视频、字幕、文档和已发布 Note 统一成 Source/Chunk/Locator | stable ID、hash、projection、incremental refresh、Source-first authority | [course_source.py](../../backend/app/course_source.py), [course_source_service.py](../../backend/app/course_source_service.py) | M0 |
| Embedding | sentence-transformers `all-MiniLM-L6-v2`, NumPy | Source/Card 向量表示与 cosine retrieval | token/chunk 粒度、归一化、dimension/model identity、batching、cache invalidation | [embedding.py](../../backend/app/embedding.py), [source_index_service.py](../../backend/app/source_index_service.py) | M0 |
| 本地模型 | Ollama-compatible HTTP, Qwen | Grounded Chat、卡片、Study、关系候选生成 | prompt/structured output、temperature、timeout、model identity/name、拒答、不能把输出当真值；model digest 属于后续实验可复现要求 | [llm_client.py](../../backend/app/llm_client.py), [card_service.py](../../backend/app/card_service.py) | M0 |
| Retrieval/RAG | Dense retrieval, BM25/RRF/graph research baselines | 找证据、构造上下文、回答或拒答 | Recall@k/MRR/nDCG、ranking vs coverage、context budget、leakage、ablation | [source_search_service.py](../../backend/app/source_search_service.py), [rag_lab](../../backend/rag_lab/README.md) | M0 |
| Grounded Chat | FastAPI/Pydantic/SQLite/local LLM | 多轮会话、Source scope、idempotency、终态与引用 | 状态机、bounded history、concurrent retry、abstention、failure state | [chat_service.py](../../backend/app/chat_service.py), [chat tests](../../backend/tests/test_chat_service.py) | M0 |
| 引用与信任边界 | immutable citation snapshots + server resolver | 句子回到视频时间/PDF 页/段落并处理文件变化 | quote/hash/locator、snapshot、path trust、degraded historical citation | [citation_target_service.py](../../backend/app/citation_target_service.py), [citation tests](../../backend/tests/test_citation_targets.py) | M0 |
| 可靠任务 | persisted task state machine | 解析/索引/生成的 retry、cancel、restart recovery | reservation、idempotency、atomic publication、worker bound、crash injection | [reliable_task_manager.py](../../backend/app/reliable_task_manager.py), [task tests](../../backend/tests/test_reliable_task_manager.py) | M0 |
| 本地数据恢复 | SQLite backup API, Trash, drafts | autosave、撤销、删除恢复、完整备份/恢复 | snapshot consistency、managed files、write-ahead fence、restart ownership | [workspace_backup.py](../../backend/app/workspace_backup.py), [backup tests](../../backend/tests/test_workspace_backup.py) | M0 |
| 学习调度 | FSRS | 独立 ReviewItem 的间隔重复 | retrievability vs memory、scheduler state、rating transition、测试时钟 | [review_service.py](../../backend/app/review_service.py) | M0 |
| 前端 | React 19, TypeScript 6, Vite 8 | Sources/Chat/Studio UI 与异步状态 | component boundary、effect cleanup、AbortController、stale response、typed routes | [App.tsx](../../frontend/src/App.tsx), [navigation contract](../../frontend/src/features/navigation/appRoute.ts) | M0 |
| 前端测试 | Vitest, Testing Library, jsdom | 组件行为、路由、竞态和基础语义/键盘行为回归 | user-observable assertions、mock boundary、fake timers、Strict Mode；真实浏览器 E2E 与完整 accessibility audit 仍是缺口 | [vitest.config.ts](../../frontend/vitest.config.ts), [Graph tests](../../frontend/src/GraphView.test.tsx) | M0 |
| 当前图探索 | react-force-graph-2d + CardRelation | similarity/manual/model-assisted Card 图 | force layout 只是视图；Card 不等于 Concept；候选边不等于事实 | [GraphView.tsx](../../frontend/src/GraphView.tsx), [card_relation_service.py](../../backend/app/card_relation_service.py) | Session 1 |
| 目标概念路径 | relational graph + BFS/DFS/Kahn | evidence-grounded Concept、关系寻踪、前置拓扑层 | graph version、证据有效性、并发无环发布、确定性、复杂度 | [ADR-0008](../decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md) | Session 1 |
| 桌面运行时 | Tauri 2, Rust, PyInstaller | Windows 包装、本地 backend sidecar 生命周期 | process identity、port ownership、shutdown、managed paths、locked build | [backend.rs](../../frontend/src-tauri/src/backend.rs), [Cargo.toml](../../frontend/src-tauri/Cargo.toml) | M0 |
| Topic 聚类 | scikit-learn `AgglomerativeClustering` | 从 Card 提议可编辑 Topic 分组 | feature scaling、distance threshold、cluster stability、proposal 不是真值 | [topic_suggestion_service.py](../../backend/app/topic_suggestion_service.py) | M0 |
| ML/多模态实验 | PyTorch, ONNX Runtime, RapidOCR | OCR/CTC 与 RAG 的隔离、可复现实验 | split、artifact hash、overfit gate、metrics、negative result、product/research boundary | [multimodal lab](../../backend/multimodal_lab/README.md), [RAG study](../RAG%20retrieval%20and%20graph%20study.md) | M0 |

### 版本与所有权来源

- Python 直接依赖及允许版本来自
  [backend/pyproject.toml](../../backend/pyproject.toml) 和 `uv.lock`；
- React/TypeScript/Vite/Vitest 来自
  [frontend/package.json](../../frontend/package.json) 和 `package-lock.json`；
- Tauri/Rust 依赖来自
  [Cargo.toml](../../frontend/src-tauri/Cargo.toml) 和 `Cargo.lock`；
- FFmpeg/ffprobe、Ollama-compatible server 和本地模型属于 external runtime，
  不是 Python/Node 包；必须分别检查可执行文件、模型身份和失败状态；
- RapidOCR 可能在内部使用其他库，但只有仓库直接声明和实际调用的依赖才
  能作为本项目主动掌握/使用的技术栈来陈述。

### 当前没有使用或尚未达到的技术

以下名称不能为了简历好看而加入当前技术栈：

- PostgreSQL、Redis、Celery、Kafka；
- Neo4j、专用 vector database；
- Docker/Kubernetes、微服务、云端认证或多租户 SaaS；
- React Router（项目使用自有 typed query-route/history contract）；
- 直接 OpenCV/`cv2` 依赖；
- Playwright 自动化浏览器 E2E；
- 常规 PR/push 全栈 CI；
- Ruff/mypy 后端静态质量门。

其中一部分是有意识的当前设计（例如 SQLite 而不是 Neo4j），另一部分是
P1.4/G4 的真实工程缺口（例如 change-level CI 和自动化 E2E）。面试时必须
能区分“没有必要使用”和“尚未完成”。

## 深挖顺序

不是按框架名逐个背，而是按五个纵向系统学习：

```text
1. Source projection and incremental indexing
   parser -> Source/Chunk/Locator -> hash/version -> embedding index

2. Grounded Chat and abstention
   conversation -> retrieval -> bounded evidence -> answer/refusal -> citation

3. Citation snapshot and trust boundary
   answer sentence -> immutable quote/hash/locator -> server resolver -> Source

4. Reliable tasks and recovery
   reserve -> run -> checkpoint -> publish/cancel/fail -> restart recovery

5. Evidence-grounded Concept graph
   Source revision -> evidence -> reviewed draft -> graph version -> traversal
```

它们覆盖项目最有价值的共同技术：API、SQL、事务、状态机、并发、React
异步、IR/RAG、人机协同标注、评估与图算法。

## 每篇技术笔记模板

后续专题笔记都采用相同结构：

1. **一句话 mental model**：这个组件负责什么，不负责什么；
2. **真实代码入口**：route、schema、service、store、UI 和测试；
3. **数据流/状态机/ER 图**：由维护者先画；
4. **关键不变量**：什么情况绝不能发生；
5. **设计选择与 alternatives**：为什么不用另一个方案；
6. **失败与调试**：至少一个实际或注入的 failure case；
7. **性能与复杂度**：测量什么，不能凭空声称什么；
8. **亲手修改**：文件、diff、测试、commit；
9. **闭卷问题**：第二天和一周后仍能回答；
10. **复习记录**：日期、掌握等级、仍然模糊的点。

## 复习节奏

每个 Session 完成后按以下节奏复习，而不是重复阅读全文：

| 时间 | 动作 | 通过条件 |
| --- | --- | --- |
| 当天 | 画图、亲手改、口述一次 | 能解释 happy path 和一个 failure path |
| +1 天 | 不看笔记重画核心数据流 | 实体、方向和 source of truth 正确 |
| +3 天 | 回答闭卷问题并读一次真实代码 | 能指到 route/service/store/test |
| +7 天 | 做一个小变体或 Bug 测试 | 能预测受影响层并通过测试 |
| +14 天 | 5 分钟 system-design defense | 能比较至少一个 alternative |
| +30 天 | 面试式复盘 | 能陈述指标、限制和个人贡献 |

不要为了维持日期伪造“已复习”。错过后从当前理解继续，并记录真正的
薄弱点。

## Session 索引

| Session | 主题 | 产品阶段 | 目标 | 状态 |
| --- | --- | --- | --- | --- |
| [Session 1](session-01-source-card-graph-contract.md) | Source、Card、Topic、Concept 与当前/目标图数据流 | G0 | M0 -> M1 | Started |
| Session 2 | additive graph migration 与 ER/transaction contract | G1 | M1 -> M2 | Planned |
| Session 3 | Concept API vertical slice 与 course isolation | G1 | M1 -> M2 | Planned |

## 专题深挖

专题笔记跟随真实模块实现，但不自动提升个人掌握等级：

- [可复现、可审计的 Source Slice](deep-dive-reproducible-source-slices.md)
  （G0.2b，Draft/M0）：canonical bytes、Git provenance、public/private
  boundary、historical authority 与 replay readiness。

## 复习总账

只在维护者实际完成后追加，不预填完成状态：

| Date | Topic | Closed-book result | User-owned artifact | Tests/commit | Mastery | Next review |
| --- | --- | --- | --- | --- | --- | --- |
