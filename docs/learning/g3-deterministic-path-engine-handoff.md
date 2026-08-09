# G3 确定性路径引擎学习交接

- **产品状态：** 后端 Local / Trace / Learning 纵向切片已实现
- **个人掌握：** Draft / M0；读完文档不等于会独立实现
- **主文档：**
  [Concept Graph Path Engine](../modules/concept-graph-path-engine.md)

## 一句话理解

路径引擎不“猜”学习路径。它从一个经过审核、不可变、证据仍然有效的
GraphVersion 中，用确定性图算法算出结果，再把每一步的原始证据一起返回。

```text
Published GraphVersion
-> 同一事务内校验并读取 Concept / Relation / Evidence
-> active + Source-current authority gate
-> adjacency
-> BFS 或 prerequisite closure + Kahn
-> 稳定顺序 + result_hash
```

## 你需要能指到的代码

| 层 | 文件 | 责任 |
| --- | --- | --- |
| HTTP | `backend/app/concept_graph_path_api.py` | query validation 和 HTTP error translation |
| Service | `backend/app/concept_graph_path_service.py` | 读取指定版本、检查 authority、翻译领域异常 |
| Store boundary | `backend/app/concept_graph_publication_store.py` | 在一个 SQLite read snapshot 中校验并装载完整图 |
| Algorithms/DTO | `backend/app/concept_graph_path.py` | adjacency、BFS、Kahn、顺序和 hash |
| Tests | `backend/tests/test_concept_graph_path*.py` | 算法反例与真实 API/SQLite 集成 |

旧的 `card_relation_service.py` 和 `GraphView.tsx` 不是权威路径实现。它们是
Card 发现图，可能包含 suggested/hidden 边，也没有 GraphVersion 和逐边证据。

## 三个算法到底在算什么

### Local

从 root 做 N-hop BFS。visited 在节点第一次入队时写入，因此环不会让节点
重复。`direction_mode` 决定能否沿一条边扩展；返回的 Relation 保留数据库中
原来的方向。节点上限只按稳定顺序截断，并明确返回截断标记。

### Trace

BFS 保证无权图中的最少 hop。多个同长路径存在时，不让数据库返回顺序或
Python set 顺序决定答案，而是依次使用 relation type priority、neighbor ID、
Relation ID 和 traversal orientation 破平局。

要区分三种结果：

- `found`：找到最短路径；
- `unreachable`：整个允许的 frontier 都走完了；
- `limits_reached`：被 hop/node bound 停下，不能断言真的不可达。

### Learning

`A --prerequisite--> B` 表示先学 A 再学 B。以 B 为目标时，要沿 incoming
prerequisite 边反向收集所有祖先，再在这个闭包上做 Kahn 拓扑排序。
layers 表示真正的先后约束；linearization 只是对互不依赖节点给出一个稳定
展示顺序，不是唯一最佳教学顺序。

如果闭包超过上限，系统拒绝返回半条路径。少掉一个前置知识仍然返回“成功”
会破坏这个功能最重要的不变量。

## 五个面试关键点

1. **为什么不用 LLM 直接生成路径？** LLM 可以解释结果，但不能保证最短、
   无环、满足所有 prerequisite，也不能保证重复请求顺序相同。
2. **为什么必须指定 GraphVersion？** 路径的节点和边必须绑定同一份不可变
   事实快照，否则审核或 Source 更新时一次请求可能读到两套图。
3. **incoming/outgoing 是相对什么？** 相对存储方向；对称关系没有语义方向，
   两种模式都可以双向走。
4. **为什么 bounded miss 不等于 unreachable？** 搜索被上限截断时，未访问
   frontier 后面仍可能存在路径。
5. **为什么 SQLite 足够？** 当前规模下，关系表负责事务和完整性，内存邻接表
   负责遍历；没有测量证据证明需要 Neo4j。

## 当前真实技术债

每次请求目前都会完整校验、再次 hydrate，并重新建立 adjacency，没有跨请求
cache。因此只能说算法和 API 已实现，不能说已经通过 1k/10k P95 性能门槛。
G4 还需要：

- 把 Explore 改造成 Overview / Local / Trace / Learning；
- 实现 server-owned Graph Evidence resolver，不能让前端用 `asset_id` 拼 URL；
- 做窄屏、键盘、stale/unreachable/limits UI 和真实浏览器 E2E；
- 在真实人工 gold 完成后，才做公开课程 path/locator 质量评测。

G2.4 的 Git 72 小时 readiness 工具被明确延后。这是产品优先级纠偏，不是
删除人类 gold：先把用户可见的路径闭环做好，再回到 Pass B 和最终评测。

## 你的亲手任务

不要重写整个模块。完成下面任意一个小任务并能逐行解释，才开始从 M0 向 M1/M2
移动：

1. 不看实现画出 `GET -> service -> snapshot -> BFS/Kahn -> DTO` 数据流。
2. 手写一个 6 节点图，分别预测 outgoing、incoming、both 的 Trace。
3. 新增并修复一个小测试：零 hop、node bound、对称边或并列最短路径任选一个。
4. 解释一次 Source drift 为什么返回 `409`，而未知 Concept 为什么返回 `404`。

闭卷检查：BFS 为什么在入队时标记 visited？Kahn 如何发现环？result hash 为什么
必须包含 graph content hash 和 normalized request？为什么 Learning 不能截断后仍
返回成功？
