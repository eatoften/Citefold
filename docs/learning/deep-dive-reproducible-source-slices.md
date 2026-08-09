# Deep Dive: 可复现、可审计的 Source Slice

- **状态：** Draft / M0（代码已实现，不代表维护者已掌握）
- **产品阶段：** G0.2b
- **对应模块：** [Golden Graph Source-Slice Builder](../modules/golden-graph-source-slice-builder.md)
- **设计决策：** [ADR-0009](../decisions/ADR-0009-deterministic-redacted-source-slice.md)

## 一句话 mental model

Source slice 不是“把 PDF 切几段文本”，而是把一个有许可证的精确原件，经过
固定代码、配置和环境，转换为可定位的私有 `CourseSourceChunk`，同时只发布
不含原文的 hash/locator 证据叶；以后任何实验结果都必须能回答“从哪些字节、
用哪版代码、为何得到这些 chunks”。

## 数据流和信任边界

```text
tracked manifest + draft protocol             [PUBLIC AUTHORITY]
                 |
                 v
ignored exact PDF ---------------------------- [PRIVATE ORIGINAL]
                 |
       clean Git commit + verified tool bytes
                 |
                 v
isolated PDF parse -> normalized pages -> UTF-8 windows
                 |                     |
                 |                     +-> CourseSourceChunk + text [PRIVATE]
                 |
                 +-> catalog/chunk hashes/locators/summary          [PUBLIC]
                                      |
                                      v
                         frozen historical authority
                                      |
                    explicit current replay-readiness gate
```

必须分清三类证明：

1. **Integrity（完整性）**：JSON 与 `.sha256` sidecar 是否一致。文件可以自洽，
   但仍可能是攻击者自己编造的，因此 sidecar 不是 trust root。
2. **Historical authority（历史权威）**：协议、公开叶和 recorded Git commit
   内的精确 blobs 是否共同闭合。未来代码升级不应抹掉它。
3. **Replay readiness（当前可重放）**：今天的 tracked closure、Python、Unicode、
   `pypdf`、lockfile、clean worktree 和私有原件是否仍满足旧协议。它是瞬时能力，
   不是永久状态；只有真正 rebuild 后比较 hash 才证明输出相同。

V1 对第三方依赖验证的是 exact lockfile、声明版本、已安装版本和我们自己的 parser
adapter bytes，并不是对整个已安装 wheel/site-packages 的密码学证明。更强的供应链
复现需要从 lock 在隔离环境重建并校验分发物；面试时不能把版本检查说成 byte-level
attestation。

## 关键技术与项目入口

| 技术 | 在这里解决的问题 | 真实入口 |
| --- | --- | --- |
| Pydantic strict/frozen DTO | 拒绝缺字段、额外字段、错误类型和可变 authority envelope | `backend/golden_graph/bindings.py`, `schemas.py` |
| canonical JSON + SHA-256 | 让相同语义产生相同公开 bytes；拒绝覆盖冲突 | `canonical_io.py` |
| Git commit/blob provenance | 保存执行闭包的历史身份，区分历史读取与当前 replay | `protocol.py` |
| subprocess + `python -I` | 把不可信 PDF parser 与主进程隔离并设置 deadline | `source_slice_builder.py` |
| UTF-8 byte offsets | 给 hash、覆盖校验和 PDF 页 locator 一个精确单位 | `source_slice_builder.py` |
| NFKC + LF normalization | 固定 semantic bytes；代价是可能折叠展示差异 | parser config/implementation artifacts |
| ignored canonical private envelope | 让 G2 能恢复 Source text，但不把课件正文提交到 Git | `private_projection.py`, `source_slice_builder.py` |

## 不能破坏的不变量

- CLI 或 LLM 不能临时挑页；page scope 只来自预注册 protocol。
- 选中页的 Chunk locator 并集必须覆盖完整 normalized page bytes，不能只“碰到”
  一小段就声称已覆盖。
- parser/chunker 必须执行已经验证过的那份 bytes，不能 hash A、执行 B。
- public DTO、CLI receipt、异常和 `repr` 不能泄露原文、私有路径或私有 hash。
- private envelope 的 sidecar 只证明完整性；writer/loader 还必须接收外部 protocol、
  build-spec 和三个公开叶身份。
- `FrozenProtocolAuthority` 不能暗示当前机器可 replay；只有更强的
  `ReplayReadyFrozenProtocolAuthority` 可以表达这个瞬时能力。
- 模型输出、自动 cards 和自动 relations 都不能成为 human gold。

## 为什么不用更“高级”的方案

- **没有上容器/Kubernetes：** 当前是本地桌面单用户项目；`python -I` 和超时能
  限制常见失败，但不是 hostile-input sandbox。需要处理真正敌意文件时，再用
  OS/container resource controls，不能在简历中提前声称。
- **没有把课件正文提交到 benchmark：** 上游许可允许使用不等于所有嵌入图和
  slide body 都适合二次分发。公开 hash/locator，维护者按 manifest 重新获取原件。
- **没有按 tokenizer 切块：** tokenizer 会引入模型版本依赖；v1 用 code-point-safe
  UTF-8 window，优先获得可检查的 byte coverage。后续可以做 tokenizer ablation。
- **没有直接写产品数据库：** G0.2 先证明可复现 projection；直接 insert 会绕过
  product Source generation/currentness transaction。数据库发布是独立 checkpoint。

## 实际失败案例

最关键的一次红队发现是：历史 loader 复用了当前 publication gate。这样第二天
正常重构 builder，就会导致昨天 frozen protocol 无法读取。修复方法不是放宽所有
校验，而是拆成两种 authority：历史层从 recorded commit 读取 bounded Git blobs；
replay 层才比较 live worktree、runtime 和私有原件。回归测试构造 C1 build、C2
publish、C3 code evolution，要求历史读取成功、replay readiness 失败。

## 复杂度与测量

- PDF parse 和 page normalization 对输入字节/页文本近似线性；实际耗时主要来自
  `pypdf` 和 PDF 结构，而不是 SHA-256。
- page-local sliding windows 对 normalized UTF-8 bytes 近似 O(n)；overlap 会增加
  常数和 Chunk 数。
- Git provenance 校验只读取固定闭包和少量配置 blobs，不扫描整个仓库。
- 当前测试证明契约、失败原子性和可复现机制，不证明 Concept/Relation accuracy。
  准确率必须等 human gold 与冻结 evaluation bundle 完成后再计算。

## 维护者亲手任务（完成前保持 M0）

1. 不看本文，画出 public/private boundary 和两种 authority。
2. 在测试中新增一种 dirty-worktree case（例如 staged-but-uncommitted config），
   让 `require_current_replay_readiness` 拒绝它。
3. 解释为什么“JSON + 自己的 sidecar”不能授权 private materialization。
4. 运行一次真实 CS336 replay，记录 catalog/chunk hash；修改一个不相关文档并
   commit，预测哪些 hash 应相同、summary 为什么可能不同。
5. 亲手提交上述测试或一个真实 Bug fix，作为 ownership artifact。

## 闭卷问题

1. 为什么 raw PDF SHA 相同仍不足以定义 semantic chunks？
2. NFKC、UTF-8 offsets 和 tokenizer offsets 的取舍是什么？
3. 为什么 historical authority 不应该比较当前 builder 文件？
4. 为什么 replay-ready receipt 仍不能声称 replay 已成功？
5. 如果 catalog hash 相同但 build-summary hash 不同，可能是哪类 provenance 变化？
6. parser snapshot 关闭了哪个 TOCTOU 问题？还有什么进程隔离限制没有关闭？

通过条件：能闭卷回答六题、画对数据流、完成一个测试/修复，并指出实际代码入口。
