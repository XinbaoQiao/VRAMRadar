from pathlib import Path
import unittest


WEB_ROOT = Path(__file__).parents[1] / "src" / "vram_radar" / "web"


class WebUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javascript = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.localization = (WEB_ROOT / "localization.js").read_text(encoding="utf-8")
        cls.styles = (WEB_ROOT / "app.css").read_text(encoding="utf-8")
        cls.markup = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

    def test_language_setting_is_persistent_complete_and_loaded_before_the_app(self):
        self.assertIn('id="ui-language"', self.markup)
        self.assertIn('<option value="zh-CN">简体中文</option>', self.markup)
        self.assertIn('<option value="en">English</option>', self.markup)
        self.assertLess(
            self.markup.index('<script src="localization.js"></script>'),
            self.markup.index('<script src="app.js"></script>'),
        )
        self.assertIn("window.VRAMRadarI18n?.setLanguage(currentProfile.ui_language || 'zh-CN')", self.javascript)
        self.assertIn("ui_language: ui.language.value", self.javascript)
        self.assertIn("window.VRAMRadarI18n?.setLanguage(currentProfile?.ui_language || 'zh-CN')", self.javascript)
        self.assertIn("MutationObserver", self.localization)
        self.assertIn("document.documentElement.lang = language", self.localization)
        self.assertIn("english_interface_has_no_untranslated_chinese", (Path(__file__).parents[1] / "tools" / "benchmark_webview_ui.py").read_text(encoding="utf-8"))

    def test_favorite_gpu_alert_settings_are_persistent_localized_and_progressive(self):
        self.assertIn('id="favorite-alert-enabled"', self.markup)
        self.assertIn('id="favorite-alert-min-memory"', self.markup)
        self.assertIn('type="number" min="0" max="1000" step="0.5"', self.markup)
        self.assertIn("favorite_alert_enabled: ui.favoriteAlertEnabled.checked", self.javascript)
        self.assertIn(
            "favorite_alert_min_memory_gib: Number(ui.favoriteAlertMinMemory.value || 0)",
            self.javascript,
        )
        self.assertIn("ui.favoriteAlertMinMemory.disabled = !ui.favoriteAlertEnabled.checked", self.javascript)
        self.assertIn("currentProfile?.favorite_alert_enabled !== false", self.javascript)

    def test_task_completion_alerts_support_default_and_per_task_watches(self):
        self.assertIn('id="task-completion-alert-enabled"', self.markup)
        self.assertIn('id="task-alert-indicator"', self.markup)
        self.assertIn("if (snapshot.profile_update) acceptProfile(snapshot.profile_update);", self.javascript)
        self.assertIn(
            "snapshot.profile_update || revision == null || revision !== lastRenderedRevision",
            self.javascript,
        )
        self.assertIn("currentProfile?.task_completion_alert_enabled !== false", self.javascript)
        self.assertIn("task_completion_watches:", self.javascript)
        self.assertIn("api.set_task_completion_watch", self.javascript)
        self.assertIn("api.mark_task_completion_alerts_read", self.javascript)
        self.assertIn("task-watch-toggle", self.javascript)
        self.assertIn("return `process:${pid}`;", self.javascript)
        self.assertNotIn("return startedAt ? `process:${pid}:${startedAt}`", self.javascript)
        self.assertIn("function taskCompletionWatchRenderSignature(serverId)", self.javascript)
        self.assertIn("taskCompletionWatchRenderSignature(server.server_id)", self.javascript)

    def test_notification_center_is_persistent_unified_and_always_visible(self):
        bell_start = self.markup.index('id="task-alert-indicator"')
        bell_markup = self.markup[bell_start:self.markup.index('>', bell_start)]
        self.assertNotIn(" hidden", bell_markup)
        self.assertIn('id="notification-center"', self.markup)
        self.assertIn('id="notification-list"', self.markup)
        self.assertIn('id="clear-notifications"', self.markup)
        self.assertIn("snapshot?.notifications", self.javascript)
        self.assertIn("api.mark_notifications_read", self.javascript)
        self.assertIn("api.clear_notifications", self.javascript)
        self.assertIn("function clearAllNotifications", self.javascript)
        clear_handler = self.javascript[
            self.javascript.index("async function clearAllNotifications"):
            self.javascript.index("async function setNotificationCenterOpen")
        ]
        self.assertIn("renderNotificationCenter(currentSnapshot)", clear_handler)
        self.assertIn("favorite_gpu_available", self.javascript)
        self.assertIn("resource_available", self.javascript)
        self.assertIn("update_available", self.javascript)
        self.assertIn("notification-update-action install-latest-update", self.javascript)
        self.assertIn("发现新版本后会进入通知中心", self.markup)
        self.assertIn("任务完成、版本更新与 GPU 可用消息", self.markup)

    def test_english_translation_covers_stale_error_and_process_details(self):
        for source in (
            "数据已过期",
            "服务器连接超时",
            "旧快照仅供参考，不计入顶部实时汇总。",
            "进程 / 任务",
            "GPU 明细",
            "显存合计",
            "启动时间",
            "主机 CPU",
            "个逻辑核心",
            "逻辑核心未知",
            "运行 / 等待任务数",
            "1、5、15 分钟数值表示正在运行、等待 CPU 或处于不可中断 I/O 等待中的平均任务数。",
            "进程 CPU",
            "查看命令摘要",
            "清空通知",
        ):
            self.assertIn(f"['{source}'", self.localization)
        self.assertIn("Live GPU memory · last success", self.localization)
        self.assertIn("Error code: $1 · Last success: $2", self.localization)

    def test_task_watches_support_bulk_removal_and_explicit_other_user_selection(self):
        self.assertIn("api.clear_task_completion_watches", self.javascript)
        self.assertIn("clear-task-watches", self.javascript)
        self.assertIn("data-task-owner", self.javascript)
        self.assertIn("data-task-owner-scope", self.javascript)
        self.assertNotIn("if (!mine) return '';", self.javascript)
        self.assertIn("其他用户的任务不会自动关注", self.markup)
        self.assertIn('data-server-navigator-filter="watches"', self.markup)
        self.assertNotIn('data-server-navigator-filter="recent"', self.markup)
        self.assertNotIn('id="task-completion-watch-list"', self.markup)
        self.assertIn("navigator-task-watches", self.javascript)
        self.assertIn('data-server-navigator-filter="watches" aria-pressed="false">关注</button>', self.markup)
        self.assertIn('class="navigator-watch-marker"', self.javascript)
        self.assertIn("navigator-watch-summary-copy", self.javascript)
        self.assertIn(".navigator-watch-marker", self.styles)

    def test_connection_type_defaults_to_auto_with_manual_fallback(self):
        self.assertIn('<option value="auto">自动识别（推荐）</option>', self.markup)
        self.assertIn("auto_detect_backend: selectedBackend === 'auto'", self.javascript)
        self.assertIn("api.test_connection(server.id)", self.javascript)
        self.assertIn("Notify me when favorite GPUs are available", self.localization)
        self.assertIn("Leave empty: notify only for idle GPUs", self.localization)

    def test_copy_ssh_prefers_an_openssh_config_block_and_localized_feedback(self):
        self.assertIn("result.copy_text || result.command", self.javascript)
        self.assertIn("result.copy_format === 'openssh-config'", self.javascript)
        self.assertIn("SSH Config 配置块已复制", self.javascript)
        self.assertIn("SSH Config host block copied", self.localization)

    def test_task_time_columns_are_unambiguous(self):
        self.assertIn("运行时长", self.javascript)
        self.assertIn("提交时间", self.javascript)
        self.assertIn("时间限额", self.javascript)
        self.assertIn("结束时间", self.javascript)
        self.assertNotIn("已运行 / 上限", self.javascript)
        self.assertNotIn("耗时 / 结束", self.javascript)
        self.assertIn("value == null ? '未记录'", self.javascript)

    def test_status_view_shows_task_ownership_names_but_omits_direct_hostnames(self):
        task_table = self.javascript[
            self.javascript.index("function renderTaskName"):self.javascript.index("function schedulerMemoryMeter")
        ]
        live_table = self.javascript[
            self.javascript.index("function renderLiveTable"):self.javascript.index("function taskBadge")
        ]
        self.assertIn("task.name", task_table)
        self.assertIn("task.user", self.javascript)
        self.assertNotIn("server.host", live_table)
        self.assertIn("任务名称", task_table)
        self.assertIn('<th scope="col">用户</th>', task_table)
        self.assertIn('<th scope="col">任务名称</th>', task_table)
        self.assertIn("未记录", task_table)
        self.assertIn("task-name-details", task_table)
        self.assertIn("self-user-tag", self.javascript)

    def test_task_hierarchy_separates_self_other_and_time_scope(self):
        for text in (
            "我的任务",
            "其他用户",
            "正在运行与排队",
            "过去 ${number((server.tasks || {}).history_window_hours || 24)} 小时结果",
            "仅显示 Slurm 对当前登录账号可见的 GPU 作业",
            "PID 进程列表",
        ):
            self.assertIn(text, self.javascript)
        for selector in (".task-owner-stack", ".task-owner-group.mine", ".task-owner-content", ".module-context"):
            self.assertIn(selector, self.styles)
        for selector in (".task-name-cell", ".task-name-details", ".task-name-full"):
            self.assertIn(selector, self.styles)

    def test_direct_gpu_processes_separate_owners_without_claiming_scheduler_history(self):
        direct_module = self.javascript[
            self.javascript.index("function formatElapsedSeconds"):self.javascript.index("function schedulerMemoryMeter")
        ]
        for text in (
            "我的进程",
            "其他用户",
            "归属不可见",
            "进程 / 任务",
            "显存合计",
            "CPU",
            "运行时长",
            "启动时间",
            "不是调度队列，不包含排队或完成历史",
            "上次 GPU 进程",
            "不能据此判断它们现在仍在运行",
        ):
            self.assertIn(text, direct_module)
        self.assertIn("process.command_preview", direct_module)
        self.assertIn('<details class="process-command-details" open>', direct_module)
        self.assertIn("其他用户命令摘要未启用", direct_module)
        self.assertIn("敏感参数已遮盖", direct_module)
        self.assertIn("其他用户摘要还需在服务器设置中开启", direct_module)
        self.assertIn("process.command_visibility", direct_module)
        self.assertIn("function renderCpuOverview", self.javascript)
        self.assertIn("cpu-load-values", self.javascript)
        self.assertNotIn("下方“进程 CPU”沿用 nvitop 的口径", self.javascript)
        self.assertNotIn("多线程进程可以超过 100%", self.javascript)
        self.assertIn("function formatCpuPercent", direct_module)
        cpu_formatter = direct_module[
            direct_module.index("function formatCpuPercent"):
            direct_module.index("function renderProcessName")
        ]
        self.assertIn("value == null || value === ''", cpu_formatter)
        self.assertIn('<th scope="col">进程 CPU</th>', direct_module)
        self.assertNotIn("command_raw", self.javascript)
        self.assertIn("escapeHtml(preview)", direct_module)
        self.assertIn('data-task-module="gpu-processes"', direct_module)
        for selector in (
            ".process-module",
            ".process-summary-pill",
            ".process-command-details",
            ".process-gpu-list",
            ".cpu-overview",
            ".cpu-load-item",
            ".cpu-overview-help",
        ):
            self.assertIn(selector, self.styles)
        self.assertIn(
            "direct_cpu_information_is_rendered",
            (Path(__file__).parents[1] / "tools" / "benchmark_webview_ui.py").read_text(encoding="utf-8"),
        )

    def test_visual_hierarchy_uses_distinct_semantic_levels_and_compact_defaults(self):
        for contract in (
            '<h3 class="server-name">',
            '<h4>${moduleTitle}</h4>',
            '<h4>任务详情</h4>',
            '<h4>代码工作目录</h4>',
            '<h5>${escapeHtml(title)}</h5>',
            '<h6>${escapeHtml(title)}</h6>',
            "function renderModuleContext",
            'data-context-note="${escapeHtml(key)}"',
            "moduleOpen(server.server_id, 'gpu-processes', false)",
            "moduleOpen(server.server_id, 'cluster-tasks', false)",
            "processes: groups.others, currentUser, defaultOpen: false",
        ):
            self.assertIn(contract, self.javascript)
        self.assertNotIn("function renderNodeTopology", self.javascript)
        self.assertNotIn('<section class="module-section">', self.javascript)
        self.assertNotIn("GPU 集群状态", self.javascript)
        for style in (
            ".server-name { margin: 0; font-family: var(--display-font); font-size: 20px",
            ".cluster-heading h4 { margin: 0; font-family: var(--display-font); font-size: 16px",
            ".task-owner-heading h5 { margin: 0; font-family: var(--display-font); font-size: 14.5px",
            ".task-period-head h6 { margin: 0; font-family: var(--display-font); font-size: 13px",
            ".cluster-module { margin: 8px 12px 0",
            ".task-owner-stack { display: grid; gap: 8px; margin-left: 5px",
        ):
            self.assertIn(style, self.styles)

    def test_module_expansion_state_is_scoped_by_server_and_module(self):
        self.assertIn("`${serverId}:${moduleKey}`", self.javascript)
        self.assertIn("cluster.dataset.module", self.javascript)
        self.assertIn("group.dataset.taskModule", self.javascript)

    def test_account_home_and_lazy_directory_tree_are_progressively_disclosed(self):
        for contract in (
            "登录账号",
            "主目录",
            "代码工作目录",
            "account.home_directory",
            "api.inspect_account_directory(serverId, rootPath, force)",
            "function renderDirectoryEntries",
            "function loadDirectoryTree",
            "function pinDefaultDirectory",
            "function resetDefaultDirectory",
            "固定为默认目录",
            "恢复自动定位",
            "展开更多",
            'data-module="account-directory"',
            'data-directory-path="${safePath}"',
            "展开后读取",
            "名称、类型、大小和修改时间",
        ):
            self.assertIn(contract, self.javascript)
        self.assertIn("directoryTrees = new Map()", self.javascript)
        self.assertIn("openDirectoryNodes.clear()", self.javascript)
        self.assertIn(
            '<header class="server-head"><div class="server-identity">',
            self.javascript,
        )
        self.assertIn(
            "${renderAccountOverview(server)}<div class=\"server-head-controls\"><span class=\"server-status\">",
            self.javascript,
        )
        self.assertNotIn("</header>${renderAccountOverview(server)}", self.javascript)
        for selector in (
            ".account-overview",
            ".account-fact",
            ".directory-module",
            ".directory-tree",
            ".directory-node",
            ".directory-file",
            ".directory-rootbar",
            ".directory-module[open] > summary",
        ):
            self.assertIn(selector, self.styles)
        self.assertIn("position: sticky; top: var(--titlebar-height, 62px)", self.styles)
        self.assertIn("function syncDirectoryStickyOffset", self.javascript)
        self.assertIn("details.directory-module > summary", self.javascript)
        self.assertIn("contain: layout style; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius-md); overflow: visible;", self.styles)
        self.assertNotIn('<span class="section-index" aria-hidden="true">01</span>', self.markup)
        self.assertNotIn('<span class="section-index" aria-hidden="true">02</span>', self.markup)
        for layout_contract in (
            ".server-head { display: grid; grid-template-columns: minmax(180px, .75fr) minmax(260px, 1.15fr) minmax(340px, auto)",
            ".account-overview { grid-column: 2; min-width: 0; display: flex",
            ".server-head-controls { grid-column: 3; min-width: 0; display: flex",
            ".server-status { display: inline-flex",
            ".server-quick-actions { min-width: 0; display: flex",
            "@media (max-width: 1040px)",
            ".server-head { grid-template-columns: minmax(165px, 1fr) auto",
            ".account-overview { grid-column: 1 / -1; grid-row: 2; display: grid",
        ):
            self.assertIn(layout_contract, self.styles)
        self.assertNotIn(".server-quick-actions { grid-column: 1 / -1", self.styles)
        account_presentation = self.javascript[
            self.javascript.index("function renderAccountOverview"):
            self.javascript.index("function configuredDefaultDirectory")
        ]
        server_header = self.javascript[
            self.javascript.index("function renderServer(server"):
            self.javascript.index("function snapshotRevision")
        ]
        self.assertNotIn("server.ssh_alias", account_presentation + server_header)

    def test_server_order_uses_direct_dragging_with_keyboard_fallback(self):
        for selector in ("server-drag-handle", "server-position", "server-order-status"):
            self.assertIn(selector, self.markup)
        self.assertIn('draggable="true"', self.markup)
        self.assertIn("聚焦手柄后按上下方向键", self.markup)
        self.assertNotIn("move-server-up", self.markup)
        self.assertNotIn("move-server-down", self.markup)
        for text in (
            "function refreshServerEditorOrder",
            "function moveServerEditor",
            "function reorderServerDraft",
            "function setupServerEditorDrag",
            "settingsServerDrafts.splice",
            "'dragstart'",
            "'dragover'",
            "'drop'",
            "'ArrowUp'",
            "'ArrowDown'",
            "focusDragHandle: true",
            "const SERVER_EDITOR_PAGE_SIZE = 20",
            "function renderServerEditorPage",
            "querySelectorAll('.server-editor')",
        ):
            self.assertIn(text, self.javascript)
        self.assertIn("const sortable = !settingsServerQuery", self.javascript)
        self.assertIn(".server-editor-actions", self.styles)
        self.assertIn(".server-drag-handle", self.styles)
        self.assertIn(".server-editor.drag-over-before", self.styles)
        self.assertNotIn(".order-button", self.styles)

    def test_global_layout_density_keeps_one_clear_visual_hierarchy(self):
        for contract in (
            "--font-body: 15px",
            ".titlebar { position: sticky; top: 0; z-index: 20; grid-column: 1 / -1; min-height: 62px",
            ".button { min-height: 36px",
            "main { grid-row: 2; grid-column: 1; width: 100%; max-width: 1320px",
            ".capacity-metric { min-height: 174px",
            ".server-card { display: grid; grid-template-columns: 46px",
            ".server-head { display: grid; grid-template-columns:",
            "dialog { width: min(1020px",
            ".dialog-content { min-width: 0; padding: 16px",
            ".server-editor-list { display: grid; gap: 8px; }",
        ):
            self.assertIn(contract, self.styles)
        self.assertIn(".first-run-benefits::before { content: none; }", self.styles)

    def test_automatic_catalog_action_discovers_and_imports_in_one_step(self):
        self.assertIn("自动发现并导入", self.markup)
        self.assertIn("~/.ssh/config", self.markup)
        self.assertIn("不读取私钥、密码", self.markup)
        discover = self.javascript[
            self.javascript.index("async function discoverServerConfig"):
            self.javascript.index("async function importServerConfig")
        ]
        self.assertIn("await api.discover_server_config()", discover)
        self.assertIn("api.import_server_config(result.paths", discover)
        self.assertIn("ui.discoverServerConfig.disabled = true", discover)
        self.assertIn("ui.discoverServerConfig.disabled = false", discover)

    def test_import_messages_distinguish_parsing_from_save_and_connection_validation(self):
        import_flow = self.javascript[
            self.javascript.index("function applyImportedServerConfig"):
            self.javascript.index("function collectProfile")
        ]
        self.assertIn("已解析", import_flow)
        self.assertIn("尚未保存", import_flow)
        self.assertIn("尚未连接验证", import_flow)
        self.assertNotIn("正在验证并导入", import_flow)
        self.assertLess(import_flow.index("已解析"), import_flow.index("尚未保存"))

    def test_removed_import_candidates_persist_as_alias_tombstones(self):
        self.assertIn("let pendingIgnoredSshAliases = new Set()", self.javascript)
        settings = self.javascript[
            self.javascript.index("function openSettings"):
            self.javascript.index("async function discoverServerConfig")
        ]
        self.assertIn(
            "pendingIgnoredSshAliases = new Set(currentProfile?.ignored_ssh_aliases || [])",
            settings,
        )

        imported = self.javascript[
            self.javascript.index("function applyImportedServerConfig"):
            self.javascript.index("async function importServerConfig")
        ]
        self.assertIn("ignoredAliasKeys", imported)
        self.assertIn("visibleCandidates", imported)
        self.assertIn(
            "populateServerEditors(visibleCandidates, {importedCandidate: true, allowEmpty: true})",
            imported,
        )
        self.assertIn("result.servers.length - visibleCandidates.length", imported)
        self.assertIn("已保留 ${pendingRemovalCount} 台本次移除项", imported)
        self.assertIn("已解析 ${visibleCandidates.length} 台服务器候选", imported)

        editor = self.javascript[
            self.javascript.index("function addServerEditor"):
            self.javascript.index("function addAndFocusServerEditor")
        ]
        self.assertIn("editor.dataset.originalSshAlias = values._original_ssh_alias ?? values.ssh_alias ?? ''", editor)
        self.assertIn("values._imported_candidate === true || options.importedCandidate === true", editor)
        self.assertIn("const wasSaved = (currentProfile?.servers || []).some", editor)
        self.assertIn("const wasImportedCandidate = editor.dataset.importedCandidate === 'true'", editor)
        self.assertIn(
            "if (wasSaved || wasImportedCandidate) rememberIgnoredSshAlias(editor.dataset.originalSshAlias)",
            editor,
        )
        self.assertNotIn("rememberIgnoredSshAlias(value('ssh_alias'))", editor)

        profile_collector = self.javascript[
            self.javascript.index("function collectProfile"):
            self.javascript.index("function collectPasswordUpdates")
        ]
        self.assertIn("const activeAliasKeys", profile_collector)
        self.assertIn("const ignoredSshAliases", profile_collector)
        self.assertIn("ignored_ssh_aliases: ignoredSshAliases", profile_collector)
        self.assertIn("!activeAliasKeys.has(sshAliasKey(alias))", profile_collector)

    def test_stale_automatic_discovery_cannot_replace_manual_edits(self):
        onboarding = self.javascript[
            self.javascript.index("function invalidateServerDiscovery"):self.javascript.index("async function importServerConfig")
        ]
        set_step = self.javascript[
            self.javascript.index("function setOnboardingStep"):
            self.javascript.index("function setSettingsMode")
        ]
        discover = self.javascript[
            self.javascript.index("async function discoverServerConfig"):
            self.javascript.index("async function importServerConfig")
        ]
        self.assertIn("const generation = ++serverDiscoveryGeneration", discover)
        self.assertIn("onboardingStep === 2", discover)
        self.assertIn("ui.dialog.open", discover)
        self.assertGreaterEqual(discover.count("if (!requestIsCurrent()) return"), 2)
        self.assertLess(
            discover.index("if (!requestIsCurrent()) return"),
            discover.index("applyImportedServerConfig(imported)"),
        )
        self.assertIn("onboardingStep === 2 && nextStep !== 2", onboarding)
        self.assertIn("onboardingDiscoveryStarted = false", set_step)
        self.assertLess(
            set_step.index("onboardingDiscoveryStarted = false"),
            set_step.index("onboardingStep = nextStep"),
        )
        self.assertIn("onboardingStep === 2 && !onboardingDiscoveryStarted", set_step)
        self.assertIn("void discoverServerConfig()", set_step)
        close_handler = self.javascript[self.javascript.index("ui.dialog.addEventListener('close'"):]
        self.assertIn("if (ui.dialog.open) return", close_handler)
        self.assertIn("invalidateServerDiscovery()", close_handler)
        self.assertIn("ui.editorList.replaceChildren()", close_handler)

    def test_server_settings_are_searchable_paged_and_release_closed_editor_dom(self):
        for identifier in (
            'id="server-editor-toolbar"',
            'id="server-editor-search"',
            'id="server-editor-previous-page"',
            'id="server-editor-next-page"',
        ):
            self.assertIn(identifier, self.markup)
        for contract in (
            "const SERVER_EDITOR_PAGE_SIZE = 20",
            "function filteredServerDraftIndices",
            "function serverDraftIndexFromEditor",
            "function syncVisibleServerDrafts",
            "indices.slice(settingsServerPageOffset, settingsServerPageOffset + SERVER_EDITOR_PAGE_SIZE)",
            "function invalidServerDraft",
            "function revealInvalidServerDraft",
            "settingsServerDrafts = []",
            "const sortable = !settingsServerQuery",
            "function reorderServerDraft",
            "服务器 ID 与前面的服务器重复",
        ):
            self.assertIn(contract, self.javascript)

    def test_resource_watch_rechecks_changed_criteria_without_stale_notification_state(self):
        watch = self.javascript[
            self.javascript.index("async function evaluateResourceWatch"):
            self.javascript.index("function refreshServerEditorOrder")
        ]
        self.assertIn("const criteriaRevision = resourceWatchCriteriaRevision", watch)
        self.assertIn("criteriaRevision !== resourceWatchCriteriaRevision", watch)
        self.assertIn("function scheduleResourceWatchEvaluation", watch)
        self.assertIn("resourceWatchMatched = false", watch)
        listeners = self.javascript[self.javascript.index("ui.recommend.addEventListener"):]
        self.assertGreaterEqual(listeners.count("scheduleResourceWatchEvaluation()"), 2)

    def test_server_config_guide_is_progressive_and_commands_are_copyable(self):
        for text in (
            "服务器发现与导入",
            "自动检测不到？",
            "Windows 教程",
            "macOS 教程",
            "配置文件应该写什么？",
            "使用 VS Code / Cursor Remote-SSH",
            "找到路径以后",
        ):
            self.assertIn(text, self.markup)
        self.assertGreaterEqual(self.markup.count('class="copy-command"'), 7)
        self.assertIn('data-copy-target="windows-find-config"', self.markup)
        self.assertIn('data-copy-target="macos-find-config"', self.markup)
        self.assertIn('data-platform="windows">一键打开 PowerShell', self.markup)
        self.assertIn('data-platform="macos">一键打开终端', self.markup)
        self.assertIn("async function writeClipboard", self.javascript)
        self.assertIn("async function openSetupTerminal", self.javascript)
        self.assertIn("api.open_setup_terminal(platformName)", self.javascript)
        self.assertIn("navigator.clipboard?.writeText", self.javascript)
        self.assertIn("document.execCommand('copy')", self.javascript)
        self.assertIn("void copyCommand(copyButton)", self.javascript)
        self.assertIn("void openSetupTerminal(setupTerminal.dataset.platform)", self.javascript)
        for selector in (".import-steps", ".platform-guide", ".guide-launcher", ".command-box", ".copy-command"):
            self.assertIn(selector, self.styles)

    def test_product_layout_prioritizes_live_capacity_and_progressive_actions(self):
        for text in (
            "GPU 资源总览",
            "当前可用显存",
            "服务器状态",
            "帮我找一台可用 GPU",
            "填写需求",
        ):
            self.assertIn(text, self.markup + self.javascript)
        self.assertIn('<details class="match-panel">', self.markup)
        self.assertIn('id="server-list-meta"', self.markup)
        self.assertIn("所有监控就绪 GPU 合计", self.javascript)
        for selector in (
            ".dashboard-intro",
            ".metric-detail",
            ".match-summary",
            ".content-heading",
        ):
            self.assertIn(selector, self.styles)
        self.assertNotIn("不会提交或占用 GPU", self.markup)
        self.assertNotIn("本机只读监控", self.markup)

    def test_gpu_recommendation_is_hidden_until_requested_and_emphasizes_key_facts(self):
        self.assertIn(
            'id="recommendation" class="recommendation" aria-live="polite" hidden',
            self.markup,
        )
        recommendation = self.javascript[
            self.javascript.index("function renderRecommendationResult"):
            self.javascript.index("function refreshServerEditorOrder")
        ]
        for contract in (
            "recommendationRequested = true",
            "ui.recommendation.hidden = false",
            "推荐服务器",
            "单卡可用显存",
            "分区",
            "卡型",
            "空闲 GPU",
            "function clearRecommendation",
        ):
            self.assertIn(contract, recommendation)
        self.assertIn("if (recommendationRequested) await updateRecommendation()", self.javascript)
        self.assertEqual(recommendation.count("result.reason"), 1)
        for selector in (
            ".recommendation-result",
            ".recommendation-main strong",
            ".recommendation-memory strong",
            ".recommendation-facts",
        ):
            self.assertIn(selector, self.styles)

    def test_multi_server_navigator_is_compact_informative_and_keyboard_accessible(self):
        for selector in (
            'id="server-navigator"',
            'id="server-navigator-drag"',
            'data-side="right"',
            'class="server-navigator-panel"',
            'id="server-navigator-list"',
            'id="server-navigator-search"',
            'id="server-navigator-empty"',
            'id="server-navigator-position"',
            'id="server-navigator-status"',
            'aria-label="当前筛选位置"',
            'id="previous-server"',
            'id="next-server"',
            'aria-label="服务器快速导航"',
            'aria-label="筛选服务器"',
            'data-server-navigator-filter="all"',
            'data-server-navigator-filter="available"',
            'data-server-navigator-filter="tasks"',
            'data-server-navigator-filter="issues"',
        ):
            self.assertIn(selector, self.markup)
        for contract in (
            "function renderServerNavigator",
            "function applyServerNavigatorSide",
            "function persistServerNavigatorSide",
            "function beginServerNavigatorDrag",
            "function moveServerNavigatorDrag",
            "function finishServerNavigatorDrag",
            "function serverNavigatorResourceSummary",
            "function serverNavigatorOwnTaskSummary",
            "function serverNavigatorOwnActivity",
            "function serverNavigatorActivityCount",
            "function serverNavigatorHasAvailableResource",
            "function serverNavigatorSearchText",
            "function serverMatchesNavigator",
            "function syncActiveServerFromScroll",
            "function navigateToServer",
            "function navigateRelativeServer",
            "function scheduleServerNavigatorSearchRender",
            "servers.length > 1",
            "serverNavigatorFilter === 'available'",
            "serverNavigatorFilter === 'tasks'",
            "serverNavigatorFilter === 'issues'",
            "ui.serverNavigatorSearch.addEventListener('input'",
            "ui.previousServer.addEventListener('click'",
            "ui.nextServer.addEventListener('click'",
            "当前服务器不在筛选结果中",
            "scrollIntoView",
            "aria-current",
            "window.addEventListener('scroll'",
            "window.addEventListener('resize'",
            "api.set_navigator_side",
            "addEventListener('pointerdown'",
            "addEventListener('pointermove'",
            "addEventListener('pointerup'",
            "addEventListener('pointercancel'",
        ):
            self.assertIn(contract, self.javascript)
        for selector in (
            ".server-navigator",
            ".server-navigator-panel",
            ".server-navigator[data-side=\"left\"]",
            ".server-navigator-drag",
            ".server-navigator.dragging",
            ".server-navigator:hover",
            ".server-navigator:focus-within",
            ".server-navigator-item.active",
            ".server-navigator-copy",
            ".server-navigator-controls",
            ".server-navigator-search",
            ".server-navigator-filters",
            ".server-navigator-empty",
            ".server-navigator-footer",
            ".server-navigator-position",
        ):
            self.assertIn(selector, self.styles)
        self.assertIn(".server-navigator { position: sticky; z-index: 15; top:", self.styles)
        self.assertIn("grid-row: 2; grid-column: 2", self.styles)
        self.assertIn("main { grid-row: 2; grid-column: 1", self.styles)
        search_index = self.javascript[
            self.javascript.index("function serverNavigatorSearchText"):self.javascript.index("function serverMatchesNavigator")
        ]
        self.assertNotIn("server.host", search_index)
        self.assertNotIn("ssh_alias", search_index)
        navigator_events = self.javascript[
            self.javascript.index("ui.serverNavigatorDrag.addEventListener('pointerdown'"):
            self.javascript.index("ui.previousServer.addEventListener('click'")
        ]
        self.assertNotIn("addEventListener('keydown'", navigator_events)
        self.assertNotIn('id="server-navigator-position" class="server-navigator-position" aria-live=', self.markup)
        self.assertIn('id="server-navigator-status" class="sr-only" role="status" aria-live="polite"', self.markup)
        self.assertIn('已定位到服务器 ${server.display_name}', self.javascript)
        self.assertIn("scroll-margin-top: 78px", self.styles)
        self.assertIn("@media (max-width: 900px)", self.styles)
        self.assertIn("--navigator-panel-width: clamp(206px, 17vw, 224px)", self.styles)
        self.assertIn("--navigator-rail-width: 40px", self.styles)
        self.assertIn(".app-shell:has(.server-navigator:not([hidden]))", self.styles)
        self.assertIn("grid-template-columns: minmax(0, 1fr) calc(var(--navigator-rail-width)", self.styles)
        self.assertNotIn("grid-template-columns: minmax(0, 1fr) calc(var(--navigator-panel-width)", self.styles)
        self.assertIn("position: sticky", self.styles)
        self.assertNotIn(".server-navigator { position: fixed", self.styles)

    def test_server_navigator_task_filter_shows_only_my_current_activity(self):
        navigator = self.javascript[
            self.javascript.index("function serverNavigatorOwnActivity"):self.javascript.index("function setActiveServer")
        ]
        own_activity = navigator[
            navigator.index("function serverNavigatorOwnActivity"):navigator.index("function serverNavigatorActivityCount")
        ]
        self.assertIn("server.tasks?.current_user", own_activity)
        self.assertIn("server.tasks?.active", own_activity)
        self.assertIn("task.user", own_activity)
        self.assertIn("process.owner_scope === 'mine'", own_activity)
        own_summary = navigator[
            navigator.index("function serverNavigatorOwnTaskSummary"):navigator.index("function serverNavigatorResourceSummary")
        ]
        self.assertNotIn("server.tasks?.counts", own_summary)
        self.assertIn("serverNavigatorFilter === 'tasks'", navigator)
        matches = navigator[
            navigator.index("function serverMatchesNavigator"):navigator.index("function renderServerNavigatorItem")
        ]
        self.assertIn("serverNavigatorActivityCount(server) > 0", matches)
        self.assertNotIn("server.tasks?.counts", matches)
        self.assertIn("const taskSummary = serverNavigatorOwnTaskSummary(server)", navigator)
        self.assertNotIn("function serverNavigatorTaskSummary", navigator)
        self.assertNotIn("当前无任务", navigator)
        self.assertNotIn("当前无 GPU 进程", navigator)

    def test_server_navigator_never_treats_stale_or_offline_activity_as_current(self):
        own_activity = self.javascript[
            self.javascript.index("function serverNavigatorOwnActivity"):
            self.javascript.index("function serverNavigatorActivityCount")
        ]
        online_guard = "if (server.connection?.state !== 'online') return [];"
        self.assertIn(online_guard, own_activity)
        self.assertLess(own_activity.index(online_guard), own_activity.index("server.backend === 'slurm_ssh'"))

    def test_non_retryable_errors_still_offer_manual_revalidation(self):
        error_renderer = self.javascript[
            self.javascript.index("function renderError"):
            self.javascript.index("function serverGlyph")
        ]
        self.assertIn("retry-server", error_renderer)
        self.assertIn("重新验证", error_renderer)
        self.assertIn("disabled", error_renderer)
        self.assertNotIn("error.retryable ?", error_renderer)

    def test_unknown_host_key_uses_automatic_accept_new_without_confirmation(self):
        error_renderer = self.javascript[
            self.javascript.index("function renderError"):
            self.javascript.index("function serverGlyph")
        ]
        self.assertIn("error.code === 'host_key_changed'", error_renderer)
        self.assertNotIn("trust-server-host-key", self.javascript)
        self.assertNotIn("function trustServerHostKey", self.javascript)

    def test_settings_primary_groups_are_collapsed_progressive_disclosures(self):
        self.assertIn('<details id="profile-settings"', self.markup)
        self.assertIn('<details id="import-panel"', self.markup)
        self.assertIn("界面、提醒与更新", self.markup)
        self.assertIn("服务器发现与导入", self.markup)
        self.assertIn("settings-group-body", self.markup)
        self.assertIn("if (onboarding && onboardingStep === 2) ui.importPanel.open = true", self.javascript)
        self.assertIn("ui.profileSettings.open = true", self.javascript)
        self.assertIn("if (firstEditor) firstEditor.open = true", self.javascript)
        self.assertIn(".settings-group > summary", self.styles)

    def test_slurm_environment_fallbacks_are_editable_and_safely_validated(self):
        for field in ("slurm_module", "slurm_bin_directory", "slurm_init_script"):
            self.assertIn(f'data-field="{field}"', self.markup)
            self.assertIn(field, self.javascript)
        self.assertIn("[data-slurm-environment]", self.javascript)
        self.assertIn("Slurm 远端路径必须是以 / 开头的绝对路径", self.javascript)

    def test_server_navigator_avoids_layout_bound_animation_and_linear_scroll_scans(self):
        for contract in (
            "let serverNavigationCards = []",
            "let serverNavigationCardsById = new Map()",
            "let serverNavigatorItems = new Map()",
            "let serverNavigatorPositions = new Map()",
            "while (low <= high)",
            "serverNavigationCardsById.get(activeServerId)",
            "serverNavigatorItems.get(serverId)",
            "window.requestAnimationFrame(() =>",
            "scrollIntoView({behavior: 'auto'",
        ):
            self.assertIn(contract, self.javascript)
        scroll_sync = self.javascript[
            self.javascript.index("function syncActiveServerFromScroll"):self.javascript.index("function scheduleServerNavigationSync")
        ]
        self.assertNotIn("querySelectorAll('.server-card')", scroll_sync)
        self.assertNotIn("cards.find", scroll_sync)
        self.assertNotIn("cards.reduce", scroll_sync)
        self.assertNotIn("behavior: reducedMotion ? 'auto' : 'smooth'", self.javascript)
        self.assertNotIn("window.matchMedia?.('(max-width: 900px)').matches", scroll_sync)
        self.assertIn("contain: layout paint style", self.styles)
        self.assertIn("will-change: transform", self.styles)
        self.assertIn("transition: transform .16s ease", self.styles)
        self.assertNotIn("transition: width .2s ease", self.styles)
        schedule_sync = self.javascript[
            self.javascript.index("function scheduleServerNavigationSync"):self.javascript.index("function renderServerNavigator(servers)")
        ]
        self.assertLess(schedule_sync.index("ui.serverNavigator.hidden"), schedule_sync.index("window.requestAnimationFrame"))

    def test_empty_profile_uses_a_dedicated_progressive_onboarding(self):
        for selector in (
            'id="first-run-home"',
            'id="dashboard-content"',
            'id="onboarding-progress"',
            'id="onboarding-welcome"',
            'data-onboarding-marker="1"',
            'data-onboarding-marker="2"',
            'data-onboarding-marker="3"',
        ):
            self.assertIn(selector, self.markup)
        for text in (
            "先添加一台 GPU 服务器",
            "自动发现",
            "检查并完成",
            "没有 SSH 配置也没关系",
            "稍后设置",
        ):
            self.assertIn(text, self.markup)
        for contract in (
            "function setOnboardingStep",
            "function setSettingsMode",
            "ui.firstRunHome.hidden = hasServers",
            "ui.dashboardContent.hidden = !hasServers",
            "ui.refresh.hidden = !hasServers",
            "openSettings({onboarding: true})",
        ):
            self.assertIn(contract, self.javascript)
        self.assertIn(".first-run-home", self.styles)
        self.assertIn(".onboarding-progress", self.styles)
        self.assertIn(".onboarding-welcome", self.styles)
        onboarding = self.javascript[
            self.javascript.index("function setOnboardingStep"):self.javascript.index("async function discoverServerConfig")
        ]
        self.assertIn("onboardingStep === 2", onboarding)
        self.assertIn("void discoverServerConfig()", onboarding)
        self.assertIn("onboardingDiscoveryStarted = false", onboarding)

    def test_complete_content_keeps_only_one_click_collapse(self):
        for selector in (
            'id="collapse-dashboard"',
            'id="collapse-settings"',
        ):
            self.assertIn(selector, self.markup)
        for selector in (
            'id="expand-dashboard"',
            'id="restore-dashboard"',
            'id="expand-settings"',
            'id="restore-settings"',
        ):
            self.assertNotIn(selector, self.markup)
        self.assertEqual(self.markup.count("一键收起"), 2)
        self.assertNotIn("全部展开", self.markup)
        self.assertNotIn("恢复默认", self.markup)
        for contract in (
            "function setDetailsOpen",
            "function collapseDashboardDisclosure",
            "function collapseSettingsDisclosure",
            "function prepareSettingsDisclosure",
            "dashboardDisclosureMode",
            "openClusters.clear()",
            "openTaskGroups.clear()",
            "openContextNotes.clear()",
        ):
            self.assertIn(contract, self.javascript)
        self.assertIn(".disclosure-tools", self.styles)
        self.assertIn(".compact-button", self.styles)

    def test_server_editor_keeps_common_fields_visible_and_advanced_fields_nested(self):
        self.assertIn('<details class="server-editor">', self.markup)
        self.assertIn('<div class="server-editor-body">', self.markup)
        editor_template = self.markup[
            self.markup.index('<template id="server-editor-template">'):
            self.markup.index('</template>', self.markup.index('<template id="server-editor-template">'))
        ]
        body_start = editor_template.index('<div class="server-editor-body">')
        body_end = editor_template.rindex('</div>')
        self.assertLess(body_start, editor_template.index('<div class="field-grid server-primary-fields">'))
        self.assertLess(editor_template.index('<details class="server-editor-more">'), body_end)
        self.assertLess(editor_template.index('<details class="ssh-key-setup">'), body_end)
        self.assertIn('<details class="server-editor-more">', self.markup)
        self.assertIn("登录与高级设置", self.markup)
        self.assertIn("连接与登录细节仍放在第二层设置中", self.markup)
        self.assertIn('data-server-editor-name', self.markup)
        self.assertIn('data-auth-overview', self.markup)
        self.assertIn("refreshEditorName", self.javascript)
        self.assertIn("authOverview.textContent", self.javascript)
        for selector in (".settings-section", ".server-editor-more", ".server-editor-more-body"):
            self.assertIn(selector, self.styles)
        self.assertIn("if (editor && !options.focusDragHandle) editor.open = true", self.javascript)

    def test_server_editor_primary_fields_share_aligned_rows(self):
        self.assertIn('class="field-grid server-primary-fields"', self.markup)
        self.assertIn(".field-grid > label:not(.check-label) { align-content: start; }", self.styles)
        self.assertIn(".server-primary-fields input, .server-primary-fields select { height: 38px; }", self.styles)
        self.assertIn(".primary-help { grid-column: 1 / -1;", self.styles)
        benchmark = (Path(__file__).parents[1] / "tools" / "benchmark_webview_ui.py").read_text(encoding="utf-8")
        self.assertIn("settings_primary_connection_fields_align", benchmark)

    def test_server_editor_exposes_config_source_and_preserves_hidden_advanced_fields(self):
        self.assertIn('data-field="ssh_config_file"', self.markup)
        self.assertIn("OpenSSH 配置文件", self.markup)
        profile_collector = self.javascript[
            self.javascript.index("function collectProfile"):
            self.javascript.index("function collectPasswordUpdates")
        ]
        self.assertIn("ssh_config_file: value('ssh_config_file')", profile_collector)
        self.assertIn("connect_timeout_seconds", profile_collector)
        self.assertNotIn("host_key_fingerprint", profile_collector)
        self.assertIn("existing", profile_collector)
        self.assertNotIn("editor.dataset.sshConfigFile", profile_collector)

    def test_connection_test_never_probes_stale_saved_configuration(self):
        helper_start = self.javascript.index("function serverEditorHasUnsavedConnectionChanges")
        helper_end = self.javascript.index("async function configureServerSshKey", helper_start)
        helper = self.javascript[helper_start:helper_end]
        for field in (
            "id",
            "backend",
            "ssh_alias",
            "host",
            "port",
            "username",
            "identity_file",
            "ssh_config_file",
            "password",
            "clear_password",
        ):
            self.assertIn(field, helper)
        self.assertIn("data-field=\"port_override\"", helper)

        connection_test = self.javascript[
            self.javascript.index("async function testServerEditorConnection"):
            self.javascript.index("function refreshEditorEnabledState")
        ]
        self.assertIn("const savedServer = currentProfile?.servers?.find", connection_test)
        self.assertIn("serverEditorHasUnsavedConnectionChanges(editor, savedServer)", connection_test)
        self.assertIn("请先保存", connection_test)
        self.assertLess(
            connection_test.index("serverEditorHasUnsavedConnectionChanges(editor, savedServer)"),
            connection_test.index("api.test_connection(serverId)"),
        )

        key_setup = self.javascript[
            self.javascript.index("async function configureServerSshKey"):
            self.javascript.index("async function testServerEditorConnection")
        ]
        self.assertIn("serverEditorHasUnsavedConnectionChanges(editor, savedServer)", key_setup)

    def test_other_user_summaries_default_on_and_cover_direct_and_slurm_servers(self):
        self.assertIn('data-field="show_other_user_commands"', self.markup)
        self.assertIn("显示其他用户的任务与命令摘要", self.markup)
        self.assertIn("show_other_user_commands: true", self.javascript)
        self.assertIn("Slurm：显示其他用户的作业名、状态与时间", self.javascript)
        self.assertIn("调度器视图不读取完整 shell 命令", self.javascript)
        self.assertNotIn("server-command-setting').hidden", self.javascript)
        self.assertIn("show_other_user_commands", self.javascript)
        self.assertIn(".server-command-setting", self.styles)

    def test_connecting_server_is_presented_as_configuring_not_as_an_error(self):
        renderer = self.javascript[
            self.javascript.index("function renderConfiguring"):
            self.javascript.index("function serverCardRenderSignature")
        ]
        self.assertIn("正在配置并验证服务器", renderer)
        self.assertIn("完成前不会显示为错误", renderer)
        self.assertIn("state === 'connecting'", renderer)
        self.assertIn("renderConfiguring(server)", renderer)
        self.assertIn(".configuring-panel", self.styles)
        self.assertIn("正在配置中", self.javascript)

    def test_server_password_editor_sends_secrets_outside_profile_payload(self):
        self.assertIn('data-field="password" type="password"', self.markup)
        self.assertIn('autocomplete="new-password"', self.markup)
        self.assertIn('data-field="clear_password"', self.markup)
        self.assertIn("function collectPasswordUpdates", self.javascript)
        self.assertIn(
            "api.save_profile(proposedProfile, collectPasswordUpdates(), collectServerRenames())",
            self.javascript,
        )
        self.assertIn("function collectServerRenames", self.javascript)
        self.assertIn("editor.dataset.originalId", self.javascript)
        self.assertIn("editor.dataset.sshConfigFile", self.javascript)
        profile_collector = self.javascript[
            self.javascript.index("function collectProfile"):self.javascript.index("function collectPasswordUpdates")
        ]
        self.assertNotIn("password", profile_collector)
        self.assertIn("navigator_side", profile_collector)

    def test_server_editor_has_progressive_secure_ssh_key_setup(self):
        for text in (
            "一键配置 SSH 免密登录",
            "使用现有密钥",
            "生成专用密钥",
            "私钥始终留在本机",
            "非替换式追加",
            "不会自动改写",
            "authorized_keys",
            "保留匹配密钥",
            "配置并验证",
        ):
            self.assertIn(text, self.markup)
        for contract in (
            "async function configureServerSshKey",
            "api.configure_ssh_key(serverId",
            "window.confirm(confirmation)",
            "private_key_path",
            "public_key_path",
            "prefer_identity_auth",
        ):
            self.assertIn(contract, self.javascript)
        for selector in (
            ".ssh-key-setup",
            ".ssh-key-setup-body",
            ".key-mode-options",
            ".ssh-key-safety",
        ):
            self.assertIn(selector, self.styles)

    def test_icons_and_status_styles_have_one_vector_system(self):
        for symbol in ("⚙", "⌄", "↯"):
            self.assertNotIn(symbol, self.javascript + self.markup)
        self.assertIn('class="ui-icon"', self.markup)
        self.assertIn(".task-badge.warning", self.styles)
        self.assertIn(".status-dot.online", self.styles)

    def test_precision_radar_identity_uses_functional_svg_and_restrained_surfaces(self):
        for contract in (
            'class="brand-mark"',
            'class="signal-sweep"',
            "function capacityTape",
            "function serverGlyph",
            'class="metric capacity-metric"',
            'class="server-rail"',
        ):
            self.assertIn(contract, self.markup + self.javascript)
        for token in (
            "--instrument:",
            "--instrument-accent:",
            "--display-font:",
            ".capacity-visual",
            ".capacity-tape",
            ".server-card.online .server-rail",
        ):
            self.assertIn(token, self.styles)
        for forbidden in ("gradient", "backdrop-filter", "border-radius: 999"):
            self.assertNotIn(forbidden, self.styles)

    def test_memory_meter_has_accessible_semantics(self):
        self.assertIn('role="progressbar"', self.javascript)
        self.assertIn('aria-valuenow="${percent}"', self.javascript)
        self.assertIn(".memory-track.critical", self.styles)
        self.assertIn("function schedulerMemoryMeter", self.javascript)
        self.assertIn("调度显存占用", self.javascript)
        self.assertIn("GPU 调度占用率", self.javascript)
        self.assertIn("按 Slurm 已分配整卡计算，不代表进程实时显存", self.javascript)
        self.assertIn("allocatedGpus * perGpu", self.javascript)
        self.assertIn("summary.total_vram_gib", self.javascript)
        self.assertIn('role="img" aria-label="${escapeHtml(accessible)}"', self.javascript)
        self.assertIn("可用比例", self.javascript)
        self.assertIn("总量待补充", self.javascript)
        self.assertIn("tape-cell${index < activeCount ? ' available' : ''}", self.javascript)
        self.assertIn("percent <= 20 ? 'critical' : percent <= 50 ? 'warning' : 'healthy'", self.javascript)
        self.assertIn(".capacity-visual.warning .capacity-visual-head strong", self.styles)
        self.assertIn(".capacity-visual.critical .capacity-visual-head strong", self.styles)
        self.assertIn(".capacity-visual.healthy .capacity-visual-head strong", self.styles)
        self.assertIn("var(--instrument-warning)", self.styles)
        self.assertIn("var(--instrument-critical)", self.styles)
        self.assertIn(".scheduler-memory-note", self.styles)
        self.assertIn('<progress class="memory-track ${tone}"', self.javascript)
        self.assertNotIn('style="width:', self.javascript)

    def test_large_cluster_nodes_are_compact_lazy_paged_and_revision_bound(self):
        for contract in (
            "const CLUSTER_NODE_PAGE_SIZE = 75",
            "function renderLargeClusterSummary",
            "function loadClusterNodes",
            "api.get_cluster_nodes(",
            "const currentDataRevision = currentSnapshot?.servers?.find",
            "const requestRevision = next.revision",
            "requestRevision,",
            "const latestDataRevision = currentSnapshot?.servers?.find",
            "const responseRevision = result.revision ?? requestRevision",
            "if (revisionChanged)",
            "result?.code === 'snapshot_changed'",
            "cluster.dataset.module === 'cluster-nodes'",
            "event.target.closest?.('.cluster-node-filters')",
            "const values = new FormData(form)",
            "const clusterPage = event.target.closest('.cluster-node-page')",
        ):
            self.assertIn(contract, self.javascript)
        for selector in (
            ".large-cluster-overview",
            ".cluster-group-grid",
            ".cluster-node-filters",
            ".cluster-node-pager",
        ):
            self.assertIn(selector, self.styles)

    def test_scheduler_node_columns_use_one_fixed_table_track(self):
        for contract in (
            'table class="scheduler-node-table"',
            '<colgroup><col class="node-name-column">',
            "function copyableValue",
            "copyableValue(node.node, '节点名')",
        ):
            self.assertIn(contract, self.javascript)
        for selector in (
            ".scheduler-node-table { min-width: 1040px; table-layout: fixed; }",
            ".scheduler-node-table .node-memory-column { width: 25%; }",
            ".scheduler-node-table .node-state-column { width: 12%; }",
            ".scheduler-node-table th:last-child, .scheduler-node-table td:last-child { white-space: normal; }",
            ".copyable-cell { vertical-align: middle; }",
            ".copyable-value { max-width: 100%; display: inline-flex",
        ):
            self.assertIn(selector, self.styles)
        self.assertNotIn(".copyable-cell { display: flex", self.styles)

    def test_scheduler_node_translation_is_generic_and_covers_all_state_labels(self):
        self.assertIn("[/^(.+) · ([\\d,.]+) GiB\\/卡$/, '$1 · $2 GiB/GPU']", self.localization)
        for source, target in (
            ("已分配", "Allocated"),
            ("即将完成", "Completing"),
            ("不可用", "Unavailable"),
            ("排空中", "Draining"),
            ("正在排空", "Draining"),
            ("故障", "Failed"),
            ("部分占用", "Partially allocated"),
            ("已保留", "Reserved"),
            ("维护中", "Maintenance"),
        ):
            self.assertIn(f"['{source}', '{target}']", self.localization)

    def test_large_server_fleet_keeps_main_paging_independent_from_navigator_filters(self):
        self.assertIn("const SERVER_FLEET_PAGE_SIZE = 50", self.javascript)
        self.assertIn("const SERVER_NAVIGATOR_RENDER_LIMIT = 80", self.javascript)
        main_page = self.javascript[
            self.javascript.index("function mainServerEntries"):
            self.javascript.index("function updateServerFleetPager")
        ]
        self.assertNotIn("filteredServerEntries", main_page)
        self.assertIn("servers.slice(serverFleetPageOffset", main_page)
        self.assertIn("ui.serverListPreviousPage.addEventListener('click'", self.javascript)
        self.assertIn("ui.serverListNextPage.addEventListener('click'", self.javascript)
        self.assertIn("const targetOffset = Math.floor(serverIndex / SERVER_FLEET_PAGE_SIZE)", self.javascript)
        self.assertIn(".server-list-pager", self.styles)

    def test_background_refresh_repaints_on_completion_without_blocking_the_ui(self):
        for contract in (
            "function scheduleRefreshCompletion",
            "await api.get_snapshot()",
            "snapshot.monitoring?.in_flight",
            "const revision = snapshotRevision(snapshot)",
            "revision == null || revision !== lastRenderedRevision",
            "function syncUnchangedSnapshotStatus(snapshot)",
            "syncUnchangedSnapshotStatus(snapshot)",
            "currentSnapshot = snapshot",
            "后台读取中…界面仍可操作",
            "scheduleRefreshCompletion(generation, attempt + 1)",
        ):
            self.assertIn(contract, self.javascript)

    def test_cluster_node_requests_ignore_out_of_order_results(self):
        cluster_load = self.javascript[
            self.javascript.index("async function loadClusterNodes"):
            self.javascript.index("function renderSchedulerTable")
        ]
        for contract in (
            "const clusterNodeRequestGenerations = new Map()",
            "clusterNodeRequestGenerations.set(serverId, requestGeneration)",
            "clusterNodeRequestGenerations.get(serverId) !== requestGeneration",
        ):
            self.assertIn(contract, self.javascript)
        self.assertGreaterEqual(
            cluster_load.count("clusterNodeRequestGenerations.get(serverId) !== requestGeneration"),
            3,
        )

    def test_scheduler_allocation_detail_failure_is_visible_and_conservative(self):
        scheduler = self.javascript[
            self.javascript.index("function renderSchedulerTable"):
            self.javascript.index("function accountForServer")
        ]
        navigator = self.javascript[
            self.javascript.index("function serverNavigatorResourceSummary"):
            self.javascript.index("function serverNavigatorHasAvailableResource")
        ]
        self.assertIn("server.slurm_capabilities?.node_allocation_detail === false", scheduler)
        self.assertIn("空闲容量按未知处理", scheduler)
        self.assertIn("server.slurm_capabilities?.task_gpu_request_detail === false", scheduler)
        self.assertIn("server.slurm_capabilities?.queue_scope_limited === true", scheduler)
        self.assertIn("已自动回退为当前账号任务", scheduler)
        self.assertIn("尚未分配节点的 GPU 排队任务可能不可识别", scheduler)
        self.assertIn("server.slurm_capabilities?.node_allocation_detail === false", navigator)
        self.assertIn("空闲容量未知", navigator)
        self.assertIn("node.allocation_detail_supported === false", self.javascript)
        self.assertIn("节点分配明细不可读", self.javascript)

    def test_directory_requests_ignore_stale_results_and_merge_into_latest_tree(self):
        directory_load = self.javascript[
            self.javascript.index("function directoryRequestKey"):
            self.javascript.index("function expandLoadedDirectories")
        ]
        for contract in (
            "const directoryRequestTokens = new Map()",
            "let directoryRequestSequence = 0",
            "const requestGeneration = ++directoryRequestSequence",
            "directoryRequestTokens.set(requestKey, requestGeneration)",
            "const latest = directoryTrees.get(serverId)",
            "mergeDirectoryAccount(serverId, latest, result.account, result.cache)",
        ):
            self.assertIn(contract, self.javascript)
        self.assertGreaterEqual(
            directory_load.count("directoryRequestTokens.get(requestKey) !== requestGeneration"),
            2,
        )
        self.assertIn(
            "if (directoryRequestTokens.get(requestKey) === requestGeneration) directoryRequestTokens.delete(requestKey)",
            directory_load,
        )
        pin_reset = self.javascript[
            self.javascript.index("async function pinDefaultDirectory"):
            self.javascript.index("function renderError")
        ]
        self.assertGreaterEqual(pin_reset.count("invalidateDirectoryRequests(serverId)"), 2)
        save = self.javascript[
            self.javascript.index("async function saveSettings"):
            self.javascript.index("async function loadApplication")
        ]
        self.assertIn("invalidateChangedServerCaches(previousProfile, result.profile)", save)
        self.assertNotIn("directoryTrees.clear()", save)

    def test_directory_and_snapshot_repaints_are_incremental_and_bounded(self):
        for contract in (
            "const MAX_DIRECTORY_ROOTS_PER_SERVER = 32",
            "const uiRenderMetrics = Object.seal",
            "function repaintDirectory(serverId)",
            "function reconcileServerCards(entries)",
            "function serverCardRenderSignature(server, index)",
            "function populateServerEditors(servers, options = {})",
            "document.createDocumentFragment()",
            "request_background_refresh",
            "document.addEventListener('visibilitychange'",
        ):
            self.assertIn(contract, self.javascript)
        directory_load = self.javascript[
            self.javascript.index("async function loadDirectoryTree"):
            self.javascript.index("function expandLoadedDirectories")
        ]
        self.assertIn("repaintDirectory(serverId)", directory_load)
        self.assertNotIn("render(currentSnapshot)", directory_load)
        self.assertIn("result.cache", directory_load)
        renderer = self.javascript[
            self.javascript.index("function render(snapshot)"):
            self.javascript.index("function showToast")
        ]
        self.assertIn("reconcileServerCards(entries)", renderer)
        self.assertNotIn("ui.list.innerHTML", renderer)
        directory_entries = self.javascript[
            self.javascript.index("function renderDirectoryEntries"):
            self.javascript.index("function renderDirectoryRootBar")
        ]
        self.assertIn("const children = expanded ? renderLevel(absolutePath) : ''", directory_entries)

    def test_directory_cache_has_a_bounded_freshness_deadline_without_rebuilding_unchanged_trees(self):
        for contract in (
            "const directoryFreshnessDeadlines = new Map()",
            "let directoryFreshnessTimer = null",
            "function rememberDirectoryFreshness",
            "cache?.revalidate_after_seconds",
            "function scheduleDirectoryFreshnessValidation",
            "function deferDirectoryFreshness",
            "const DIRECTORY_FRESHNESS_ERROR_RETRY_MS = 30_000",
            "function directoryValidationVisible",
            "server.connection?.state !== 'online'",
            "!module?.open",
            "await loadDirectoryTree(serverId, false, rootPath || null)",
            "clearDirectoryFreshness(serverId)",
        ):
            self.assertIn(contract, self.javascript)
        directory_load = self.javascript[
            self.javascript.index("async function loadDirectoryTree"):
            self.javascript.index("function expandLoadedDirectories")
        ]
        self.assertIn("directoryFreshnessDeadlines.get(requestKey)", directory_load)
        self.assertIn("rememberDirectoryFreshness(serverId, rootPath, result.cache)", directory_load)
        self.assertGreaterEqual(directory_load.count("deferDirectoryFreshness(serverId, rootPath)"), 2)
        self.assertIn("if (result.unchanged && latest?.status === 'loaded')", directory_load)
        unchanged_branch = directory_load[
            directory_load.index("if (result.unchanged && latest?.status === 'loaded')"):
            directory_load.index("} else if (result.unchanged)")
        ]
        self.assertNotIn("directoryStateFromAccount", unchanged_branch)
        self.assertNotIn("mergeDirectoryAccount", unchanged_branch)
        self.assertIn("if (!rootPath && force) invalidateDirectoryRequests(serverId)", directory_load)

    def test_loaded_directory_is_explicitly_marked_stale_when_server_is_not_online(self):
        directory_render = self.javascript[
            self.javascript.index("function renderDirectoryModule"):
            self.javascript.index("function directoryRequestKey")
        ]
        self.assertIn("server.connection?.state !== 'online'", directory_render)
        self.assertIn("旧目录快照 · 当前服务器未监控就绪，仅供参考", directory_render)
        self.assertIn("${stale ? '旧目录快照 · ' : ''}", directory_render)

    def test_large_fleet_and_local_actions_avoid_unrelated_full_repaints(self):
        navigator = self.javascript[
            self.javascript.index("function renderServerNavigator"):
            self.javascript.index("function scheduleServerNavigatorSearchRender")
        ]
        self.assertIn("const allMatches = filteredServerEntries(servers)", navigator)
        self.assertIn("visibleServerEntries(allMatches)", navigator)
        self.assertNotIn("visibleServerEntries(servers)", navigator)
        cluster_load = self.javascript[
            self.javascript.index("async function loadClusterNodes"):
            self.javascript.index("function renderSchedulerTable")
        ]
        self.assertIn("repaintClusterNodes(serverId)", cluster_load)
        self.assertNotIn("render(currentSnapshot)", cluster_load)
        favorite = self.javascript[
            self.javascript.index("async function setFavoriteServer"):
            self.javascript.index("async function setServerEnabled")
        ]
        self.assertIn("repaintFavoriteServer(serverId)", favorite)
        self.assertNotIn("render(currentSnapshot)", favorite)

    def test_convenience_controls_are_wired_where_they_are_used(self):
        for contract in (
            "ui.monitoringToggle.addEventListener('click'",
            "ui.saveView.addEventListener('click'",
            "ui.resourceWatchEnabled.addEventListener('change'",
            "const favorite = event.target.closest('.favorite-server')",
            "const toggleServer = event.target.closest('.toggle-server-monitoring')",
            "const openTerminal = event.target.closest('.open-terminal')",
            "const contextCopy = event.target.closest('[data-copy-value]')",
            "ui.copyDiagnostics.addEventListener('click'",
            "ui.openLogsDirectory.addEventListener('click'",
        ):
            self.assertIn(contract, self.javascript)
        self.assertIn("? await api.get_redacted_diagnostics()", self.javascript)
        self.assertIn(": await api.get_redacted_diagnostics(serverId)", self.javascript)
        self.assertIn("? await api.copy_redacted_diagnostics()", self.javascript)
        self.assertIn(": await api.copy_redacted_diagnostics(serverId)", self.javascript)
        self.assertIn("诊断已复制到剪贴板，可直接粘贴给维护者", self.javascript)
        bulk = self.javascript[
            self.javascript.index("function collapseDashboardDisclosure"):
            self.javascript.index("function collapseSettingsDisclosure")
        ]
        self.assertNotIn("loadDirectoryTree", bulk)
        for selector in (
            ".server-quick-actions",
            ".saved-view-chip",
            ".watch-toggle",
            ".connection-test-result",
        ):
            self.assertIn(selector, self.styles)
        self.assertIn(".server-status-label { display: none; }", self.styles)
        self.assertIn(".server-quick-actions .open-terminal { display: none; }", self.styles)

    def test_ssh_copy_uses_the_backend_canonical_copy_payload(self):
        copy_flow = self.javascript[
            self.javascript.index("async function copyServerSshCommand"):
            self.javascript.index("function scheduleRefreshCompletion")
        ]
        self.assertIn("await api.get_ssh_command(serverId)", copy_flow)
        self.assertIn("await writeClipboard(result.copy_text || result.command)", copy_flow)
        self.assertIn("result.copy_format === 'openssh-config'", copy_flow)
        self.assertIn("SSH Config 配置块已复制", copy_flow)
        self.assertIn("result.endpoint_complete", copy_flow)
        self.assertIn("完整 SSH 命令已复制（地址、用户与端口已包含）", copy_flow)
        self.assertIn("result.warning", copy_flow)
        quick_actions = self.javascript[
            self.javascript.index("function renderServerQuickActions"):
            self.javascript.index("function serverNavigatorOwnActivity")
        ]
        self.assertIn("copy-server-ssh", quick_actions)
        self.assertIn('data-server-id="${escapeHtml(serverId)}"', quick_actions)
        self.assertIn("const copySsh = event.target.closest('.copy-server-ssh')", self.javascript)
        self.assertIn("copyServerSshCommand(copySsh.dataset.serverId)", self.javascript)
        self.assertNotIn("function sshCommandForServer", self.javascript)
        self.assertNotIn("function shellQuote", self.javascript)

    def test_refresh_clock_uses_the_last_completed_data_revision_time(self):
        self.assertIn("function snapshotDataUpdatedAt(snapshot)", self.javascript)
        helper = self.javascript[
            self.javascript.index("function snapshotDataUpdatedAt(snapshot)"):
            self.javascript.index("function syncUnchangedSnapshotStatus")
        ]
        self.assertIn("snapshot.monitoring?.data_updated_at", helper)
        for start_marker, end_marker in (
            ("function syncUnchangedSnapshotStatus", "function render(snapshot)"),
            ("function render(snapshot)", "function showToast"),
        ):
            block = self.javascript[
                self.javascript.index(start_marker):self.javascript.index(end_marker)
            ]
            self.assertIn("snapshotDataUpdatedAt(snapshot)", block)
            self.assertNotIn("new Date(snapshot.fetched_at)", block)
            self.assertIn("状态更新于", block)

    def test_refresh_ignores_older_status_responses_errors_and_cleanup(self):
        refresh = self.javascript[
            self.javascript.index("async function refresh(force"):
            self.javascript.index("function resourceCriteriaFromInputs")
        ]
        awaited = refresh.index("const snapshot = await api.get_status(force, serverId)")
        guarded = refresh.index("if (generation !== refreshPollGeneration) return", awaited)
        rendered = refresh.index("render(snapshot)", guarded)
        self.assertLess(awaited, guarded)
        self.assertLess(guarded, rendered)
        self.assertIn("catch (error) {\n    if (generation !== refreshPollGeneration) return;", refresh)
        self.assertIn(
            "finally {\n    if (generation === refreshPollGeneration) ui.refresh.disabled = false;",
            refresh,
        )

    def test_startup_notice_has_priority_and_is_dismissible(self):
        render = self.javascript[
            self.javascript.index("function render(snapshot)"):
            self.javascript.index("function showToast")
        ]
        notice_lookup = render.index("const startupNotice = (Array.isArray(snapshot.notices) ? snapshot.notices : []).find")
        notice_branch = render.index("if (startupNotice)", notice_lookup)
        unavailable_branch = render.index("else if (unavailable > 0", notice_branch)
        self.assertLess(notice_lookup, notice_branch)
        self.assertLess(notice_branch, unavailable_branch)
        self.assertIn("escapeHtml(String(startupNotice.message).trim())", render)
        startup_branch = render[notice_branch:unavailable_branch]
        self.assertIn("startupNotice.code", startup_branch)
        self.assertIn("dismiss-notice", startup_branch)
        self.assertIn(">关闭</button>", startup_branch)
        self.assertNotIn("startupNotice.details", startup_branch)
        self.assertIn("const result = await api.dismiss_notice(code)", self.javascript)
        self.assertIn("void dismissNotice(dismissNoticeButton.dataset.noticeCode)", self.javascript)

    def test_removed_companion_has_no_live_web_surface(self):
        combined = self.javascript + self.markup + self.styles
        for removed in ("iPhone 伴侣", "createCompanionApi", "pairing-token", "companion-mode", "start-companion"):
            self.assertNotIn(removed, combined)
        self.assertIn("window.addEventListener('pywebviewready', initialize", self.javascript)

    def test_responsive_desktop_metadata_and_safe_areas_are_present(self):
        self.assertIn('name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"', self.markup)
        self.assertNotIn('rel="manifest"', self.markup)
        self.assertNotIn('rel="apple-touch-icon"', self.markup)
        self.assertIn("env(safe-area-inset-top)", self.styles)
        self.assertIn("min-height: 44px", self.styles)
        self.assertIn("prefers-reduced-motion: reduce", self.styles)
        self.assertIn("--data-font", self.styles)

    def test_readable_type_scrollbars_and_compact_navigator_survive_narrow_windows(self):
        for contract in (
            "--ui-font:",
            "--display-font:",
            "--mono-font:",
            "--data-font: var(--mono-font)",
            "--font-body: 15px",
            "font-family: var(--ui-font)",
            "code, pre, kbd, samp { font-family: var(--mono-font); }",
            "html::-webkit-scrollbar",
            "scrollbar-color: var(--scrollbar-thumb) transparent",
            "scrollbar-gutter: stable",
            ".dialog-content { min-width: 0; padding: 16px; overflow-x: hidden; overflow-y: auto; }",
            ".notice-copy, .error-copy, .form-error, .toast",
            "overflow-wrap: anywhere",
            ".account-home strong { max-width: min(620px, 62vw); overflow: visible",
            ".directory-root strong { min-width: 0; overflow: visible",
            ".title-actions { min-width: 0; display: flex; flex: 1 1 auto",
            ".server-navigator, .server-navigator[data-side=\"left\"] { position: relative",
            ".server-navigator-head, .server-navigator-controls, .server-navigator-list",
            "grid-row: 3; grid-column: 1",
        ):
            self.assertIn(contract, self.styles)
        self.assertNotIn(".server-navigator { display: none; }", self.styles)
        self.assertNotIn("transition: max-height", self.styles)
        self.assertNotIn("transition: padding", self.styles)
        self.assertIn(
            '<meta name="theme-color" content="#f8f9f4" media="(prefers-color-scheme: light)">',
            self.markup,
        )
        self.assertIn(
            '<meta name="theme-color" content="#111816" media="(prefers-color-scheme: dark)">',
            self.markup,
        )

    def test_desktop_checks_for_updates(self):
        self.assertIn('id="update-notice"', self.markup)
        self.assertIn("async function checkForUpdates", self.javascript)
        self.assertNotIn("clientMode", self.javascript)
        self.assertIn("void checkForUpdates()", self.javascript)
        initialize = self.javascript[self.javascript.index("async function initialize()") :]
        self.assertLess(
            initialize.index("await loadApplication()"),
            initialize.index("void checkForUpdates()"),
        )
        self.assertIn("UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000", self.javascript)
        self.assertIn("UPDATE_CHECK_RETRY_MS = 5 * 60 * 1000", self.javascript)
        self.assertIn('id="check-for-updates"', self.markup)
        self.assertIn("ui.updateNotice.hidden = true", self.javascript)
        self.assertNotIn("ui.updateNotice.hidden = false", self.javascript)
        self.assertNotIn("ui.updateNotice.innerHTML", self.javascript)
        self.assertNotIn("retry-update-check", self.javascript)
        self.assertNotIn("未能检查更新", self.javascript)
        self.assertNotIn("检查失败，可稍后重试；服务器监控不受影响", self.javascript)
        self.assertIn("const reason = localizedText(message || '暂时无法连接 GitHub')", self.javascript)
        self.assertIn("ui.updateCheckStatus.textContent = reason", self.javascript)
        self.assertIn("checkForUpdates({interactive: true})", self.javascript)
        self.assertIn("async function installLatestUpdate", self.javascript)
        self.assertIn("api.start_latest_update()", self.javascript)
        self.assertIn("api.get_update_progress()", self.javascript)
        self.assertIn("function pollUpdateProgress", self.javascript)
        self.assertIn("api.install_latest_update()", self.javascript)
        self.assertIn("window.confirm(explanation)", self.javascript)
        self.assertIn(".update-notice", self.styles)
        self.assertIn("发现新版本后会进入通知中心", self.markup)
        self.assertIn("下载并校验安装包", self.javascript)
        self.assertIn("notification-update-action", self.javascript)
        self.assertIn("update-download-progress", self.javascript)
        self.assertIn('aria-live="polite"', self.javascript)
        self.assertIn(".button.is-busy", self.styles)
        self.assertIn(".button:active", self.styles)
        self.assertIn(".update-download-progress", self.styles)
        self.assertIn("Confirming the official update", self.localization)
        self.assertIn("Downloading and verifying the update", self.localization)


if __name__ == "__main__":
    unittest.main()
