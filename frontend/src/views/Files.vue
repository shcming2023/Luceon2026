<template>
  <div class="materials-root">
    <header class="page-header">
      <div class="page-heading">
        <h1 class="page-title">材料生产台</h1>
        <div class="page-meta">
          <span>共 {{ total }} 份材料</span>
          <span v-if="summary?.latest_run">最近任务：{{ pipelineStatusText(summary.latest_run.status) }}</span>
        </div>
      </div>
      <div class="header-actions">
        <el-button :icon="Upload" type="primary" @click="uploadDialogVisible = true">上传 PDF</el-button>
        <el-tooltip content="扫描 MinIO 资产桶并刷新本地索引，不提交 GPU 任务" placement="bottom">
          <el-button :icon="Refresh" :loading="syncing" @click="syncMaterials(true)">同步资产</el-button>
        </el-tooltip>
        <el-dropdown trigger="click" @command="handleHeaderCommand">
          <el-button :icon="Cpu" :disabled="preflighting || pipelineBusy">
            GPU 解析
            <el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="preflight" :icon="VideoPlay">解析预检</el-dropdown-item>
              <el-dropdown-item command="pipeline" :icon="Cpu">启动 GPU 解析</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <section class="summary-band" aria-label="阶段概览">
      <button
        v-for="tile in summaryTiles"
        :key="tile.key"
        type="button"
        class="summary-tile"
        :class="{ active: params.stage === tile.stage }"
        @click="applyStageFilter(tile.stage)"
      >
        <strong>{{ tile.value }}</strong>
        <span>{{ tile.label }}</span>
      </button>
    </section>

    <section :class="['workspace-status', `tone-${taskTickerTone}`]">
      <div class="workspace-state">
        <span class="state-icon">
          <el-icon><component :is="pipelineStateIcon" /></el-icon>
        </span>
        <div class="state-copy">
          <span class="state-label">{{ pipelineStateLabel }}</span>
          <strong>{{ pipelineHeadline }}</strong>
          <span>{{ pipelineDetail }}</span>
        </div>
      </div>
      <div v-if="recentOperation" :class="['recent-work', recentOperation.status]">
        <span>最近</span>
        <strong>{{ recentOperation.filename }}</strong>
        <em>{{ recentOperation.action }} · {{ operationStatusText(recentOperation.status) }}</em>
      </div>
      <div class="workspace-actions">
        <el-button v-if="recentOperation" size="small" @click="locateRecentOperation">定位</el-button>
        <el-button v-if="recentOperation" size="small" text @click="clearRecentOperation">清除</el-button>
      </div>
    </section>

    <section class="filter-bar">
      <el-input v-model="params.search" class="search-input" clearable placeholder="搜索书名、文件名、系列、ISBN、material_id">
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <el-select v-model="params.stage" clearable class="stage-select" placeholder="全部阶段">
        <el-option v-for="stage in stageOptions" :key="stage.value" :label="stage.label" :value="stage.value" />
      </el-select>
      <div class="filter-actions">
        <el-button :icon="Filter" @click="metadataFiltersExpanded = !metadataFiltersExpanded">
          {{ metadataFiltersExpanded ? '收起筛选' : '更多筛选' }}
          <span v-if="metadataFilterCount" class="filter-count">{{ metadataFilterCount }}</span>
        </el-button>
        <el-button v-if="metadataFilterCount" link @click="clearMetadataFilters">清除</el-button>
      </div>
    </section>

    <transition name="filter-reveal">
      <section v-show="metadataFiltersExpanded" class="metadata-filter-bar" aria-label="教材元数据筛选">
        <el-select v-model="params.metadata_status" clearable placeholder="元数据状态">
          <el-option label="待提取" value="missing" />
          <el-option label="AI 已提取" value="ai_extracted" />
          <el-option label="人工已修改" value="manual" />
          <el-option label="提取失败" value="failed" />
        </el-select>
        <el-select v-model="params.subject" clearable filterable placeholder="学科">
          <el-option v-for="item in metadataOptions.subjects" :key="item" :label="item" :value="item" />
        </el-select>
        <el-select v-model="params.country" clearable filterable placeholder="出版国家">
          <el-option v-for="item in metadataOptions.countries" :key="item" :label="item" :value="item" />
        </el-select>
        <el-autocomplete
          v-model="params.series"
          clearable
          value-key="value"
          placeholder="系列名"
          :fetch-suggestions="suggestSeries"
          :trigger-on-focus="true"
        />
        <div class="year-filter">
          <el-input-number v-model="params.year_from" :min="0" :max="2200" controls-position="right" placeholder="起始年" />
          <span>至</span>
          <el-input-number v-model="params.year_to" :min="0" :max="2200" controls-position="right" placeholder="结束年" />
        </div>
      </section>
    </transition>

    <section v-if="selectedRows.length || batchState.running" class="batch-bar">
      <div class="batch-summary">
        <strong>{{ batchState.running ? '批量任务执行中' : `已选择 ${selectedRows.length} 条` }}</strong>
        <span v-if="!batchState.running">可批量补全元数据 {{ selectedRows.length }}</span>
        <span v-else>
          {{ batchState.done }}/{{ batchState.total }} · 成功 {{ batchState.success }} · 失败 {{ batchState.failed }} · {{ batchState.currentName }}
        </span>
      </div>
      <div class="batch-actions">
        <el-button v-if="batchState.running" size="small" @click="stopBatchAfterCurrent">停止后续</el-button>
        <el-button
          size="small"
          :loading="metadataBatchExtracting"
          :disabled="batchState.running || metadataBatchExtracting || !selectedRows.length"
          @click="extractSelectedMetadata"
        >
          AI 补全元数据
        </el-button>
        <el-button
          size="small"
          type="primary"
          :icon="Cpu"
          :loading="workflowV2BatchStarting"
          :disabled="batchState.running || workflowV2BatchStarting || !selectedWorkflowV23StartableRows.length"
          @click="startSelectedWorkflowV23Jobs"
        >
          批量升级到 Worker V2.3
        </el-button>
        <el-button
          v-if="workerV1Policy.batch_enabled"
          size="small"
          type="warning"
          :icon="Cpu"
          :loading="codexBatchStarting"
          :disabled="batchState.running || codexBatchStarting || !selectedCodexStartableRows.length"
          @click="startSelectedCodexJobs"
        >
          批量 Codex 重扫
        </el-button>
        <span v-else class="batch-policy-note">Worker V1 批量已冻结</span>
      </div>
      <div v-if="batchState.logs.length" class="batch-log">最近失败：{{ batchState.logs[0] }}</div>
    </section>

    <section class="table-shell">
      <el-table
        ref="materialTable"
        v-loading="loading"
        :data="orderedMaterials"
        height="100%"
        row-key="id"
        :row-class-name="materialRowClassName"
        :header-cell-style="{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)', fontWeight: 600 }"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="44" fixed="left" reserve-selection />
        <el-table-column prop="filename" label="材料" min-width="330">
          <template #default="{ row }">
            <div class="material-cell">
              <div class="material-title-row">
                <span class="file-name">{{ displayTitle(row) }}</span>
                <el-tag v-if="isActiveTaskRow(row)" size="small" type="primary" effect="plain">当前任务</el-tag>
                <el-tag v-else-if="isRecentOperationRow(row)" size="small" type="warning" effect="plain">最近</el-tag>
                <el-tag size="small" :type="metadataStatusType(row.book_metadata)" effect="plain">
                  {{ metadataStatusLabel(row.book_metadata) }}
                </el-tag>
              </div>
              <div v-if="metadataSubtitle(row)" class="book-subtitle">{{ metadataSubtitle(row) }}</div>
              <div class="material-meta">
                <span>{{ formatFileSize(row.size) }}</span>
                <span>{{ formatDateTime(row.last_synced_at || row.created_at) || '未同步' }}</span>
                <el-tooltip :content="row.input_object || row.material_id || row.id" placement="top">
                  <span>{{ compactMaterialId(row) }}</span>
                </el-tooltip>
              </div>
              <div v-if="metadataChips(row).length" class="metadata-chips">
                <span v-for="chip in metadataChips(row)" :key="chip">{{ chip }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="阶段" min-width="320">
          <template #default="{ row }">
            <div class="pipeline-cell">
              <div class="stage-summary">
                <span :class="['pipeline-status', `stage-${row.stage_status}`]">{{ rowStageMeta(row).label }}</span>
                <span class="stage-note">{{ rowStageNote(row) }}</span>
              </div>
              <button v-if="workflowV2ForRow(row)" type="button" class="workflow-v2-line" @click.stop="openWorkflowV2Drawer(row)">
                <span class="workflow-v2-label">Worker V2.3</span>
                <strong>{{ workflowV2StageForRow(row) }}</strong>
                <span>{{ workflowV2StatusForRow(row) }}</span>
                <span v-if="workflowV2ModelCallsForRow(row)">模型 {{ workflowV2ModelCallsForRow(row) }}</span>
                <span v-if="workflowV2FindingsForRow(row)" class="workflow-v2-findings">问题 {{ workflowV2FindingsForRow(row) }}</span>
              </button>
              <div class="stage-track">
                <span
                  v-for="stage in artifactStages"
                  :key="stage.key"
                  :class="['stage-step', { done: stage.done(row), active: currentStageKey(row) === stage.key }]"
                >
                  <span class="stage-dot"></span>
                  <span class="stage-step-label">{{ stage.label }}</span>
                </span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="下一步" width="190" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button
                size="small"
                :icon="primaryAction(row).icon"
                :type="primaryAction(row).type"
                :loading="codexStartingIds.has(row.id) || primaryActionLoading(row)"
                :disabled="!primaryAction(row).enabled"
                @click="runPrimaryAction(row)"
              >
                {{ primaryAction(row).label }}
              </el-button>
              <el-dropdown trigger="click" @command="handleDropdownCommand(row, $event)">
                <el-button class="more-button" size="small" :icon="MoreFilled" />
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="metadata-edit" :icon="DocumentChecked">编辑元数据</el-dropdown-item>
                    <el-dropdown-item command="metadata-extract" :icon="Cpu">AI 提取元数据</el-dropdown-item>
                    <el-dropdown-item command="compare-review" :disabled="!hasLatexAsset(row)" :icon="View">PDF 比对</el-dropdown-item>
                    <el-dropdown-item v-if="isPipelineAdmin" command="resume-popo" :disabled="!hasMineruAsset(row) || hasPopoAsset(row) || pipelineBusy" :icon="Refresh">管理员：从冻结 MinerU 恢复 Popo</el-dropdown-item>
                    <el-dropdown-item command="start-worker-v2" :disabled="!hasPopoAsset(row) || hasCurrentWorkflowV2(row)" :icon="Cpu">
                      {{ workflowV2ForRow(row) ? '升级到 V2.3' : '启动 Worker V2.3' }}
                    </el-dropdown-item>
                    <el-dropdown-item command="retry-worker-v2" :disabled="workflowV2ForRow(row)?.status !== 'failed'" :icon="Refresh">重试 V2.3 当前阶段</el-dropdown-item>
                    <el-dropdown-item command="start-codex" :disabled="!canStartCodex(row)" :icon="Cpu">
                      {{ row.codex_job?.status === 'published' ? 'Worker V1 输出已发布' : (hasLatexAsset(row) ? 'Worker V1 单任务重扫' : 'Worker V1 单任务审计') }}
                    </el-dropdown-item>
                    <el-dropdown-item command="run-worker" :disabled="!canRunCodexWorker(row)" :icon="VideoPlay">
                      运行 Worker
                    </el-dropdown-item>
                    <el-dropdown-item divided command="preview-pdf" :disabled="!row.input_object" :icon="Document">打开 PDF</el-dropdown-item>
                    <el-dropdown-item command="download-pdf" :disabled="!row.input_object" :icon="Download">下载 PDF</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </el-table>
        <el-empty v-if="!loading && !materials.length" :description="emptyText">
        <el-button type="primary" :icon="Upload" @click="uploadDialogVisible = true">上传 PDF</el-button>
        <el-button :loading="syncing" @click="syncMaterials(true)">同步资产</el-button>
      </el-empty>
    </section>

    <footer class="pagination-row">
      <el-pagination
        v-model:current-page="params.page"
        v-model:page-size="params.page_size"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next"
      />
    </footer>

    <el-drawer v-model="workflowV2DrawerVisible" title="Worker V2.3 任务" size="520px">
      <div v-loading="workflowV2DetailLoading" class="workflow-v2-detail">
        <template v-if="activeWorkflowV2Job">
          <div class="workflow-v2-detail-head">
            <div>
              <span>当前阶段</span>
              <strong>{{ workflowV2StageLabel(activeWorkflowV2Job.current_stage_key) }}</strong>
            </div>
            <el-tag :type="activeWorkflowV2Job.status === 'failed' ? 'danger' : activeWorkflowV2Job.status === 'needs_review' ? 'warning' : activeWorkflowV2Job.status === 'succeeded' ? 'success' : 'primary'">
              {{ workflowV2StatusText(activeWorkflowV2Job.status) }}
            </el-tag>
          </div>
          <div class="workflow-v2-detail-actions">
            <el-button v-if="activeWorkflowV2Job.status === 'failed'" type="primary" :loading="workflowV2ActionLoading" @click="retryActiveWorkflowV2">重试当前阶段</el-button>
            <template v-if="activeWorkflowV2Job.status === 'needs_review' && activeWorkflowV2Candidate">
              <el-button type="primary" plain @click="openWorkflowV2CandidatePdf">查看候选 PDF</el-button>
              <el-button @click="downloadWorkflowV2CandidateLatex">下载待修复 LaTeX ZIP</el-button>
              <el-button :loading="workflowV2ActionLoading" @click="handoffActiveWorkflowV2">转人工处理</el-button>
              <el-button type="primary" :loading="workflowV2ActionLoading" @click="revalidateActiveWorkflowV2">修复后重新验证</el-button>
            </template>
            <el-button
              v-else-if="activeWorkflowV2Job.status === 'needs_review'"
              type="primary"
              :loading="workflowV2ActionLoading"
              @click="restartActiveWorkflowV2CurrentStage"
            >
              应用修复并重试当前阶段
            </el-button>
            <el-button
              v-if="activeWorkflowV2Job.status === 'succeeded' && workflowV2ReviewClosureMissing(activeWorkflowV2Job)"
              type="warning"
              :loading="workflowV2ActionLoading"
              @click="restartActiveWorkflowV2CurrentStage"
            >
              补全审阅闭环
            </el-button>
            <el-button
              v-if="activeWorkflowV2Job.status === 'succeeded'"
              :loading="workflowV2ActionLoading"
              @click="restartActiveWorkflowV2LatexBuild"
            >
              从排版构建重新运行
            </el-button>
          </div>
          <section v-if="activeWorkflowV2Candidate?.blockers?.length" class="workflow-v2-section workflow-v2-blockers">
            <h3>排版阻断证据</h3>
            <p class="workflow-v2-muted">候选件已完整保存；这些是质量门禁，不是服务故障。可先查看 PDF/下载 LaTeX，再转人工或修复后重新验证。</p>
            <article v-for="blocker in activeWorkflowV2Candidate.blockers" :key="blocker.code" class="workflow-v2-blocker-card">
              <div>
                <strong>{{ workflowV2BlockerLabel(blocker.code) }}</strong>
                <el-tag size="small" type="warning" effect="plain">{{ blocker.count || 1 }} 处</el-tag>
              </div>
              <small v-if="blocker.max_overfull_pt">最大溢出 {{ blocker.max_overfull_pt }}pt</small>
              <small v-for="(item, index) in workflowV2BlockerEvidence(blocker)" :key="index">{{ item }}</small>
            </article>
          </section>
          <section class="workflow-v2-section">
            <h3>阶段记录</h3>
            <div v-for="stage in activeWorkflowV2Job.stages" :key="stage.id" class="workflow-v2-record">
              <strong>{{ workflowV2StageLabel(stage.stage_key) }}</strong>
              <span>第 {{ stage.attempt }} 次</span>
              <el-tag size="small" effect="plain" :type="stage.status === 'failed' ? 'danger' : stage.status === 'needs_review' ? 'warning' : stage.status === 'succeeded' ? 'success' : 'info'">{{ workflowV2StatusText(stage.status) }}</el-tag>
              <small v-if="stage.error?.message">{{ workflowV2ErrorSummary(stage.error) }}</small>
            </div>
          </section>
          <section class="workflow-v2-section">
            <h3>可观察证据</h3>
            <div class="workflow-v2-counters">
              <span>事件 <strong>{{ activeWorkflowV2Job.events?.length || 0 }}</strong></span>
              <span>产物 <strong>{{ activeWorkflowV2Job.artifacts?.length || 0 }}</strong></span>
              <span>模型调用 <strong>{{ activeWorkflowV2Job.model_calls?.length || 0 }}</strong></span>
              <span>开放问题 <strong>{{ activeWorkflowV2OpenFindingCount }}</strong></span>
            </div>
          </section>
          <section v-if="activeWorkflowV2Job.qa_findings?.length" class="workflow-v2-section">
            <h3>QA 问题</h3>
            <div v-for="finding in activeWorkflowV2Job.qa_findings" :key="finding.id" class="workflow-v2-finding-row">
              <el-tag size="small" type="danger" effect="plain">{{ finding.severity }}</el-tag>
              <strong>{{ finding.code }}</strong>
              <span v-if="finding.page_number">第 {{ finding.page_number }} 页</span>
            </div>
          </section>
          <section v-if="activeWorkflowV2Job.repair_attempts?.length" class="workflow-v2-section">
            <h3>修复尝试</h3>
            <div v-for="repair in activeWorkflowV2Job.repair_attempts" :key="repair.id" class="workflow-v2-record">
              <strong>{{ repair.repair_kind }}</strong>
              <el-tag size="small" effect="plain" :type="repair.status === 'failed' ? 'danger' : repair.status === 'succeeded' ? 'success' : 'info'">{{ repair.status }}</el-tag>
              <span v-if="repair.result?.post_patch_quality_status">质量门：{{ repair.result.post_patch_quality_status }}</span>
              <small v-if="repair.result?.remaining_blockers?.length">仍有 {{ repair.result.remaining_blockers.length }} 类阻断</small>
            </div>
          </section>
          <section v-if="activeWorkflowV2Job.model_calls?.length" class="workflow-v2-section">
            <h3>模型调用</h3>
            <div v-for="call in activeWorkflowV2VisibleModelCalls" :key="call.id" class="workflow-v2-call-row">
              <strong>{{ call.purpose }}</strong>
              <span>{{ call.model }}</span>
              <span>{{ call.status }}</span>
              <small v-if="call.usage?.total_tokens">{{ call.usage.total_tokens }} tokens</small>
            </div>
            <p v-if="activeWorkflowV2HiddenModelCallCount" class="workflow-v2-muted">另有 {{ activeWorkflowV2HiddenModelCallCount }} 个视觉审查批次，可通过 API 查看完整记录。</p>
          </section>
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="uploadDialogVisible" title="上传 PDF" width="520px">
      <el-upload
        v-model:file-list="uploadFileList"
        drag
        multiple
        accept=".pdf,application/pdf"
        :auto-upload="false"
        :limit="20"
      >
        <el-icon class="upload-icon"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖入 PDF 或点击选择</div>
      </el-upload>
      <el-alert
        v-if="uploadResumeContext && !uploading"
        class="upload-resume"
        type="warning"
        :closable="false"
        show-icon
        title="检测到上次上传未完整确认"
        :description="uploadResumeDescription"
      />
      <el-button v-if="uploadResumeContext && !uploading" link @click="clearUploadResumeContext">清除恢复提示</el-button>
      <el-progress v-if="uploading" :percentage="uploadProgress" class="upload-progress" />
      <el-alert
        v-if="uploadError"
        class="upload-error"
        type="error"
        :closable="false"
        show-icon
        :title="uploadError"
      />
      <template #footer>
        <el-button @click="uploadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadableFiles.length" @click="submitUpload">
          上传
        </el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="metadataDrawerVisible"
      class="metadata-drawer"
      size="520px"
      append-to-body
      :title="activeMetadataRow ? displayTitle(activeMetadataRow) : '教材元数据'"
    >
      <div v-if="activeMetadataRow" class="metadata-editor">
        <div class="metadata-editor-head">
          <div>
            <span>原始文件</span>
            <strong>{{ activeMetadataRow.filename }}</strong>
          </div>
          <el-tag :type="metadataStatusType(activeMetadataRow.book_metadata)" effect="plain">
            {{ metadataStatusLabel(activeMetadataRow.book_metadata) }}
          </el-tag>
        </div>

        <el-form label-position="top" class="metadata-form">
          <el-form-item label="原书名">
            <el-input v-model="metadataForm.original_title" placeholder="例如 Cambridge Primary Mathematics Learner's Book 1" />
          </el-form-item>
          <div class="metadata-form-grid">
            <el-form-item label="出版年份">
              <el-input-number v-model="metadataForm.publication_year" :min="0" :max="2200" controls-position="right" />
            </el-form-item>
            <el-form-item label="出版日期">
              <el-input v-model="metadataForm.publication_date" placeholder="原文日期或年份" />
            </el-form-item>
          </div>
          <div class="metadata-form-grid">
            <el-form-item label="版别">
              <el-input v-model="metadataForm.edition" placeholder="2nd Edition / 第二版" />
            </el-form-item>
            <el-form-item label="学科">
              <el-input v-model="metadataForm.subject" placeholder="Mathematics / English" />
            </el-form-item>
          </div>
          <div class="metadata-form-grid">
            <el-form-item label="出版国家">
              <el-input v-model="metadataForm.publication_country" placeholder="United Kingdom / 中国" />
            </el-form-item>
            <el-form-item label="系列名">
              <el-input v-model="metadataForm.series_name" placeholder="Cambridge Primary Mathematics" />
            </el-form-item>
          </div>
          <div class="metadata-form-grid">
            <el-form-item label="出版社">
              <el-input v-model="metadataForm.publisher" />
            </el-form-item>
            <el-form-item label="ISBN">
              <el-input v-model="metadataForm.isbn" />
            </el-form-item>
          </div>
          <div class="metadata-form-grid">
            <el-form-item label="语言">
              <el-input v-model="metadataForm.language" />
            </el-form-item>
            <el-form-item label="年级/阶段">
              <el-input v-model="metadataForm.grade_level" />
            </el-form-item>
          </div>
        </el-form>

        <section class="metadata-evidence">
          <header>
            <span>AI 证据</span>
            <small>{{ metadataSampleLabel }}</small>
          </header>
          <div v-if="metadataEvidenceRows.length" class="evidence-list">
            <article v-for="(item, index) in metadataEvidenceRows" :key="index">
              <span>{{ item.field }}</span>
              <p>{{ item.quote }}</p>
              <small>{{ item.source }}</small>
            </article>
          </div>
          <el-empty v-else description="暂无证据片段，可先执行 AI 提取" :image-size="76" />
        </section>

        <div v-if="metadataForm.extraction_error" class="metadata-error">
          {{ metadataForm.extraction_error }}
        </div>
      </div>

      <template #footer>
        <div class="metadata-drawer-footer">
          <el-checkbox v-model="metadataForceExtract">覆盖人工修改</el-checkbox>
          <div>
            <el-button :loading="metadataExtracting" @click="extractActiveMetadata">AI 提取</el-button>
            <el-button type="primary" :loading="metadataSaving" @click="saveActiveMetadata">保存修改</el-button>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch, type Component } from 'vue'
import {
  ArrowDown,
  CircleCheckFilled,
  Cpu,
  Document,
  DocumentChecked,
  Download,
  Filter,
  MoreFilled,
  Refresh,
  Search,
  Timer,
  Upload,
  UploadFilled,
  VideoPlay,
  View,
  WarningFilled
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadUserFile } from 'element-plus'
import { useRouter } from 'vue-router'
import { codexWorkerApi, type CodexWorkerStatus } from '@/api/codexWorker'
import { materialsApi, type WorkflowV2JobSummary } from '@/api/materials'
import type {
  MaterialItem,
  MaterialBookMetadata,
  MaterialMetadataOptions,
  MaterialSummary,
  ObjectRef,
  PipelinePreflightResponse,
  PipelineRun,
  PipelineStatusResponse
} from '@/types/material'
import { formatFileSize } from '@/utils/format'
import { formatDateTime as formatDate } from '@/utils/status'
import { fetchCurrentUser, useCurrentUser } from '@/utils/user'

const router = useRouter()
const currentUser = useCurrentUser()
const isPipelineAdmin = computed(() => Boolean(currentUser.value?.capabilities?.pipeline_admin))
const materials = ref<MaterialItem[]>([])
const total = ref(0)
const loading = ref(false)
const syncing = ref(false)
const uploadDialogVisible = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadError = ref('')
const uploadFileList = ref<UploadUserFile[]>([])
type UploadResumeContext = { filenames: string[]; progress: number; updatedAt?: string }
const uploadResumeKey = 'luceon-upload-resume'
const uploadResumeContext = ref<UploadResumeContext | null>(null)
const uploadResumeDescription = computed(() => {
  const context = uploadResumeContext.value
  if (!context) return ''
  const filenames = context.filenames.slice(0, 3).join('、')
  const remainder = context.filenames.length > 3 ? ` 等 ${context.filenames.length} 个文件` : ''
  const progress = context.progress > 0 ? `，中断前浏览器进度 ${context.progress}%` : ''
  return `${filenames}${remainder}${progress}。浏览器不能自动恢复本地文件，请重新选择同一批文件；已写入资产库的文件会按 SHA-256 去重复用，只继续处理未完成文件。`
})
const summary = ref<MaterialSummary | null>(null)
const pipeline = reactive<PipelineStatusResponse>({ run: null, events: [] })
const preflight = ref<PipelinePreflightResponse | null>(null)
const searchTimer = ref<number | null>(null)
let materialsRequestSerial = 0
const pollingTimer = ref<number | null>(null)
const initialSyncChecked = ref(false)
const preflighting = ref(false)
const metadataFiltersExpanded = ref(false)
const materialTable = ref()
const selectedRows = ref<MaterialItem[]>([])
const metadataOptions = ref<MaterialMetadataOptions>({
  subjects: [],
  countries: [],
  series: [],
  publishers: [],
  languages: [],
  editions: []
})
const metadataDrawerVisible = ref(false)
const metadataSaving = ref(false)
const metadataExtracting = ref(false)
const metadataBatchExtracting = ref(false)
const codexBatchStarting = ref(false)
const workflowV2BatchStarting = ref(false)
const codexStartingIds = ref(new Set<string>())
const workerStartingIds = ref(new Set<string>())
const popoResumeIds = ref(new Set<string>())
const workerStatuses = ref<Record<string, CodexWorkerStatus>>({})
const workflowV2Jobs = ref<Record<string, WorkflowV2JobSummary>>({})

function readUploadResumeContext() {
  try {
    const raw = sessionStorage.getItem(uploadResumeKey)
    if (!raw) return
    const value = JSON.parse(raw) as Partial<UploadResumeContext>
    if (!Array.isArray(value.filenames) || !value.filenames.every(filename => typeof filename === 'string')) {
      sessionStorage.removeItem(uploadResumeKey)
      return
    }
    uploadResumeContext.value = {
      filenames: value.filenames,
      progress: Number.isFinite(value.progress) ? Math.max(0, Math.min(100, Number(value.progress))) : 0,
      updatedAt: typeof value.updatedAt === 'string' ? value.updatedAt : undefined,
    }
  } catch {
    sessionStorage.removeItem(uploadResumeKey)
  }
}

function saveUploadResumeContext(files: File[], progress: number) {
  const context: UploadResumeContext = {
    filenames: files.map(file => file.name),
    progress,
    updatedAt: new Date().toISOString(),
  }
  sessionStorage.setItem(uploadResumeKey, JSON.stringify(context))
  uploadResumeContext.value = context
}

function clearUploadResumeContext() {
  sessionStorage.removeItem(uploadResumeKey)
  uploadResumeContext.value = null
}
const workflowV2PollingTimer = ref<number | null>(null)
const workflowV2DrawerVisible = ref(false)
const workflowV2DetailLoading = ref(false)
const workflowV2ActionLoading = ref(false)
const activeWorkflowV2Job = ref<Record<string, any> | null>(null)
const activeWorkflowV2Candidate = ref<Record<string, any> | null>(null)
const activeWorkflowV2OpenFindingCount = computed(() =>
  (activeWorkflowV2Job.value?.qa_findings || []).filter((item: any) => item.status === 'open').length
)
const activeWorkflowV2VisibleModelCalls = computed(() => {
  const calls = activeWorkflowV2Job.value?.model_calls || []
  const core = calls.filter((call: any) => !String(call.purpose || '').startsWith('independent_visual_qa_pages_'))
  const visual = calls.filter((call: any) => String(call.purpose || '').startsWith('independent_visual_qa_pages_'))
  return [...core, ...visual.slice(-5)]
})
const activeWorkflowV2HiddenModelCallCount = computed(() =>
  Math.max(0, (activeWorkflowV2Job.value?.model_calls?.length || 0) - activeWorkflowV2VisibleModelCalls.value.length)
)
const workerPollingTimer = ref<number | null>(null)
const workerV1Policy = reactive({
  mode: 'audit_only',
  batch_enabled: false,
  auto_retry_enabled: false,
  single_job_audit_enabled: true
})
const metadataForceExtract = ref(false)
const activeMetadataRow = ref<MaterialItem | null>(null)
const metadataForm = reactive<MaterialBookMetadata>({
  id: '',
  material_pk: '',
  original_title: '',
  publication_date: '',
  publication_year: null,
  edition: '',
  subject: '',
  publication_country: '',
  series_name: '',
  publisher: '',
  isbn: '',
  language: '',
  grade_level: '',
  status: 'missing',
  source: 'manual',
  confidence: null,
  manual_override: false,
  evidence: [],
  sample: {},
  extraction_model: '',
  extraction_error: '',
  extracted_at: null,
  created_at: null,
  updated_at: null
})

type RecentOperationStatus = 'started' | 'running' | 'succeeded' | 'failed' | 'opened'
type RecentOperation = {
  materialPk: string
  materialId: string
  filename: string
  action: string
  status: RecentOperationStatus
  runId?: string
  updatedAt: string
}

type HeaderCommand = 'preflight' | 'pipeline'
type RowCommand =
  | 'metadata-edit'
  | 'metadata-extract'
  | 'compare-review'
  | 'resume-popo'
  | 'start-codex'
  | 'run-worker'
  | 'start-worker-v2'
  | 'retry-worker-v2'
  | 'view-worker-v2'
  | 'preview-pdf'
  | 'download-pdf'

type PrimaryRowAction = {
  label: string
  command: RowCommand | null
  enabled: boolean
  type: 'primary' | 'success' | 'warning' | 'info'
  icon: Component
}

type PipelineTarget = {
  material_id?: string
  input_object?: string
  material_ids?: string[]
  input_objects?: string[]
}

const RECENT_OPERATION_STORAGE_KEY = 'luceon.files.recentOperation'
const recentOperation = ref<RecentOperation | null>(loadRecentOperation())
const batchState = reactive({
  running: false,
  stopping: false,
  mode: '',
  total: 0,
  done: 0,
  success: 0,
  failed: 0,
  currentName: '',
  logs: [] as string[]
})

const params = reactive({
  page: 1,
  page_size: 20,
  search: '',
  stage: '',
  metadata_status: '',
  subject: '',
  country: '',
  series: '',
  year_from: null as number | null,
  year_to: null as number | null
})

const metadataFilterCount = computed(() => [
  params.metadata_status,
  params.subject,
  params.country,
  params.series,
  params.year_from,
  params.year_to
].filter(value => value !== '' && value !== null).length)

function clearMetadataFilters() {
  params.metadata_status = ''
  params.subject = ''
  params.country = ''
  params.series = ''
  params.year_from = null
  params.year_to = null
}

const stageMeta: Record<string, { label: string; type: 'primary' | 'success' | 'warning' | 'danger' | 'info' }> = {
  input: { label: 'PDF', type: 'info' },
  mineru_done: { label: 'MinerU', type: 'warning' },
  popo_done: { label: 'Popo', type: 'primary' },
  latex_done: { label: 'LaTeX', type: 'success' },
  raw_done: { label: '旧节点', type: 'info' },
  clean_stale: { label: '旧节点', type: 'info' },
  clean_done: { label: '旧节点', type: 'info' },
  standard_done: { label: '旧节点', type: 'info' },
  failed: { label: '失败', type: 'danger' }
}

function rowStageMeta(row: MaterialItem) {
  if (pipelineBusy.value && isActiveTaskRow(row)) {
    return { label: '解析中', type: 'primary' as const }
  }
  if (row.pipeline_status === 'running' && row.stage_status === 'clean_stale') {
    return { label: '旧节点任务中', type: 'primary' as const }
  }
  if (row.pipeline_status === 'queued' && row.stage_status === 'clean_stale') {
    return { label: '旧节点排队中', type: 'primary' as const }
  }
  return stageMeta[row.stage_status] || { label: row.stage_status || '未知', type: 'info' as const }
}

function currentStageKey(row: MaterialItem) {
  const map: Record<string, string> = {
    input: 'pdf',
    mineru_done: 'mineru',
    popo_done: 'popo',
    latex_done: 'latex',
    raw_done: 'popo',
    clean_stale: 'popo',
    clean_done: 'popo',
    standard_done: 'popo',
    failed: 'pdf'
  }
  return map[row.stage_status] || 'pdf'
}

function rowStageNote(row: MaterialItem) {
  const v2 = workflowV2ForRow(row)
  if (v2) return workflowV2StatusLabel(v2)
  if (pipelineBusy.value && isActiveTaskRow(row)) return 'GPU 解析运行中'
  if (row.pipeline_status === 'running') return '任务运行中'
  if (row.pipeline_status === 'queued') return '任务排队中'
  const workerStatus = workerStatusForRow(row)
  if (workerStatus?.state) return workerStatusText(workerStatus)
  if (codexJobActive(row)) return codexJobStatusText(row.codex_job?.status || '')
  if (hasLatexAsset(row)) return '可进行 PDF 比对'
  if (row.codex_job) return codexJobStatusText(row.codex_job.status)
  if (hasPopoAsset(row)) return '可启动 Worker V2.3'
  if (hasMineruAsset(row)) return '等待 Popo 或继续 GPU 解析'
  if (row.input_object) return '等待上游解析'
  return '缺少源 PDF'
}

function workflowV2ForRow(row: MaterialItem) {
  return workflowV2Jobs.value[row.id] || workflowV2Jobs.value[row.material_id]
}

function hasCurrentWorkflowV2(row: MaterialItem) {
  return workflowV2ForRow(row)?.is_current_workflow === true
}

function workflowV2StageLabel(key: string) {
  const labels: Record<string, string> = {
    canonical_clean_material: '正文守恒重建',
    outline_reconstruction: '目录重建',
    semantic_annotation: '语义标注',
    deterministic_elegantbook: '排版构建',
    bounded_deepseek_polish_qa: '核心门禁',
    bounded_llm_polish: '受限精修',
    independent_final_review: '独立审查'
  }
  return labels[key] || key
}

function workflowV2StatusLabel(job: WorkflowV2JobSummary) {
  const attempt = job.current_attempt > 1 ? ` · 第 ${job.current_attempt} 次` : ''
  const version = job.is_current_workflow ? '' : ' · 早期版本'
  return `${workflowV2StatusText(job.status)}${attempt}${version}`
}

function workflowV2StatusText(status: string) {
  const labels: Record<string, string> = {
    queued: '排队中',
    running: '执行中',
    failed: '技术失败',
    needs_review: '质量阻断 · 待处理',
    succeeded: '已通过',
    cancelled: '已取消'
  }
  return labels[status] || status
}

function workflowV2BlockerLabel(code: string) {
  const labels: Record<string, string> = {
    latex_missing_glyphs: '字体缺字',
    latex_obvious_overflow: '明显横向溢出',
    latex_unresolved_reference_or_resource: '引用或资源未解析',
    latex_project_zip_too_large: 'LaTeX ZIP 超过 50MB',
    latex_project_structure_invalid: 'LaTeX ZIP 目录结构不合规',
    manual_review_handoff_missing: '缺少人工交接记录',
    elegantbook_locked_main_template_changed: '锁定模板发生变化',
    elegantbook_body_defines_custom_latex: '正文新增了自定义 LaTeX 定义'
  }
  return labels[code] || code
}

function workflowV2BlockerEvidence(blocker: Record<string, any>) {
  if (Array.isArray(blocker.characters)) {
    return blocker.characters.slice(0, 12).map((item: any) => {
      const occurrence = item.occurrences?.[0] || {}
      const positions = [
        item.pdf_page_hint ? `PDF 第 ${item.pdf_page_hint} 页` : '',
        occurrence.source_page_idx ? `源页 ${occurrence.source_page_idx}` : '',
        occurrence.line ? `LaTeX 第 ${occurrence.line} 行` : ''
      ].filter(Boolean).join(' · ')
      return `${item.character || item.codepoint} ${item.codepoint || ''}${positions ? ` — ${positions}` : ''}`
    })
  }
  if (Array.isArray(blocker.boxes)) {
    return blocker.boxes.slice(0, 12).map((item: any) => {
      const positions = [
        item.pdf_page_hint ? `PDF 第 ${item.pdf_page_hint} 页` : '',
        item.source_page_idx ? `源页 ${item.source_page_idx}` : '',
        item.line_start ? `LaTeX 第 ${item.line_start}–${item.line_end || item.line_start} 行` : ''
      ].filter(Boolean).join(' · ')
      return `${item.width_pt}pt${positions ? ` — ${positions}` : ''}${item.excerpt ? ` — ${item.excerpt}` : ''}`
    })
  }
  return []
}

function workflowV2ReviewClosureMissing(job: Record<string, any>) {
  const enteredReview = Array.isArray(job?.stages) && job.stages.some((stage: any) => stage.status === 'needs_review')
  const hasHandoff = Array.isArray(job?.repair_attempts) && job.repair_attempts.some((repair: any) => repair.repair_kind === 'manual_handoff')
  return enteredReview && !hasHandoff
}

function workflowV2StageForRow(row: MaterialItem) {
  const job = workflowV2ForRow(row)
  return job ? workflowV2StageLabel(job.current_stage_key) : ''
}

function workflowV2StatusForRow(row: MaterialItem) {
  const job = workflowV2ForRow(row)
  return job ? workflowV2StatusLabel(job) : ''
}

function workflowV2ModelCallsForRow(row: MaterialItem) {
  return workflowV2ForRow(row)?.model_call_count || 0
}

function workflowV2FindingsForRow(row: MaterialItem) {
  return workflowV2ForRow(row)?.open_finding_count || 0
}

function workflowV2ErrorSummary(error: { code?: string; message?: string }) {
  const message = String(error?.message || '').replace(/\s+/g, ' ').split('[SQL:', 1)[0].trim()
  const prefix = error?.code ? `${error.code}：` : ''
  const value = `${prefix}${message}`
  return value.length > 180 ? `${value.slice(0, 177)}...` : value
}

async function openWorkflowV2Drawer(row: MaterialItem) {
  const summary = workflowV2ForRow(row)
  if (!summary) return
  workflowV2DrawerVisible.value = true
  workflowV2DetailLoading.value = true
  activeWorkflowV2Candidate.value = null
  try {
    activeWorkflowV2Job.value = await materialsApi.getWorkflowV2Job(summary.id)
    if (activeWorkflowV2Job.value?.status === 'needs_review') {
      try {
        activeWorkflowV2Candidate.value = await materialsApi.getWorkflowV2ReviewCandidate(summary.id)
      } catch (error: any) {
        if (error?.response?.status !== 404) throw error
      }
    }
  } finally {
    workflowV2DetailLoading.value = false
  }
}

function codexJobStatusText(status: string) {
  const map: Record<string, string> = {
    queued: 'Codex 任务排队中',
    running: 'Codex 任务运行中',
    dry_run_succeeded: 'Codex dry-run 已完成',
    validating: 'Codex 输出验证中',
    published: 'Codex 输出已发布',
    failed: 'Codex 精修失败',
    cancelled: 'Codex 精修已取消'
  }
  return map[status] || `Codex ${status || '未知状态'}`
}

function workerStatusText(status: CodexWorkerStatus) {
  const map: Record<string, string> = {
    queued: 'Worker 已提交',
    running: 'Worker running',
    publishing: 'Worker publishing',
    published: 'Worker published',
    failed: 'Worker failed',
    missing: 'Worker 找不到任务'
  }
  if (status.state === 'failed' && status.message) return `Worker failed：${status.message}`
  return map[status.state] || `Worker ${status.state || 'unknown'}`
}

const stageOptions = [
  { label: 'PDF', value: 'pdf' },
  { label: 'MinerU', value: 'mineru' },
  { label: 'Popo', value: 'popo' },
  { label: 'LaTeX', value: 'latex' },
  { label: '失败', value: 'failed' }
]

const hasRef = (ref: ObjectRef) => Boolean(ref?.bucket && ref?.object)

const artifactStages = [
  { key: 'pdf', label: 'PDF', done: (row: MaterialItem) => Boolean(row.input_object) },
  { key: 'mineru', label: 'MinerU', done: (row: MaterialItem) => row.mineru_available || hasRef(row.mineru_manifest) },
  { key: 'popo', label: 'Popo', done: (row: MaterialItem) => row.popo_available || hasRef(row.popo_manifest) },
  { key: 'latex', label: 'LaTeX', done: (row: MaterialItem) => row.latex_available || hasRef(row.latex_manifest) }
]

const availabilityLine = computed(() => {
  const stages = summary.value?.availability || summary.value?.stages || {}
  const rows = [
    ['PDF', stages.input || 0],
    ['Popo', stages.popo_done || 0],
    ['LaTeX', stages.latex_done || 0]
  ]
  return rows.map(([label, value]) => `${label} ${value}`).join(' · ')
})
const summaryTiles = computed(() => {
  const stages = summary.value?.availability || summary.value?.stages || {}
  return [
    { key: 'all', label: '全部', value: total.value, stage: '' },
    { key: 'pdf', label: 'PDF', value: stages.input || 0, stage: 'pdf' },
    { key: 'popo', label: 'Popo', value: stages.popo_done || 0, stage: 'popo' },
    { key: 'latex', label: 'LaTeX', value: stages.latex_done || 0, stage: 'latex' }
  ]
})

const emptyText = computed(() => {
  if (
    params.search ||
    params.stage ||
    params.metadata_status ||
    params.subject ||
    params.country ||
    params.series ||
    params.year_from ||
    params.year_to
  ) return '没有匹配的材料'
  return '暂无材料'
})

const uploadableFiles = computed(() => uploadFileList.value.map(item => item.raw).filter(Boolean) as File[])
const pipelineBusy = computed(() => pipeline.run?.status === 'queued' || pipeline.run?.status === 'running')
const pipelineSummary = computed(() => asRecord(pipeline.run?.summary))
const pipelinePreflight = computed(() => asRecord(pipelineSummary.value.preflight))
const activeMaterialIds = computed(() => {
  const values = [
    pipelineSummary.value.material_id,
    pipelinePreflight.value.material_id,
    ...(Array.isArray(pipelineSummary.value.material_ids) ? pipelineSummary.value.material_ids : [])
  ]
  return new Set(values.map(value => textValue(value, '')).filter(Boolean))
})
const activeInputObjects = computed(() => {
  const values = [
    pipelineSummary.value.input_object,
    pipelinePreflight.value.input_object,
    ...(Array.isArray(pipelineSummary.value.input_objects) ? pipelineSummary.value.input_objects : [])
  ]
  return new Set(values.map(value => textValue(value, '')).filter(Boolean))
})
const pipelineProgress = computed(() => {
  if (!pipeline.run) return 0
  if (typeof pipeline.run.progress_percent === 'number') return pipeline.run.progress_percent
  if (pipeline.run.status === 'succeeded') return 100
  if (pipeline.run.status === 'failed' || pipeline.run.status === 'partial') return 100
  if (pipeline.run.total > 0) return Math.min(95, Math.round((pipeline.run.processed / pipeline.run.total) * 100))
  return pipeline.run.status === 'running' ? 35 : 10
})
const latestPipelineEvent = computed(() => pipeline.events[0] || null)
const taskTickerTone = computed(() => {
  if (pipeline.run?.status === 'failed') return 'danger'
  if (pipeline.run?.status === 'partial') return 'warning'
  if (preflight.value && !preflight.value.ready) return 'warning'
  if (pipelineBusy.value) return 'active'
  if (pipeline.run?.status === 'succeeded' || preflight.value?.ready) return 'success'
  return 'idle'
})
const pipelineStateIcon = computed(() => {
  if (pipeline.run?.status === 'failed' || pipeline.run?.status === 'partial' || (preflight.value && !preflight.value.ready)) return WarningFilled
  if (pipelineBusy.value) return Timer
  if (pipeline.run?.status === 'succeeded' || preflight.value?.ready) return CircleCheckFilled
  return Timer
})
const pipelineStateLabel = computed(() => {
  if (pipeline.run) return '当前任务'
  if (preflight.value) return '预检结果'
  return '任务状态'
})
const pipelineHeadline = computed(() => {
  if (pipeline.run) return `${pipelineModeText(pipeline.run.mode)} · ${pipelineStatusText(pipeline.run.status)}`
  if (preflight.value) return preflight.value.ready ? '解析预检通过' : '解析预检未通过'
  if (summary.value?.latest_run) {
    const run = summary.value.latest_run
    return `${pipelineModeText(run.mode)} · ${pipelineStatusText(run.status)}`
  }
  return '空闲'
})
const pipelineDetail = computed(() => {
  if (pipeline.run) {
    const progress = `${pipeline.run.processed}/${pipeline.run.total || '?'} · ${pipelineProgress.value}%`
    if (pipeline.run.error_message) return `${progress} · ${pipeline.run.error_message}`
    if (latestPipelineEvent.value) return `${progress} · ${latestPipelineEvent.value.message}`
    return progress
  }
  if (preflight.value) {
    return preflight.value.ready ? `待提交 ${preflight.value.selected_count} 个 PDF` : preflightFailureText(preflight.value)
  }
  if (summary.value?.latest_run) return formatDateTime(summary.value.latest_run.created_at)
  return availabilityLine.value || '等待材料'
})
const metadataEvidenceRows = computed(() => {
  return (metadataForm.evidence || []).slice(0, 8).map(item => ({
    field: String(item.field || '证据'),
    quote: String(item.quote || ''),
    source: String(item.source || 'sample')
  })).filter(item => item.quote)
})
const metadataSampleLabel = computed(() => {
  const chars = typeof metadataForm.sample?.sampled_chars === 'number' ? metadataForm.sample.sampled_chars : 0
  const strategy = typeof metadataForm.sample?.strategy === 'string' ? metadataForm.sample.strategy : ''
  if (!chars && !strategy) return '文件名 + 前部文本 + 关键词窗口'
  return `${chars ? `${chars.toLocaleString('zh-CN')} 字符样本` : '文本样本'}`
})
const orderedMaterials = computed(() => {
  const recent = recentOperation.value
  return [...materials.value].sort((a, b) => rowPriority(a, recent) - rowPriority(b, recent))
})
const selectedCodexStartableRows = computed(() => selectedRows.value.filter(canStartCodex))
const selectedWorkflowV23StartableRows = computed(() => selectedRows.value.filter(
  row => hasPopoAsset(row) && !hasCurrentWorkflowV2(row)
))

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function textValue(value: unknown, fallback = '未记录') {
  return typeof value === 'string' && value.trim() ? value : fallback
}

type FilterSuggestion = { value: string }

function suggestSeries(query: string, cb: (items: FilterSuggestion[]) => void) {
  const keyword = query.trim().toLowerCase()
  const options = metadataOptions.value.series || []
  const matched = options
    .filter(item => !keyword || item.toLowerCase().includes(keyword))
    .slice(0, 12)
    .map(value => ({ value }))
  if (keyword && !matched.some(item => item.value.toLowerCase() === keyword)) {
    matched.unshift({ value: query.trim() })
  }
  cb(matched)
}

function loadRecentOperation(): RecentOperation | null {
  try {
    const raw = window.localStorage.getItem(RECENT_OPERATION_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as RecentOperation
    return parsed?.materialPk || parsed?.materialId ? parsed : null
  } catch {
    return null
  }
}

function saveRecentOperation(operation: RecentOperation | null) {
  recentOperation.value = operation
  if (!operation) {
    window.localStorage.removeItem(RECENT_OPERATION_STORAGE_KEY)
    return
  }
  window.localStorage.setItem(RECENT_OPERATION_STORAGE_KEY, JSON.stringify(operation))
}

function rememberOperation(row: MaterialItem, action: string, status: RecentOperationStatus = 'started', run?: PipelineRun) {
  saveRecentOperation({
    materialPk: row.id,
    materialId: row.material_id || '',
    filename: row.filename || row.title || row.material_id || row.id,
    action,
    status,
    runId: run?.id,
    updatedAt: new Date().toISOString()
  })
}

function clearRecentOperation() {
  saveRecentOperation(null)
}

async function locateRecentOperation() {
  const recent = recentOperation.value
  if (!recent) return
  params.search = recent.materialId || recent.filename || recent.materialPk
  params.page = 1
  await fetchMaterials()
}

function operationStatusText(status: RecentOperationStatus) {
  const map: Record<RecentOperationStatus, string> = {
    opened: '已打开',
    started: '已启动',
    running: '执行中',
    succeeded: '已完成',
    failed: '失败'
  }
  return map[status] || status
}

function isActiveTaskRow(row: MaterialItem) {
  return activeMaterialIds.value.has(row.material_id) || activeInputObjects.value.has(row.input_object)
}

function isRecentOperationRow(row: MaterialItem) {
  const recent = recentOperation.value
  return Boolean(recent && (row.id === recent.materialPk || (!!recent.materialId && row.material_id === recent.materialId)))
}

function pipelineTargetForRow(row: MaterialItem | null | undefined): PipelineTarget {
  if (!row) return {}
  return {
    material_id: row.material_id || undefined,
    input_object: row.input_object || undefined
  }
}

function pipelineTargetForRows(rows: MaterialItem[]): PipelineTarget {
  const materialIds = [...new Set(rows.map(row => row.material_id).filter(Boolean))]
  const inputObjects = [...new Set(rows.map(row => row.input_object).filter(Boolean))]
  return {
    material_ids: materialIds,
    input_objects: inputObjects
  }
}

function currentPipelineTarget(): PipelineTarget {
  if (selectedRows.value.length) {
    return pipelineTargetForRows(selectedRows.value.slice(0, 5))
  }
  const recent = recentOperation.value
  if (recent) {
    const recentRow = materials.value.find(row => row.id === recent.materialPk || (!!recent.materialId && row.material_id === recent.materialId))
    if (recentRow && (params.search === recent.materialId || params.search === recent.filename || params.search === recent.materialPk)) {
      return pipelineTargetForRow(recentRow)
    }
  }
  if (params.search && orderedMaterials.value.length === 1) {
    return pipelineTargetForRow(orderedMaterials.value[0])
  }
  return {}
}

function rowPriority(row: MaterialItem, recent: RecentOperation | null) {
  if (workerRunning(row)) return 0
  if (recent && (row.id === recent.materialPk || (!!recent.materialId && row.material_id === recent.materialId))) return 1
  if (isActiveTaskRow(row)) return 2
  return 3
}

function materialRowClassName({ row }: { row: MaterialItem }) {
  if (isActiveTaskRow(row)) return 'is-active-task-row'
  if (isRecentOperationRow(row)) return 'is-recent-operation-row'
  return ''
}

function pipelineStatusText(status: string) {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    succeeded: '已完成',
    partial: '部分完成',
    failed: '失败',
    idle: '空闲'
  }
  return map[status] || status
}

function pipelineModeText(mode: string) {
  if (mode.startsWith('popo2latex')) return '旧 LaTeX 任务'
  const map: Record<string, string> = {
    apply: 'PDF解析',
    dry_run: '预检',
    popo2raw: '旧节点',
    raw2clean: '旧节点',
    clean2standard: '旧节点'
  }
  return map[mode] || mode
}

function formatDateTime(value?: string | null) {
  return value ? formatDate(value) : ''
}

function applyStageFilter(stage: string) {
  params.stage = stage
  params.page = 1
}

function compactMaterialId(row: MaterialItem) {
  const id = row.material_id || row.id || ''
  if (!id) return '未绑定 ID'
  return id.length > 18 ? `${id.slice(0, 10)}...${id.slice(-4)}` : id
}

function displayTitle(row: MaterialItem) {
  return row.book_metadata?.original_title || row.title || row.filename
}

function metadataSubtitle(row: MaterialItem) {
  const title = row.book_metadata?.original_title || ''
  return title && title !== row.filename ? row.filename : ''
}

function metadataChips(row: MaterialItem) {
  const meta = row.book_metadata
  if (!meta) return []
  return [
    meta.subject,
    meta.publication_year ? String(meta.publication_year) : '',
    meta.edition,
    meta.publication_country,
    meta.series_name
  ].filter(Boolean).slice(0, 4)
}

function metadataStatusLabel(meta?: MaterialBookMetadata | null) {
  if (!meta || meta.status === 'missing') return '待提取'
  if (meta.status === 'ai_extracted') return meta.confidence ? `AI ${Math.round(meta.confidence * 100)}%` : 'AI 已提取'
  if (meta.status === 'manual') return '人工已改'
  if (meta.status === 'failed') return '提取失败'
  return meta.status
}

function metadataStatusType(meta?: MaterialBookMetadata | null) {
  if (!meta || meta.status === 'missing') return 'info'
  if (meta.status === 'ai_extracted') return 'success'
  if (meta.status === 'manual') return 'warning'
  if (meta.status === 'failed') return 'danger'
  return 'info'
}

function applyMetadataToRow(row: MaterialItem, metadata: MaterialBookMetadata) {
  row.book_metadata = metadata
  if (activeMetadataRow.value?.id === row.id) {
    activeMetadataRow.value = row
  }
}

function resetMetadataForm(metadata?: MaterialBookMetadata | null) {
  const source = metadata || {
    id: '',
    material_pk: '',
    original_title: '',
    publication_date: '',
    publication_year: null,
    edition: '',
    subject: '',
    publication_country: '',
    series_name: '',
    publisher: '',
    isbn: '',
    language: '',
    grade_level: '',
    status: 'missing',
    source: 'manual',
    confidence: null,
    manual_override: false,
    evidence: [],
    sample: {},
    extraction_model: '',
    extraction_error: '',
    extracted_at: null,
    created_at: null,
    updated_at: null
  }
  Object.assign(metadataForm, JSON.parse(JSON.stringify(source)))
}

async function fetchMaterials() {
  const requestSerial = ++materialsRequestSerial
  loading.value = true
  try {
    const data = await materialsApi.getMaterials(params)
    if (requestSerial !== materialsRequestSerial) return
    materials.value = data.materials
    total.value = data.total
  } finally {
    if (requestSerial === materialsRequestSerial) loading.value = false
  }
}

async function fetchSummary() {
  summary.value = await materialsApi.getSummary()
}

async function fetchMetadataOptions() {
  metadataOptions.value = await materialsApi.getMetadataOptions()
}

async function fetchPipeline() {
  const data = await materialsApi.getPipelineStatus()
  pipeline.run = data.run
  pipeline.events = data.events
  syncRecentOperationFromPipeline()
  updatePipelinePolling()
}

async function fetchWorkflowV2Jobs() {
  const jobs = await materialsApi.getWorkflowV2JobSummaries()
  const next: Record<string, WorkflowV2JobSummary> = {}
  for (const job of jobs) {
    if (!next[job.material_pk]) next[job.material_pk] = job
    if (!next[job.material_id]) next[job.material_id] = job
  }
  workflowV2Jobs.value = next
}

function syncRecentOperationFromPipeline() {
  const run = pipeline.run
  if (!run) return
  const activeRow = materials.value.find(isActiveTaskRow)
  const recent = recentOperation.value
  if (activeRow && (!recent || String(recent.runId || '') !== String(run.id))) {
    saveRecentOperation({
      materialPk: activeRow.id,
      materialId: activeRow.material_id,
      filename: displayTitle(activeRow),
      action: 'GPU 解析',
      status: run.status === 'succeeded' ? 'succeeded' : ['failed', 'partial'].includes(run.status) ? 'failed' : 'running',
      runId: run.id,
      updatedAt: new Date().toISOString()
    })
    return
  }
  if (!recent) return
  const sameMaterial = activeMaterialIds.value.has(recent.materialId)
  const sameRun = recent.runId && String(recent.runId) === String(run.id)
  if (!sameMaterial && !sameRun) return
  const nextStatus: RecentOperationStatus =
    run.status === 'succeeded' ? 'succeeded' : ['failed', 'partial'].includes(run.status) ? 'failed' : pipelineBusy.value ? 'running' : recent.status
  if (nextStatus !== recent.status || recent.runId !== run.id) {
    saveRecentOperation({ ...recent, status: nextStatus, runId: run.id, updatedAt: new Date().toISOString() })
  }
}

async function loadDashboard() {
  const policyResult = await Promise.allSettled([
    fetchMaterials(),
    fetchSummary(),
    fetchPipeline(),
    fetchMetadataOptions(),
    materialsApi.getWorkerV1Policy(),
    fetchWorkflowV2Jobs()
  ])
  const policy = policyResult[4]
  if (policy.status === 'fulfilled') Object.assign(workerV1Policy, policy.value)
  await hydrateWorkerStatuses()
  if (!initialSyncChecked.value && total.value === 0) {
    initialSyncChecked.value = true
    await syncMaterials(false)
  }
}

async function syncMaterials(showMessage = true) {
  syncing.value = true
  try {
    const data = await materialsApi.sync()
    await Promise.all([fetchMaterials(), fetchSummary(), fetchPipeline()])
    if (showMessage) {
      ElMessage.success(`同步完成：${data.total} 份材料`)
    }
  } finally {
    syncing.value = false
  }
}

async function submitUpload() {
  if (!uploadableFiles.value.length) return
  try {
    await fetchCurrentUser()
  } catch {
    uploadError.value = '登录已失效。文件选择已保留，请重新登录后继续上传。'
    saveUploadResumeContext(uploadableFiles.value, 0)
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  uploadError.value = ''
  saveUploadResumeContext(uploadableFiles.value, 0)
  try {
    const data = await materialsApi.upload(uploadableFiles.value, progress => {
      uploadProgress.value = progress
      saveUploadResumeContext(uploadableFiles.value, progress)
    })
    const uploadedMaterials = data.files
      .filter(item => item.status === 'success' && item.material)
      .map(item => item.material as MaterialItem)
    uploadDialogVisible.value = false
    uploadFileList.value = []
    clearUploadResumeContext()
    if (uploadedMaterials.length) {
      const latestMaterial = uploadedMaterials[uploadedMaterials.length - 1]
      params.page = 1
      params.stage = ''
      params.search = ''
      clearMetadataFilters()
      metadataFiltersExpanded.value = false
      saveRecentOperation({
        materialPk: latestMaterial.id,
        materialId: latestMaterial.material_id || '',
        filename: uploadedMaterials.length > 1
          ? `${latestMaterial.filename || latestMaterial.title} 等 ${uploadedMaterials.length} 份`
          : latestMaterial.filename || latestMaterial.title || latestMaterial.id,
        action: `上传 ${uploadedMaterials.length} 份 PDF`,
        status: 'succeeded',
        updatedAt: new Date().toISOString()
      })
    }
    await Promise.all([fetchMaterials(), fetchSummary()])
    ElMessage.success(uploadedMaterials.length
      ? `上传完成：${data.success} 个 PDF 已置顶`
      : `上传完成：${data.success} 个成功`)
  } catch (error: any) {
    uploadError.value = error?.isAuthExpired
      ? '登录在上传期间失效。文件选择和最后进度已保留，请重新登录后继续。'
      : error?.code === 'ECONNABORTED'
      ? '上传超时，文件没有进入材料库。请保留文件并重新提交。'
      : '上传中断，文件没有进入材料库。请保留当前文件并重新提交。'
  } finally {
    uploading.value = false
  }
}

function preflightFailureText(result: PipelinePreflightResponse) {
  const reasons: string[] = []
  const resourceGate = result.resource_gate
  if (resourceGate?.applies && !resourceGate.ok) {
    reasons.push(`${resourceGate.reason || 'GPU 临时产物余量不足'}（当前 ${formatFileSize(resourceGate.available_headroom_bytes || 0)}，要求至少 ${formatFileSize(resourceGate.required_headroom_bytes || 0)}）`)
  }
  if (!result.gpu_ok) reasons.push('GPU 服务未就绪')
  if (!result.staged_api_ok) reasons.push('分段 MinerU/Popo API 不可用')
  if (result.active_marker_count > 0) reasons.push(`存在 ${result.active_marker_count} 个 active marker`)
  if (result.selected_count <= 0) reasons.push('没有可提交 PDF')
  return reasons.join('；') || `状态：${result.status}`
}

function preflightSummaryRows(result: PipelinePreflightResponse) {
  const rows = [
    `GPU服务：${result.gpu_ok ? '正常' : '异常'}`,
    `分段接口：${result.staged_api_ok ? '可用' : '不可用'}`,
    `待提交PDF：${result.selected_count} 个`,
    `Active Marker：${result.active_marker_count} 个`,
    `预检状态：${result.status}`
  ]
  if (result.resource_gate?.applies) {
    rows.splice(4, 0, `超大PDF资源门禁：${result.resource_gate.ok ? '通过' : '拦截'}；余量 ${formatFileSize(result.resource_gate.available_headroom_bytes || 0)} / 要求 ${formatFileSize(result.resource_gate.required_headroom_bytes || 0)}`)
  }
  return rows
}

function preflightConfirmContent(result: PipelinePreflightResponse) {
  return h(
    'div',
    { class: 'preflight-confirm' },
    preflightSummaryRows(result).map(row => h('p', { style: 'margin: 0 0 6px;' }, row))
  )
}

async function runPreflightCheck(showMessage: boolean) {
  preflighting.value = true
  try {
    const target = currentPipelineTarget()
    const result = await materialsApi.preflightPipeline(5, target)
    preflight.value = result
    if (showMessage) {
      if (result.ready) {
        ElMessage.success(`预检通过：待提交 ${result.selected_count} 个 PDF`)
      } else {
        ElMessage.warning(`预检未通过：${preflightFailureText(result)}`)
      }
    }
    return result
  } finally {
    preflighting.value = false
  }
}

async function runPreflight() {
  await runPreflightCheck(true)
}

async function handleHeaderCommand(command: HeaderCommand) {
  if (command === 'preflight') {
    await runPreflight()
    return
  }
  await startPipeline()
}

async function startPipeline() {
  const target = currentPipelineTarget()
  const result = await runPreflightCheck(false)
  if (!result.ready) {
    ElMessage.warning(`启动已拦截：${preflightFailureText(result)}`)
    return
  }
  await ElMessageBox.confirm(
    preflightConfirmContent(result),
    '启动GPU解析',
    { type: 'warning', confirmButtonText: '启动', cancelButtonText: '取消' }
  )
  await materialsApi.startPipeline(true, 5, target)
  await fetchPipeline()
  ElMessage.success('解析任务已启动')
}

function hasPopoAsset(row: MaterialItem) {
  return Boolean(row.popo_available || hasRef(row.popo_manifest))
}

function hasMineruAsset(row: MaterialItem) {
  return Boolean(row.mineru_available || hasRef(row.mineru_manifest))
}

function hasLatexAsset(row: MaterialItem) {
  const workflowV2 = workflowV2ForRow(row)
  return Boolean(
    row.latex_available
      || hasRef(row.latex_manifest)
      || (row.codex_job?.status === 'published' && hasRef(row.codex_job.output_manifest))
      || (
        workflowV2?.status === 'succeeded'
        && workflowV2.current_stage_key === 'bounded_deepseek_polish_qa'
      )
  )
}

function canStartCodex(row: MaterialItem) {
  return hasPopoAsset(row) && !codexJobActive(row) && row.codex_job?.status !== 'published'
}

function codexJobActive(row: MaterialItem) {
  return ['queued', 'running', 'dry_run_succeeded', 'validating'].includes(row.codex_job?.status || '')
}

function workerStatusForRow(row: MaterialItem) {
  const jobId = row.codex_job?.id || ''
  return jobId ? workerStatuses.value[jobId] : null
}

function workerRunning(row: MaterialItem) {
  const status = workerStatusForRow(row)
  return Boolean(status?.running || status?.state === 'running' || status?.state === 'publishing')
}

function canRunCodexWorker(row: MaterialItem) {
  const job = row.codex_job
  if (!job?.id) return false
  if (workerRunning(row)) return false
  return ['queued', 'dry_run_succeeded'].includes(job.status || '')
}

function primaryActionLoading(row: MaterialItem) {
  const jobId = row.codex_job?.id || ''
  return Boolean(popoResumeIds.value.has(row.id) || (jobId && workerStartingIds.value.has(jobId)) || workerRunning(row))
}

function primaryAction(row: MaterialItem): PrimaryRowAction {
  const workflowV2 = workflowV2ForRow(row)
  if (workflowV2 && !workflowV2.is_current_workflow && hasPopoAsset(row)) {
    return { label: '升级到 V2.3', command: 'start-worker-v2', enabled: true, type: 'primary', icon: Cpu }
  }
  if (workflowV2?.status === 'needs_review') {
    return { label: '处理 V2.3 质量阻断', command: 'view-worker-v2', enabled: true, type: 'warning', icon: View }
  }
  if (workflowV2?.status === 'failed') {
    return { label: '查看 V2.3 问题', command: 'view-worker-v2', enabled: true, type: 'warning', icon: View }
  }
  if (workflowV2 && ['queued', 'running'].includes(workflowV2.status)) {
    return { label: workflowV2StatusLabel(workflowV2), command: null, enabled: false, type: 'info', icon: Timer }
  }
  if (!workflowV2 && hasPopoAsset(row)) {
    return { label: '启动 Worker V2.3', command: 'start-worker-v2', enabled: true, type: 'primary', icon: Cpu }
  }
  const workerStatus = workerStatusForRow(row)
  if (workerStatus?.state === 'running' || workerStatus?.state === 'publishing') {
    return {
      label: workerStatusText(workerStatus),
      command: null,
      enabled: false,
      type: 'warning',
      icon: Timer
    }
  }
  if (canRunCodexWorker(row)) {
    return {
      label: '运行 Worker',
      command: 'run-worker',
      enabled: true,
      type: 'warning',
      icon: VideoPlay
    }
  }
  if (hasLatexAsset(row)) {
    return {
      label: 'PDF 比对',
      command: 'compare-review',
      enabled: true,
      type: 'primary',
      icon: View
    }
  }
  if (hasPopoAsset(row)) {
    const codexStatus = row.codex_job?.status || ''
    if (!codexStatus || codexStatus === 'failed' || codexStatus === 'cancelled') {
      return {
        label: '启动 Codex 精修',
        command: 'start-codex',
        enabled: true,
        type: 'warning',
        icon: Cpu
      }
    }
    return {
      label: codexJobStatusText(codexStatus),
      command: null,
      enabled: false,
      type: 'info',
      icon: Timer
    }
  }
  if (row.input_object) {
    return {
      label: '打开 PDF',
      command: 'preview-pdf',
      enabled: true,
      type: 'info',
      icon: Document
    }
  }
  return {
    label: '等待同步',
    command: null,
    enabled: false,
    type: 'info',
    icon: Timer
  }
}

function runPrimaryAction(row: MaterialItem) {
  const action = primaryAction(row)
  if (!action.enabled || !action.command) return
  handleRowCommand(row, action.command)
}

function handleSelectionChange(rows: MaterialItem[]) {
  selectedRows.value = rows
}

async function openMetadataDrawer(row: MaterialItem) {
  activeMetadataRow.value = row
  metadataDrawerVisible.value = true
  metadataForceExtract.value = false
  resetMetadataForm(row.book_metadata)
  try {
    const metadata = await materialsApi.getMetadata(row.id)
    applyMetadataToRow(row, metadata)
    resetMetadataForm(metadata)
  } catch (error) {
    ElMessage.warning(error instanceof Error ? error.message : '元数据加载失败')
  }
}

async function extractRowMetadata(row: MaterialItem, force = false, openAfter = false) {
  if (openAfter) {
    activeMetadataRow.value = row
    metadataDrawerVisible.value = true
    resetMetadataForm(row.book_metadata)
  }
  metadataExtracting.value = true
  try {
    const metadata = await materialsApi.extractMetadata(row.id, force)
    applyMetadataToRow(row, metadata)
    if (activeMetadataRow.value?.id === row.id) resetMetadataForm(metadata)
    await fetchMetadataOptions()
    ElMessage.success('元数据已提取')
  } catch (error: any) {
    const message = error?.response?.data?.detail || error?.message || '元数据提取失败'
    ElMessage.warning(message)
    if (openAfter) {
      try {
        const metadata = await materialsApi.getMetadata(row.id)
        applyMetadataToRow(row, metadata)
        resetMetadataForm(metadata)
      } catch {
        // keep the drawer open with existing local values
      }
    }
  } finally {
    metadataExtracting.value = false
  }
}

async function extractActiveMetadata() {
  if (!activeMetadataRow.value) return
  await extractRowMetadata(activeMetadataRow.value, metadataForceExtract.value, false)
}

async function saveActiveMetadata() {
  const row = activeMetadataRow.value
  if (!row) return
  metadataSaving.value = true
  try {
    const metadata = await materialsApi.updateMetadata(row.id, {
      original_title: metadataForm.original_title,
      publication_date: metadataForm.publication_date,
      publication_year: metadataForm.publication_year,
      edition: metadataForm.edition,
      subject: metadataForm.subject,
      publication_country: metadataForm.publication_country,
      series_name: metadataForm.series_name,
      publisher: metadataForm.publisher,
      isbn: metadataForm.isbn,
      language: metadataForm.language,
      grade_level: metadataForm.grade_level,
      confidence: metadataForm.confidence,
      evidence: metadataForm.evidence
    })
    applyMetadataToRow(row, metadata)
    resetMetadataForm(metadata)
    await fetchMetadataOptions()
    ElMessage.success('元数据已保存')
  } finally {
    metadataSaving.value = false
  }
}

async function extractSelectedMetadata() {
  const rows = [...selectedRows.value]
  if (!rows.length) return
  try {
    await ElMessageBox.confirm(
      `将对已选 ${rows.length} 份材料按顺序进行 AI 元数据提取。模型只会接收文件名、前部文本样本和关键词窗口。`,
      'AI 补全元数据',
      { type: 'warning', confirmButtonText: '开始提取', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  metadataBatchExtracting.value = true
  let success = 0
  let failed = 0
  try {
    for (const row of rows) {
      try {
        const metadata = await materialsApi.extractMetadata(row.id, false)
        applyMetadataToRow(row, metadata)
        success += 1
      } catch {
        failed += 1
      }
    }
  } finally {
    metadataBatchExtracting.value = false
  }
  await fetchMetadataOptions()
  if (failed) {
    ElMessage.warning(`元数据提取结束：成功 ${success}，失败 ${failed}`)
  } else {
    ElMessage.success(`元数据提取完成：${success} 份`)
  }
}

function stopBatchAfterCurrent() {
  batchState.stopping = true
  ElMessage.info('已设置为当前任务完成后停止')
}

function codexSkillVersion() {
  return `manual-${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}`
}

function codexModeForRow(row: MaterialItem) {
  return hasLatexAsset(row) ? 'refresh_legacy' : 'new_pdf'
}

function codexRunReasonForRow(row: MaterialItem, batch = false) {
  if (hasLatexAsset(row)) return batch ? 'batch_refresh_legacy' : 'manual_refresh_legacy'
  return batch ? 'batch_new_pdf' : 'manual_new_pdf'
}

async function startCodexJob(row: MaterialItem, batch = false) {
  if (!canStartCodex(row)) {
    ElMessage.warning('该材料暂不能启动 Codex 任务')
    return null
  }
  const next = new Set(codexStartingIds.value)
  next.add(row.id)
  codexStartingIds.value = next
  try {
    const job = await materialsApi.createCodexJob(row.id, {
      mode: codexModeForRow(row),
      skill_version: codexSkillVersion(),
      run_reason: codexRunReasonForRow(row, batch),
      force: true,
      payload: {
        source: batch ? 'files_batch_action' : 'files_row_action',
        previous_job_id: row.codex_job?.id || '',
        had_legacy_output: hasLatexAsset(row)
      }
    })
    row.codex_job = job
    recentOperation.value = {
      materialPk: row.id,
      materialId: row.material_id,
      filename: displayTitle(row),
      action: hasLatexAsset(row) ? 'Codex 重扫已入队' : 'Codex 精修已入队',
      status: 'started',
      runId: job.id,
      updatedAt: new Date().toISOString()
    }
    if (!batch) ElMessage.success(`Codex 任务已入队：#${job.id}`)
    return job
  } catch (error: any) {
    if (!batch) {
      const message = error?.response?.data?.detail || error?.message || '创建 Codex 任务失败'
      ElMessage.warning(message)
    }
    return null
  } finally {
    const current = new Set(codexStartingIds.value)
    current.delete(row.id)
    codexStartingIds.value = current
  }
}

async function startWorkflowV2(row: MaterialItem) {
  if (!hasPopoAsset(row)) {
    ElMessage.warning('材料尚未完成 Popo，不能启动 Worker V2.3')
    return
  }
  workflowV2ActionLoading.value = true
  try {
    const created = await materialsApi.createWorkflowV2Job(row.id)
    await materialsApi.runWorkflowV2Job(created.job.id)
    ElMessage.success('Worker V2.3 已入队，将自动逐阶段执行')
    await fetchWorkflowV2Jobs()
  } catch (error: any) {
    ElMessage.warning(error?.response?.data?.detail || error?.message || 'Worker V2.3 启动失败')
  } finally {
    workflowV2ActionLoading.value = false
  }
}

async function retryWorkflowV2(row: MaterialItem) {
  const job = workflowV2ForRow(row)
  if (!job || job.status !== 'failed') return
  workflowV2ActionLoading.value = true
  try {
    await materialsApi.retryWorkflowV2Job(job.id)
    ElMessage.success('失败阶段已创建新尝试并重新入队')
    await fetchWorkflowV2Jobs()
    if (workflowV2DrawerVisible.value) activeWorkflowV2Job.value = await materialsApi.getWorkflowV2Job(job.id)
  } catch (error: any) {
    ElMessage.warning(error?.response?.data?.detail || error?.message || 'Worker V2.3 重试失败')
  } finally {
    workflowV2ActionLoading.value = false
  }
}

async function retryActiveWorkflowV2() {
  const materialPk = activeWorkflowV2Job.value?.material_pk
  const row = materials.value.find(item => String(item.id) === String(materialPk))
  if (row) await retryWorkflowV2(row)
}

function openWorkflowV2CandidatePdf() {
  const url = activeWorkflowV2Candidate.value?.files?.pdf
  if (url) window.open(url, '_blank', 'noopener')
}

function downloadWorkflowV2CandidateLatex() {
  const url = activeWorkflowV2Candidate.value?.files?.latex_zip
  if (!url) return
  const link = document.createElement('a')
  link.href = url
  link.download = 'latex-project-needs-review.zip'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

async function handoffActiveWorkflowV2() {
  const jobId = activeWorkflowV2Job.value?.id
  if (!jobId) return
  workflowV2ActionLoading.value = true
  try {
    const result = await materialsApi.handoffWorkflowV2ReviewCandidate(jobId)
    activeWorkflowV2Job.value = result.job
    ElMessage.success('候选件已登记为人工处理，原始候选件保持不可变')
  } catch (error: any) {
    ElMessage.warning(error?.response?.data?.detail || error?.message || '转人工处理失败')
  } finally {
    workflowV2ActionLoading.value = false
  }
}

async function revalidateActiveWorkflowV2() {
  const jobId = activeWorkflowV2Job.value?.id
  if (!jobId) return
  workflowV2ActionLoading.value = true
  try {
    const result = await materialsApi.revalidateWorkflowV2ReviewCandidate(jobId)
    activeWorkflowV2Job.value = result.job
    activeWorkflowV2Candidate.value = null
    await fetchWorkflowV2Jobs()
    ElMessage.success('已从当前阶段创建新尝试并进入重新验证')
  } catch (error: any) {
    ElMessage.warning(error?.response?.data?.detail || error?.message || '重新验证失败')
  } finally {
    workflowV2ActionLoading.value = false
  }
}

async function restartActiveWorkflowV2CurrentStage() {
  const jobId = activeWorkflowV2Job.value?.id
  const stageKey = activeWorkflowV2Job.value?.current_stage_key
  if (!jobId || !stageKey) return
  workflowV2ActionLoading.value = true
  try {
    const result = await materialsApi.restartWorkflowV2Job(jobId, stageKey)
    activeWorkflowV2Job.value = result.job
    await fetchWorkflowV2Jobs()
    ElMessage.success('已从当前阶段创建新尝试并进入重新验证')
  } catch (error: any) {
    ElMessage.warning(error?.response?.data?.detail || error?.message || '当前阶段重新验证失败')
  } finally {
    workflowV2ActionLoading.value = false
  }
}

async function restartActiveWorkflowV2LatexBuild() {
  const jobId = activeWorkflowV2Job.value?.id
  if (!jobId) return
  workflowV2ActionLoading.value = true
  try {
    const result = await materialsApi.restartWorkflowV2Job(jobId, 'deterministic_elegantbook')
    activeWorkflowV2Job.value = result.job
    activeWorkflowV2Candidate.value = null
    await fetchWorkflowV2Jobs()
    ElMessage.success('已保留上游冻结产物，并从排版构建创建新尝试')
  } catch (error: any) {
    ElMessage.warning(error?.response?.data?.detail || error?.message || '排版构建重新运行失败')
  } finally {
    workflowV2ActionLoading.value = false
  }
}

async function startSelectedCodexJobs() {
  const rows = selectedCodexStartableRows.value
  if (!rows.length) return
  try {
    await ElMessageBox.confirm(
      `将为已选 ${rows.length} 份材料创建新的 Codex 异步任务。任务只入队，不会在浏览器请求中直接执行长时间精修。`,
      '批量 Codex 重扫',
      { type: 'warning', confirmButtonText: '创建任务', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  codexBatchStarting.value = true
  let success = 0
  let failed = 0
  try {
    for (const row of rows) {
      const job = await startCodexJob(row, true)
      if (job) success += 1
      else failed += 1
    }
  } finally {
    codexBatchStarting.value = false
  }
  await fetchMaterials()
  if (failed) ElMessage.warning(`Codex 任务创建结束：成功 ${success}，失败 ${failed}`)
  else ElMessage.success(`已创建 ${success} 个 Codex 任务`)
}

async function startSelectedWorkflowV23Jobs() {
  const rows = selectedWorkflowV23StartableRows.value
  if (!rows.length) return
  try {
    await ElMessageBox.confirm(
      `将把已选 ${rows.length} 份已完成 Popo 的材料人工升级到 Worker V2.3。未选材料不会重刷。`,
      '批量升级到 Worker V2.3',
      { type: 'warning', confirmButtonText: '确认升级', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  workflowV2BatchStarting.value = true
  let success = 0
  let failed = 0
  try {
    for (const row of rows) {
      try {
        const created = await materialsApi.createWorkflowV2Job(row.id, 'files_manual_batch_v2_3')
        await materialsApi.runWorkflowV2Job(created.job.id)
        success += 1
      } catch {
        failed += 1
      }
    }
  } finally {
    workflowV2BatchStarting.value = false
  }
  await fetchWorkflowV2Jobs()
  if (failed) ElMessage.warning(`Worker V2.3 批量升级结束：成功 ${success}，失败 ${failed}`)
  else ElMessage.success(`已人工提交 ${success} 个 Worker V2.3 任务`)
}

async function runCodexWorker(row: MaterialItem) {
  const jobId = row.codex_job?.id || ''
  if (!jobId || !canRunCodexWorker(row)) {
    ElMessage.warning('该材料没有可运行的 Codex job')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将调用本机 Worker 执行 Codex job #${jobId}，完成后自动发布 ElegantBook 输出并刷新材料列表。`,
      '运行 Worker',
      { type: 'warning', confirmButtonText: '运行 Worker', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  const next = new Set(workerStartingIds.value)
  next.add(jobId)
  workerStartingIds.value = next
  try {
    const status = await codexWorkerApi.runJob(jobId)
    workerStatuses.value = { ...workerStatuses.value, [jobId]: status }
    rememberOperation(row, 'Worker 已启动', 'running')
    ensureWorkerPolling()
    ElMessage.success(`Worker 已提交：job #${jobId}`)
  } catch (error: any) {
    const message = error?.message || '本机 Worker 控制端不可用'
    ElMessage.warning(message)
  } finally {
    const current = new Set(workerStartingIds.value)
    current.delete(jobId)
    workerStartingIds.value = current
  }
}

function ensureWorkerPolling() {
  if (workerPollingTimer.value) return
  workerPollingTimer.value = window.setInterval(pollWorkerStatuses, 5000)
}

async function hydrateWorkerStatuses() {
  const jobIds = [...new Set(
    materials.value
      .map(row => row.codex_job)
      .filter(job => job?.id && ['queued', 'running', 'dry_run_succeeded', 'validating'].includes(job.status || ''))
      .map(job => job!.id)
  )]
  if (!jobIds.length) return
  const settled = await Promise.allSettled(jobIds.map(jobId => codexWorkerApi.getJob(jobId)))
  const nextStatuses = { ...workerStatuses.value }
  settled.forEach((result, index) => {
    if (result.status === 'fulfilled') nextStatuses[jobIds[index]] = result.value
  })
  workerStatuses.value = nextStatuses
  if (Object.values(nextStatuses).some(status => status.running || ['running', 'publishing'].includes(status.state))) {
    ensureWorkerPolling()
  }
}

async function pollWorkerStatuses() {
  const jobIds = new Set<string>()
  for (const [jobId, status] of Object.entries(workerStatuses.value)) {
    if (status.running || ['queued', 'running', 'publishing'].includes(status.state)) jobIds.add(jobId)
  }
  for (const row of materials.value) {
    const jobId = row.codex_job?.id || ''
    if (jobId && workerStatuses.value[jobId]?.running) jobIds.add(jobId)
  }
  if (!jobIds.size) {
    if (workerPollingTimer.value) {
      window.clearInterval(workerPollingTimer.value)
      workerPollingTimer.value = null
    }
    return
  }

  let shouldRefresh = false
  const nextStatuses = { ...workerStatuses.value }
  for (const jobId of jobIds) {
    try {
      const status = await codexWorkerApi.getJob(jobId)
      const previous = workerStatuses.value[jobId]
      nextStatuses[jobId] = status
      if ((!previous || previous.state !== status.state) && ['published', 'failed'].includes(status.state)) {
        shouldRefresh = true
        if (status.state === 'published') ElMessage.success(`Worker published：job #${jobId}`)
        if (status.state === 'failed') ElMessage.warning(`Worker failed：job #${jobId}`)
      }
    } catch {
      // Keep the latest visible status; the next polling tick may recover.
    }
  }
  workerStatuses.value = nextStatuses
  if (shouldRefresh) {
    await fetchMaterials()
    await fetchSummary()
  }
}

function updatePipelinePolling() {
  if (pipelineBusy.value && !pollingTimer.value) {
    pollingTimer.value = window.setInterval(async () => {
      await fetchPipeline()
      await fetchSummary()
      if (!pipelineBusy.value) {
        await fetchMaterials()
      }
    }, 5000)
  }
  if (!pipelineBusy.value && pollingTimer.value) {
    window.clearInterval(pollingTimer.value)
    pollingTimer.value = null
  }
}

function handleRowCommand(row: MaterialItem, command: RowCommand) {
  if (command === 'metadata-edit') return openMetadataDrawer(row)
  if (command === 'metadata-extract') return extractRowMetadata(row, false, true)
  if (command === 'compare-review') return openCompareReview(row)
  if (command === 'resume-popo') return resumePopoFromFrozenMineru(row)
  if (command === 'start-codex') return startCodexJob(row)
  if (command === 'run-worker') return runCodexWorker(row)
  if (command === 'start-worker-v2') return startWorkflowV2(row)
  if (command === 'retry-worker-v2') return retryWorkflowV2(row)
  if (command === 'view-worker-v2') return openWorkflowV2Drawer(row)
  if (command === 'preview-pdf') return previewPdf(row)
  if (command === 'download-pdf') return downloadPdf(row)
}

function handleDropdownCommand(row: MaterialItem, command: string | number | object) {
  if (typeof command !== 'string') return
  handleRowCommand(row, command as RowCommand)
}

async function resumePopoFromFrozenMineru(row: MaterialItem) {
  if (!isPipelineAdmin.value) {
    ElMessage.warning('仅管线管理员可执行 Popo 异常恢复')
    return
  }
  if (!hasMineruAsset(row) || hasPopoAsset(row) || pipelineBusy.value) return
  const next = new Set(popoResumeIds.value)
  next.add(row.id)
  popoResumeIds.value = next
  try {
    const result = await materialsApi.preflightPopoResume(row.id)
    if (!result.ready) {
      ElMessage.warning(`恢复已拦截：${preflightFailureText(result)}`)
      return
    }
    await ElMessageBox.confirm(
      h('div', { class: 'preflight-confirm' }, [
        h('p', { style: 'margin: 0 0 6px;' }, `文件：${row.filename}`),
        h('p', { style: 'margin: 0 0 6px;' }, '仅复用已冻结的 MinerU 产物，不重复执行 MinerU。'),
        h('p', { style: 'margin: 0;' }, '将提交 Popo GPU 任务并在完成后冻结结果。')
      ]),
      '从冻结 MinerU 恢复 Popo',
      { type: 'warning', confirmButtonText: '提交 Popo', cancelButtonText: '取消' }
    )
    const run = await materialsApi.startPopoResume(row.id)
    rememberOperation(row, '恢复 Popo', 'started', run)
    await fetchPipeline()
    ElMessage.success('Popo 恢复任务已提交')
  } finally {
    const current = new Set(popoResumeIds.value)
    current.delete(row.id)
    popoResumeIds.value = current
  }
}

async function openCompareReview(row: MaterialItem) {
  if (!hasLatexAsset(row)) {
    ElMessage.warning('该材料还没有可比对的 ElegantBook 输出')
    return
  }
  let assetId = row.review_asset_id
  let outputId = ''
  try {
    const target = await materialsApi.getReviewTarget(row.id)
    assetId = assetId || target.review_asset_id
    outputId = target.output_id || ''
  } catch {
    if (!assetId) {
      ElMessage.warning('尚未建立审查索引，请先同步资产')
      return
    }
  }
  rememberOperation(row, 'PDF比对', 'opened')
  router.push({ path: '/review/compare', query: { asset_id: assetId, ...(outputId ? { output_id: outputId } : {}) } })
}

function previewPdf(row: MaterialItem) {
  window.open(materialsApi.getContentUrl(row.id), '_blank')
}

async function downloadPdf(row: MaterialItem) {
  const { url } = await materialsApi.getDownloadUrl(row.id)
  window.open(url, '_blank')
}

watch(
  () => [
    params.page,
    params.page_size,
    params.stage,
    params.metadata_status,
    params.subject,
    params.country,
    params.series,
    params.year_from,
    params.year_to
  ],
  () => fetchMaterials()
)

watch(
  () => params.search,
  () => {
    if (searchTimer.value) window.clearTimeout(searchTimer.value)
    searchTimer.value = window.setTimeout(() => {
      params.page = 1
      fetchMaterials()
    }, 300)
  }
)

onMounted(() => {
  readUploadResumeContext()
  loadDashboard()
})

onMounted(() => {
  workflowV2PollingTimer.value = window.setInterval(fetchWorkflowV2Jobs, 5000)
})

onBeforeUnmount(() => {
  if (searchTimer.value) window.clearTimeout(searchTimer.value)
  if (pollingTimer.value) window.clearInterval(pollingTimer.value)
  if (workerPollingTimer.value) window.clearInterval(workerPollingTimer.value)
  if (workflowV2PollingTimer.value) window.clearInterval(workflowV2PollingTimer.value)
})
</script>

<style scoped>
.materials-root {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}

.page-header {
  display: flex;
  min-height: 48px;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.page-heading {
  min-width: 0;
}

.page-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 24px;
  font-weight: 720;
  line-height: 1.2;
  letter-spacing: 0;
}

.page-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 5px;
  color: var(--text-muted);
  font-size: 12px;
}

.header-actions {
  display: flex;
  flex-wrap: wrap;
  flex-shrink: 0;
  justify-content: flex-end;
  gap: 7px;
}

.summary-band {
  display: grid;
  overflow: hidden;
  flex-shrink: 0;
  grid-template-columns: repeat(4, minmax(92px, 1fr));
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  background: var(--bg-primary);
}

.summary-tile {
  min-width: 0;
  padding: 11px 15px;
  border: 0;
  border-right: 1px solid var(--border-light);
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  transition: background var(--transition-fast), box-shadow var(--transition-fast);
}

.summary-tile:last-child {
  border-right: 0;
}

.summary-tile:hover {
  background: var(--bg-hover);
}

.summary-tile.active {
  background: var(--primary-faint);
  box-shadow: inset 0 -2px 0 var(--primary-color);
}

.summary-tile strong {
  display: block;
  color: var(--text-primary);
  font-size: 19px;
  font-weight: 700;
  line-height: 1.1;
}

.summary-tile span {
  display: block;
  margin-top: 4px;
  font-size: 11px;
  font-weight: 520;
}

.workspace-status {
  display: grid;
  min-height: 64px;
  flex-shrink: 0;
  grid-template-columns: minmax(280px, 1fr) minmax(230px, 0.7fr) auto;
  align-items: center;
  gap: 14px;
  padding: 10px 13px;
  border: 1px solid var(--border-light);
  border-left-width: 3px;
  border-radius: var(--radius-lg);
  background: var(--bg-primary);
}

.workspace-status.tone-idle {
  border-left-color: var(--border-color);
}

.workspace-status.tone-active {
  border-left-color: var(--primary-color);
}

.workspace-status.tone-success {
  border-left-color: var(--success-color);
}

.workspace-status.tone-warning {
  border-left-color: var(--warning-color);
}

.workspace-status.tone-danger {
  border-left-color: var(--danger-color);
}

.workspace-state {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.state-icon {
  display: inline-flex;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.tone-active .state-icon {
  background: var(--primary-tint);
  color: var(--primary-dark);
}

.tone-success .state-icon {
  background: rgb(46 139 87 / 0.09);
  color: var(--success-dark);
}

.tone-warning .state-icon {
  background: rgb(179 107 0 / 0.09);
  color: var(--warning-dark);
}

.tone-danger .state-icon {
  background: rgb(217 45 32 / 0.08);
  color: var(--danger-dark);
}

.state-copy {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.state-label,
.recent-work > span {
  color: var(--text-muted);
  font-size: 10px;
  font-weight: 650;
}

.state-copy strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.state-copy span:last-child {
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent-work {
  display: grid;
  min-width: 0;
  gap: 1px;
  padding-left: 14px;
  border-left: 1px solid var(--border-light);
}

.recent-work strong,
.recent-work em {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent-work strong {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 620;
}

.recent-work em {
  color: var(--text-secondary);
  font-size: 11px;
  font-style: normal;
}

.workspace-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}

.filter-bar,
.metadata-filter-bar,
.batch-bar {
  flex-shrink: 0;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  background: var(--bg-primary);
}

.filter-bar {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 170px auto;
  align-items: center;
  gap: 9px;
  padding: 9px;
}

.search-input {
  min-width: 0;
}

.stage-select {
  width: 170px;
}

.filter-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
}

.filter-count {
  display: inline-grid;
  min-width: 18px;
  height: 18px;
  margin-left: 4px;
  place-items: center;
  border-radius: 9px;
  background: var(--primary-tint);
  color: var(--primary-dark);
  font-size: 10px;
  font-weight: 700;
}

.metadata-filter-bar {
  display: grid;
  grid-template-columns: repeat(4, minmax(118px, 1fr)) minmax(230px, 1.25fr);
  gap: 9px;
  padding: 9px;
  background: #fafbfc;
}

.year-filter {
  display: grid;
  grid-template-columns: minmax(90px, 1fr) auto minmax(90px, 1fr);
  align-items: center;
  gap: 7px;
  color: var(--text-muted);
  font-size: 11px;
}

.year-filter :deep(.el-input-number) {
  width: 100%;
}

.filter-reveal-enter-active,
.filter-reveal-leave-active {
  transition: opacity var(--transition-fast), transform var(--transition-fast);
}

.filter-reveal-enter-from,
.filter-reveal-leave-to {
  opacity: 0;
  transform: translateY(-3px);
}

.batch-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  padding: 9px 11px;
  border-left: 3px solid var(--primary-color);
  background: var(--primary-whisper);
}

.batch-summary {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 9px;
  color: var(--text-secondary);
  font-size: 12px;
}

.batch-summary strong {
  color: var(--text-primary);
  font-weight: 650;
}

.batch-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}

.batch-policy-note {
  align-self: center;
  color: var(--text-tertiary);
  font-size: 12px;
}

.batch-log {
  width: 100%;
  overflow: hidden;
  color: var(--danger-color);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.table-shell {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  background: var(--bg-primary);
}

.table-shell :deep(.el-table) {
  min-height: 0;
  flex: 1;
}

.table-shell :deep(.el-table__inner-wrapper) {
  height: 100%;
}

.table-shell :deep(.el-table__cell) {
  padding: 10px 0;
}

.table-shell :deep(.el-table__header .cell) {
  font-size: 11px;
  font-weight: 680;
}

.table-shell :deep(.el-table-fixed-column--left),
.table-shell :deep(.el-table-fixed-column--right) {
  background: var(--bg-primary);
}

.table-shell :deep(.el-table-fixed-column--right) {
  box-shadow: -10px 0 16px -16px rgb(16 24 40 / 0.28);
}

.table-shell :deep(.is-active-task-row td) {
  background: var(--primary-faint) !important;
}

.table-shell :deep(.is-recent-operation-row td) {
  background: rgb(179 107 0 / 0.035) !important;
}

.material-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
}

.material-title-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.file-name {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 650;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.book-subtitle {
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.material-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 9px;
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.25;
}

.material-meta span {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metadata-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.metadata-chips span {
  max-width: 170px;
  overflow: hidden;
  padding: 1px 6px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  background: #fafbfc;
  color: var(--text-secondary);
  font-size: 10px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pipeline-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}

.stage-summary {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
}

.pipeline-status {
  flex: 0 0 auto;
  padding: 2px 7px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  background: #f4f5f6;
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 680;
  line-height: 1.45;
}

.pipeline-status.stage-raw_done,
.pipeline-status.stage-clean_done,
.pipeline-status.stage-standard_done {
  border-color: rgb(46 139 87 / 0.18);
  background: rgb(46 139 87 / 0.08);
  color: var(--success-dark);
}

.pipeline-status.stage-popo_done,
.pipeline-status.stage-mineru_done {
  border-color: rgb(0 113 227 / 0.16);
  background: var(--primary-faint);
  color: var(--primary-dark);
}

.pipeline-status.stage-clean_stale {
  border-color: rgb(179 107 0 / 0.18);
  background: rgb(179 107 0 / 0.07);
  color: var(--warning-dark);
}

.pipeline-status.stage-failed {
  border-color: rgb(217 45 32 / 0.18);
  background: rgb(217 45 32 / 0.07);
  color: var(--danger-dark);
}

.stage-note {
  min-width: 0;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workflow-v2-line {
  width: fit-content;
  max-width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 10px;
  white-space: nowrap;
}

.workflow-v2-line:hover strong {
  color: var(--primary-dark);
}

.workflow-v2-line strong {
  color: var(--text-primary);
  font-weight: 650;
}

.workflow-v2-label {
  padding: 1px 5px;
  border: 1px solid rgb(0 113 227 / 0.2);
  border-radius: var(--radius-sm);
  background: var(--primary-faint);
  color: var(--primary-dark);
  font-weight: 680;
}

.workflow-v2-findings {
  color: var(--danger-dark);
}

.workflow-v2-detail {
  min-height: 180px;
}

.workflow-v2-detail-head,
.workflow-v2-record,
.workflow-v2-call-row,
.workflow-v2-finding-row,
.workflow-v2-counters {
  display: flex;
  align-items: center;
  gap: 8px;
}

.workflow-v2-detail-head {
  justify-content: space-between;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-light);
}

.workflow-v2-detail-head div {
  display: grid;
  gap: 3px;
}

.workflow-v2-detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 14px;
}

.workflow-v2-detail-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.workflow-v2-detail-head span,
.workflow-v2-record span,
.workflow-v2-call-row span,
.workflow-v2-finding-row span {
  color: var(--text-secondary);
  font-size: 11px;
}

.workflow-v2-section {
  padding: 16px 0;
  border-bottom: 1px solid var(--border-light);
}

.workflow-v2-section h3 {
  margin: 0 0 10px;
  font-size: 13px;
}

.workflow-v2-record,
.workflow-v2-call-row,
.workflow-v2-finding-row {
  min-height: 34px;
  flex-wrap: wrap;
}

.workflow-v2-record strong,
.workflow-v2-call-row strong,
.workflow-v2-finding-row strong {
  min-width: 0;
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
}

.workflow-v2-record small,
.workflow-v2-call-row small {
  width: 100%;
  color: var(--danger-dark);
  font-size: 10px;
}

.workflow-v2-counters {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
}

.workflow-v2-counters span {
  padding: 8px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 11px;
}

.workflow-v2-muted {
  margin: 8px 0 0;
  color: var(--text-muted);
  font-size: 10px;
}

.workflow-v2-blocker-card {
  display: grid;
  gap: 6px;
  margin-top: 10px;
  padding: 10px;
  border: 1px solid var(--warning-border, #f5d9a8);
  border-radius: var(--radius-sm);
  background: #fffaf1;
}

.workflow-v2-blocker-card > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.workflow-v2-blocker-card small {
  color: var(--text-secondary);
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.stage-track {
  display: grid;
  min-width: 0;
  grid-template-columns: repeat(4, minmax(44px, 1fr));
  align-items: start;
}

.stage-step {
  position: relative;
  display: grid;
  min-width: 0;
  justify-items: center;
  gap: 3px;
  color: var(--text-muted);
  font-size: 9px;
}

.stage-step::before {
  content: '';
  position: absolute;
  top: 4px;
  right: 0;
  left: 0;
  height: 1px;
  background: var(--border-color);
}

.stage-step:first-child::before {
  left: 50%;
}

.stage-step:last-child::before {
  right: 50%;
}

.stage-step.done::before {
  background: rgb(46 139 87 / 0.38);
}

.stage-dot {
  position: relative;
  z-index: 1;
  width: 9px;
  height: 9px;
  border: 2px solid var(--border-color);
  border-radius: 50%;
  background: var(--bg-primary);
}

.stage-step.done .stage-dot {
  border-color: var(--success-color);
  background: var(--success-color);
}

.stage-step.active .stage-dot {
  box-shadow: 0 0 0 3px var(--primary-ring);
}

.stage-step-label {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-actions {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}

.row-actions :deep(.el-button) {
  min-height: 30px;
  margin-left: 0 !important;
  padding: 5px 9px !important;
}

.more-button {
  width: 30px;
  padding: 5px 0 !important;
}

.pagination-row {
  display: flex;
  min-height: 32px;
  flex-shrink: 0;
  align-items: center;
  justify-content: flex-end;
}

.upload-icon {
  color: var(--primary-color);
  font-size: 38px;
}

.upload-progress {
  margin-top: 14px;
}

.upload-resume {
  margin-top: 12px;
}

.upload-error {
  margin-top: 12px;
}

.metadata-drawer :deep(.el-drawer__body) {
  padding: 16px 20px;
}

.metadata-drawer :deep(.el-drawer__footer) {
  padding: 12px 20px;
  border-top: 1px solid var(--border-light);
}

.metadata-editor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.metadata-editor-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 12px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  background: #fafbfc;
}

.metadata-editor-head div {
  min-width: 0;
}

.metadata-editor-head span {
  display: block;
  color: var(--text-muted);
  font-size: 10px;
  font-weight: 650;
}

.metadata-editor-head strong {
  display: block;
  overflow: hidden;
  margin-top: 3px;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 620;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metadata-form {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.metadata-form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.metadata-form :deep(.el-input-number) {
  width: 100%;
}

.metadata-evidence {
  display: grid;
  gap: 9px;
}

.metadata-evidence header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.metadata-evidence header span {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 650;
}

.metadata-evidence header small {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-list {
  display: grid;
  gap: 7px;
}

.evidence-list article {
  padding: 9px 10px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  background: var(--bg-primary);
}

.evidence-list article span {
  color: var(--primary-dark);
  font-size: 11px;
  font-weight: 650;
}

.evidence-list article p {
  margin: 4px 0;
  color: var(--text-primary);
  font-size: 12px;
  line-height: 1.45;
}

.evidence-list article small {
  color: var(--text-muted);
  font-size: 10px;
}

.metadata-error {
  padding: 9px 10px;
  border-left: 3px solid var(--danger-color);
  background: rgb(217 45 32 / 0.05);
  color: var(--danger-dark);
  font-size: 12px;
}

.metadata-drawer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

@media (max-width: 1180px) {
  .metadata-filter-bar {
    grid-template-columns: repeat(3, minmax(118px, 1fr));
  }

  .year-filter {
    grid-column: span 2;
  }
}

@media (max-width: 980px) {
  .workspace-status {
    grid-template-columns: 1fr auto;
  }

  .recent-work {
    grid-column: 1 / -1;
    padding: 8px 0 0;
    border-top: 1px solid var(--border-light);
    border-left: 0;
  }
}

@media (max-width: 760px) {
  .materials-root {
    overflow: auto;
  }

  .page-header {
    align-items: stretch;
    flex-direction: column;
  }

  .header-actions {
    justify-content: flex-start;
  }

  .summary-band {
    grid-template-columns: repeat(2, minmax(100px, 1fr));
  }

  .summary-tile:nth-child(2) {
    border-right: 0;
  }

  .summary-tile:nth-child(-n + 2) {
    border-bottom: 1px solid var(--border-light);
  }

  .workspace-status,
  .filter-bar,
  .metadata-filter-bar {
    grid-template-columns: 1fr;
  }

  .workspace-actions,
  .filter-actions {
    justify-content: flex-start;
  }

  .recent-work {
    grid-column: auto;
  }

  .stage-select {
    width: 100%;
  }

  .year-filter {
    grid-column: auto;
  }

  .table-shell {
    min-height: 520px;
    flex: none;
  }

  .metadata-form-grid {
    grid-template-columns: 1fr;
  }

  .metadata-drawer-footer {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
