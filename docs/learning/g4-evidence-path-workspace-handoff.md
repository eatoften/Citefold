# G4 证据路径工作区学习交接

- **产品状态：** 可操作的 Path -> Relation -> Source 纵向切片已实现
- **阶段状态：** G4 仍在进行；公开课程质量、性能和自动化 E2E 尚未完成
- **个人掌握：** Draft / M0；需要亲手画图和修改后才能提升
- **主文档：**
  [Concept Graph Evidence Workspace](../modules/concept-graph-evidence-workspace.md)

## 一句话理解

前端负责让用户选择问题和展示结果，后端负责决定图谱版本、路径顺序和证据能否
安全回源。浏览器不能拿 Locator 自己拼本地文件 URL。

```text
course
-> current GraphVersion
-> backend BFS / Trace / Kahn result
-> inspect one immutable Relation
-> composite evidence identity
-> server resolver
-> CitationInspector
-> exact Source location or snapshot-only fallback
```

## 你需要能指到的代码

| 层 | 文件 | 责任 |
| --- | --- | --- |
| Studio integration | `frontend/src/App.tsx` | lazy-load 工作区，并把 graph evidence 接到通用 inspector |
| Workspace | `frontend/src/features/concept-graph/ConceptGraphWorkspace.tsx` | 四种视图、异步状态、选择与展示 |
| Typed API | `frontend/src/features/concept-graph/conceptGraphApi.ts` | exact-version 请求和结构化错误 |
| Shared inspector | `frontend/src/features/citations/CitationInspector.tsx` | Source 预览、上下文、降级和焦点恢复 |
| HTTP boundary | `backend/app/main.py` | loopback-only target/content routes |
| Immutable lookup | `backend/app/concept_graph_publication_store.py` | 用复合身份从 GraphVersion 还原 evidence snapshot |
| File trust | `backend/app/citation_target_service.py` | Source/Chunk/Locator/file currentness 与安全文件读取 |

## 为什么不是“前端拿到 Locator 就直接打开”

Locator 是证据地址，不是访问授权。若浏览器用其中的 `asset_id` 或文件字段拼 URL，
就会绕过课程隔离、受管目录、文件 hash、Source 漂移和 no-follow 检查。当前设计让
前端只提交不可变图谱中的复合身份，服务端重新查证后才返回可访问的媒体 URL。

这也是为什么 Graph 和 Chat 共用 `CitationInspector`，却不共用数据库主键：两者
都是 Source evidence，但一个归属于聊天引用，一个归属于特定 GraphVersion 的
Concept/Relation evidence。

## React 异步问题

只调用 `abort()` 还不够。网络库或 mock 可能仍然完成 Promise，因此组件同时使用：

```text
AbortController  -> 尽早取消 I/O
request epoch    -> 拒绝迟到结果写入当前 state
response identity -> 拒绝错误 course/version/content_hash
```

实际验收发现，最初 cleanup 只取消 Graph 请求，没有取消 Path 请求。切换离开
Explore 后，迟到 Path 仍可能更新状态。最终修复在 unmount/course change 时同时
abort controller 并推进 epoch。

## Source 漂移为什么保留 quote 却拒绝 content

GraphVersion 中的 quote、Chunk hash 和 Locator 是不可变历史证据。当前 Source
可能被重新解析、移动或修改。此时删除历史 quote 会破坏审计，继续打开当前文件
又可能把旧引用指向新内容。因此：

- `/target` 返回 `snapshot_only`，仍显示保存的 quote；
- `/content` 返回 `409`，拒绝把不一致的当前文件冒充原证据。

损坏的当前 Locator 也按这个规则降级，不能成为 `500` 或绕过校验。

## 面试时要能回答

1. 为什么 Path API 必须带 exact GraphVersion，而不是只传 course？
2. 为什么前端必须原样消费服务器顺序，不能自己再跑一次 BFS？
3. `unreachable` 与 `limits_reached` 的产品语义有什么不同？
4. Graph evidence 为什么复用 CitationInspector，却需要独立 resolver path？
5. 如何防止快速切换课程时 A 课程的迟到响应覆盖 B 课程？
6. 为什么 SQLite + 内存图算法目前比引入 Neo4j 更合理？什么测量会改变结论？

## 你的亲手任务

学习阶段不要重写模块。先完成下面两件事：

1. 闭卷画出 `GraphVersion -> path -> relation evidence -> Source` 数据流，并标出
   哪些值来自不可变快照、哪些值必须与当前 Source 比较。
2. 在指导下做一个小改动，例如为 Trace 增加关系类型选择；要求只改 typed API、
   UI control 和一个用户行为测试，并能解释为何算法仍由后端负责。

完成前，产品可以是“已实现”，但个人掌握仍然保持 M0。
