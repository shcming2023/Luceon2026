# Worker V3 预封版隔离资格验证

## 目的与结论边界

这个入口解决一个特定的发布闭环问题：候选技能发行包在仍为
`status=incomplete`、尚不能登记为正式发行时，也必须先证明其正式
Producer、独立 Evaluator 和 PromotionController 可以按真实顺序执行。

资格验证通过只表示：

- 指定的只读候选发行目录能够在隔离控制平面内执行；
- 指定的七件冻结输入与 `source_evidence` 绑定一致；
- 每个已运行阶段都形成候选、独立评估和隔离晋升证据；
- 报告中的发行、输入、阶段、候选、评估、晋升和模型调用哈希可核验。

它**不等于**发行登记、RC/Stable 封版、生产部署、真实样本 UAT 或人工验收。
资格库中的 `status=qualification` 也不是正式
`status=registered`。后续封版仍须按发行配方补齐证据、重新构建不可变发行
包并走正式登记。

## 强制隔离

资格入口只有显式 CLI：

`backend/scripts/workflow_v3_qualification.py`

它不挂载到 API，不被普通 Worker 队列发现，也没有生产 MinIO 适配器。运行
前和运行中强制满足：

1. `LUCEON_ENVIRONMENT` 必须严格等于 `qualification`；
2. 拒绝继承任何 `WORKFLOW_V3_DATABASE_URL`；
3. `--run-root` 必须从未存在；
4. 在该目录内新建独立 SQLite、目录制品仓和工作目录；
5. 发行目录、七件套目录、`source_evidence` 和模型回放文件均必须只读、无
   符号链接，且不能与运行目录重叠；
6. 只接受 `status=incomplete` 且 `rc_eligible=false`、
   `stable_eligible=false` 的发行；
7. 普通 Executor、Evaluator、PromotionController 和普通发行绑定校验拒绝
   资格状态；
8. 资格模式没有在线 LLM/视觉模型回退。需要模型的发行必须提供精确请求哈希
   回放文件；缺少或不匹配即失败。

因此，资格运行不会写生产 Worker V3 数据库、生产 MinIO、正式发行注册表或
生产投影。

## 输入契约

`--source-package-root` 必须是预先物化的只读目录，并且只包含以下七个制品，
顺序和 `source_evidence.artifacts` 一致：

1. `source_pdf`
2. `mineru_manifest`
3. `mineru_frozen_marker`
4. `mineru_archive`
5. `frozen_source`（冻结 Popo manifest）
6. `popo_frozen_marker`
7. `popo_archive`

每一行必须包含精确字段：

`role`、`kind`、`bucket`、`object`、`sha256`、`size_bytes`、
`read_only=true`。

目录中的相对路径为 `<bucket>/<object>`。目录不得多一个或少一个文件。
`source_evidence` 还必须绑定 MinerU/Popo run ID、顶层对象引用、
`input_set_sha256` 和指向冻结 Popo manifest 的 `review_asset`。

准备完成后设置只读权限：

```bash
find "$SOURCE_PACKAGE" -type f -exec chmod 0444 {} +
find "$SOURCE_PACKAGE" -type d -exec chmod 0555 {} +
chmod 0444 "$SOURCE_EVIDENCE_JSON"
```

候选发行可以采用以下二选一输入：

- 已物化只读目录：文件 `0444`、正式入口脚本 `0555`、所有目录 `0555`、
  `release-manifest.json` 为 `0444`；
- `build_worker_v3_skill_release.py` 直接生成的只读 incomplete 归档，同时传入
  构建器输出的外部 archive SHA-256。

归档模式先在不创建 run root 的情况下复用正式归档校验，拒绝外部 SHA
不符、路径逃逸、重复成员、链接、非普通成员、异常元数据、声明外文件及
incomplete/eligibility 漂移。通过后才只解到新 run root 的 `release/`，
不会进入正式 installed releases。

## 模型回放格式

若发行的 `model_policy` 不是 `{"mode":"none"}`，必须提供
`--fixture-responses-json`。文件协议为：

```json
{
  "schema_version": "luceon.worker-v3-llm-fixtures/v1",
  "responses": [
    {
      "request_sha256": "<完整规范请求的 SHA-256>",
      "provider": "<发行绑定 provider>",
      "model": "<发行绑定 model>",
      "response_id": "<固定响应 ID>",
      "parsed_result": {},
      "raw_response": {},
      "usage": {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0
      }
    }
  ]
}
```

回放采用完整请求的规范 JSON 哈希精确匹配；每条响应只能消费一次，不支持
模糊匹配、默认响应或在线补偿。资格通过要求声明响应与实际调用一一对应，
存在缺失、重复消费或未使用响应都会失败。报告列出这些请求哈希，并明确
`network_used=false`。

## 运行方式

先确保当前 shell 没有生产数据库变量：

```bash
unset WORKFLOW_V3_DATABASE_URL
```

运行到机械生成 ElegantBook：

```bash
LUCEON_ENVIRONMENT=qualification \
PYTHONPATH=backend \
python backend/scripts/workflow_v3_qualification.py \
  --release-root "$INCOMPLETE_RELEASE" \
  --source-package-root "$SOURCE_PACKAGE" \
  --source-evidence-json "$SOURCE_EVIDENCE_JSON" \
  --run-root "$NEW_RUN_ROOT" \
  --stop-after deterministic_elegantbook \
 --fixture-responses-json "$FIXTURE_RESPONSES"
```

若直接使用发行构建器产出的 incomplete 归档，将 `--release-root` 替换为：

```bash
  --release-archive "$INCOMPLETE_RELEASE_ARCHIVE" \
  --release-archive-sha256 "$INCOMPLETE_RELEASE_ARCHIVE_SHA256"
```

若真实 Spec 05 首次编译只产生 `C2_REVIEW_REQUIRED_OPEN` 警告，先检查
`final_render_pack/pages/` 中与每个警告对应的哈希绑定页面，再制作只读
`spec05-warning-review/1.0` 文件。资格入口可显式增加：

```bash
  --spec05-warning-review-json "$SPEC05_WARNING_REVIEW_JSON"
```

该文件不能预先豁免门禁。入口只会在 `deterministic_elegantbook` 的真实
Evaluator 返回 `needs_review`、警告指纹和顺序完全一致时消费它，随后写入
隔离制品仓中的完整不可变 review-resolution manifest，并只恢复一次 Spec 05。
未消费、指纹不完整、顺序漂移、第二次仍未通过均 fail closed；报告同时保留
首次 `needs_review` 与恢复代际证据。普通 API、生产数据库和正式 MinIO 不会
读取这个资格参数。

运行完整 12 阶段：

```bash
LUCEON_ENVIRONMENT=qualification \
PYTHONPATH=backend \
python backend/scripts/workflow_v3_qualification.py \
  --release-root "$INCOMPLETE_RELEASE" \
  --source-package-root "$SOURCE_PACKAGE" \
  --source-evidence-json "$SOURCE_EVIDENCE_JSON" \
  --run-root "$NEW_RUN_ROOT" \
  --stop-after ready_for_user_acceptance \
  --fixture-responses-json "$FIXTURE_RESPONSES"
```

`model_policy={"mode":"none"}` 的小型编排夹具可省略回放参数；实际包含受约束
LLM 或视觉审阅的候选发行不得省略。

## 输出和机器核验

运行目录至少包含：

- `qualification.sqlite3`：本轮唯一控制平面数据库；
- `artifacts/`：本轮唯一目录制品仓；
- `work/producer/` 和 `work/evaluator/`：角色隔离工作目录；
- `qualification-report.json`；
- `qualification-report.json.sha256`。

报告协议为 `luceon.worker-v3-qualification-report/v1`，外层
`payload_sha256` 是整个 `payload` 的规范 JSON 哈希；旁车文件是报告文件
本身的 SHA-256。报告和旁车在完成后均为 `0444`。

报告逐阶段记录：

- stage、attempt、状态和输入晋升绑定；
- Producer execution 身份与 runtime identity；
- candidate 对象身份、哈希、大小、不可变标记；
- Evaluator policy、全部 gate、finding 和决策；
- Promotion 与 candidate SHA 链；
- 所有模型调用及其请求、原始响应和规范结果哈希。

验证时至少确认：

1. 旁车 SHA-256 等于报告文件哈希；
2. `payload_sha256` 等于规范化 `payload` 哈希；
3. 每阶段 `candidate.sha256 == promotion.artifact_sha256`；
4. 后继阶段输入 SHA 等于前一阶段已晋升候选 SHA；
5. 所有已运行评估均 `decision=passed` 且全部 gate 为 `true`；
6. 七件源制品运行前后哈希、大小和只读权限不变；
7. `production_state_written=false`、`release_promoted=false`；
8. 使用模型时，fixture 声明与实际调用一一对应且
   `network_used=false`。
9. 归档模式下，`archive_sha256` 等于外部绑定，且
   `materialized_tree_sha256 == tree_sha256`。
10. 使用 Spec 05 警告审阅时，`review_resolutions` 绑定首次 Evaluator、
    candidate、finding、精确警告指纹和恢复代际；最终通过阶段的 `attempts`
    同时保留首次 `needs_review` 与恢复尝试。

任何一项不成立都不能用这份报告补齐发行证据。

## 失败与停止规则

- 预检失败时不会创建运行目录；
- 执行开始后的失败会尽可能生成只读失败报告，保留最小失败阶段证据；
- 不允许复用失败运行目录；修正候选发行或夹具后必须使用全新的
  `--run-root`；
- 不得把资格 SQLite 复制到生产数据库，也不得把资格目录制品写回生产
  MinIO；
- 不得把资格报告直接改写为 RC/Stable 结论；
- 只有发行配方的独立证据门禁全部关闭后，才可重新构建正式不可变发行并登记。
