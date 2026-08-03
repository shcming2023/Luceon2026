# Worker V3 发行源装配与资格审计

## 目的

本流程把明确列出的 ElegantBookCompiler 规范、六项技能快照、Worker V3
适配器、模板和运行时证据装配为一个自包含发行源。历史
ElegantBookCompiler 稳定包只作为不可变来源证据保留，不提供运行入口。
本流程解决的是“来源是否明确、内容是否漂移、入口是否具备正式协议”
这三个问题，不代替后续单元测试、契约测试、五样本 UAT、镜像构建或
发布审批。

生产 Worker 只读取最终安装的只读发行目录。配方默认读取仓库内
`release/worker-v3/vendor-skills`，不读取活动技能目录；生成的发行目录和
运行时入口不得依赖任意用户主目录或可变仓库路径。

发行目录不是普通 Worker 的容器镜像替代品。Executor、Evaluator、
Promoter、Projector 四类常驻角色必须使用同一个专用
`backend/Dockerfile.worker-v3` 封版镜像，镜像 digest 与发行 ID/归档哈希
一并进入资格证据；不得回退到 backend 镜像。生产发行不包含 Codex、
App Server、Broker、跨 UID Runner、凭据或 Expert 资格证明。

当前审计配方为：

`release/worker-v3/recipe.current-audit.json`

它刻意声明为 `status=incomplete`。这不是 RC，也不能被 Worker 安装或执行。

## 安全约束

- 每个文件、目录树或 ZIP 选择集都有预期 SHA-256；任何漂移立即失败。
- 目录树逐文件哈希并生成规范化 tree hash；ZIP 同时核验整个归档哈希和所选成员 tree hash。
- 拒绝路径逃逸、重复目标、重复 ZIP 成员、符号链接和非普通文件。
- `.DS_Store`、隐藏项、`.pytest_cache`、`__pycache__`、`*.pyc`、交换文件等不会进入发行源。
- 输出目标必须不存在；装配使用同目录临时目录，成功后原子重命名。
- `--verify-only` 不创建输出。
- 每个阶段必须在配方中分别声明 `producer` 与 `evaluator`。只有同时静态证明
  `WORKER_V3_ENTRYPOINT_PROTOCOL = "luceon.worker-v3-stage-entrypoint/v1"`、
  正确的 `WORKER_V3_STAGE`、对应的
  `WORKER_V3_ENTRYPOINT_ROLE = "producer"|"evaluator"` 以及
  `--request/--result` CLI 的脚本，才可保留 `formal` 分类。Producer 必须是
  `candidate-only`，Evaluator 必须是独立可执行文件并使用
  `read-only-evaluator`；缺任一入口或让 Producer 自评都会使发行保持
  `incomplete`。
- 装配器不修改来源、不自动接受新哈希、不提升版本，也不执行任何模型或网络调用。
- `executable_baseline.policy` 固定为 `sole-authority`。`scripts/` 以及六项
  skill 的 `scripts/` 中出现的任何文件，都必须来自显式列出的
  `executable_baseline` 来源；formal 入口也必须属于该集合。
- 历史归档与依赖锁只能以单文件 `provenance_only` 来源进入
  `references/provenance/`。它们不得被解包为脚本，不得设为可执行，不得
  充当 entrypoint、dynamic resource、运行依赖、运行身份、模板、prompt、
  schema 或 attestation。旧证据中的主机路径因此可以原样保全，但没有
  任何运行权限。

## 核验与装配

在隔离工作树根目录运行：

```bash
python3 backend/scripts/assemble_worker_v3_release_source.py \
  --recipe release/worker-v3/recipe.current-audit.json \
  --verify-only
```

在另一台构建机上，可只覆盖构建时根目录：

```bash
python3 backend/scripts/assemble_worker_v3_release_source.py \
  --recipe release/worker-v3/recipe.current-audit.json \
  --root ebc=/absolute/path/to/ElegantbookCompiler \
  --root skills=/absolute/path/to/versioned-skill-sources \
  --verify-only
```

确认审计输出后，装配到一个全新的临时位置：

```bash
python3 backend/scripts/assemble_worker_v3_release_source.py \
  --recipe release/worker-v3/recipe.current-audit.json \
  --output /private/tmp/worker-v3-release-source
```

再使用发行打包器生成确定性归档：

```bash
python3 backend/scripts/build_worker_v3_skill_release.py \
  --source /private/tmp/worker-v3-release-source \
  --output /private/tmp/worker-v3-release-source.tar.gz
```

当前输出应明确显示 `status=incomplete`。`build_worker_v3_skill_release.py` 可以为审计和补齐工作生成 incomplete 归档，但安装器会拒绝安装它。

需要在正式登记前证明这份 incomplete 候选的 Producer、Evaluator 和
PromotionController 可执行时，只能使用
[`qualification-runbook.md`](qualification-runbook.md) 中的全新 SQLite +
目录制品仓隔离资格入口；该结果不是发行登记或生产 UAT。

正式配方中的单阶段形状如下；旧的 flat 单入口形状只作为资格审计输入，
会明确产生 `dual_entrypoint_recipe_required` 和
`formal_evaluator_entrypoint_missing`，不会被自动复制成 Evaluator：

```json
{
  "stage": "intake_snapshot",
  "producer": {
    "id": "worker-v3.intake-snapshot.produce",
    "classification": "formal",
    "tool_path": "scripts/stages/intake_snapshot.producer.py",
    "timeout_seconds": 3600
  },
  "evaluator": {
    "id": "worker-v3.intake-snapshot.evaluate",
    "classification": "formal",
    "tool_path": "scripts/stages/intake_snapshot.evaluator.py",
    "timeout_seconds": 3600
  }
}
```

## 当前入口与剩余缺口

配方已为 12 个阶段分别绑定 release-local、schema-bound 的 Producer 和
Evaluator，共 24 个 formal 入口；旧稳定包脚本不再进入 `scripts/`、
`validators/` 或运行时依赖。Spec 01–03 原子内核、Spec 04/05/06 内核以及
Worker V3 适配器都来自 six-skill + adapter sole baseline。

当前机器审计仍应保持 `incomplete`，且只保留尚未用最终封版环境证明的真实
缺口：

1. 最终 Overleaf adapter 镜像身份和真实 ZIP 编译资格；
2. 全页视觉证据 provider/reviewer 的 clean-image 资格；
3. Spec 05 在最终 Worker V3 镜像上的不改代码真实教材资格；

以上是本文修订时 `--verify-only` 的已知缺口快照，不是永久固定清单。
每次装配应以当前审计输出为准；任何后续实现或测试只有在产物哈希、镜像
digest 与实跑证明被新 RC 配方绑定后，才可以关闭对应缺口。

## 运行命名空间与证据收口

正式 Compose 路径使用 MinIO adapter，默认对象边界为：

- candidate：`worker-v3-candidates/v3/candidates/...`
- formal projection：`eduassets-elegantbook/elegantbook/v3/...`

发行资格不得使用本地 `DirectoryArtifactStore` 作为上述 MinIO 路径的
替代证据，也不得复用 V2.3 的 `elegantbook/...` 正式前缀。

五样本 shadow UAT 与最终封版烟测结束后，使用只读采集器生成跨
页面、DB、MinIO 和运行时的一致性证据：

```bash
python backend/scripts/workflow_v3_uat_evidence.py \
  --cohort-id <cohort> \
  --cohort-field cohort_id \
  --workflow-db-url <dedicated-worker-v3-db-url> \
  --material-db-url <material-db-url> \
  --ui-snapshot evidence/ui.json \
  --runtime-snapshot evidence/runtime.json \
  --json-out evidence/worker-v3-uat.json \
  --markdown-out evidence/worker-v3-uat.md
```

正式资格不允许用 `--allow-missing-ui` 或 `--allow-missing-runtime` 降级证据。

关闭缺口后，不应直接把本配方中的 `status` 改绿。正确顺序是：

1. 重新生成并人工复核显式源清单；
2. 更新预期哈希并运行 `--verify-only`；
3. 运行发行、契约、故障注入和五样本 shadow UAT；
4. 由独立资格步骤生成新的 RC 配方和证据；
5. 构建不可变镜像后，执行“不改代码、不重构建、不重部署”的最终批量贯通烟测。

任何一个 P0 未关闭，发行都必须保持 `incomplete`。
