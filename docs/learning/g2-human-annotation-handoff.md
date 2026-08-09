# G2 学习交接：从可复现 Source 到真实人工 Concept 清单

- **产品阶段：** G2.1
- **状态：** Draft / M0（工具已实现，不代表维护者已经掌握或完成标注）
- **模块文档：**
  [Golden Graph Human Annotation Workflow](../modules/golden-graph-human-annotation-workflow.md)
- **设计决策：**
  [ADR-0010](../decisions/ADR-0010-staged-human-gold-and-key-control-attestation.md)
- **标注规则：**
  [已被 G0.2 hash 绑定的 Graph Annotation Protocol](../graph-annotation-protocol.md)

## 一句话 mental model

G2.1 不是让程序“生成 gold”，而是搭一座可信的交接桥：程序固定 Source
和表格结构，项目先在 Git 历史中注册 reviewer public key，真实的人做语义判断，
程序再把私有原文证据解析成公开的定位/hash，最后用外部密钥证明“那把预先注册
的 key 批准了这组 bytes”。这些证明能力不同，不能混成一句“人工 gold 已验证”。

## 你必须能画出的 artifact DAG

```text
Frozen Protocol + Private Source Materialization
        + ReviewerKeyPolicyAuthority (prior Git commit)
                    |
                    v
          Private Mutable Worksheet
                    |
             人工 Concept 决策
                    |
                    v
 Inventory Candidate + Alias Candidate + Seal Request
                    |
          人工检查 + 外部 SSH 签名
                    |
                    v
 Public Inventory / Alias / Request / Attestation / Seal / Pair Manifest
                    |
                    v
     这里只是 Concept authority，不是 GoldBundle
```

闭卷时要能解释每一条边为什么存在：上游 protocol 防止换数据；private Source
让 quote 可以真实回源；seal request 固定准备签署的精确 bytes；attestation 绑定
key；pair manifest 固定后续 Relation Pass A/B 的完整问题空间。

## 六个命令分别在做什么

1. `prepare-reviewer-key-policy`：把 public allowed-signers line 变成 fixed-path
   candidate 和 sidecar；只准备文件，不签发 authority。
2. `verify-reviewer-key-policy`：用 full registration commit 验证 local policy
   与可达历史 blob 完全一致，签发 repository policy authority。
3. `init-concepts`：只有 frozen Source 和 prior-commit reviewer policy 都验证后，
   才创建一次空白 private worksheet；不覆盖旧文件。
4. `prepare-concept-seal`：要求同一 policy 和 worksheet 已完整，由真实 Source quote 计算公开
   byte span/hash，并生成三个待检查候选文件。
5. `seal-concepts`：从 registered policy 内部取得 allowed-signers bytes，重新推导
   并比对候选文件，验证外部 SSH signature，生成并 reload 六个公开叶子。
6. `verify-concepts`：从磁盘重新加载整张 DAG，验证 hash、推导关系、Source
   绑定、签名和完整 pair universe。

真实 CS336 reviewer policy 尚未在先前 commit 注册，因此 hardened workflow
还没有接受一个真实 worksheet。pre-policy 的空 draft 含 0 个 candidate/label，
但 schema 不兼容；维护者确认后必须手动移除，并在 policy 注册后重新初始化。
后续命令更不能凭空让它变成 gold。

## 最容易在面试里说错的三件事

### 1. “签名证明是我本人做的标注”——错

SSH signature 只证明 allowed-signers 文件中的 key 控制者签过这份 canonical
request。`ReviewerKeyPolicyAuthority` 进一步证明这把 public key 已在标注前进入
可达 Git 历史，而不是签名时临时自我授权。两者仍不证明 key 控制者是人、不证明
真实身份，也不证明标注时没有看模型答案。本项目当前是 local self-attested
workflow，必须诚实这样说。

### 2. “ConceptInventorySeal 就是 golden graph”——错

它只固定 `C_gold` 候选清单、alias 和完整 pair universe。Relation 的 Pass A/B、
adjudication、evidence 和最终 `GoldBundleSeal` 尚未发生，因此没有完整 gold graph。

### 3. “测试通过，所以 Concept 是正确的”——错

测试只能验证 schema、lineage、hash、quote 能否回源、pair 是否穷举、signature
是否匹配，以及 tamper 是否被拒绝。Concept 划分和定义是否合理，仍是你的语义
责任。

## 技术栈复习卡

| 技术 | 要会解释的问题 | 代码入口 |
| --- | --- | --- |
| Pydantic strict/frozen | 为什么 DTO 不允许 extra 字段；为什么 frozen 不等于业务真相 | `backend/golden_graph/annotation_models.py` |
| canonical JSON + SHA-256 | 为什么签名必须针对确定 bytes；sidecar 为什么不是外部 trust root | `annotation_artifacts.py`, `canonical_io.py` |
| OpenSSH `-Y sign/verify` | namespace 如何防跨协议重放；allowed signers 和 private key 各在哪一边 | `ssh_attestation.py` |
| Git-backed reviewer policy | 为什么 allowed-signers 必须先注册；registration commit、policy hash 和 fingerprint 如何绑定 | `reviewer_policy.py` |
| atomic no-replace I/O | retry、并发、crash remnant 和 conflicting publication 如何处理 | `annotation_artifacts.py` |
| UTF-8 byte locator | 为什么不能用 Python character index 冒充稳定证据地址 | `annotation_workflow.py` |
| combinations / quadratic pairs | 为什么 Relation 必须评完整 pair universe；12-20 个 Concept 时规模是多少 | `annotation_workflow.py` |
| pytest adversarial tests | 哪些 failure 是 schema error、authority error、tamper 或环境 skip | `backend/tests/test_golden_graph_annotation_artifacts.py`, `test_golden_graph_annotation_workflow.py`, `test_golden_graph_reviewer_policy.py`, `test_golden_graph_ssh_attestation.py` |

## 维护者亲手任务

这次任务不能由 Codex 替你完成，因为它正是 human authority：

1. 在标注开始前运行 `prepare-reviewer-key-policy`，检查只含 public key 的
   canonical policy 和 sidecar，然后单独 commit/push；保存完整 registration
   commit SHA。private key 绝不进 Git。
2. 运行 `verify-reviewer-key-policy`；确认 receipt 只表达当前 repository policy
   可用于新工作，不表达真实身份。策略从当前 `HEAD` 移除后，新标注必须失败，但旧
   seal 仍应通过 historical verification。
3. 确认旧 worksheet 是 0 candidate，手动移除后运行带同一 full commit 的
   `init-concepts`。
4. 在不打开任何 system proposal 的情况下，阅读 frozen CS336 Lecture 3 Source。
5. 依照 hash-bound guide 填写 candidate，包括 include/exclude、canonical key、
   preferred name、definition、alias、evidence quote 和 rationale。
6. 对每个 evidence quote 回到原 slide 检查；重复文本必须填写正确的 page-global
   UTF-8 start。
7. 只有全部判断完成后才把 worksheet 改为 `complete`，填写真实声明和 UTC 时间。
8. 执行 prepare，逐项检查 public semantics 与三个 hash，再用你控制的 key 签名。
9. 执行 seal/verify，检查 Git diff 没有 Source 原文、私有路径或个人信息。
10. 用自己的话写一段 design defense：为什么这里要分 Concept seal 和最终 gold
   bundle，以及为什么 SSH 不能证明 humanity。

## 掌握验收

完成以下证据前保持 M0：

- 不看本文画出 public/private boundary 和 artifact DAG；
- 解释一次 worksheet 在 prepare 后被修改时为什么 seal 必须失败；
- 亲手完成至少一个 include 和一个 exclude 决策，并能回源说明；
- 在测试中新增或修复一个 tamper/failure case，并逐行解释；
- 回答下面五个闭卷问题。

闭卷问题：

1. 为什么 private worksheet 不带一个“权威 sidecar”就直接变 gold？
2. 为什么 `ConceptInventorySeal` 必须明确写着 not gold bundle？
3. 签名 namespace 关闭了哪一种重放风险，又没有关闭什么身份风险？
4. 为什么 pair manifest 要在 Relation 判断前由 Concept keys 确定？
5. 如果 public JSON、sidecar 和 SSH signature 都正确，仍可能有哪些语义错误？

扩展后的 focused suite 为 `55 passed, 1 skipped`；本地完整 backend regression
为 `1028 passed, 7 skipped, 1 warning`，remote CI 仍属于 push gate。真实
CS336 policy 尚未注册，因而还没有被 hardened workflow 接受的 worksheet；human
label、Concept seal、gold bundle 和 accuracy/path evidence 都是零。最终测试数字只能
记在产品证据栏，不能自动提升个人 mastery。
