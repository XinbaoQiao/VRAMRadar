const ui = {
  summary: document.getElementById('summary'),
  list: document.getElementById('server-list'),
  notice: document.getElementById('global-notice'),
  updateNotice: document.getElementById('update-notice'),
  dashboardContent: document.getElementById('dashboard-content'),
  firstRunHome: document.getElementById('first-run-home'),
  startOnboarding: document.getElementById('start-onboarding'),
  refresh: document.getElementById('refresh-button'),
  refreshClock: document.getElementById('refresh-clock'),
  monitoringToggle: document.getElementById('monitoring-toggle'),
  taskAlertIndicator: document.getElementById('task-alert-indicator'),
  taskAlertCount: document.getElementById('task-alert-count'),
  notificationCenter: document.getElementById('notification-center'),
  notificationList: document.getElementById('notification-list'),
  markNotificationsRead: document.getElementById('mark-notifications-read'),
  settings: document.getElementById('settings-button'),
  dialog: document.getElementById('settings-dialog'),
  form: document.getElementById('settings-form'),
  editorList: document.getElementById('server-editor-list'),
  editorTemplate: document.getElementById('server-editor-template'),
  editorToolbar: document.getElementById('server-editor-toolbar'),
  editorSearch: document.getElementById('server-editor-search'),
  editorPageStatus: document.getElementById('server-editor-page-status'),
  editorPreviousPage: document.getElementById('server-editor-previous-page'),
  editorNextPage: document.getElementById('server-editor-next-page'),
  serverOrderStatus: document.getElementById('server-order-status'),
  profileName: document.getElementById('profile-name'),
  refreshSeconds: document.getElementById('refresh-seconds'),
  language: document.getElementById('ui-language'),
  closeBehavior: document.getElementById('close-behavior'),
  favoriteAlertEnabled: document.getElementById('favorite-alert-enabled'),
  favoriteAlertMinMemory: document.getElementById('favorite-alert-min-memory'),
  taskCompletionAlertEnabled: document.getElementById('task-completion-alert-enabled'),
  taskCompletionWatchList: document.getElementById('task-completion-watch-list'),
  checkForUpdates: document.getElementById('check-for-updates'),
  updateCheckStatus: document.getElementById('update-check-status'),
  copyDiagnostics: document.getElementById('copy-diagnostics'),
  openLogsDirectory: document.getElementById('open-logs-directory'),
  settingsError: document.getElementById('settings-error'),
  requiredMemory: document.getElementById('required-memory'),
  requiredGpuCount: document.getElementById('required-gpu-count'),
  preferredGpu: document.getElementById('preferred-gpu'),
  preferredPartition: document.getElementById('preferred-partition'),
  requireSameNode: document.getElementById('require-same-node'),
  recommendationLimit: document.getElementById('recommendation-limit'),
  savedViewChips: document.getElementById('saved-view-chips'),
  savedViewName: document.getElementById('saved-view-name'),
  saveView: document.getElementById('save-view-button'),
  resourceWatchEnabled: document.getElementById('resource-watch-enabled'),
  resourceWatchStatus: document.getElementById('resource-watch-status'),
  recommend: document.getElementById('recommend-button'),
  recommendation: document.getElementById('recommendation'),
  serverListMeta: document.getElementById('server-list-meta'),
  serverListPager: document.getElementById('server-list-pager'),
  serverListPageStatus: document.getElementById('server-list-page-status'),
  serverListPreviousPage: document.getElementById('server-list-previous-page'),
  serverListNextPage: document.getElementById('server-list-next-page'),
  serverNavigator: document.getElementById('server-navigator'),
  serverNavigatorDrag: document.getElementById('server-navigator-drag'),
  serverNavigatorList: document.getElementById('server-navigator-list'),
  serverNavigatorCount: document.getElementById('server-navigator-count'),
  serverNavigatorSearch: document.getElementById('server-navigator-search'),
  serverNavigatorEmpty: document.getElementById('server-navigator-empty'),
  serverNavigatorPosition: document.getElementById('server-navigator-position'),
  serverNavigatorStatus: document.getElementById('server-navigator-status'),
  previousServer: document.getElementById('previous-server'),
  nextServer: document.getElementById('next-server'),
  collapseDashboard: document.getElementById('collapse-dashboard'),
  serverConfigPath: document.getElementById('server-config-path'),
  autoSyncServers: document.getElementById('auto-sync-servers'),
  discoverServerConfig: document.getElementById('discover-server-config'),
  importServerConfig: document.getElementById('import-server-config'),
  importStatus: document.getElementById('import-status'),
  dialogKicker: document.getElementById('dialog-kicker'),
  dialogTitle: document.getElementById('dialog-title'),
  dialogDescription: document.getElementById('dialog-description'),
  settingsDisclosureTools: document.getElementById('settings-disclosure-tools'),
  onboardingProgress: document.getElementById('onboarding-progress'),
  onboardingWelcome: document.getElementById('onboarding-welcome'),
  profileSettings: document.getElementById('profile-settings'),
  importPanel: document.getElementById('import-panel'),
  serverSettingsHeading: document.getElementById('server-settings-heading'),
  closeSettings: document.getElementById('close-settings'),
  cancelSettings: document.getElementById('cancel-settings'),
  onboardingLater: document.getElementById('onboarding-later'),
  onboardingBack: document.getElementById('onboarding-back'),
  onboardingNext: document.getElementById('onboarding-next'),
  saveSettings: document.getElementById('save-settings'),
  toast: document.getElementById('toast'),
};

let currentProfile = null;
let currentSnapshot = null;
let refreshTimer = null;
let refreshPollTimer = null;
let refreshPollGeneration = 0;
let refreshDeferredWhileHidden = false;
let toastTimer = null;
let updateCheckTimer = null;
let updateCheckInFlight = false;
let updateAvailableShown = false;
let latestUpdateAction = 'browser';
let lastUpdateCheckAt = 0;
let api = null;
let settingsMode = 'settings';
let onboardingStep = 1;
let onboardingDiscoveryStarted = false;
let serverDiscoveryGeneration = 0;
let serverEditorSequence = 0;
let settingsServerDrafts = [];
let settingsServerQuery = '';
let settingsServerPageOffset = 0;
let settingsSaveInFlight = false;
let draggedServerDraftIndex = -1;
let pendingIgnoredSshAliases = new Set();
let dashboardDisclosureMode = 'default';
const UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;
const UPDATE_CHECK_RETRY_MS = 5 * 60 * 1000;
const UPDATE_CHECK_ON_FOCUS_AFTER_MS = 60 * 60 * 1000;
let recommendationRequested = false;
let activeServerId = '';
let serverNavigationFrame = null;
let serverNavigationCards = [];
let serverNavigationCardsById = new Map();
let serverNavigatorQuery = '';
let serverNavigatorFilter = 'all';
let serverNavigatorSearchFrame = null;
let serverNavigatorVisibleIds = [];
let serverNavigatorItems = new Map();
let serverNavigatorPositions = new Map();
let serverNavigatorSide = 'right';
let serverNavigatorDragState = null;
let suppressServerNavigatorDragClick = false;
let favoriteServerIds = new Set();
let recentServerIds = [];
let monitoringPaused = false;
let profileServerListReference = null;
let profileServersById = new Map();
let lastRenderedRevision = null;
let lastSummaryRenderSignature = '';
let lastNavigatorRenderSignature = '';
const renderedServerCardSignatures = new Map();
let resourceWatchMatched = false;
let resourceWatchLastNotificationAt = 0;
let resourceWatchEvaluation = null;
let resourceWatchCriteriaRevision = 0;
let resourceWatchDebounceTimer = null;
const RESOURCE_WATCH_COOLDOWN_MS = 15 * 60 * 1000;
const clusterNodePages = new Map();
const clusterNodeRequestGenerations = new Map();
const CLUSTER_NODE_PAGE_SIZE = 75;
const SERVER_FLEET_PAGE_SIZE = 50;
const LARGE_SERVER_FLEET_THRESHOLD = 100;
const SERVER_NAVIGATOR_RENDER_LIMIT = 80;
const SERVER_EDITOR_PAGE_SIZE = 20;
let serverFleetPageOffset = 0;
const openClusters = new Set();
const openTaskGroups = new Set();
const openDirectoryNodes = new Set();
const openContextNotes = new Set();
const directoryTrees = new Map();
const directoryRequestTokens = new Map();
const directoryFreshnessDeadlines = new Map();
let directoryRequestSequence = 0;
let directoryFreshnessTimer = null;
const MAX_DIRECTORY_ROOTS_PER_SERVER = 32;
const DIRECTORY_FRESHNESS_ERROR_RETRY_MS = 30_000;
const uiRenderMetrics = Object.seal({
  fullRenders: 0,
  serverCardCreates: 0,
  directoryRepaints: 0,
  navigatorBuilds: 0,
});
window.__VRAM_RADAR_PERF__ = uiRenderMetrics;

function profileServerFor(serverId) {
  const servers = currentProfile?.servers || [];
  if (servers !== profileServerListReference) {
    profileServerListReference = servers;
    profileServersById = new Map(servers.map(server => [server.id, server]));
  }
  return profileServersById.get(serverId);
}

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[character]);
const activeLocale = () => window.VRAMRadarI18n?.language === 'en' ? 'en-US' : 'zh-CN';
const localizedText = value => window.VRAMRadarI18n?.translateText(value, window.VRAMRadarI18n.language) || value;
const number = value => value == null ? '未知' : Number(value).toLocaleString(activeLocale(), {maximumFractionDigits: 2});
const ICONS = Object.freeze({
  alert: '<circle cx="12" cy="12" r="9"></circle><path d="M12 7.5v5"></path><path d="M12 16.5h.01"></path>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"></path><path d="M10 21h4"></path>',
  cancelled: '<circle cx="12" cy="12" r="9"></circle><path d="m9 9 6 6"></path><path d="m15 9-6 6"></path>',
  check: '<circle cx="12" cy="12" r="9"></circle><path d="m8.5 12 2.25 2.25 4.75-4.75"></path>',
  chevron: '<path d="m7 10 5 5 5-5"></path>',
  clock: '<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path>',
  copy: '<rect x="8" y="8" width="11" height="11" rx="1.5"></rect><path d="M16 8V5H5v11h3"></path>',
  file: '<path d="M7 3.5h6l4 4V20H7z"></path><path d="M13 3.5V8h4"></path>',
  folder: '<path d="M3.5 7.5h6l2-2h9v13h-17z"></path>',
  link: '<path d="M9.5 14.5 14.5 9.5"></path><path d="M7.5 16.5H6a3.5 3.5 0 0 1 0-7h3"></path><path d="M16.5 7.5H18a3.5 3.5 0 0 1 0 7h-3"></path>',
  memory: '<rect x="5" y="7" width="14" height="10" rx="2"></rect><path d="M9 3v4M15 3v4M9 17v4M15 17v4M3 10h2M3 14h2M19 10h2M19 14h2"></path>',
  pause: '<circle cx="12" cy="12" r="9"></circle><path d="M10 9v6M14 9v6"></path>',
  play: '<circle cx="12" cy="12" r="9"></circle><path d="m10.5 9 4.5 3-4.5 3z"></path>',
  star: '<path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6-5.4-2.8-5.4 2.8 1-6-4.4-4.3 6.1-.9z"></path>',
  terminal: '<rect x="3.5" y="5" width="17" height="14" rx="2"></rect><path d="m7 9 3 3-3 3M12.5 15h4"></path>',
  user: '<circle cx="12" cy="8.5" r="3.25"></circle><path d="M5.75 19c.65-3.35 2.74-5.25 6.25-5.25s5.6 1.9 6.25 5.25"></path>',
  users: '<circle cx="9.5" cy="8.5" r="3"></circle><path d="M3.75 18.5c.6-3.15 2.52-4.9 5.75-4.9 1.24 0 2.29.26 3.14.76"></path><path d="M15 6.3a2.8 2.8 0 0 1 0 5.4M15.3 13.9c2.72.28 4.35 1.82 4.95 4.6"></path>',
});
const icon = (name, className = 'ui-icon') => `<svg class="${className}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONS[name] || ''}</svg>`;
const stateLabel = state => ({
  connecting: '正在配置中', online: '监控就绪', stale: '数据已过期', offline: '网络不可达', auth_required: '需要认证',
  security_blocked: '安全阻止', misconfigured: '配置异常', disabled: '已停用'
})[state] || state;
const backendLabel = backend => backend === 'slurm_ssh' ? 'Slurm GPU 调度状态' : 'GPU 实时显存';
const taskStateLabel = state => ({
  PENDING: '排队中', RUNNING: '运行中', COMPLETING: '收尾中', CONFIGURING: '准备中', SUSPENDED: '已暂停',
  COMPLETED: '已完成', FAILED: '失败', CANCELLED: '已取消', TIMEOUT: '超时', OUT_OF_MEMORY: '作业内存不足'
})[state] || state;
const taskStateClass = state => ({
  PENDING: 'pending', RUNNING: 'running', COMPLETING: 'running', CONFIGURING: 'pending', SUSPENDED: 'warning',
  COMPLETED: 'completed', FAILED: 'failed', CANCELLED: 'cancelled', TIMEOUT: 'failed', OUT_OF_MEMORY: 'failed'
})[state] || 'neutral';
const taskStateIcon = state => ({
  PENDING: 'clock', RUNNING: 'play', COMPLETING: 'clock', CONFIGURING: 'clock', SUSPENDED: 'pause',
  COMPLETED: 'check', FAILED: 'alert', CANCELLED: 'cancelled', TIMEOUT: 'clock', OUT_OF_MEMORY: 'memory'
})[state] || 'clock';

function formatSlurmDuration(value) {
  const raw = String(value || '').trim();
  if (!raw || raw === 'N/A' || raw === 'Unknown' || raw === 'NOT_SET') return '—';
  if (raw === 'UNLIMITED') return '不限时';
  if (raw === 'Partition_Limit') return '分区默认';
  const [dayPart, timePart] = raw.includes('-') ? raw.split('-', 2) : [null, raw];
  const pieces = timePart.split(':');
  if (!pieces.every(piece => /^\d+$/.test(piece)) || ![2, 3].includes(pieces.length)) return raw;
  const days = dayPart == null ? 0 : Number(dayPart);
  const hours = pieces.length === 3 ? Number(pieces[0]) : 0;
  const minutes = Number(pieces[pieces.length - 2]);
  const seconds = Number(pieces[pieces.length - 1]);
  const units = [];
  if (days) units.push(`${days}天`);
  if (hours) units.push(`${hours}小时`);
  if (minutes) units.push(`${minutes}分钟`);
  if (seconds && units.length < 2) units.push(`${seconds}秒`);
  return units.slice(0, 2).join('') || '0分钟';
}

function formatTaskTimestamp(value) {
  if (!value || value === 'Unknown' || value === 'N/A') return '—';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(activeLocale(), {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

function formatTaskLocation(task) {
  if (task.nodes) return task.nodes;
  const reason = String(task.reason || '').replace(/^\((.*)\)$/, '$1');
  return ({
    Priority: '等待调度优先级', Resources: '等待 GPU 资源', Dependency: '等待依赖任务',
    ReqNodeNotAvail: '请求节点暂不可用', QOSMaxJobsPerUserLimit: '已达到用户任务上限',
  })[reason] || reason || '等待分配';
}

function schedulerStateLabel(value) {
  const raw = String(value || '').toLowerCase();
  const normalized = raw.replace(/[+*~#@%$-]+$/g, '');
  return ({
    alloc: '已分配', comp: '即将完成', down: '不可用', drain: '排空中', drng: '正在排空',
    fail: '故障', idle: '空闲', maint: '维护中', mix: '部分占用', resv: '已保留', unk: '未知',
  })[normalized] || value || '未知';
}

function schedulerStateClass(value) {
  const normalized = String(value || '').toLowerCase().replace(/[+*~#@%$-]+$/g, '');
  if (normalized === 'idle') return 'idle';
  if (normalized === 'mix') return 'partial';
  if (['alloc', 'comp', 'resv'].includes(normalized)) return 'busy';
  return 'unavailable';
}

const formatGpuType = value => String(value || '未知卡型').replace(/\s+x(\d+)/gi, ' × $1');

function formatTime(value) {
  if (!value) return '从未成功';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(activeLocale());
}

function formatRetry(value) {
  if (!value) return '';
  const seconds = Math.max(0, Math.round((new Date(value).valueOf() - Date.now()) / 1000));
  return seconds > 0 ? `约 ${seconds} 秒后自动重试` : '即将自动重试';
}

function capacityTape(summary) {
  const free = Math.max(0, Number(summary.free_vram_gib) || 0);
  const total = Number(summary.total_vram_gib);
  const totalKnown = Number.isFinite(total) && total > 0;
  const percent = totalKnown ? Math.max(0, Math.min(100, Math.round(free / total * 100))) : null;
  const tone = percent == null ? 'unknown' : percent <= 20 ? 'critical' : percent <= 50 ? 'warning' : 'healthy';
  const toneLabel = tone === 'critical' ? '紧张' : tone === 'warning' ? '偏低' : tone === 'healthy' ? '充足' : '未知';
  const cellCount = 18;
  const activeCount = percent == null ? 0 : Math.min(cellCount, Math.max(percent > 0 ? 1 : 0, Math.round(percent / 100 * cellCount)));
  const cells = Array.from({length: cellCount}, (_, index) => {
    const x = 17 + index * 16;
    return `<rect class="tape-cell${index < activeCount ? ' available' : ''}" x="${x}" y="22" width="10" height="30" rx="1"></rect>`;
  }).join('');
  const percentLabel = percent == null ? '—' : `${percent}<small>%</small>`;
  const totalLabel = totalKnown ? `总量 ${number(total)} GiB` : '总量待补充';
  const accessible = totalKnown
    ? `在线显存池，可用 ${number(free)} GiB，共 ${number(total)} GiB，可用率 ${percent}%，状态${toneLabel}`
    : `在线显存池，可用 ${number(free)} GiB，总量暂不可用`;
  return `<div class="capacity-visual ${tone}" role="img" aria-label="${escapeHtml(accessible)}"><div class="capacity-visual-head"><span>可用比例</span><strong>${percentLabel}</strong></div><svg class="capacity-tape" viewBox="0 0 320 72" aria-hidden="true" focusable="false"><rect class="tape-frame" x="8" y="14" width="304" height="46" rx="4"></rect>${cells}<path class="tape-baseline" d="M8 66h304"></path><path class="tape-ticks" d="M8 66v4M104 66v4M200 66v4M312 66v4"></path></svg><div class="capacity-scale"><span>0 GiB</span><span>${escapeHtml(totalLabel)}</span></div></div>`;
}

function renderSummary(summary) {
  return `<article class="metric capacity-metric"><div class="metric-copy"><div class="metric-label">当前可用显存</div><div class="metric-value"><strong>${number(summary.free_vram_gib)}</strong><span>GiB</span></div><div class="metric-detail">所有监控就绪 GPU 合计</div></div>${capacityTape(summary)}</article><div class="metric-stack"><article class="metric compact-metric"><div class="metric-label">监控就绪</div><div class="metric-value"><strong>${number(summary.online_servers)}</strong><span>/ ${number(summary.total_servers)}</span></div><div class="metric-detail">SSH 已认证 / 资源已读取</div></article><article class="metric compact-metric"><div class="metric-label">已读取 GPU</div><div class="metric-value"><strong>${number(summary.total_gpus)}</strong><span>块</span></div><div class="metric-detail">仅统计当前有效快照</div></article></div>`;
}

function setRefreshClock(text, active = false) {
  ui.refreshClock.innerHTML = `<i class="${active ? 'reading' : ''}" aria-hidden="true"></i><span>${escapeHtml(text)}</span>`;
}

function memoryTrack(used, total, label = '显存占用率', valueLabel = '已使用') {
  const percent = total ? Math.min(100, Math.max(0, Math.round(used / total * 100))) : 0;
  const tone = percent >= 90 ? 'critical' : percent >= 75 ? 'warning' : 'normal';
  return `<progress class="memory-track ${tone}" role="progressbar" max="100" value="${percent}" aria-label="${escapeHtml(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}" aria-valuetext="${escapeHtml(valueLabel)} ${percent}%">${percent}%</progress>`;
}

function renderLiveTable(server) {
  const memoryTable = `<div class="table-wrap" role="region" aria-label="GPU 实时显存，可横向滚动"><table><caption class="sr-only">GPU 实时显存</caption><thead><tr><th scope="col">GPU</th><th scope="col">卡型</th><th scope="col">已用显存</th><th scope="col">空闲显存</th><th scope="col">利用率</th><th scope="col">温度</th></tr></thead><tbody>${(server.gpus || []).map(gpu => `<tr><td>GPU ${escapeHtml(gpu.gpu_index)}</td><td>${escapeHtml(gpu.gpu_type)}</td><td class="memory-cell"><div class="memory-values"><strong>${number(gpu.memory_used_gib)} GiB</strong><span>总量 ${number(gpu.memory_total_gib)} GiB</span></div>${memoryTrack(gpu.memory_used_gib, gpu.memory_total_gib)}</td><td>${number(gpu.memory_free_gib)} GiB</td><td>${gpu.utilization_percent == null ? '未知' : `${number(gpu.utilization_percent)}%`}</td><td>${gpu.temperature_c == null ? '未知' : `${number(gpu.temperature_c)} °C`}</td></tr>`).join('')}</tbody></table></div>`;
  return memoryTable + renderDirectProcessModule(server);
}

function taskBadge(state, count = null, label = null) {
  const visibleLabel = label || taskStateLabel(state);
  const countMarkup = count == null ? '' : `<strong>${number(count)}</strong>`;
  return `<span class="task-badge ${taskStateClass(state)}" aria-label="${escapeHtml(visibleLabel)}">${icon(taskStateIcon(state), 'badge-icon')}<span>${escapeHtml(visibleLabel)}</span>${countMarkup}</span>`;
}

function renderTaskUser(task, currentUser) {
  const user = String(task.user || '').trim();
  if (!user) return '<span class="task-user unknown">未提供</span>';
  const isSelf = Boolean(currentUser && user === currentUser);
  return `<span class="task-user${isSelf ? ' self' : ''}"><span>${escapeHtml(user)}</span>${isSelf ? '<span class="self-user-tag">我</span>' : ''}</span>`;
}

const formatTaskGpuCount = value => value == null ? '未记录' : `${number(value)} 张`;

function contextCopyButton(value, label) {
  const text = String(value ?? '').trim();
  if (!text) return '';
  return `<button class="context-copy" type="button" data-copy-value="${escapeHtml(text)}" aria-label="复制${escapeHtml(label)}" title="复制${escapeHtml(label)}">${icon('copy')}</button>`;
}

function copyableValue(value, label) {
  const text = String(value ?? '').trim();
  const safeText = escapeHtml(text || '—');
  return `<span class="copyable-value"><span title="${safeText}">${safeText}</span>${contextCopyButton(text, label)}</span>`;
}

function renderTaskName(task) {
  const name = String(task.name || '').trim();
  if (!name) return '<span class="task-name missing">未记录</span>';
  const safeName = escapeHtml(name);
  if (Array.from(name).length <= 32) return `<span class="task-name" title="${safeName}">${safeName}</span>`;
  return `<details class="task-name-details"><summary title="查看完整任务名称：${safeName}"><span class="task-name-preview">${safeName}</span><span class="task-name-hint"><span class="when-closed">展开</span><span class="when-open">收起</span></span></summary><div class="task-name-full">${safeName}</div></details>`;
}

function taskCompletionKey(kind, task) {
  if (kind === 'slurm') {
    const jobId = String(task.job_id || '').trim();
    const submittedAt = String(task.submitted_at || '').trim();
    return submittedAt ? `slurm:${jobId}:${submittedAt}` : `slurm:${jobId}`;
  }
  const pid = String(task.pid || '').trim();
  const startedAt = String(task.started_at || '').trim();
  return startedAt ? `process:${pid}:${startedAt}` : `process:${pid}`;
}

function taskCompletionWatchButton(serverId, kind, task, currentUser) {
  const mine = task.owner_scope === 'mine' || (currentUser && task.user === currentUser);
  const taskId = String(kind === 'slurm' ? task.job_id : task.pid || '').trim();
  if (!taskId) return '';
  const taskKey = taskCompletionKey(kind, task);
  const watched = (currentProfile?.task_completion_watches || []).some(
    item => item.server_id === serverId && item.task_key === taskKey,
  );
  const label = String(task.name || task.command_preview || (kind === 'slurm' ? taskId : `PID ${taskId}`)).trim();
  const owner = String(task.user || '').trim();
  const ownerScope = mine ? 'mine' : (owner ? 'other' : 'unknown');
  const watchTitle = watched
    ? '取消单独提醒'
    : (mine ? '单独关注完成提醒' : '仅关注这个用户的这一项任务');
  return `<button class="task-watch-toggle" type="button" data-server-id="${escapeHtml(serverId)}" data-task-key="${escapeHtml(taskKey)}" data-task-kind="${escapeHtml(kind)}" data-task-id="${escapeHtml(taskId)}" data-task-label="${escapeHtml(label)}" data-task-owner="${escapeHtml(owner)}" data-task-owner-scope="${ownerScope}" aria-pressed="${watched}" title="${watchTitle}">${icon('bell')}<span>${watched ? '已关注' : '关注'}</span></button>`;
}

function renderTaskTable(tasks, recent = false, currentUser = '', emptyMessage = '', serverId = '') {
  if (!tasks.length) return `<div class="module-empty">${escapeHtml(emptyMessage || (recent ? '所选时间范围内没有可显示的 GPU 终态任务' : '当前没有排队或运行中的 GPU 任务'))}</div>`;
  const timeHeaders = recent ? '<th scope="col">结束时间</th>' : '<th scope="col">提交时间</th><th scope="col">时间限额</th>';
  const timeCells = task => recent
    ? `<td class="time-value" data-label="结束时间">${escapeHtml(formatTaskTimestamp(task.ended_at))}</td>`
    : `<td class="time-value" data-label="提交时间">${escapeHtml(formatTaskTimestamp(task.submitted_at))}</td><td class="time-value" data-label="时间限额">${escapeHtml(formatSlurmDuration(task.time_limit))}</td>`;
  const runDuration = task => !recent && task.state === 'PENDING' ? '尚未开始' : formatSlurmDuration(task.elapsed);
  const actionHeader = recent ? '' : '<th scope="col">提醒</th>';
  return `<div class="table-wrap task-table" role="region" aria-label="GPU 任务列表，可横向滚动"><table><caption class="sr-only">${recent ? '近 24 小时 GPU 任务结果' : '当前 GPU 任务队列'}</caption><thead><tr><th scope="col">用户</th><th scope="col">任务 ID</th><th scope="col">任务名称</th><th scope="col">状态</th><th scope="col">节点或排队原因</th><th scope="col">运行时长</th>${timeHeaders}<th scope="col">占用 GPU</th>${actionHeader}</tr></thead><tbody>${tasks.map(task => `<tr><td data-label="用户">${renderTaskUser(task, currentUser)}</td><td class="mono copyable-cell" data-label="任务 ID">${copyableValue(task.job_id, '任务 ID')}</td><td class="task-name-cell" data-label="任务名称">${renderTaskName(task)}</td><td data-label="状态">${taskBadge(task.state)}</td><td class="task-location" data-label="节点或排队原因">${escapeHtml(formatTaskLocation(task))}</td><td class="time-value" data-label="运行时长">${escapeHtml(runDuration(task))}</td>${timeCells(task)}<td class="number-value" data-label="占用 GPU">${escapeHtml(formatTaskGpuCount(task.gpu_count))}</td>${recent ? '' : `<td data-label="提醒">${taskCompletionWatchButton(serverId, 'slurm', task, currentUser)}</td>`}</tr>`).join('')}</tbody></table></div>`;
}

function partitionTasksByOwner(items, currentUser) {
  const result = {mine: [], others: []};
  (items || []).forEach(task => {
    if (currentUser && task.user === currentUser) result.mine.push(task);
    else result.others.push(task);
  });
  return result;
}

function moduleOpen(serverId, moduleKey, defaultOpen = false) {
  const key = `${serverId}:${moduleKey}`;
  if (openClusters.has(key)) return ' open';
  if (openClusters.has(`${key}:closed`)) return '';
  return defaultOpen ? ' open' : '';
}

function taskGroupOpen(serverId, moduleKey, group, defaultOpen = false) {
  const key = `${serverId}:${moduleKey}:${group}`;
  if (openTaskGroups.has(key)) return ' open';
  if (openTaskGroups.has(`${key}:closed`)) return '';
  return defaultOpen ? ' open' : '';
}

function contextNoteOpen(serverId, key) {
  return openContextNotes.has(`${serverId}:${key}`) ? ' open' : '';
}

function renderModuleContext(serverId, key, text) {
  return `<details class="module-context" data-context-note="${escapeHtml(key)}" data-server-id="${escapeHtml(serverId)}"${contextNoteOpen(serverId, key)}><summary>说明</summary><p>${text}</p></details>`;
}

function renderTaskPeriod(title, tasks, recent, currentUser, emptyMessage, historySupported = true, serverId = '') {
  const body = recent && !historySupported
    ? '<div class="module-empty">当前服务器未提供近期任务历史，仍可查看当前任务。</div>'
    : renderTaskTable(tasks, recent, currentUser, emptyMessage, serverId);
  return `<section class="task-period"><header class="task-period-head"><h6>${escapeHtml(title)}</h6><span>${number(tasks.length)} 条</span></header>${body}</section>`;
}

function renderTaskOwnerGroup(server, options) {
  const {key, title, subtitle, iconName, active, recent, currentUser, historySupported, defaultOpen, activeEmpty, recentEmpty} = options;
  const meta = `${number(active.length)} 当前 · ${number(recent.length)} 近期`;
  const secondary = subtitle ? `<small>${escapeHtml(subtitle)}</small>` : '';
  return `<details class="task-group task-owner-group ${escapeHtml(key)}" data-task-group="${escapeHtml(key)}" data-task-module="cluster-tasks" data-server-id="${escapeHtml(server.server_id)}"${taskGroupOpen(server.server_id, 'cluster-tasks', key, defaultOpen)}><summary><span class="task-owner-heading">${icon(iconName, 'owner-icon')}<span><h5>${escapeHtml(title)}</h5>${secondary}</span></span><span class="task-group-meta">${escapeHtml(meta)} ${icon('chevron', 'task-group-chevron')}</span></summary><div class="task-owner-content">${renderTaskPeriod('正在运行与排队', active, false, currentUser, activeEmpty, true, server.server_id)}${renderTaskPeriod(`过去 ${number((server.tasks || {}).history_window_hours || 24)} 小时结果`, recent, true, currentUser, recentEmpty, historySupported, server.server_id)}</div></details>`;
}

function formatElapsedSeconds(value) {
  if (value == null || Number.isNaN(Number(value))) return '权限受限';
  let seconds = Math.max(0, Math.floor(Number(value)));
  const days = Math.floor(seconds / 86400);
  seconds %= 86400;
  const hours = Math.floor(seconds / 3600);
  seconds %= 3600;
  const minutes = Math.floor(seconds / 60);
  const units = [];
  if (days) units.push(`${days}天`);
  if (hours) units.push(`${hours}小时`);
  if (minutes) units.push(`${minutes}分钟`);
  if (!units.length) units.push(`${seconds}秒`);
  return units.slice(0, 2).join('');
}

function renderProcessName(process) {
  const preview = String(process.command_preview || '').trim();
  const command = preview
    ? `<details class="process-command-details"><summary>查看命令摘要</summary><code>${escapeHtml(preview)}</code><small>敏感参数已遮盖${process.command_truncated ? ' · 已安全截断' : ''}</small></details>`
    : process.command_visibility === 'hidden_for_privacy'
    ? '<span class="process-command-missing">其他用户命令摘要未启用</span>'
    : '<span class="process-command-missing">命令详情受服务器权限限制</span>';
  return `<div class="process-name-stack">${renderTaskName(process)}${command}</div>`;
}

function renderProcessAllocations(process) {
  const allocations = process.allocations || [];
  if (!allocations.length) return '<span class="process-gpu-allocation muted">GPU 未识别</span>';
  return `<div class="process-gpu-list">${allocations.map(allocation => `<span class="process-gpu-allocation"><strong>${allocation.gpu_index == null ? 'GPU 未识别' : `GPU ${escapeHtml(allocation.gpu_index)}`}</strong><small>${allocation.memory_used_gib == null ? '显存未知' : `${number(allocation.memory_used_gib)} GiB`}</small></span>`).join('')}</div>`;
}

function renderProcessTable(processes, currentUser, emptyMessage, serverId = '') {
  if (!processes.length) return `<div class="module-empty">${escapeHtml(emptyMessage)}</div>`;
  return `<div class="table-wrap task-table process-table" role="region" aria-label="当前 GPU 进程，可横向滚动"><table><caption class="sr-only">当前 GPU 进程</caption><thead><tr><th scope="col">用户</th><th scope="col">PID</th><th scope="col">进程 / 任务</th><th scope="col">GPU 明细</th><th scope="col">显存合计</th><th scope="col">运行时长</th><th scope="col">启动时间</th><th scope="col">提醒</th></tr></thead><tbody>${processes.map(process => `<tr><td data-label="用户">${renderTaskUser(process, currentUser)}</td><td class="mono copyable-cell" data-label="PID">${copyableValue(process.pid, 'PID')}</td><td class="task-name-cell process-name-cell" data-label="进程 / 任务">${renderProcessName(process)}</td><td data-label="GPU 明细">${renderProcessAllocations(process)}</td><td class="number-value" data-label="显存合计">${process.memory_used_gib == null ? '未知' : `${number(process.memory_used_gib)} GiB`}</td><td class="time-value" data-label="运行时长">${escapeHtml(formatElapsedSeconds(process.elapsed_seconds))}</td><td class="time-value" data-label="启动时间">${process.started_at ? escapeHtml(formatTaskTimestamp(process.started_at)) : '权限受限'}</td><td data-label="提醒">${taskCompletionWatchButton(serverId, 'process', process, currentUser)}</td></tr>`).join('')}</tbody></table></div>`;
}

function renderProcessOwnerGroup(server, options) {
  const {key, title, subtitle, iconName, processes, currentUser, defaultOpen, emptyMessage} = options;
  const secondary = subtitle ? `<small>${escapeHtml(subtitle)}</small>` : '';
  return `<details class="task-group task-owner-group process-owner-group ${escapeHtml(key)}" data-task-group="${escapeHtml(key)}" data-task-module="gpu-processes" data-server-id="${escapeHtml(server.server_id)}"${taskGroupOpen(server.server_id, 'gpu-processes', key, defaultOpen)}><summary><span class="task-owner-heading">${icon(iconName, 'owner-icon')}<span><h5>${escapeHtml(title)}</h5>${secondary}</span></span><span class="task-group-meta">${number(processes.length)} 个 ${icon('chevron', 'task-group-chevron')}</span></summary><div class="task-owner-content">${renderProcessTable(processes, currentUser, emptyMessage, server.server_id)}</div></details>`;
}

function renderDirectProcessModule(server) {
  const processState = server.processes;
  const stale = server.connection?.state !== 'online';
  const moduleTitle = stale ? '上次 GPU 进程' : 'GPU 进程';
  const moduleStatus = processState?.supported
    ? `${number((processState.active || []).length)} 个可见进程`
    : '进程信息不可用';
  const visibilityText = stale
    ? '这是上次成功采样时可见的 GPU 进程，不能据此判断它们现在仍在运行。'
    : '显示当前直连服务器可见的 GPU 进程；不是调度队列，不包含排队或完成历史。可见命令只显示经过敏感参数遮盖与长度限制的摘要，其他用户摘要还需在服务器设置中开启。';
  const moduleHead = `<summary><span class="cluster-heading"><h4>${moduleTitle}</h4></span><span class="task-summary"><span class="process-summary-pill">${escapeHtml(moduleStatus)}</span></span><span class="details-chevron">${icon('chevron')}</span></summary>`;
  const context = renderModuleContext(
    server.server_id,
    'process-scope',
    `${escapeHtml(visibilityText)}${processState?.metadata_limited ? ' 进程较多，部分用户与时间详情将在后续刷新中补齐。' : ''}`,
  );
  if (!processState?.supported) {
    const warning = processState?.warning || (stale ? '这份旧快照尚未包含 GPU 进程；下一次成功刷新后会自动补充。' : '等待下一次刷新获取 GPU 进程。');
    return `<details class="cluster-module process-module" data-module="gpu-processes" data-server-id="${escapeHtml(server.server_id)}"${moduleOpen(server.server_id, 'gpu-processes', false)}>${moduleHead}<div class="cluster-content"><div class="module-empty process-unavailable">${escapeHtml(warning)}</div>${context}</div></details>`;
  }
  const currentUser = String(processState.current_user || '').trim();
  const groups = {mine: [], others: [], unknown: []};
  (processState.active || []).forEach(process => {
    if (process.owner_scope === 'mine') groups.mine.push(process);
    else if (process.owner_scope === 'other') groups.others.push(process);
    else groups.unknown.push(process);
  });
  const otherUsers = new Set(groups.others.map(process => String(process.user || '').trim()).filter(Boolean));
  let ownerGroups = renderProcessOwnerGroup(server, {
    key: 'process-mine', title: '我的进程', subtitle: currentUser, iconName: 'user',
    processes: groups.mine, currentUser, defaultOpen: true, emptyMessage: '当前没有我的 GPU 进程',
  }) + renderProcessOwnerGroup(server, {
    key: 'process-others', title: '其他用户', subtitle: `${number(otherUsers.size)} 位`, iconName: 'users',
    processes: groups.others, currentUser, defaultOpen: false, emptyMessage: '当前没有可见的其他用户 GPU 进程',
  });
  if (groups.unknown.length) {
    ownerGroups += renderProcessOwnerGroup(server, {
      key: 'process-unknown', title: '归属不可见', subtitle: '权限受限', iconName: 'users',
      processes: groups.unknown, currentUser, defaultOpen: false, emptyMessage: '',
    });
  }
  return `<details class="cluster-module process-module" data-module="gpu-processes" data-server-id="${escapeHtml(server.server_id)}"${moduleOpen(server.server_id, 'gpu-processes', false)}>${moduleHead}<div class="cluster-content"><div class="task-owner-stack">${ownerGroups}</div>${context}</div></details>`;
}

function schedulerMemoryMeter(node) {
  const total = node.total_vram_gib == null ? Number.NaN : Number(node.total_vram_gib);
  const free = node.free_vram_gib == null ? Number.NaN : Number(node.free_vram_gib);
  const perGpu = node.memory_per_gpu_gib == null ? Number.NaN : Number(node.memory_per_gpu_gib);
  const allocatedGpus = Number(node.allocated_gpus);
  if (![total, free, perGpu, allocatedGpus].every(Number.isFinite) || total <= 0 || perGpu <= 0) {
    return '<span class="scheduler-memory-unavailable">未配置单卡显存，无法计算</span>';
  }
  const allocated = Math.min(total, Math.max(0, allocatedGpus * perGpu));
  return `<div class="memory-cell scheduler-memory-cell"><div class="memory-values"><strong>已分配 ${number(allocated)} GiB</strong><span>调度器未分配 ${number(free)} / ${number(total)} GiB</span></div>${memoryTrack(allocated, total, 'GPU 调度占用率', '已分配（按整卡）')}<small class="scheduler-memory-note">按 Slurm 已分配整卡计算，不代表进程实时显存</small></div>`;
}

function renderClusterModule(server) {
  const tasks = server.tasks || {active: [], recent: [], counts: {}, history_supported: false};
  const currentUser = String(tasks.current_user || '').trim();
  const activeByOwner = partitionTasksByOwner(tasks.active || [], currentUser);
  const recentByOwner = partitionTasksByOwner(tasks.recent || [], currentUser);
  const otherUsers = new Set([...activeByOwner.others, ...recentByOwner.others].map(task => String(task.user || '').trim()).filter(Boolean));
  const counts = tasks.counts || {};
  const queuedCount = counts.PENDING || 0;
  const configuringCount = counts.CONFIGURING || 0;
  const runningCount = (counts.RUNNING || 0) + (counts.COMPLETING || 0);
  const issueCount = (counts.SUSPENDED || 0) + (counts.FAILED || 0) + (counts.CANCELLED || 0) + (counts.TIMEOUT || 0) + (counts.OUT_OF_MEMORY || 0);
  const summaryItems = [
    ['PENDING', queuedCount, '排队'], ['CONFIGURING', configuringCount, '准备'], ['RUNNING', runningCount, '运行'],
    ['COMPLETED', counts.COMPLETED || 0, '完成'], ['FAILED', issueCount, '异常'],
  ].filter(([, count]) => count > 0);
  const summaryBadges = summaryItems.length
    ? summaryItems.map(([state, count, label]) => taskBadge(state, count, label)).join('')
    : '<span class="task-summary-empty">暂无 GPU 任务</span>';
  const clusterOpen = moduleOpen(server.server_id, 'cluster-tasks', false);
  const scopeText = `仅显示 Slurm 对当前登录账号可见的 GPU 作业；集群权限策略可能隐藏其他用户任务。任务名称来自提交者设置的 Slurm JobName；这里不是计算节点内的 PID 进程列表。${currentUser ? '' : ' 正在识别当前账号。'}`;
  const context = renderModuleContext(server.server_id, 'task-scope', escapeHtml(scopeText));
  const ownerGroups = currentUser
    ? renderTaskOwnerGroup(server, {
        key: 'mine', title: '我的任务', subtitle: currentUser, iconName: 'user',
        active: activeByOwner.mine, recent: recentByOwner.mine, currentUser, historySupported: Boolean(tasks.history_supported), defaultOpen: true,
        activeEmpty: '当前没有我的排队或运行中 GPU 任务', recentEmpty: '过去 24 小时内没有我的可见 GPU 终态任务',
      }) + renderTaskOwnerGroup(server, {
        key: 'others', title: '其他用户', subtitle: `${number(otherUsers.size)} 位`, iconName: 'users',
        active: activeByOwner.others, recent: recentByOwner.others, currentUser, historySupported: Boolean(tasks.history_supported), defaultOpen: false,
        activeEmpty: '当前账号未看到其他用户的活动 GPU 任务', recentEmpty: '过去 24 小时内当前账号未看到其他用户的 GPU 终态任务',
      })
    : renderTaskOwnerGroup(server, {
        key: 'visible', title: '当前可见任务', subtitle: '账号识别中', iconName: 'users',
        active: tasks.active || [], recent: tasks.recent || [], currentUser: '', historySupported: Boolean(tasks.history_supported), defaultOpen: true,
        activeEmpty: '当前没有可见的排队或运行中 GPU 任务', recentEmpty: '过去 24 小时内没有可见的 GPU 终态任务',
      });
  return `<details class="cluster-module" data-module="cluster-tasks" data-server-id="${escapeHtml(server.server_id)}"${clusterOpen}><summary><span class="cluster-heading"><h4>任务详情</h4></span><span class="task-summary">${summaryBadges}</span><span class="details-chevron">${icon('chevron')}</span></summary><div class="cluster-content"><div class="task-owner-stack">${ownerGroups}</div>${context}</div></details>`;
}

function renderSchedulerNodeRows(nodes) {
  return (nodes || []).slice(0, CLUSTER_NODE_PAGE_SIZE).map(node => `<tr><td class="copyable-cell">${copyableValue(node.node, '节点名')}</td><td title="${escapeHtml(node.partition)}">${escapeHtml(node.partition)}</td><td title="${escapeHtml(formatGpuType(node.gpu_type))} · ${number(node.memory_per_gpu_gib)} GiB/卡">${escapeHtml(formatGpuType(node.gpu_type))} · ${number(node.memory_per_gpu_gib)} GiB/卡</td><td>${number(node.free_gpus)} 张（共 ${number(node.total_gpus)} 张）</td><td>${schedulerMemoryMeter(node)}</td><td>${number((node.tasks || []).length)}</td><td><span class="node-state ${schedulerStateClass(node.state)}" title="Slurm: ${escapeHtml(node.state)}">${escapeHtml(schedulerStateLabel(node.state))}</span></td></tr>`).join('');
}

function renderSchedulerNodeTable(nodes, label = 'GPU 节点容量') {
  if (!(nodes || []).length) return '<div class="module-empty">当前条件下没有节点</div>';
  return `<div class="table-wrap" role="region" aria-label="${escapeHtml(label)}，可横向滚动"><table class="scheduler-node-table"><colgroup><col class="node-name-column"><col class="node-partition-column"><col class="node-gpu-column"><col class="node-free-column"><col class="node-memory-column"><col class="node-tasks-column"><col class="node-state-column"></colgroup><caption class="sr-only">${escapeHtml(label)}</caption><thead><tr><th scope="col">节点</th><th scope="col">分区</th><th scope="col">卡型 / 单卡显存</th><th scope="col">空闲 GPU</th><th scope="col">调度显存占用</th><th scope="col">活动任务</th><th scope="col">节点状态</th></tr></thead><tbody>${renderSchedulerNodeRows(nodes)}</tbody></table></div>`;
}

function clusterNodePageState(serverId) {
  if (!clusterNodePages.has(serverId)) {
    clusterNodePages.set(serverId, {
      status: 'idle', nodes: [], offset: 0, total: 0, query: '', gpuType: '', partition: '',
      onlyAvailable: false, issuesOnly: false, error: '', revision: null,
    });
  }
  return clusterNodePages.get(serverId);
}

function largeClusterGroups(server) {
  return (Array.isArray(server.node_groups) ? server.node_groups : []).slice(0, 100);
}

function renderLargeClusterSummary(server) {
  const groups = largeClusterGroups(server);
  if (!groups.length) return '<div class="large-cluster-empty">容量汇总将在刷新后显示</div>';
  return `<div class="cluster-group-grid">${groups.map(group => {
    const totalNodes = group.total_nodes ?? group.node_count;
    const freeGpus = group.free_gpus ?? group.available_gpus;
    const totalGpus = group.total_gpus;
    const issueNodes = group.issue_nodes ?? group.unavailable_nodes ?? 0;
    return `<article class="cluster-group"><div><strong>${escapeHtml(formatGpuType(group.gpu_type))}</strong><span>${escapeHtml(group.partition || '未分区')}</span></div><dl><div><dt>空闲 GPU</dt><dd>${number(freeGpus)} / ${number(totalGpus)}</dd></div><div><dt>节点</dt><dd>${number(totalNodes)}</dd></div>${Number(issueNodes) ? `<div class="issue"><dt>异常</dt><dd>${number(issueNodes)}</dd></div>` : ''}</dl></article>`;
  }).join('')}</div>`;
}

function renderLargeClusterNodes(server) {
  const state = clusterNodePageState(server.server_id);
  const groups = largeClusterGroups(server);
  const gpuTypes = [...new Set(groups.map(group => String(group.gpu_type || '').trim()).filter(Boolean))].slice(0, 50);
  const partitions = [...new Set(groups.map(group => String(group.partition || '').trim()).filter(Boolean))].slice(0, 50);
  const controls = `<form class="cluster-node-filters" data-server-id="${escapeHtml(server.server_id)}"><label><span>搜索节点</span><input name="query" type="search" value="${escapeHtml(state.query)}" placeholder="节点名"></label><label><span>卡型</span><select name="gpuType"><option value="">全部卡型</option>${gpuTypes.map(value => `<option value="${escapeHtml(value)}"${state.gpuType === value ? ' selected' : ''}>${escapeHtml(formatGpuType(value))}</option>`).join('')}</select></label><label><span>分区</span><select name="partition"><option value="">全部分区</option>${partitions.map(value => `<option value="${escapeHtml(value)}"${state.partition === value ? ' selected' : ''}>${escapeHtml(value)}</option>`).join('')}</select></label><label class="check-label"><input name="onlyAvailable" type="checkbox"${state.onlyAvailable ? ' checked' : ''}><span>仅空闲</span></label><label class="check-label"><input name="issuesOnly" type="checkbox"${state.issuesOnly ? ' checked' : ''}><span>仅异常</span></label><button class="button apply-cluster-node-filters" type="submit">筛选</button></form>`;
  let body = '<div class="module-empty">展开后按页读取节点</div>';
  if (state.status === 'loading') body = '<div class="module-empty">正在读取节点…</div>';
  else if (state.status === 'error') body = `<div class="directory-error"><span>${escapeHtml(state.error)}</span><button class="button load-cluster-nodes" type="button" data-server-id="${escapeHtml(server.server_id)}">重试</button></div>`;
  else if (state.status === 'loaded') body = renderSchedulerNodeTable(state.nodes, '筛选后的 GPU 节点');
  const start = state.total ? state.offset + 1 : 0;
  const end = Math.min(state.total, state.offset + state.nodes.length);
  const pager = state.status === 'loaded' ? `<div class="cluster-node-pager"><span>${number(start)}–${number(end)} / ${number(state.total)}</span><button class="button cluster-node-page" type="button" data-server-id="${escapeHtml(server.server_id)}" data-page-offset="${Math.max(0, state.offset - CLUSTER_NODE_PAGE_SIZE)}"${state.offset <= 0 ? ' disabled' : ''}>上一页</button><button class="button cluster-node-page" type="button" data-server-id="${escapeHtml(server.server_id)}" data-page-offset="${state.offset + CLUSTER_NODE_PAGE_SIZE}"${state.offset + state.nodes.length >= state.total ? ' disabled' : ''}>下一页</button></div>` : '';
  return `${controls}${body}${pager}`;
}

function renderLargeClusterModule(server) {
  const totalNodes = server.cluster_summary?.total_nodes ?? server.large_cluster?.total_nodes ?? server.node_count;
  return `<section class="large-cluster-overview"><header><div><h4>集群容量</h4><p>${number(totalNodes)} 个节点 · 先按卡型和分区汇总</p></div><span class="large-cluster-badge">大集群模式</span></header>${renderLargeClusterSummary(server)}</section><details class="cluster-module large-cluster-nodes" data-module="cluster-nodes" data-server-id="${escapeHtml(server.server_id)}"${moduleOpen(server.server_id, 'cluster-nodes', false)}><summary><span class="cluster-heading"><h4>节点明细</h4><small>每页最多 ${CLUSTER_NODE_PAGE_SIZE} 个节点</small></span><span class="directory-summary">按需读取</span><span class="details-chevron">${icon('chevron')}</span></summary><div class="cluster-content">${renderLargeClusterNodes(server)}</div></details>`;
}

function repaintClusterNodes(serverId) {
  const server = currentSnapshot?.servers?.find(item => item.server_id === serverId);
  const card = document.getElementById(serverCardAnchor(serverId));
  const content = card?.querySelector('.large-cluster-nodes > .cluster-content');
  if (!server || !content) return false;
  content.innerHTML = renderLargeClusterNodes(server);
  return true;
}

async function loadClusterNodes(serverId, offset = 0, overrides = {}) {
  if (!api?.get_cluster_nodes) return;
  const requestGeneration = (clusterNodeRequestGenerations.get(serverId) || 0) + 1;
  clusterNodeRequestGenerations.set(serverId, requestGeneration);
  const previous = clusterNodePageState(serverId);
  const currentDataRevision = currentSnapshot?.servers?.find(server => server.server_id === serverId)?.connection?.data_revision ?? null;
  const next = {
    ...previous,
    ...overrides,
    offset: Math.max(0, Number(offset) || 0),
    status: 'loading',
    error: '',
    revision: currentDataRevision ?? overrides.revision ?? previous.revision,
  };
  const requestRevision = next.revision;
  clusterNodePages.set(serverId, next);
  repaintClusterNodes(serverId);
  try {
    const result = await api.get_cluster_nodes(
      serverId, next.offset, CLUSTER_NODE_PAGE_SIZE, next.query, next.gpuType,
      next.partition, next.onlyAvailable, next.issuesOnly,
      requestRevision,
    );
    if (clusterNodeRequestGenerations.get(serverId) !== requestGeneration) return;
    if (result?.code === 'snapshot_changed' && next.offset > 0) {
      showToast('节点状态已更新，已返回第一页');
      await loadClusterNodes(serverId, 0, {...next, revision: null});
      return;
    }
    if (!result?.ok) throw new Error(result?.error || '无法读取节点');
    const latestDataRevision = currentSnapshot?.servers?.find(server => server.server_id === serverId)?.connection?.data_revision ?? null;
    const responseRevision = result.revision ?? requestRevision;
    const revisionChanged = (requestRevision != null && responseRevision != null && String(requestRevision) !== String(responseRevision))
      || (latestDataRevision != null && responseRevision != null && String(latestDataRevision) !== String(responseRevision));
    if (revisionChanged) {
      clusterNodePages.delete(serverId);
      clusterNodeRequestGenerations.set(serverId, requestGeneration + 1);
      showToast('节点状态已更新，请重新读取');
      repaintClusterNodes(serverId);
      return;
    }
    const nodes = (result.nodes || result.items || []).slice(0, CLUSTER_NODE_PAGE_SIZE);
    clusterNodePages.set(serverId, {
      ...next,
      status: 'loaded',
      nodes,
      total: Number(result.total ?? nodes.length),
      offset: Number(result.cursor ?? result.offset ?? next.offset),
      revision: responseRevision,
    });
  } catch (error) {
    if (clusterNodeRequestGenerations.get(serverId) !== requestGeneration) return;
    clusterNodePages.set(serverId, {...next, status: 'error', nodes: [], error: error.message || String(error)});
  }
  if (clusterNodeRequestGenerations.get(serverId) !== requestGeneration) return;
  repaintClusterNodes(serverId);
}

function renderSchedulerTable(server) {
  const largeCluster = Boolean(server.large_cluster) || Array.isArray(server.node_groups);
  const capacity = largeCluster
    ? renderLargeClusterModule(server)
    : renderSchedulerNodeTable(server.nodes || []);
  return capacity + renderClusterModule(server);
}

function accountForServer(server) {
  const account = server.account || {};
  return {
    username: String(account.username || server.processes?.current_user || server.tasks?.current_user || '').trim(),
    home_directory: String(account.home_directory || '').trim(),
  };
}

function renderAccountOverview(server) {
  const account = accountForServer(server);
  if (!account.username && !account.home_directory) return '';
  const user = account.username
    ? `<span class="account-fact">${icon('user')}<span><small>登录账号</small><strong>${escapeHtml(account.username)}</strong></span></span>`
    : '';
  const home = account.home_directory
    ? `<span class="account-fact account-home">${icon('folder')}<span><small>主目录</small><strong class="mono">${escapeHtml(account.home_directory)}</strong></span></span>`
    : `<span class="account-fact account-home missing">${icon('folder')}<span><small>主目录</small><strong>未返回</strong></span></span>`;
  return `<div class="account-overview" aria-label="登录账号和主目录">${user}${home}</div>`;
}

function formatFileSize(value) {
  if (value == null) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let size = Math.max(0, Number(value));
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toLocaleString('zh-CN', {maximumFractionDigits: unit ? 1 : 0})} ${units[unit]}`;
}

function directoryNodeOpen(serverId, path) {
  return openDirectoryNodes.has(`${serverId}:${path}`) ? ' open' : '';
}

function configuredDefaultDirectory(serverId) {
  return String(profileServerFor(serverId)?.default_work_directory || '');
}

function directoryStateFromAccount(account, cache = null) {
  const tree = account?.directory_tree || {entries: []};
  const root = String(tree.root || account?.home_directory || '');
  return {
    status: 'loaded',
    account,
    root,
    rootSource: String(tree.root_source || 'home'),
    entries: [...(tree.entries || [])],
    truncated: Boolean(tree.truncated),
    loadedRoots: new Set(root ? [root] : []),
    loadedRootOrder: root ? [root] : [],
    cache: cache && typeof cache === 'object' ? {...cache} : null,
    refreshing: false,
    loadingPath: '',
    pathError: null,
  };
}

function mergeDirectoryAccount(serverId, existing, account, cache = null) {
  const tree = account?.directory_tree || {entries: []};
  const target = String(tree.root || '');
  if (!existing || target === existing.root) return directoryStateFromAccount(account, cache);
  const prefix = target.endsWith('/') ? target : `${target}/`;
  const rows = new Map();
  existing.entries.forEach(entry => {
    if (!String(entry.absolute_path || '').startsWith(prefix)) rows.set(entry.absolute_path, {...entry});
  });
  if (rows.has(target)) rows.get(target).has_more = false;
  (tree.entries || []).forEach(entry => rows.set(entry.absolute_path, entry));
  const loadedRoots = new Set(existing.loadedRoots || []);
  loadedRoots.add(target);
  const loadedRootOrder = [...(existing.loadedRootOrder || [])].filter(root => root !== target);
  loadedRootOrder.push(target);
  while (loadedRootOrder.length > MAX_DIRECTORY_ROOTS_PER_SERVER) {
    const evictedRoot = loadedRootOrder.shift();
    if (!evictedRoot || evictedRoot === existing.root) continue;
    loadedRoots.delete(evictedRoot);
    directoryFreshnessDeadlines.delete(directoryRequestKey(serverId, evictedRoot));
    [...rows.entries()].forEach(([absolutePath, entry]) => {
      const parent = String(entry.parent_absolute_path || '');
      if (parent === evictedRoot || parent.startsWith(`${evictedRoot}/`)) rows.delete(absolutePath);
    });
  }
  return {
    ...existing,
    status: 'loaded',
    account: {...existing.account, username: account?.username, home_directory: account?.home_directory},
    entries: [...rows.values()],
    truncated: existing.truncated || Boolean(tree.truncated),
    loadedRoots,
    loadedRootOrder,
    cache: cache && typeof cache === 'object' ? {...cache} : existing.cache,
    refreshing: false,
    loadingPath: '',
    pathError: null,
  };
}

function renderDirectoryEntries(serverId, state) {
  const entries = state.entries || [];
  if (!entries.length) return '<div class="module-empty">当前目录没有可显示的条目</div>';
  const byParent = new Map();
  entries.forEach(entry => {
    const parent = String(entry.parent_absolute_path || state.root || '');
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(entry);
  });
  const sortEntries = items => [...items].sort((left, right) => {
    const leftDirectory = left.kind === 'directory' ? 0 : 1;
    const rightDirectory = right.kind === 'directory' ? 0 : 1;
    return leftDirectory - rightDirectory || String(left.name).localeCompare(String(right.name), 'zh-CN');
  });
  const renderLevel = parent => sortEntries(byParent.get(parent) || []).map(entry => {
    const absolutePath = String(entry.absolute_path || `${state.root}/${entry.path}`.replace(/\/+/g, '/'));
    const safePath = escapeHtml(absolutePath);
    const modified = entry.modified_at ? formatTaskTimestamp(entry.modified_at) : '—';
    if (entry.kind === 'directory') {
      const expanded = openDirectoryNodes.has(`${serverId}:${absolutePath}`);
      const children = expanded ? renderLevel(absolutePath) : '';
      const childCount = (byParent.get(absolutePath) || []).length;
      const fixed = configuredDefaultDirectory(serverId) === absolutePath;
      const canLoadMore = Boolean(entry.has_more) && !state.loadedRoots?.has(absolutePath);
      const loading = state.loadingPath === absolutePath;
      const error = state.pathError?.path === absolutePath
        ? `<div class="directory-inline-error">${escapeHtml(state.pathError.message)}</div>`
        : '';
      const actions = `<div class="directory-node-actions"><button class="button compact-button pin-directory" type="button" data-server-id="${escapeHtml(serverId)}" data-directory-path="${safePath}"${fixed ? ' disabled' : ''}>${fixed ? '当前默认' : '固定为默认目录'}</button>${canLoadMore || loading ? `<button class="button compact-button load-directory-more" type="button" data-server-id="${escapeHtml(serverId)}" data-directory-path="${safePath}"${loading ? ' disabled' : ''}>${loading ? '正在读取' : '展开更多'}</button>` : ''}</div>`;
      return `<details class="directory-node" role="treeitem" data-directory-path="${safePath}" data-server-id="${escapeHtml(serverId)}"${directoryNodeOpen(serverId, absolutePath)}><summary title="修改时间：${escapeHtml(modified)}"><span class="directory-name">${icon('folder')}<strong>${escapeHtml(entry.name)}</strong>${contextCopyButton(absolutePath, '文件夹路径')}</span><span class="directory-meta">${number(childCount)} 项 ${icon('chevron', 'directory-chevron')}</span></summary><div class="directory-children" role="group">${actions}${error}${children || (canLoadMore ? '<div class="directory-empty">继续展开以读取下一级</div>' : '<div class="directory-empty">当前文件夹为空</div>')}</div></details>`;
    }
    const iconName = entry.kind === 'symlink' ? 'link' : 'file';
    const kindLabel = entry.kind === 'symlink' ? '链接 · ' : entry.kind === 'other' ? '其他 · ' : '';
    return `<div class="directory-file" role="treeitem"><span class="directory-name">${icon(iconName)}<strong>${escapeHtml(entry.name)}</strong>${contextCopyButton(absolutePath, '文件路径')}</span><span class="directory-meta">${escapeHtml(kindLabel + formatFileSize(entry.size_bytes))} · ${escapeHtml(modified)}</span></div>`;
  }).join('');
  return `<div class="directory-tree" role="tree" aria-label="代码工作目录结构">${renderLevel(state.root)}</div>`;
}

function renderDirectoryRootBar(serverId, state) {
  const pinned = configuredDefaultDirectory(serverId);
  const isPinnedRoot = Boolean(pinned) && pinned === state.root;
  const sourceLabel = isPinnedRoot || state.rootSource === 'pinned'
    ? '已固定'
    : state.rootSource === 'auto' ? '自动定位' : '账号主目录';
  const cacheLabel = state.cache?.state === 'hit'
    ? '已复用缓存'
    : state.cache?.state === 'validated'
      ? '缓存已校验'
      : state.cache?.state === 'stale_hit' ? '缓存待深校验' : '已读取';
  return `<div class="directory-rootbar"><div class="directory-root"><span class="directory-source">${escapeHtml(sourceLabel)}</span><strong class="mono" title="${escapeHtml(state.root)}">${escapeHtml(state.root)}</strong>${contextCopyButton(state.root, '工作目录路径')}<small>${escapeHtml(cacheLabel)}</small></div><div class="directory-root-actions"><button class="button compact-button refresh-directory" type="button" data-server-id="${escapeHtml(serverId)}" data-directory-path="${escapeHtml(state.root)}"${state.refreshing ? ' disabled' : ''}>${state.refreshing ? '正在刷新' : '刷新目录'}</button><button class="button compact-button pin-directory" type="button" data-server-id="${escapeHtml(serverId)}" data-directory-path="${escapeHtml(state.root)}"${isPinnedRoot ? ' disabled' : ''}>${isPinnedRoot ? '当前默认' : '固定当前目录'}</button><button class="button compact-button expand-loaded-directories" type="button" data-server-id="${escapeHtml(serverId)}">展开已加载</button>${pinned ? `<button class="button compact-button reset-directory-default" type="button" data-server-id="${escapeHtml(serverId)}">恢复自动定位</button>` : ''}</div></div>`;
}

function renderDirectoryModule(server) {
  const account = accountForServer(server);
  const state = directoryTrees.get(server.server_id);
  if (!account.home_directory && state?.status !== 'loaded') return '';
  const stale = state?.status === 'loaded' && server.connection?.state !== 'online';
  let summary = '展开后读取';
  let body = '<div class="module-empty directory-placeholder">展开时读取文件夹名称、类型、大小和修改时间</div>';
  if (state?.status === 'loading') {
    summary = '正在读取';
    body = '<div class="module-empty directory-loading">正在读取文件夹结构…</div>';
  } else if (state?.status === 'error') {
    summary = '读取失败';
    body = `<div class="directory-error"><span>${escapeHtml(state.error)}</span><button class="button retry-directory" type="button" data-server-id="${escapeHtml(server.server_id)}">重试</button></div>`;
  } else if (state?.status === 'loaded') {
    const tree = state.account?.directory_tree || {supported: true};
    summary = `${state.refreshing ? '正在刷新 · ' : ''}${stale ? '旧目录快照 · ' : ''}${number((state.entries || []).length)} 项${state.truncated ? ' · 已截断' : ''}`;
    const staleBanner = stale ? '<div class="directory-stale-banner">旧目录快照 · 当前服务器未监控就绪，仅供参考</div>' : '';
    body = staleBanner + (tree.supported
      ? `${renderDirectoryRootBar(server.server_id, state)}${state.pathError && !state.pathError.path ? `<div class="directory-inline-error">${escapeHtml(state.pathError.message)}</div>` : ''}${renderDirectoryEntries(server.server_id, state)}`
      : `<div class="module-empty directory-unavailable"><span>${escapeHtml(tree.warning || '当前目录不可读取')}</span>${configuredDefaultDirectory(server.server_id) ? `<button class="button compact-button reset-directory-default" type="button" data-server-id="${escapeHtml(server.server_id)}">恢复自动定位</button>` : ''}</div>`);
  }
  const visibleRoot = state?.status === 'loaded'
    ? (!state.account?.directory_tree?.supported && configuredDefaultDirectory(server.server_id)
      ? configuredDefaultDirectory(server.server_id)
      : state.root)
    : account.home_directory || state?.root || '';
  return `<details class="cluster-module directory-module" data-module="account-directory" data-server-id="${escapeHtml(server.server_id)}"${moduleOpen(server.server_id, 'account-directory', false)}><summary><span class="cluster-heading"><h4>代码工作目录</h4><small class="mono">${escapeHtml(visibleRoot)}</small></span><span class="directory-summary">${escapeHtml(summary)}</span><span class="details-chevron">${icon('chevron')}</span></summary><div class="cluster-content directory-content">${body}</div></details>`;
}

function repaintDirectory(serverId) {
  const server = currentSnapshot?.servers?.find(item => item.server_id === serverId);
  const card = serverNavigationCardsById.get(serverId)
    || [...ui.list.querySelectorAll('.server-card')].find(item => item.dataset.serverId === serverId);
  if (!server || !card) return;
  const current = card.querySelector('.directory-module');
  const html = renderDirectoryModule(server);
  if (!html) {
    current?.remove();
    return;
  }
  if (current) current.outerHTML = html;
  else card.querySelector('.server-surface')?.insertAdjacentHTML('beforeend', html);
  uiRenderMetrics.directoryRepaints += 1;
  applyDashboardDisclosureMode();
}

function directoryRequestKey(serverId, rootPath = null) {
  return `${serverId}\u0000${String(rootPath || '')}`;
}

function rememberDirectoryFreshness(serverId, rootPath, cache) {
  const seconds = Number(cache?.revalidate_after_seconds);
  const key = directoryRequestKey(serverId, rootPath);
  if (Number.isFinite(seconds) && seconds >= 0) {
    directoryFreshnessDeadlines.set(key, Date.now() + Math.max(100, seconds * 1000));
  } else {
    directoryFreshnessDeadlines.delete(key);
  }
  scheduleDirectoryFreshnessValidation();
}

function deferDirectoryFreshness(serverId, rootPath) {
  directoryFreshnessDeadlines.set(
    directoryRequestKey(serverId, rootPath),
    Date.now() + DIRECTORY_FRESHNESS_ERROR_RETRY_MS,
  );
  scheduleDirectoryFreshnessValidation();
}

function clearDirectoryFreshness(serverId = null) {
  if (serverId == null) directoryFreshnessDeadlines.clear();
  else {
    const prefix = `${serverId}\u0000`;
    [...directoryFreshnessDeadlines.keys()].forEach(key => {
      if (key.startsWith(prefix)) directoryFreshnessDeadlines.delete(key);
    });
  }
  scheduleDirectoryFreshnessValidation();
}

function directoryValidationVisible(serverId, rootPath) {
  if (document.hidden) return false;
  const server = currentSnapshot?.servers?.find(item => item.server_id === serverId);
  if (!server || server.connection?.state !== 'online') return false;
  const card = serverNavigationCardsById.get(serverId)
    || [...ui.list.querySelectorAll('.server-card')].find(item => item.dataset.serverId === serverId);
  const module = card?.querySelector('.directory-module');
  if (!module?.open) return false;
  return !rootPath || openDirectoryNodes.has(`${serverId}:${rootPath}`);
}

function scheduleDirectoryFreshnessValidation() {
  window.clearTimeout(directoryFreshnessTimer);
  directoryFreshnessTimer = null;
  if (!directoryFreshnessDeadlines.size) return;
  let earliest = Infinity;
  directoryFreshnessDeadlines.forEach((deadline, key) => {
    const [serverId, rootPath = ''] = key.split('\u0000', 2);
    if (directoryValidationVisible(serverId, rootPath)) earliest = Math.min(earliest, deadline);
  });
  if (!Number.isFinite(earliest)) return;
  const delay = Math.max(100, Math.min(2_147_000_000, earliest - Date.now()));
  directoryFreshnessTimer = window.setTimeout(async () => {
    directoryFreshnessTimer = null;
    const now = Date.now();
    const due = [...directoryFreshnessDeadlines.entries()]
      .filter(([key, deadline]) => {
        const [serverId, rootPath = ''] = key.split('\u0000', 2);
        return deadline <= now && directoryValidationVisible(serverId, rootPath);
      })
      .sort((left, right) => left[1] - right[1]);
    if (!due.length) {
      scheduleDirectoryFreshnessValidation();
      return;
    }
    const [key] = due[0];
    const [serverId, rootPath = ''] = key.split('\u0000', 2);
    await loadDirectoryTree(serverId, false, rootPath || null);
    scheduleDirectoryFreshnessValidation();
  }, delay);
}

function invalidateDirectoryRequests(serverId = null) {
  if (serverId == null) {
    directoryRequestTokens.clear();
    clearDirectoryFreshness();
    return;
  }
  const prefix = `${serverId}\u0000`;
  [...directoryRequestTokens.keys()].forEach(key => {
    if (key.startsWith(prefix)) directoryRequestTokens.delete(key);
  });
  clearDirectoryFreshness(serverId);
}

async function loadDirectoryTree(serverId, force = false, rootPath = null) {
  const existing = directoryTrees.get(serverId);
  const requestKey = directoryRequestKey(serverId, rootPath);
  if (!rootPath && !force && existing?.status === 'loading') return;
  if (
    !force
    && existing?.status === 'loaded'
    && Number(directoryFreshnessDeadlines.get(requestKey) || 0) > Date.now()
  ) return;
  if (rootPath && existing?.loadingPath === rootPath) return;
  if (!rootPath && force) invalidateDirectoryRequests(serverId);
  const requestGeneration = ++directoryRequestSequence;
  directoryRequestTokens.set(requestKey, requestGeneration);
  directoryTrees.set(serverId, existing?.status === 'loaded'
    ? {
        ...existing,
        refreshing: Boolean(force),
        loadingPath: rootPath || '',
        pathError: null,
      }
    : {status: 'loading', refreshing: Boolean(force)});
  repaintDirectory(serverId);
  try {
    const result = await api.inspect_account_directory(serverId, rootPath, force);
    if (directoryRequestTokens.get(requestKey) !== requestGeneration) return;
    const latest = directoryTrees.get(serverId);
    if (!result.ok) {
      deferDirectoryFreshness(serverId, rootPath);
      if (latest?.status === 'loaded') {
        directoryTrees.set(serverId, {
          ...latest,
          refreshing: false,
          loadingPath: '',
          pathError: {path: rootPath || '', message: result.error || '无法读取这个目录'},
        });
      } else {
        directoryTrees.set(serverId, {status: 'error', error: result.error || '无法读取文件夹结构'});
      }
    } else {
      if (!rootPath && !result.unchanged) clearDirectoryFreshness(serverId);
      rememberDirectoryFreshness(serverId, rootPath, result.cache);
      if (result.unchanged && latest?.status === 'loaded') {
        directoryTrees.set(serverId, {
          ...latest,
          cache: result.cache && typeof result.cache === 'object' ? {...result.cache} : latest.cache,
          refreshing: false,
          loadingPath: '',
          pathError: null,
        });
      } else if (result.unchanged) {
        throw new Error('目录缓存状态已失效，请重新展开');
      } else if (rootPath && !result.account?.directory_tree?.supported) {
        throw new Error(result.account?.directory_tree?.warning || '无法读取这个目录');
      } else {
        directoryTrees.set(serverId, rootPath
          ? mergeDirectoryAccount(serverId, latest, result.account, result.cache)
          : directoryStateFromAccount(result.account, result.cache));
      }
    }
  } catch (error) {
    if (directoryRequestTokens.get(requestKey) !== requestGeneration) return;
    deferDirectoryFreshness(serverId, rootPath);
    const latest = directoryTrees.get(serverId);
    if (latest?.status === 'loaded') {
      directoryTrees.set(serverId, {
        ...latest,
        refreshing: false,
        loadingPath: '',
        pathError: {path: rootPath || '', message: error.message || String(error)},
      });
    } else {
      directoryTrees.set(serverId, {status: 'error', error: error.message || String(error)});
    }
  } finally {
    if (directoryRequestTokens.get(requestKey) === requestGeneration) directoryRequestTokens.delete(requestKey);
  }
  repaintDirectory(serverId);
}

function expandLoadedDirectories(serverId) {
  const state = directoryTrees.get(serverId);
  (state?.entries || []).filter(entry => entry.kind === 'directory').forEach(entry => {
    openDirectoryNodes.add(`${serverId}:${entry.absolute_path}`);
  });
  repaintDirectory(serverId);
}

async function pinDefaultDirectory(serverId, rootPath) {
  try {
    const result = await api.set_default_directory(serverId, rootPath);
    if (!result.ok) throw new Error(result.error || '无法保存默认目录');
    invalidateDirectoryRequests(serverId);
    acceptProfile(result.profile);
    if (result.account) {
      result.account.directory_tree.root_source = 'pinned';
      directoryTrees.set(serverId, directoryStateFromAccount(result.account));
    }
    showToast('默认展开目录已保存');
    repaintDirectory(serverId);
  } catch (error) {
    showToast(error.message || String(error));
  }
}

async function resetDefaultDirectory(serverId) {
  try {
    const result = await api.set_default_directory(serverId, '');
    if (!result.ok) throw new Error(result.error || '无法恢复自动定位');
    invalidateDirectoryRequests(serverId);
    acceptProfile(result.profile);
    directoryTrees.delete(serverId);
    openDirectoryNodes.forEach(key => {
      if (key.startsWith(`${serverId}:`)) openDirectoryNodes.delete(key);
    });
    await loadDirectoryTree(serverId, true);
    showToast('已恢复自动定位');
  } catch (error) {
    showToast(error.message || String(error));
  }
}

function renderError(server) {
  const connection = server.connection;
  const disabled = connection.state === 'disabled';
  const error = connection.error || (disabled
    ? {message: '这台服务器已暂停监控', code: 'disabled', retryable: false}
    : {message: '尚未取得服务器数据', code: connection.state, retryable: true});
  const retry = formatRetry(connection.retry_at);
  const cached = connection.data_origin === 'cache';
  const securityBlocked = error.code === 'host_key_changed';
  const revalidate = disabled || securityBlocked ? '' : `<button class="button primary retry-server" type="button" data-server-id="${escapeHtml(server.server_id)}">重新验证</button>`;
  return `<div class="error-panel"><div class="error-symbol" aria-hidden="true">${icon('alert')}</div><div><div class="error-title">${escapeHtml(error.message)}</div><div class="error-copy">错误代码：${escapeHtml(error.code)}${cached ? ` · 最后成功：${escapeHtml(formatTime(connection.last_success_at))}` : ''}${retry ? ` · ${escapeHtml(retry)}` : ''}${cached ? '。旧快照仅供参考，不计入顶部实时汇总。' : '。当前没有可显示的 GPU 快照。'}</div></div><div class="error-actions">${revalidate}<button class="button open-settings" type="button">连接设置</button>${api?.get_redacted_diagnostics ? `<button class="button copy-server-diagnostics" type="button" data-server-id="${escapeHtml(server.server_id)}">复制诊断</button>` : ''}${api?.open_logs_directory ? '<button class="button open-logs" type="button">打开日志</button>' : ''}</div></div>`;
}

function renderConfiguring(server) {
  const backend = server.backend === 'slurm_ssh' ? 'Slurm 调度器' : 'GPU 采集器';
  return `<div class="configuring-panel" role="status"><div class="configuring-symbol" aria-hidden="true">${icon('clock')}</div><div><div class="configuring-title">正在配置并验证服务器</div><div class="configuring-copy">正在连接 SSH、检查${escapeHtml(backend)}并读取第一份 GPU 数据；完成前不会显示为错误。</div></div></div>`;
}

function serverGlyph(backend) {
  if (backend === 'slurm_ssh') {
    return '<svg viewBox="0 0 30 30" focusable="false"><rect x="4" y="4" width="8" height="8"></rect><rect x="18" y="4" width="8" height="8"></rect><rect x="4" y="18" width="8" height="8"></rect><rect x="18" y="18" width="8" height="8"></rect><path d="M12 8h6M8 12v6M22 12v6M12 22h6"></path></svg>';
  }
  return '<svg viewBox="0 0 30 30" focusable="false"><rect x="5" y="4" width="20" height="22" rx="2"></rect><path d="M9 9h12M9 15h12M9 21h7"></path><circle cx="21" cy="21" r="1.5"></circle></svg>';
}

function serverCardAnchor(serverId) {
  return `server-card-${String(serverId || '').replace(/[^A-Za-z0-9._-]/g, '-')}`;
}

function serverIsEnabled(serverId) {
  return profileServerFor(serverId)?.enabled !== false;
}

function renderServerQuickActions(server) {
  const serverId = server.server_id;
  const favorite = favoriteServerIds.has(serverId);
  const enabled = serverIsEnabled(serverId);
  const copySsh = api?.get_ssh_command ? `<button class="button compact-button copy-server-ssh" type="button" data-server-id="${escapeHtml(serverId)}" aria-label="复制 SSH 命令" title="复制 SSH 命令">${icon('copy')}<span>复制 SSH</span></button>` : '';
  const openTerminal = api?.open_terminal
    ? `<button class="button compact-button open-terminal" type="button" data-server-id="${escapeHtml(serverId)}" aria-label="打开服务器终端" title="打开终端">${icon('terminal')}<span>打开终端</span></button>`
    : '';
  return `<div class="server-quick-actions"><button class="button compact-button favorite-server${favorite ? ' active' : ''}" type="button" data-server-id="${escapeHtml(serverId)}" aria-pressed="${favorite}" aria-label="${favorite ? '取消收藏服务器' : '收藏服务器'}" title="${favorite ? '取消收藏' : '收藏服务器'}">${icon('star')}<span>${favorite ? '已收藏' : '收藏'}</span></button>${copySsh}${openTerminal}<button class="button compact-button toggle-server-monitoring" type="button" data-server-id="${escapeHtml(serverId)}" aria-pressed="${enabled}" aria-label="${enabled ? '暂停监控这台服务器' : '恢复监控这台服务器'}" title="${enabled ? '暂停监控' : '恢复监控'}">${icon(enabled ? 'pause' : 'play')}<span>${enabled ? '暂停' : '恢复'}</span></button></div>`;
}

function applyServerNavigatorSide(side) {
  serverNavigatorSide = side === 'left' ? 'left' : 'right';
  ui.serverNavigator.dataset.side = serverNavigatorSide;
  const currentLabel = serverNavigatorSide === 'left' ? '左侧' : '右侧';
  const targetLabel = serverNavigatorSide === 'left' ? '右侧' : '左侧';
  ui.serverNavigatorDrag.setAttribute('aria-label', `服务器目录在${currentLabel}，拖动或按回车移到${targetLabel}`);
  ui.serverNavigatorDrag.title = `拖动到${targetLabel}`;
}

function syncProfileConvenienceState(profile) {
  const favoriteIds = profile?.favorite_server_ids || profile?.favorites || [];
  favoriteServerIds = new Set(Array.isArray(favoriteIds)
    ? favoriteIds
    : Object.keys(favoriteIds || {}).filter(serverId => favoriteIds[serverId]));
  (profile?.servers || []).forEach(server => {
    if (server.favorite === true) favoriteServerIds.add(server.id);
  });
  const recent = profile?.recent_server_ids || profile?.recent_servers || [];
  if (Array.isArray(recent) && recent.length) recentServerIds = recent.slice(0, 8);
  monitoringPaused = Boolean(profile?.monitoring_paused ?? profile?.monitoring?.paused);
}

function acceptProfile(candidate) {
  if (!candidate || typeof candidate !== 'object') return false;
  const currentRevision = Number(currentProfile?.profile_revision);
  const candidateRevision = Number(candidate.profile_revision);
  if (Number.isFinite(currentRevision) && Number.isFinite(candidateRevision)) {
    if (candidateRevision < currentRevision) return false;
    if (
      candidateRevision === currentRevision
      && currentProfile
      && JSON.stringify(candidate) !== JSON.stringify(currentProfile)
    ) return false;
  }
  currentProfile = candidate;
  window.VRAMRadarI18n?.setLanguage(currentProfile.ui_language || 'zh-CN');
  syncProfileConvenienceState(currentProfile);
  return true;
}

function renderTaskCompletionWatchList() {
  const watches = currentProfile?.task_completion_watches || [];
  ui.taskCompletionWatchList.hidden = watches.length === 0;
  ui.taskCompletionWatchList.innerHTML = watches.length
    ? `<div class="task-watch-heading"><span>单独关注 ${number(watches.length)} 项</span><button class="clear-task-watches button compact-button" type="button">全部移除</button></div>${watches.map(watch => `<div class="task-watch-item"><span><strong>${escapeHtml(watch.label)}</strong><small>${escapeHtml(watch.server_id)} · ${escapeHtml(watch.task_kind === 'slurm' ? `任务 ${watch.task_id}` : `PID ${watch.task_id}`)}${watch.owner ? ` · ${escapeHtml(watch.owner)}` : ''}</small></span><button class="remove-task-watch button compact-button" type="button" data-server-id="${escapeHtml(watch.server_id)}" data-task-key="${escapeHtml(watch.task_key)}" data-task-kind="${escapeHtml(watch.task_kind)}" data-task-id="${escapeHtml(watch.task_id)}" data-task-label="${escapeHtml(watch.label)}" data-task-owner="${escapeHtml(watch.owner || '')}" data-task-owner-scope="${escapeHtml(watch.owner_scope || 'unknown')}">移除</button></div>`).join('')}`
    : '';
}

function notificationKindLabel(kind) {
  if (kind === 'task_completed') return localizedText('任务完成');
  if (kind === 'favorite_gpu_available') return localizedText('收藏 GPU 可用');
  if (kind === 'resource_available') return localizedText('GPU 条件满足');
  return localizedText('应用通知');
}

function notificationTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat(activeLocale(), {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function notificationEventTitle(event) {
  if (event.kind === 'task_completed') return localizedText('任务已完成');
  if (event.kind === 'favorite_gpu_available') return localizedText('收藏 GPU 已可用');
  if (event.kind === 'resource_available') return localizedText('显存雷达：资源可用');
  return String(event.title || notificationKindLabel(event.kind));
}

function notificationEventMessage(event) {
  if (event.kind === 'task_completed' && event.label) {
    return window.VRAMRadarI18n?.language === 'en'
      ? `${event.label} finished.`
      : `${event.label} 已结束。`;
  }
  if (event.kind === 'favorite_gpu_available' && event.language && event.language !== window.VRAMRadarI18n?.language) {
    return localizedText('收藏服务器已有 GPU 可用。');
  }
  if (event.kind === 'resource_available') return localizedText('你设置的 GPU 条件已有匹配结果。');
  return String(event.message || '');
}

function renderNotificationCenter(snapshot) {
  const state = snapshot?.notifications || {events: [], read_sequence: 0};
  const readSequence = Number(state.read_sequence || 0);
  const events = [...(state.events || [])].reverse();
  ui.notificationList.innerHTML = events.length
    ? events.map(event => `<article class="notification-item${Number(event.sequence || 0) > readSequence ? ' unread' : ''}"><div class="notification-item-meta"><span>${escapeHtml(notificationKindLabel(event.kind))}</span><time>${escapeHtml(notificationTime(event.created_at))}</time></div><strong>${escapeHtml(notificationEventTitle(event))}</strong><p>${escapeHtml(notificationEventMessage(event))}</p></article>`).join('')
    : '<div class="notification-empty">暂无通知</div>';
  ui.markNotificationsRead.disabled = Number(state.unread_count || 0) <= 0;
}

function renderTaskAlertIndicator(snapshot) {
  const unread = Number(snapshot?.notifications?.unread_count || 0);
  ui.taskAlertIndicator.hidden = false;
  ui.taskAlertCount.hidden = unread <= 0;
  ui.taskAlertCount.textContent = unread > 99 ? '99+' : String(unread);
  ui.taskAlertIndicator.classList.toggle('has-unread', unread > 0);
  const english = window.VRAMRadarI18n?.language === 'en';
  ui.taskAlertIndicator.setAttribute(
    'aria-label',
    unread > 0
      ? (english ? `Open notification center, ${unread} unread` : `打开通知中心，${unread} 条未读`)
      : (english ? 'Open notification center' : '打开通知中心'),
  );
  renderNotificationCenter(snapshot);
}

function repaintFavoriteServer(serverId) {
  const favorite = favoriteServerIds.has(serverId);
  document.querySelectorAll('.favorite-server').forEach(button => {
    if (button.dataset.serverId !== serverId) return;
    button.classList.toggle('active', favorite);
    button.setAttribute('aria-pressed', String(favorite));
    button.setAttribute('aria-label', favorite ? '取消收藏服务器' : '收藏服务器');
    button.title = favorite ? '取消收藏' : '收藏服务器';
    const text = button.querySelector('span');
    if (text && button.closest('.server-quick-actions')) text.textContent = favorite ? '已收藏' : '收藏';
  });
  if (serverNavigatorFilter === 'favorites' && currentSnapshot) {
    renderServerNavigator(currentSnapshot.servers);
  }
}

async function setFavoriteServer(serverId) {
  if (!api?.set_favorite_server) return showToast('当前版本暂不支持收藏服务器');
  const next = !favoriteServerIds.has(serverId);
  try {
    const result = await api.set_favorite_server(serverId, next);
    if (!result?.ok) throw new Error(result?.error || '无法保存收藏');
    if (result.profile) {
      acceptProfile(result.profile);
    } else if (next) favoriteServerIds.add(serverId);
    else favoriteServerIds.delete(serverId);
    repaintFavoriteServer(serverId);
    showToast(next ? '已收藏服务器' : '已取消收藏');
  } catch (error) {
    showToast(error.message || String(error));
  }
}

async function setServerEnabled(serverId, enabled) {
  if (!api?.set_server_enabled) return showToast('请在设置中保存服务器启用状态');
  try {
    const result = await api.set_server_enabled(serverId, enabled);
    if (!result?.ok) throw new Error(result?.error || '无法更新服务器状态');
    if (result.profile) acceptProfile(result.profile);
    showToast(enabled ? '已恢复监控' : '已暂停这台服务器');
    await refresh(true, serverId);
  } catch (error) {
    showToast(error.message || String(error));
  }
}

async function openServerTerminal(serverId) {
  if (!api?.open_terminal) return;
  try {
    const result = await api.open_terminal(serverId);
    if (result?.ok === false) throw new Error(result.error || '无法打开终端');
  } catch (error) {
    showToast(error.message || String(error));
  }
}

async function copyRedactedDiagnostics(serverId = null) {
  if (!api?.copy_redacted_diagnostics && !api?.get_redacted_diagnostics) return showToast('当前版本暂不支持诊断导出');
  try {
    let result;
    if (api.copy_redacted_diagnostics) {
      result = serverId == null
        ? await api.copy_redacted_diagnostics()
        : await api.copy_redacted_diagnostics(serverId);
    } else {
      result = serverId == null
        ? await api.get_redacted_diagnostics()
        : await api.get_redacted_diagnostics(serverId);
    }
    if (result?.ok === false) throw new Error(result.error || '无法生成诊断');
    const text = typeof result === 'string'
      ? result
      : result.text || (result.diagnostics ? JSON.stringify(result.diagnostics, null, 2) : '');
    if (!text) throw new Error('没有可复制的诊断内容');
    if (result?.copied !== true) await writeClipboard(text);
    showToast('诊断已复制到剪贴板，可直接粘贴给维护者');
  } catch (error) {
    showToast(error.message || String(error));
  }
}

async function openLogsDirectory() {
  if (!api?.open_logs_directory) return showToast('当前版本暂不支持打开日志目录');
  try {
    const result = await api.open_logs_directory();
    if (result?.ok === false) throw new Error(result.error || '无法打开日志目录');
  } catch (error) {
    showToast(error.message || String(error));
  }
}

async function openSetupTerminal(platformName) {
  if (!api?.open_setup_terminal) return showToast('当前版本暂不支持一键打开命令窗口');
  try {
    const result = await api.open_setup_terminal(platformName);
    if (result?.ok === false) throw new Error(result.error || '无法打开命令窗口');
    showToast(result?.message || '命令窗口已打开');
  } catch (error) {
    showToast(error.message || String(error));
  }
}

function updateMonitoringControls(paused) {
  monitoringPaused = Boolean(paused);
  ui.monitoringToggle.hidden = !api?.set_monitoring_paused || !currentProfile?.servers?.length;
  ui.monitoringToggle.setAttribute('aria-pressed', String(monitoringPaused));
  ui.monitoringToggle.classList.toggle('paused', monitoringPaused);
  ui.monitoringToggle.innerHTML = `${icon(monitoringPaused ? 'play' : 'pause')}<span>${monitoringPaused ? '恢复监控' : '暂停监控'}</span>`;
  if (monitoringPaused) setRefreshClock('监控已暂停');
}

async function toggleMonitoringPaused() {
  if (!api?.set_monitoring_paused) return;
  const next = !monitoringPaused;
  ui.monitoringToggle.disabled = true;
  try {
    const result = await api.set_monitoring_paused(next);
    if (!result?.ok) throw new Error(result?.error || '无法更新监控状态');
    if (result.profile) acceptProfile(result.profile);
    updateMonitoringControls(result.monitoring?.paused ?? result.paused ?? next);
    showToast(next ? '监控已暂停' : '监控已恢复');
    if (!next) await refresh(true);
  } catch (error) {
    showToast(error.message || String(error));
  } finally {
    ui.monitoringToggle.disabled = false;
  }
}

async function persistServerNavigatorSide(side) {
  const previousSide = currentProfile?.navigator_side === 'left' ? 'left' : 'right';
  const nextSide = side === 'left' ? 'left' : 'right';
  applyServerNavigatorSide(nextSide);
  try {
    const result = await api.set_navigator_side(nextSide);
    if (!result?.ok) throw new Error(result?.error || '无法保存服务器目录位置');
    acceptProfile(result.profile);
    applyServerNavigatorSide(currentProfile.navigator_side);
    showToast(`服务器目录已移到${nextSide === 'left' ? '左侧' : '右侧'}`);
  } catch (error) {
    applyServerNavigatorSide(previousSide);
    showToast(error.message || String(error));
  }
}

function beginServerNavigatorDrag(event) {
  if (event.button !== 0 || event.isPrimary === false) return;
  serverNavigatorDragState = {
    pointerId: event.pointerId,
    startX: event.clientX,
    initialSide: serverNavigatorSide,
    moved: false,
  };
  ui.serverNavigator.classList.add('dragging');
  ui.serverNavigatorDrag.setPointerCapture?.(event.pointerId);
  event.preventDefault();
}

function moveServerNavigatorDrag(event) {
  if (!serverNavigatorDragState || event.pointerId !== serverNavigatorDragState.pointerId) return;
  if (Math.abs(event.clientX - serverNavigatorDragState.startX) >= 6) {
    serverNavigatorDragState.moved = true;
  }
  if (!serverNavigatorDragState.moved) return;
  applyServerNavigatorSide(event.clientX < window.innerWidth / 2 ? 'left' : 'right');
  event.preventDefault();
}

function finishServerNavigatorDrag(event, cancelled = false) {
  if (!serverNavigatorDragState || event.pointerId !== serverNavigatorDragState.pointerId) return;
  const {initialSide, moved} = serverNavigatorDragState;
  serverNavigatorDragState = null;
  ui.serverNavigator.classList.remove('dragging');
  if (ui.serverNavigatorDrag.hasPointerCapture?.(event.pointerId)) {
    ui.serverNavigatorDrag.releasePointerCapture(event.pointerId);
  }
  if (cancelled) {
    applyServerNavigatorSide(initialSide);
    return;
  }
  if (!moved) return;
  suppressServerNavigatorDragClick = true;
  window.setTimeout(() => { suppressServerNavigatorDragClick = false; }, 0);
  if (serverNavigatorSide !== initialSide) void persistServerNavigatorSide(serverNavigatorSide);
}

function serverNavigatorOwnActivity(server) {
  if (server.connection?.state !== 'online') return [];
  if (server.backend === 'slurm_ssh') {
    const currentUser = String(server.tasks?.current_user || '').trim();
    if (!currentUser) return [];
    return (server.tasks?.active || []).filter(task => String(task.user || '').trim() === currentUser);
  }
  if (!server.processes?.supported) return [];
  return (server.processes.active || []).filter(process => process.owner_scope === 'mine');
}

function serverNavigatorActivityCount(server) {
  return serverNavigatorOwnActivity(server).length;
}

function serverNavigatorOwnTaskSummary(server) {
  const ownActivity = serverNavigatorOwnActivity(server);
  if (!ownActivity.length) return '';
  if (server.backend === 'slurm_ssh') {
    const counts = ownActivity.reduce((result, task) => {
      const state = String(task.state || '').toUpperCase();
      result[state] = (result[state] || 0) + 1;
      return result;
    }, {});
    const running = (counts.RUNNING || 0) + (counts.COMPLETING || 0) + (counts.CONFIGURING || 0);
    const summary = [];
    if (running) summary.push(`${number(running)} 运行`);
    if (counts.PENDING) summary.push(`${number(counts.PENDING)} 排队`);
    if (counts.SUSPENDED) summary.push(`${number(counts.SUSPENDED)} 暂停`);
    return summary.join(' · ');
  }
  return `${number(ownActivity.length)} 个我的 GPU 进程`;
}

function serverNavigatorResourceSummary(server) {
  if (server.connection?.state !== 'online') return stateLabel(server.connection?.state || 'offline');
  const gpus = Number(server.total_gpus) || 0;
  const free = server.free_vram_gib;
  const freeLabel = free == null ? '可用显存未知' : `${number(free)} GiB 可用`;
  if (server.backend === 'slurm_ssh' && server.free_gpus != null) {
    return `${number(server.free_gpus)}/${number(gpus)} GPU 调度器报告空闲 · ${freeLabel}`;
  }
  return `${number(gpus)} GPU · ${freeLabel}`;
}

function serverNavigatorHasAvailableResource(server) {
  if (server.connection?.state !== 'online') return false;
  if (server.backend === 'slurm_ssh' && server.free_gpus != null) return Number(server.free_gpus) > 0;
  return Number(server.free_vram_gib) > 0;
}

function serverNavigatorSearchText(server) {
  const gpuTypes = [
    ...(server.gpus || []).map(gpu => gpu.gpu_type),
    ...(server.nodes || []).map(node => node.gpu_type),
    ...(server.node_groups || []).map(group => group.gpu_type),
  ];
  const partitions = [
    ...(server.nodes || []).map(node => node.partition),
    ...(server.node_groups || []).map(group => group.partition),
  ];
  return [server.display_name, server.server_id, stateLabel(server.connection?.state || 'offline'), ...gpuTypes, ...partitions]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase();
}

function serverMatchesNavigator(server) {
  const queryMatches = !serverNavigatorQuery || serverNavigatorSearchText(server).includes(serverNavigatorQuery);
  if (!queryMatches) return false;
  if (serverNavigatorFilter === 'available') return serverNavigatorHasAvailableResource(server);
  if (serverNavigatorFilter === 'tasks') return serverNavigatorActivityCount(server) > 0;
  if (serverNavigatorFilter === 'issues') return server.connection?.state !== 'online';
  if (serverNavigatorFilter === 'favorites') return favoriteServerIds.has(server.server_id);
  if (serverNavigatorFilter === 'recent') return recentServerIds.includes(server.server_id);
  return true;
}

function filteredServerEntries(servers) {
  return servers.map((server, index) => ({server, index})).filter(item => serverMatchesNavigator(item.server));
}

function visibleServerEntries(matches) {
  if (matches.length <= SERVER_NAVIGATOR_RENDER_LIMIT) return matches;
  const activeIndex = matches.findIndex(item => item.server.server_id === activeServerId);
  const preferredStart = activeIndex < 0 ? 0 : activeIndex - 10;
  const start = Math.max(0, Math.min(preferredStart, matches.length - SERVER_NAVIGATOR_RENDER_LIMIT));
  return matches.slice(start, start + SERVER_NAVIGATOR_RENDER_LIMIT);
}

function mainServerEntries(servers) {
  if (servers.length <= LARGE_SERVER_FLEET_THRESHOLD) {
    serverFleetPageOffset = 0;
    return servers.map((server, index) => ({server, index}));
  }
  const maximumOffset = Math.max(0, Math.floor(Math.max(0, servers.length - 1) / SERVER_FLEET_PAGE_SIZE) * SERVER_FLEET_PAGE_SIZE);
  serverFleetPageOffset = Math.min(serverFleetPageOffset, maximumOffset);
  return servers.slice(serverFleetPageOffset, serverFleetPageOffset + SERVER_FLEET_PAGE_SIZE)
    .map((server, offset) => ({server, index: serverFleetPageOffset + offset}));
}

function updateServerFleetPager(servers) {
  const largeFleet = servers.length > LARGE_SERVER_FLEET_THRESHOLD;
  ui.serverListPager.hidden = !largeFleet;
  if (!largeFleet) return;
  const start = servers.length ? serverFleetPageOffset + 1 : 0;
  const end = Math.min(servers.length, serverFleetPageOffset + SERVER_FLEET_PAGE_SIZE);
  ui.serverListPageStatus.textContent = `${number(start)}–${number(end)} / ${number(servers.length)} 台`;
  ui.serverListPreviousPage.disabled = serverFleetPageOffset <= 0;
  ui.serverListNextPage.disabled = serverFleetPageOffset + SERVER_FLEET_PAGE_SIZE >= servers.length;
}

function changeServerFleetPage(direction) {
  if (!currentSnapshot) return;
  const next = serverFleetPageOffset + direction * SERVER_FLEET_PAGE_SIZE;
  serverFleetPageOffset = Math.max(0, Math.min(next, Math.max(0, currentSnapshot.servers.length - 1)));
  serverFleetPageOffset = Math.floor(serverFleetPageOffset / SERVER_FLEET_PAGE_SIZE) * SERVER_FLEET_PAGE_SIZE;
  render(currentSnapshot);
  ui.list.scrollIntoView({behavior: 'auto', block: 'start'});
}

function renderServerNavigatorItem(server, index) {
  const state = server.connection?.state || 'offline';
  const anchor = serverCardAnchor(server.server_id);
  const position = String(index + 1).padStart(2, '0');
  const taskSummary = serverNavigatorOwnTaskSummary(server);
  const label = [server.display_name, stateLabel(state), serverNavigatorResourceSummary(server), taskSummary]
    .filter(Boolean)
    .join('，');
  const favorite = favoriteServerIds.has(server.server_id);
  return `<div class="server-navigator-entry" data-server-id="${escapeHtml(server.server_id)}"><button class="server-navigator-item ${escapeHtml(state)}" type="button" data-server-id="${escapeHtml(server.server_id)}" aria-controls="${escapeHtml(anchor)}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"><span class="server-navigator-marker"><span>${position}</span><i class="status-dot ${escapeHtml(state)}"></i></span><span class="server-navigator-copy"><strong>${escapeHtml(server.display_name)}</strong><span>${escapeHtml(serverNavigatorResourceSummary(server))}</span><small>${escapeHtml(taskSummary)}</small></span></button><button class="server-navigator-favorite favorite-server${favorite ? ' active' : ''}" type="button" data-server-id="${escapeHtml(server.server_id)}" aria-pressed="${favorite}" aria-label="${favorite ? '取消收藏' : '收藏'} ${escapeHtml(server.display_name)}" title="${favorite ? '取消收藏' : '收藏'}">${icon('star')}</button></div>`;
}

function setActiveServer(serverId) {
  if (!serverId) return;
  const nextItem = serverNavigatorItems.get(serverId);
  if (activeServerId === serverId && nextItem?.getAttribute('aria-current') === 'location') return;
  const previousItem = serverNavigatorItems.get(activeServerId);
  if (previousItem) {
    previousItem.classList.remove('active');
    previousItem.removeAttribute('aria-current');
  }
  activeServerId = serverId;
  if (nextItem) {
    nextItem.classList.add('active');
    nextItem.setAttribute('aria-current', 'location');
  }
  updateServerNavigatorPosition();
}

function visibleServerNavigatorIds() {
  return serverNavigatorVisibleIds;
}

function updateServerNavigatorPosition() {
  const position = serverNavigatorPositions.get(activeServerId);
  if (!serverNavigatorVisibleIds.length) {
    ui.serverNavigatorPosition.textContent = '0 / 0';
    ui.serverNavigatorPosition.setAttribute('aria-label', '当前筛选没有匹配的服务器');
  } else if (position) {
    ui.serverNavigatorPosition.textContent = `${position} / ${serverNavigatorVisibleIds.length}`;
    ui.serverNavigatorPosition.setAttribute('aria-label', `当前为筛选结果第 ${position} 台，共 ${serverNavigatorVisibleIds.length} 台`);
  } else {
    ui.serverNavigatorPosition.textContent = `未显示 · ${serverNavigatorVisibleIds.length}`;
    ui.serverNavigatorPosition.setAttribute('aria-label', `当前服务器不在筛选结果中，筛选结果共 ${serverNavigatorVisibleIds.length} 台`);
  }
  const disabled = serverNavigatorVisibleIds.length < 2;
  ui.previousServer.disabled = disabled;
  ui.nextServer.disabled = disabled;
}

function syncActiveServerFromScroll() {
  serverNavigationFrame = null;
  if (ui.serverNavigator.hidden) return;
  if (!serverNavigationCards.length) return;
  const focusLine = Math.min(window.innerHeight * .3, 260);
  const activeCard = serverNavigationCardsById.get(activeServerId);
  if (activeCard) {
    const activeRect = activeCard.getBoundingClientRect();
    if (activeRect.top <= focusLine && activeRect.bottom > focusLine) return;
  }
  let low = 0;
  let high = serverNavigationCards.length - 1;
  let precedingIndex = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (serverNavigationCards[middle].getBoundingClientRect().top <= focusLine) {
      precedingIndex = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  const candidateIndexes = precedingIndex < 0
    ? [0]
    : precedingIndex >= serverNavigationCards.length - 1
      ? [serverNavigationCards.length - 1]
      : [precedingIndex, precedingIndex + 1];
  let closest = null;
  let closestDistance = Number.POSITIVE_INFINITY;
  candidateIndexes.forEach(index => {
    const card = serverNavigationCards[index];
    const distance = Math.abs(card.getBoundingClientRect().top - focusLine);
    if (distance < closestDistance) {
      closest = card;
      closestDistance = distance;
    }
  });
  if (closest) setActiveServer(closest.dataset.serverId);
}

function scheduleServerNavigationSync() {
  if (ui.serverNavigator.hidden) return;
  if (serverNavigationFrame != null) return;
  serverNavigationFrame = window.requestAnimationFrame(syncActiveServerFromScroll);
}

function renderServerNavigator(servers) {
  if (serverNavigatorSearchFrame != null) {
    window.cancelAnimationFrame(serverNavigatorSearchFrame);
    serverNavigatorSearchFrame = null;
  }
  const visible = servers.length > 1;
  ui.serverNavigator.hidden = !visible;
  if (!visible) {
    if (lastNavigatorRenderSignature !== 'hidden') ui.serverNavigatorList.replaceChildren();
    lastNavigatorRenderSignature = 'hidden';
    ui.serverNavigatorCount.textContent = '';
    ui.serverNavigatorEmpty.hidden = true;
    ui.serverNavigatorPosition.textContent = '—';
    ui.previousServer.disabled = true;
    ui.nextServer.disabled = true;
    serverNavigatorVisibleIds = [];
    serverNavigatorItems = new Map();
    serverNavigatorPositions = new Map();
    activeServerId = servers[0]?.server_id || '';
    return;
  }
  const allMatches = filteredServerEntries(servers);
  const matches = visibleServerEntries(allMatches);
  ui.serverNavigatorCount.textContent = allMatches.length === servers.length
    ? `${number(servers.length)} 台`
    : `${number(allMatches.length)} 匹配`;
  const navigatorHtml = matches.map(item => renderServerNavigatorItem(item.server, item.index)).join('');
  const navigatorSignature = JSON.stringify([
    serverNavigatorFilter,
    serverNavigatorQuery,
    serverNavigatorSide,
    navigatorHtml,
  ]);
  if (lastNavigatorRenderSignature !== navigatorSignature) {
    ui.serverNavigatorList.innerHTML = navigatorHtml;
    lastNavigatorRenderSignature = navigatorSignature;
    uiRenderMetrics.navigatorBuilds += 1;
  }
  serverNavigatorVisibleIds = allMatches.map(item => item.server.server_id);
  serverNavigatorItems = new Map([...ui.serverNavigatorList.querySelectorAll('.server-navigator-item')].map(item => [item.dataset.serverId, item]));
  serverNavigatorPositions = new Map(serverNavigatorVisibleIds.map((serverId, index) => [serverId, index + 1]));
  ui.serverNavigatorEmpty.hidden = allMatches.length > 0;
  ui.serverNavigator.querySelectorAll('[data-server-navigator-filter]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.serverNavigatorFilter === serverNavigatorFilter));
  });
  if (!servers.some(server => server.server_id === activeServerId)) activeServerId = servers[0].server_id;
  setActiveServer(activeServerId);
  updateServerNavigatorPosition();
  scheduleServerNavigationSync();
}

function scheduleServerNavigatorSearchRender() {
  if (serverNavigatorSearchFrame != null) return;
  serverNavigatorSearchFrame = window.requestAnimationFrame(() => {
    serverNavigatorSearchFrame = null;
    if (currentSnapshot) renderServerNavigator(currentSnapshot.servers);
  });
}

function navigateToServer(serverId) {
  const serverIndex = currentSnapshot?.servers?.findIndex(item => item.server_id === serverId) ?? -1;
  if (serverIndex >= 0 && currentSnapshot.servers.length > LARGE_SERVER_FLEET_THRESHOLD) {
    const targetOffset = Math.floor(serverIndex / SERVER_FLEET_PAGE_SIZE) * SERVER_FLEET_PAGE_SIZE;
    if (targetOffset !== serverFleetPageOffset) {
      serverFleetPageOffset = targetOffset;
      render(currentSnapshot);
    }
  }
  const card = document.getElementById(serverCardAnchor(serverId));
  if (!card) return;
  setActiveServer(serverId);
  recentServerIds = [serverId, ...recentServerIds.filter(item => item !== serverId)].slice(0, 8);
  card.scrollIntoView({behavior: 'auto', block: 'start'});
  const server = currentSnapshot?.servers?.find(item => item.server_id === serverId);
  ui.serverNavigatorStatus.textContent = server
    ? `已定位到服务器 ${server.display_name}`
    : '已定位到所选服务器';
}

function navigateRelativeServer(offset) {
  const visibleIds = visibleServerNavigatorIds();
  if (visibleIds.length < 2) return;
  const currentIndex = visibleIds.indexOf(activeServerId);
  const targetIndex = currentIndex < 0
    ? (offset > 0 ? 0 : visibleIds.length - 1)
    : (currentIndex + offset + visibleIds.length) % visibleIds.length;
  navigateToServer(visibleIds[targetIndex]);
}

function renderServer(server, index = 0) {
  const state = server.connection.state;
  const hasData = Boolean(server.view_kind);
  const metadata = `${backendLabel(server.backend)} · ${state === 'online' ? '刚刚更新' : state === 'connecting' ? '正在建立首次有效快照' : `最后成功：${formatTime(server.connection.last_success_at)}`}`;
  const data = hasData ? (server.view_kind === 'live-memory' ? renderLiveTable(server) : renderSchedulerTable(server)) : '';
  const body = state === 'online'
    ? data
    : state === 'connecting'
      ? `${renderConfiguring(server)}${hasData ? `<div class="stale-data">${data}</div>` : ''}`
      : `${renderError(server)}${hasData ? `<div class="stale-data">${data}</div>` : ''}`;
  const position = String(index + 1).padStart(2, '0');
  return `<article id="${escapeHtml(serverCardAnchor(server.server_id))}" class="server-card ${escapeHtml(state)}" data-server-id="${escapeHtml(server.server_id)}"><aside class="server-rail" aria-hidden="true"><span>${position}</span>${serverGlyph(server.backend)}</aside><div class="server-surface"><header class="server-head"><div class="server-identity"><h3 class="server-name">${escapeHtml(server.display_name)}</h3><div class="server-meta">${escapeHtml(metadata)}</div></div>${renderAccountOverview(server)}<div class="server-head-controls"><span class="server-status"><i class="status-dot ${escapeHtml(state)}"></i><span class="server-status-label">${escapeHtml(stateLabel(state))}</span></span>${renderServerQuickActions(server)}</div></header>${body}${renderDirectoryModule(server)}</div></article>`;
}

function serverCardRenderSignature(server, index) {
  const connection = server.connection || {};
  const error = connection.error || {};
  const offlineTimestamp = connection.state === 'online' ? '' : connection.last_success_at;
  return JSON.stringify([
    server.server_id,
    index,
    server.display_name,
    server.backend,
    server.view_kind,
    connection.state,
    connection.data_origin,
    connection.data_revision,
    offlineTimestamp,
    connection.retry_at,
    error.code,
    error.message,
    favoriteServerIds.has(server.server_id),
    serverIsEnabled(server.server_id),
    taskCompletionWatchRenderSignature(server.server_id),
  ]);
}

function taskCompletionWatchRenderSignature(serverId) {
  return (currentProfile?.task_completion_watches || [])
    .filter(watch => watch.server_id === serverId)
    .map(watch => watch.task_key)
    .sort()
    .join('\u001f');
}

function serverCardElement(server, index) {
  const template = document.createElement('template');
  template.innerHTML = renderServer(server, index).trim();
  uiRenderMetrics.serverCardCreates += 1;
  return template.content.firstElementChild;
}

function reconcileServerCards(entries) {
  const existing = new Map(
    [...ui.list.querySelectorAll(':scope > .server-card')].map(card => [card.dataset.serverId, card]),
  );
  const desiredIds = new Set(entries.map(item => item.server.server_id));
  entries.forEach((item, position) => {
    const serverId = item.server.server_id;
    const signature = serverCardRenderSignature(item.server, item.index);
    let card = existing.get(serverId);
    if (!card || renderedServerCardSignatures.get(serverId) !== signature) {
      const replacement = serverCardElement(item.server, item.index);
      if (card) card.replaceWith(replacement);
      card = replacement;
      existing.set(serverId, card);
      renderedServerCardSignatures.set(serverId, signature);
    }
    const positionNode = ui.list.children[position];
    if (positionNode !== card) ui.list.insertBefore(card, positionNode || null);
  });
  [...ui.list.children].forEach(child => {
    const serverId = child.dataset?.serverId;
    if (!serverId || !desiredIds.has(serverId)) child.remove();
  });
  [...renderedServerCardSignatures.keys()].forEach(serverId => {
    if (!desiredIds.has(serverId)) renderedServerCardSignatures.delete(serverId);
  });
}

function snapshotRevision(snapshot) {
  const value = Number(snapshot?.monitoring?.revision ?? snapshot?.summary?.revision);
  return Number.isFinite(value) ? value : null;
}

function snapshotDataUpdatedAt(snapshot) {
  return snapshot.monitoring?.data_updated_at || snapshot.summary?.data_updated_at || snapshot.fetched_at;
}

function syncUnchangedSnapshotStatus(snapshot) {
  currentSnapshot = snapshot;
  lastRenderedRevision = snapshotRevision(snapshot);
  renderTaskAlertIndicator(snapshot);
  const paused = snapshot.monitoring?.paused ?? snapshot.profile?.monitoring?.paused ?? currentProfile?.monitoring_paused;
  const inFlight = Boolean(snapshot.monitoring?.in_flight);
  updateMonitoringControls(paused);
  if (!paused) {
    setRefreshClock(
      inFlight
        ? '后台读取中…界面仍可操作'
        : `状态更新于 ${new Date(snapshotDataUpdatedAt(snapshot)).toLocaleTimeString('zh-CN')} · 每 ${snapshot.profile.refresh_seconds} 秒`,
      inFlight,
    );
  }
}

function render(snapshot) {
  uiRenderMetrics.fullRenders += 1;
  currentSnapshot = snapshot;
  lastRenderedRevision = snapshotRevision(snapshot);
  renderTaskAlertIndicator(snapshot);
  snapshot.servers.forEach(server => {
    const page = clusterNodePages.get(server.server_id);
    const dataRevision = server.connection?.data_revision ?? null;
    if (page?.revision != null && dataRevision != null && page.revision !== dataRevision) {
      clusterNodePages.delete(server.server_id);
      clusterNodeRequestGenerations.set(
        server.server_id,
        (clusterNodeRequestGenerations.get(server.server_id) || 0) + 1,
      );
    }
  });
  const summary = snapshot.summary;
  const hasServers = snapshot.servers.length > 0;
  ui.firstRunHome.hidden = hasServers;
  ui.dashboardContent.hidden = !hasServers;
  ui.refresh.hidden = !hasServers;
  ui.refreshClock.hidden = !hasServers;
  const summarySignature = JSON.stringify([
    summary.free_vram_gib,
    summary.total_vram_gib,
    summary.online_servers,
    summary.total_servers,
    summary.total_gpus,
  ]);
  if (lastSummaryRenderSignature !== summarySignature) {
    ui.summary.innerHTML = renderSummary(summary);
    lastSummaryRenderSignature = summarySignature;
  }
  ui.serverListMeta.textContent = summary.total_servers
    ? `${summary.online_servers}/${summary.total_servers} 台监控就绪 · ${number(summary.total_gpus)} 块 GPU`
    : '等待配置';
  const disabledCount = snapshot.servers.filter(server => server.connection?.state === 'disabled').length;
  const monitoredCount = Math.max(0, summary.total_servers - disabledCount);
  const unavailable = Math.max(0, monitoredCount - summary.online_servers);
  const inFlight = Boolean(snapshot.monitoring?.in_flight);
  const startupNotice = (Array.isArray(snapshot.notices) ? snapshot.notices : []).find(notice => (
    ['error', 'warning'].includes(String(notice?.severity || '').toLowerCase())
    && String(notice?.message || '').trim()
  ));
  if (startupNotice) {
    ui.notice.hidden = false;
    ui.notice.innerHTML = `<div><div class="notice-title">${escapeHtml(String(startupNotice.message).trim())}</div></div><button class="button dismiss-notice" type="button" data-notice-code="${escapeHtml(String(startupNotice.code || ''))}">关闭</button>`;
  } else if (unavailable > 0 && monitoredCount > 0 && !inFlight) {
    ui.notice.hidden = false;
    ui.notice.innerHTML = `<div><div class="notice-title">${unavailable === summary.total_servers ? '所有服务器尚未监控就绪' : `${unavailable} 台服务器尚未监控就绪`}</div><div class="notice-copy">每台卡片会区分网络、认证、配置和资源读取错误；旧快照不会计入实时容量。</div></div><button class="button retry-all" type="button">全部重新验证</button>`;
  } else {
    ui.notice.hidden = true;
    ui.notice.replaceChildren();
  }
  if (!snapshot.servers.length) {
    ui.list.replaceChildren();
    renderedServerCardSignatures.clear();
  } else {
    const entries = mainServerEntries(snapshot.servers);
    reconcileServerCards(entries);
    if (entries.length && !entries.some(item => item.server.server_id === activeServerId)) activeServerId = entries[0].server.server_id;
  }
  updateServerFleetPager(snapshot.servers);
  serverNavigationCards = [...ui.list.querySelectorAll('.server-card')];
  serverNavigationCardsById = new Map(serverNavigationCards.map(card => [card.dataset.serverId, card]));
  renderServerNavigator(snapshot.servers);
  const paused = snapshot.monitoring?.paused ?? snapshot.profile?.monitoring?.paused ?? currentProfile?.monitoring_paused;
  updateMonitoringControls(paused);
  if (!paused) {
    setRefreshClock(
      inFlight
        ? '后台读取中…界面仍可操作'
        : `状态更新于 ${new Date(snapshotDataUpdatedAt(snapshot)).toLocaleTimeString('zh-CN')} · 每 ${snapshot.profile.refresh_seconds} 秒`,
      inFlight,
    );
  }
  applyDashboardDisclosureMode();
  scheduleDirectoryFreshnessValidation();
}

function showToast(message) {
  ui.toast.textContent = message;
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 3000);
}

async function writeClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (_error) {
      // Local file WebViews may deny the modern Clipboard API; use the
      // selection-based fallback below.
    }
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-10000px';
  textarea.style.top = '0';
  document.body.appendChild(textarea);
  textarea.select();
  textarea.setSelectionRange(0, text.length);
  let copied = false;
  try {
    copied = typeof document.execCommand === 'function' && document.execCommand('copy');
  } finally {
    textarea.remove();
  }
  if (!copied) throw new Error('clipboard unavailable');
}

async function copyCommand(button) {
  const target = document.getElementById(button.dataset.copyTarget || '');
  const text = target?.textContent?.trim();
  if (!text) {
    showToast('没有可复制的命令');
    return;
  }
  const original = button.textContent;
  button.disabled = true;
  try {
    await writeClipboard(text);
    button.textContent = '已复制';
    showToast('命令已复制到剪贴板');
  } catch (_error) {
    button.textContent = '复制失败';
    showToast('无法访问剪贴板，请手动选择命令');
  } finally {
    setTimeout(() => {
      if (button.isConnected) {
        button.textContent = original;
        button.disabled = false;
      }
    }, 1800);
  }
}

async function copyContextValue(button) {
  const value = button.dataset.copyValue || '';
  if (!value) return;
  try {
    await writeClipboard(value);
    showToast('已复制到剪贴板');
  } catch (_error) {
    showToast('无法访问剪贴板');
  }
}

async function copyServerSshCommand(serverId) {
  if (!api?.get_ssh_command) return showToast('当前版本无法生成 SSH 命令');
  try {
    const result = await api.get_ssh_command(serverId);
    if (!result?.ok || !result.command) throw new Error(result?.error || '无法生成 SSH 命令');
    await writeClipboard(result.copy_text || result.command);
    if (result.copy_format === 'openssh-config') {
      showToast('SSH Config 配置块已复制');
    } else if (result.endpoint_complete) {
      showToast('完整 SSH 命令已复制（地址、用户与端口已包含）');
    } else {
      showToast(result.warning || (result.shell === 'powershell'
        ? 'PowerShell SSH 命令已复制'
        : 'SSH 命令已复制到剪贴板'));
    }
  } catch (error) {
    showToast(error.message || String(error));
  }
}

function scheduleRefreshCompletion(generation, attempt = 0) {
  if (!api?.get_snapshot || generation !== refreshPollGeneration) return;
  const delay = Math.min(2000, 250 + attempt * 75);
  refreshPollTimer = window.setTimeout(async () => {
    if (generation !== refreshPollGeneration) return;
    if (document.hidden) {
      refreshDeferredWhileHidden = true;
      if (api?.request_background_refresh) void api.request_background_refresh().catch(() => {});
      return;
    }
    try {
      const snapshot = await api.get_snapshot();
      if (generation !== refreshPollGeneration) return;
      const revision = snapshotRevision(snapshot);
      if (revision == null || revision !== lastRenderedRevision) {
        render(snapshot);
      } else {
        syncUnchangedSnapshotStatus(snapshot);
      }
      if (snapshot.monitoring?.in_flight) {
        scheduleRefreshCompletion(generation, attempt + 1);
        return;
      }
      if (recommendationRequested) await updateRecommendation();
      if (generation !== refreshPollGeneration) return;
      await evaluateResourceWatch();
    } catch (error) {
      if (generation !== refreshPollGeneration) return;
      ui.notice.hidden = false;
      ui.notice.innerHTML = `<div><div class="notice-title">本地应用服务异常</div><div class="notice-copy">${escapeHtml(error.message || String(error))}</div></div>`;
    }
  }, delay);
}

async function refresh(force = false, serverId = null) {
  const generation = ++refreshPollGeneration;
  window.clearTimeout(refreshPollTimer);
  ui.refresh.disabled = true;
  setRefreshClock('正在读取服务器…', true);
  try {
    const snapshot = await api.get_status(force, serverId);
    if (generation !== refreshPollGeneration) return;
    const revision = snapshotRevision(snapshot);
    if (revision == null || revision !== lastRenderedRevision) render(snapshot);
    else syncUnchangedSnapshotStatus(snapshot);
    if (snapshot.monitoring?.in_flight && api?.get_snapshot) {
      scheduleRefreshCompletion(generation);
    } else {
      if (recommendationRequested) await updateRecommendation();
      if (generation !== refreshPollGeneration) return;
      await evaluateResourceWatch();
    }
  } catch (error) {
    if (generation !== refreshPollGeneration) return;
    ui.notice.hidden = false;
    ui.notice.innerHTML = `<div><div class="notice-title">本地应用服务异常</div><div class="notice-copy">${escapeHtml(error.message || String(error))}</div></div>`;
  } finally {
    if (generation === refreshPollGeneration) ui.refresh.disabled = false;
  }
}

async function dismissNotice(code) {
  if (!api?.dismiss_notice || !code) return;
  try {
    const result = await api.dismiss_notice(code);
    if (!result?.ok) throw new Error(result?.error || '无法关闭提示');
    if (api?.get_snapshot) render(await api.get_snapshot());
  } catch (error) {
    showToast(error.message || String(error));
  }
}

function resourceCriteriaFromInputs() {
  return {
    gpu_count: Math.max(1, Number(ui.requiredGpuCount.value) || 1),
    min_memory_gib: Math.max(0, Number(ui.requiredMemory.value) || 0),
    gpu_type: ui.preferredGpu.value.trim(),
    partition: ui.preferredPartition.value.trim(),
    same_node: ui.requireSameNode.checked,
    limit: Math.max(1, Math.min(10, Number(ui.recommendationLimit.value) || 5)),
  };
}

function applyResourceCriteria(criteria = {}) {
  ui.requiredGpuCount.value = Math.max(1, Number(criteria.gpu_count ?? criteria.count) || 1);
  ui.requiredMemory.value = Math.max(0, Number(criteria.min_memory_gib ?? criteria.required_memory_gib) || 0);
  ui.preferredGpu.value = criteria.gpu_type || criteria.preferred_gpu || '';
  ui.preferredPartition.value = criteria.partition || '';
  ui.requireSameNode.checked = Boolean(criteria.same_node);
  ui.recommendationLimit.value = String(Math.max(1, Math.min(10, Number(criteria.limit) || 5)));
  if (typeof criteria.query === 'string') {
    ui.serverNavigatorSearch.value = criteria.query;
    serverNavigatorQuery = criteria.query.trim().toLocaleLowerCase();
  }
  if (['all', 'available', 'tasks', 'issues'].includes(criteria.filter)) serverNavigatorFilter = criteria.filter;
  serverFleetPageOffset = 0;
  if (currentSnapshot) render(currentSnapshot);
  clearRecommendation();
}

function savedViewEntries() {
  const views = currentProfile?.saved_views || [];
  if (Array.isArray(views)) return views.map(view => ({name: view.name, criteria: view.criteria || view})).filter(view => view.name);
  return Object.entries(views || {}).map(([name, criteria]) => ({name, criteria}));
}

function renderSavedViews() {
  const views = savedViewEntries();
  ui.savedViewChips.hidden = views.length === 0;
  ui.savedViewChips.innerHTML = views.map(view => `<span class="saved-view-chip"><button type="button" data-apply-saved-view="${escapeHtml(view.name)}">${escapeHtml(view.name)}</button><button type="button" data-delete-saved-view="${escapeHtml(view.name)}" aria-label="删除视图 ${escapeHtml(view.name)}">删除</button></span>`).join('');
}

async function saveCurrentView() {
  const name = ui.savedViewName.value.trim();
  if (!name) return showToast('请先填写视图名称');
  if (!api?.save_saved_view) return showToast('当前版本暂不支持保存视图');
  try {
    const {limit: _displayLimit, ...resourceCriteria} = resourceCriteriaFromInputs();
    const criteria = {
      ...resourceCriteria,
      query: ui.serverNavigatorSearch.value.trim(),
      filter: ['all', 'available', 'tasks', 'issues'].includes(serverNavigatorFilter) ? serverNavigatorFilter : 'all',
    };
    const result = await api.save_saved_view(name, criteria);
    if (!result?.ok) throw new Error(result?.error || '无法保存视图');
    if (result.profile) acceptProfile(result.profile);
    ui.savedViewName.value = '';
    renderSavedViews();
    showToast('筛选视图已保存');
  } catch (error) {
    showToast(error.message || String(error));
  }
}

async function deleteSavedView(name) {
  if (!api?.delete_saved_view) return;
  try {
    const result = await api.delete_saved_view(name);
    if (!result?.ok) throw new Error(result?.error || '无法删除视图');
    if (result.profile) acceptProfile(result.profile);
    renderSavedViews();
    showToast('筛选视图已删除');
  } catch (error) {
    showToast(error.message || String(error));
  }
}

async function requestResourceRecommendations(criteria) {
  if (!api?.recommend_resources) throw new Error('当前桌面组件不支持资源匹配，请重新安装最新版本');
  const result = await api.recommend_resources(
    criteria.gpu_count, criteria.min_memory_gib, criteria.gpu_type, criteria.partition, criteria.same_node, criteria.limit,
  );
  const candidates = result?.candidates || result?.results || result?.recommendations || (result?.ok && result?.result ? [result.result] : []);
  const recommendations = candidates.slice(0, criteria.limit).map(candidate => {
    const allocations = candidate.allocations || [];
    return {
      ...candidate,
      backend: candidate.backend || (allocations.some(allocation => allocation.node) ? 'slurm_ssh' : 'direct_ssh'),
      available_memory_gib: candidate.available_memory_gib ?? candidate.minimum_memory_gib,
      gpu_type: candidate.gpu_type || (candidate.gpu_types || []).join(' / '),
      partition: candidate.partition || (candidate.partitions || []).join(' / '),
      location: candidate.location || allocations.map(allocation => allocation.location).filter(Boolean).join(' + '),
      free_units: candidate.free_units ?? candidate.available_units,
    };
  });
  return {...result, recommendations};
}

function renderRecommendationResult(result, index) {
  const locationLabel = result.backend === 'slurm_ssh' ? '节点' : '设备';
  const partition = result.partition
    ? `<div class="recommendation-fact"><dt>分区</dt><dd>${escapeHtml(result.partition)}</dd></div>`
    : '';
  return `<div class="recommendation-result"><div class="recommendation-rank">${index + 1}</div><div class="recommendation-main"><span>推荐服务器</span><strong>${escapeHtml(result.display_name)}</strong></div><div class="recommendation-memory"><span>单卡可用显存</span><strong>${number(result.available_memory_gib)} <small>GiB</small></strong></div><dl class="recommendation-facts">${partition}<div class="recommendation-fact"><dt>卡型</dt><dd>${escapeHtml(result.gpu_type)}</dd></div><div class="recommendation-fact"><dt>${locationLabel}</dt><dd>${escapeHtml(result.location)}</dd></div><div class="recommendation-fact"><dt>空闲 GPU</dt><dd>${number(result.free_units)} 张</dd></div></dl></div>`;
}

async function updateRecommendation() {
  recommendationRequested = true;
  ui.recommend.disabled = true;
  ui.recommendation.hidden = false;
  ui.recommendation.innerHTML = '<div class="recommendation-pending">正在匹配…</div>';
  try {
    const result = await requestResourceRecommendations(resourceCriteriaFromInputs());
    const recommendations = result.recommendations || [];
    if (!result.ok || !recommendations.length) {
      const reason = result.reason || result.error || '当前没有满足条件的资源';
      ui.recommendation.innerHTML = `<div class="recommendation-empty"><strong>没有匹配结果</strong><span>${escapeHtml(reason)}</span></div>`;
      return;
    }
    ui.recommendation.innerHTML = `<div class="recommendation-results">${recommendations.map(renderRecommendationResult).join('')}</div>`;
  } catch (error) {
    ui.recommendation.innerHTML = `<div class="recommendation-empty"><strong>匹配失败</strong><span>${escapeHtml(error.message || String(error))}</span></div>`;
  } finally {
    ui.recommend.disabled = false;
  }
}

function clearRecommendation() {
  recommendationRequested = false;
  ui.recommendation.hidden = true;
  ui.recommendation.replaceChildren();
}

async function evaluateResourceWatch() {
  if (!ui.resourceWatchEnabled.checked || monitoringPaused || resourceWatchEvaluation) return;
  const criteriaRevision = resourceWatchCriteriaRevision;
  const criteria = resourceCriteriaFromInputs();
  resourceWatchEvaluation = (async () => {
    try {
      const result = await requestResourceRecommendations(criteria);
      if (criteriaRevision !== resourceWatchCriteriaRevision) return;
      const matched = Boolean(result?.ok && result.recommendations?.length);
      ui.resourceWatchStatus.textContent = matched ? '当前已有匹配资源' : '等待满足条件';
      const now = Date.now();
      if (matched && !resourceWatchMatched && now - resourceWatchLastNotificationAt >= RESOURCE_WATCH_COOLDOWN_MS) {
        resourceWatchLastNotificationAt = now;
        if (api?.show_notification) await api.show_notification(
          localizedText('显存雷达：资源可用'),
          localizedText('你设置的 GPU 条件已有匹配结果。'),
          'resource_available',
        );
      }
      resourceWatchMatched = matched;
    } catch (_error) {
      if (criteriaRevision === resourceWatchCriteriaRevision) ui.resourceWatchStatus.textContent = '提醒检查失败，将在下次刷新重试';
    } finally {
      resourceWatchEvaluation = null;
      if (criteriaRevision !== resourceWatchCriteriaRevision && ui.resourceWatchEnabled.checked) void evaluateResourceWatch();
    }
  })();
  await resourceWatchEvaluation;
}

function scheduleResourceWatchEvaluation() {
  resourceWatchCriteriaRevision += 1;
  resourceWatchMatched = false;
  if (!ui.resourceWatchEnabled.checked) return;
  ui.resourceWatchStatus.textContent = '正在检查新条件';
  clearTimeout(resourceWatchDebounceTimer);
  resourceWatchDebounceTimer = setTimeout(() => void evaluateResourceWatch(), 250);
}

function serverDraftFromValue(server = {}, options = {}) {
  return {
    ...server,
    backend: server.auto_detect_backend ? 'auto' : (server.backend || 'auto'),
    _detected_backend: server.backend === 'slurm_ssh' ? 'slurm_ssh' : 'direct_ssh',
    _original_id: server.id || '',
    _original_ssh_alias: server.ssh_alias || '',
    _imported_candidate: options.importedCandidate === true,
    _password: '',
    _clear_password: false,
  };
}

function serverDraftIndexFromEditor(editor) {
  const indexed = Number(editor.dataset.draftIndex);
  const originalId = String(editor.dataset.originalId || '');
  const indexedDraft = settingsServerDrafts[indexed];
  if (indexedDraft && String(indexedDraft._original_id || '') === originalId) return indexed;
  return settingsServerDrafts.findIndex(draft => String(draft._original_id || '') === originalId);
}

function syncServerDraftFromEditor(editor) {
  const draftIndex = serverDraftIndexFromEditor(editor);
  const draft = settingsServerDrafts[draftIndex];
  if (!draft) return;
  editor.dataset.draftIndex = String(draftIndex);
  editor.querySelectorAll('[data-field]').forEach(input => {
    const field = input.dataset.field;
    if (field === 'password') draft._password = input.value;
    else if (field === 'clear_password') draft._clear_password = Boolean(input.checked);
    else if (input.type === 'checkbox') draft[field] = input.checked;
    else if (field === 'port') draft[field] = Number(input.value || 22);
    else draft[field] = input.value.trim();
  });
  draft.enabled = editor.dataset.enabled !== 'false';
  draft.has_password = Boolean(editor.dataset.hasPassword === 'true');
  draft.prefer_identity_auth = editor.dataset.preferIdentityAuth === 'true';
  draft.gpu_memory_gib = JSON.parse(editor.dataset.gpuMemoryGib || '{}');
}

function syncVisibleServerDrafts() {
  ui.editorList.querySelectorAll('.server-editor').forEach(syncServerDraftFromEditor);
}

function filteredServerDraftIndices() {
  const query = settingsServerQuery.trim().toLowerCase();
  return settingsServerDrafts.map((_draft, index) => index).filter(index => {
    if (!query) return true;
    const draft = settingsServerDrafts[index];
    return [draft.display_name, draft.id, draft.ssh_alias, draft.host, draft.username]
      .some(value => String(value || '').toLowerCase().includes(query));
  });
}

function refreshServerEditorOrder() {
  const editors = [...ui.editorList.querySelectorAll('.server-editor')];
  editors.forEach(editor => {
    const index = serverDraftIndexFromEditor(editor);
    const position = index + 1;
    editor.querySelector('.server-position').textContent = String(position).padStart(2, '0');
    const handle = editor.querySelector('.server-drag-handle');
    const sortable = !settingsServerQuery && settingsServerDrafts.length > 1 && index >= 0;
    handle.draggable = sortable;
    handle.tabIndex = sortable ? 0 : -1;
    handle.setAttribute('aria-disabled', String(!sortable));
    handle.setAttribute('aria-label', settingsServerQuery
      ? '清除搜索后可排序'
      : settingsServerDrafts.length <= 1
        ? '仅一台服务器，无需排序'
        : `拖动第 ${position} 台服务器排序；按上下方向键微调`);
  });
}

function announceServerOrder(index) {
  const draft = settingsServerDrafts[index];
  if (!draft) return;
  const name = String(draft.display_name || draft.ssh_alias || draft.id || '服务器');
  ui.serverOrderStatus.textContent = `${name} 已移动到第 ${number(index + 1)} 位`;
}

function reorderServerDraft(sourceIndex, insertionIndex) {
  if (sourceIndex < 0 || sourceIndex >= settingsServerDrafts.length) return -1;
  const boundedInsertion = Math.max(0, Math.min(insertionIndex, settingsServerDrafts.length));
  const [draft] = settingsServerDrafts.splice(sourceIndex, 1);
  const adjustedInsertion = sourceIndex < boundedInsertion ? boundedInsertion - 1 : boundedInsertion;
  settingsServerDrafts.splice(adjustedInsertion, 0, draft);
  return adjustedInsertion;
}

function moveServerEditor(editor, direction) {
  syncVisibleServerDrafts();
  const index = serverDraftIndexFromEditor(editor);
  const target = index + direction;
  if (!settingsServerDrafts[index] || !settingsServerDrafts[target]) return;
  const movedIndex = reorderServerDraft(index, direction > 0 ? target + 1 : target);
  settingsServerQuery = '';
  ui.editorSearch.value = '';
  settingsServerPageOffset = Math.floor(movedIndex / SERVER_EDITOR_PAGE_SIZE) * SERVER_EDITOR_PAGE_SIZE;
  renderServerEditorPage({focusDraftIndex: movedIndex, focusDragHandle: true});
  announceServerOrder(movedIndex);
}

function clearServerEditorDropIndicators() {
  ui.editorList.querySelectorAll('.server-editor').forEach(editor => {
    editor.classList.remove('drag-over-before', 'drag-over-after');
  });
}

function clearServerEditorDraggingState() {
  ui.editorList.querySelectorAll('.server-editor.dragging').forEach(editor => editor.classList.remove('dragging'));
}

function setupServerEditorDrag(editor) {
  const handle = editor.querySelector('.server-drag-handle');
  handle.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
  });
  handle.addEventListener('keydown', event => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    event.stopPropagation();
    moveServerEditor(editor, event.key === 'ArrowUp' ? -1 : 1);
  });
  handle.addEventListener('dragstart', event => {
    if (handle.getAttribute('aria-disabled') === 'true') {
      event.preventDefault();
      return;
    }
    syncVisibleServerDrafts();
    draggedServerDraftIndex = serverDraftIndexFromEditor(editor);
    editor.classList.add('dragging');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(editor.dataset.originalId || draggedServerDraftIndex));
    }
  });
  handle.addEventListener('dragend', () => {
    draggedServerDraftIndex = -1;
    clearServerEditorDropIndicators();
    clearServerEditorDraggingState();
  });
  editor.addEventListener('dragover', event => {
    if (draggedServerDraftIndex < 0 || settingsServerQuery) return;
    const targetIndex = serverDraftIndexFromEditor(editor);
    if (targetIndex < 0 || targetIndex === draggedServerDraftIndex) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    const after = event.clientY >= editor.getBoundingClientRect().top + editor.getBoundingClientRect().height / 2;
    clearServerEditorDropIndicators();
    editor.classList.add(after ? 'drag-over-after' : 'drag-over-before');
  });
  editor.addEventListener('drop', event => {
    if (draggedServerDraftIndex < 0 || settingsServerQuery) return;
    event.preventDefault();
    syncVisibleServerDrafts();
    const targetIndex = serverDraftIndexFromEditor(editor);
    const after = event.clientY >= editor.getBoundingClientRect().top + editor.getBoundingClientRect().height / 2;
    const movedIndex = reorderServerDraft(draggedServerDraftIndex, targetIndex + (after ? 1 : 0));
    draggedServerDraftIndex = -1;
    clearServerEditorDropIndicators();
    clearServerEditorDraggingState();
    settingsServerPageOffset = Math.floor(movedIndex / SERVER_EDITOR_PAGE_SIZE) * SERVER_EDITOR_PAGE_SIZE;
    renderServerEditorPage({focusDraftIndex: movedIndex, focusDragHandle: true});
    announceServerOrder(movedIndex);
  });
}

function sshAliasKey(value) {
  return String(value || '').trim().toLowerCase();
}

function rememberIgnoredSshAlias(value) {
  const alias = String(value || '').trim();
  const key = sshAliasKey(alias);
  if (!key) return;
  const existing = [...pendingIgnoredSshAliases].find(item => sshAliasKey(item) === key);
  if (!existing) pendingIgnoredSshAliases.add(alias);
}

function clearConnectionTestResult(editor) {
  const output = editor.querySelector('.connection-test-result');
  output.hidden = true;
  output.replaceChildren();
}

function clearSshKeySetupResult(editor) {
  const output = editor.querySelector('.ssh-key-setup-result');
  output.hidden = true;
  output.replaceChildren();
}

function renderSshKeySetupResult(output, result) {
  const stages = (result?.stages || []).slice(0, 8);
  output.innerHTML = `<strong>${result?.ok ? 'SSH 免密登录已就绪' : 'SSH Key 配置未完成'}</strong>${stages.length ? `<ol>${stages.map(stage => `<li class="${escapeHtml(stage.state || '')}"><span>${escapeHtml(stage.label || stage.id)}</span><small>${escapeHtml(stage.message || '')}</small></li>`).join('')}</ol>` : `<span>${escapeHtml(result?.error || '未收到配置结果')}</span>`}`;
  output.hidden = false;
}

function serverEditorHasUnsavedConnectionChanges(editor, savedServer) {
  if (!savedServer) return true;
  const value = name => editor.querySelector(`[data-field="${name}"]`).value.trim();
  return value('id') !== String(savedServer.id || '')
    || value('backend') !== String(savedServer.backend || '')
    || value('ssh_alias') !== String(savedServer.ssh_alias || '')
    || value('host') !== String(savedServer.host || '')
    || Number(value('port') || 22) !== Number(savedServer.port || 22)
    || editor.querySelector('[data-field="port_override"]').checked !== Boolean(savedServer.port_override)
    || value('username') !== String(savedServer.username || '')
    || value('identity_file') !== String(savedServer.identity_file || '')
    || value('ssh_config_file') !== String(savedServer.ssh_config_file || '')
    || Boolean(value('password'))
    || editor.querySelector('[data-field="clear_password"]').checked;
}

async function configureServerSshKey(editor) {
  const serverId = editor.dataset.originalId || '';
  const output = editor.querySelector('.ssh-key-setup-result');
  const button = editor.querySelector('.configure-ssh-key');
  if (!serverId || !api?.configure_ssh_key) {
    output.textContent = '请先保存这台服务器，再配置 SSH 免密登录。';
    output.hidden = false;
    return;
  }
  const savedServer = currentProfile?.servers?.find(server => server.id === serverId);
  if (serverEditorHasUnsavedConnectionChanges(editor, savedServer)) {
    output.textContent = '服务器地址、账号或密码有尚未保存的修改。请先保存设置，再配置 SSH Key。';
    output.hidden = false;
    return;
  }
  const mode = editor.querySelector('[data-key-mode="generate"]').checked ? 'generate' : 'existing';
  const privatePath = editor.querySelector('[data-key-field="private_key_path"]').value.trim();
  const publicPath = editor.querySelector('[data-key-field="public_key_path"]').value.trim();
  if (mode === 'existing' && !privatePath) {
    output.textContent = '请选择现有私钥路径。';
    output.hidden = false;
    return;
  }
  const targetName = editor.querySelector('[data-server-editor-name]').textContent || '这台服务器';
  const confirmation = mode === 'generate'
    ? `将为“${targetName}”生成一把独立的 Ed25519 密钥。\n\n私钥只保存在本机且不会覆盖现有密钥；公钥只会追加到服务器，不替换 authorized_keys。若追加后验证或本地保存失败，为避免误删并发修改，应用会保留远端公钥和配套本地私钥，并提示重试或精确手动移除。是否继续？`
    : `将为“${targetName}”部署所选密钥的公钥。\n\n私钥不会上传；公钥只在无重复项时追加，不替换 authorized_keys。若追加后验证或本地保存失败，应用不会自动改写远端文件，会报告需要恢复并提示重试或精确手动移除。是否继续？`;
  if (!window.confirm(confirmation)) return;

  const controls = [...editor.querySelectorAll('.ssh-key-setup-body input, .ssh-key-setup-body button')];
  controls.forEach(control => { control.disabled = true; });
  output.textContent = '正在核对本地密钥、部署公钥并验证登录…';
  output.hidden = false;
  try {
    const result = await api.configure_ssh_key(serverId, {
      mode,
      private_key_path: privatePath,
      public_key_path: publicPath,
    });
    renderSshKeySetupResult(output, result);
    if (result?.ok && result.profile) {
      acceptProfile(result.profile);
      const saved = currentProfile.servers.find(server => server.id === serverId);
      if (saved) {
        editor.querySelector('[data-field="identity_file"]').value = saved.identity_file || '';
        editor.querySelector('[data-key-field="private_key_path"]').value = saved.identity_file || '';
        editor.dataset.preferIdentityAuth = String(Boolean(saved.prefer_identity_auth));
      }
      syncServerDraftFromEditor(editor);
      editor.refreshPasswordState?.();
      editor.querySelector('[data-key-setup-overview]').textContent = '已验证；SSH Key 优先，保存的密码仅作回退';
      showToast('SSH 免密登录已配置并验证');
    }
  } catch (error) {
    renderSshKeySetupResult(output, {ok: false, error: error.message || String(error), stages: []});
  } finally {
    controls.forEach(control => { control.disabled = false; });
  }
}

async function testServerEditorConnection(editor) {
  const serverId = editor.dataset.originalId || '';
  const output = editor.querySelector('.connection-test-result');
  const button = editor.querySelector('.test-server-connection');
  if (!serverId || !api?.test_connection) {
    output.textContent = '请先保存这台服务器，再测试连接。';
    output.hidden = false;
    return;
  }
  const savedServer = currentProfile?.servers?.find(server => server.id === serverId);
  if (serverEditorHasUnsavedConnectionChanges(editor, savedServer)) {
    output.textContent = '连接信息有尚未保存的修改，请先保存后再验证当前配置。';
    output.hidden = false;
    return;
  }
  button.disabled = true;
  output.textContent = '正在测试已保存的连接配置…';
  output.hidden = false;
  try {
    const result = await api.test_connection(serverId);
    if (result?.profile) acceptProfile(result.profile);
    const stages = (result?.stages || []).slice(0, 8);
    output.innerHTML = `<strong>${result?.ok ? '连接测试通过' : '连接测试未通过'}</strong>${stages.length ? `<ol>${stages.map(stage => `<li class="${escapeHtml(stage.state || '')}"><span>${escapeHtml(stage.label || stage.id)}</span><small>${escapeHtml(stage.message || '')}</small></li>`).join('')}</ol>` : `<span>${escapeHtml(result?.error || '')}</span>`}`;
  } catch (error) {
    output.textContent = error.message || String(error);
  } finally {
    button.disabled = false;
  }
}

function refreshEditorEnabledState(editor) {
  const enabled = editor.dataset.enabled !== 'false';
  const button = editor.querySelector('.toggle-server-enabled');
  button.setAttribute('aria-pressed', String(enabled));
  button.textContent = enabled ? '暂停监控' : '恢复监控';
  editor.classList.toggle('paused', !enabled);
}

async function toggleEditorEnabled(editor) {
  const enabled = editor.dataset.enabled !== 'false';
  const serverId = editor.dataset.originalId || '';
  if (serverId && api?.set_server_enabled) {
    await setServerEnabled(serverId, !enabled);
    const saved = currentProfile?.servers?.find(server => server.id === serverId);
    editor.dataset.enabled = String(saved?.enabled !== false);
  } else {
    editor.dataset.enabled = String(!enabled);
  }
  clearConnectionTestResult(editor);
  refreshEditorEnabledState(editor);
  syncServerDraftFromEditor(editor);
}

function addServerEditor(server = {}, options = {}) {
  const fragment = ui.editorTemplate.content.cloneNode(true);
  const editor = fragment.querySelector('.server-editor');
  const target = options.target || ui.editorList;
  const usedIds = options.usedIds || new Set(
    [...ui.editorList.querySelectorAll('[data-field="id"]')].map(input => input.value.trim()),
  );
  let generatedIndex = 1;
  while (usedIds.has(`server-${generatedIndex}`)) generatedIndex += 1;
  const defaults = {id: `server-${generatedIndex}`, display_name: '', backend: 'auto', auto_detect_backend: true, ssh_alias: '', host: '', port: 22, port_override: false, username: '', identity_file: '', ssh_config_file: '', has_password: false, show_other_user_commands: true, prefer_identity_auth: false, connect_timeout_seconds: 10};
  const values = {...defaults, ...server};
  usedIds.add(values.id);
  editor.dataset.draftIndex = String(options.draftIndex ?? settingsServerDrafts.length);
  editor.dataset.gpuMemoryGib = JSON.stringify(values.gpu_memory_gib || {});
  editor.dataset.enabled = String(values.enabled !== false);
  editor.dataset.hasPassword = String(Boolean(values.has_password));
  editor.dataset.originalId = values._original_id ?? values.id ?? '';
  editor.dataset.originalSshAlias = values._original_ssh_alias ?? values.ssh_alias ?? '';
  editor.dataset.importedCandidate = String(values._imported_candidate === true || options.importedCandidate === true);
  editor.dataset.sshConfigFile = values.ssh_config_file || '';
  editor.dataset.preferIdentityAuth = String(Boolean(values.prefer_identity_auth));
  editor.querySelectorAll('[data-field]').forEach(input => {
    if (input.type === 'checkbox') input.checked = Boolean(values[input.dataset.field]);
    else input.value = values[input.dataset.field] ?? '';
    input.addEventListener('input', () => {
      clearConnectionTestResult(editor);
      syncServerDraftFromEditor(editor);
    });
    input.addEventListener('change', () => {
      clearConnectionTestResult(editor);
      syncServerDraftFromEditor(editor);
    });
  });
  editor.querySelector('[data-field="port"]').addEventListener('input', () => {
    editor.querySelector('[data-field="port_override"]').checked = true;
    syncServerDraftFromEditor(editor);
  });
  const passwordInput = editor.querySelector('[data-field="password"]');
  const clearControl = editor.querySelector('[data-field="clear_password"]');
  passwordInput.value = values._password || '';
  clearControl.checked = Boolean(values._clear_password);
  const passwordStatus = editor.querySelector('[data-password-status]');
  const authOverview = editor.querySelector('[data-auth-overview]');
  const refreshPasswordState = () => {
    const replacing = Boolean(passwordInput.value);
    const keyPreferred = editor.dataset.preferIdentityAuth === 'true' && Boolean(editor.querySelector('[data-field="identity_file"]').value.trim());
    clearControl.disabled = replacing || editor.dataset.hasPassword !== 'true';
    if (replacing) {
      passwordStatus.textContent = '保存后将更新系统凭据';
      authOverview.textContent = keyPreferred ? 'SSH Key 优先，新密码仅作本地回退' : '将使用系统凭据中保存的新密码';
    } else if (clearControl.checked) {
      passwordStatus.textContent = '保存后将删除系统凭据';
      authOverview.textContent = '保存后恢复 ssh-agent 或私钥登录';
    } else if (editor.dataset.hasPassword === 'true') {
      passwordStatus.textContent = '系统凭据中已有密码';
      authOverview.textContent = keyPreferred ? 'SSH Key 优先，密码仅在认证失败时回退' : '登录密码已安全保存在系统凭据库';
    } else {
      passwordStatus.textContent = '未保存密码';
      authOverview.textContent = '默认使用 OpenSSH、ssh-agent 或私钥';
    }
  };
  editor.refreshPasswordState = refreshPasswordState;
  passwordInput.addEventListener('input', refreshPasswordState);
  clearControl.addEventListener('change', refreshPasswordState);
  refreshPasswordState();
  const displayName = editor.querySelector('[data-field="display_name"]');
  const editorName = editor.querySelector('[data-server-editor-name]');
  const refreshEditorName = () => {
    editorName.textContent = displayName.value.trim() || editor.querySelector('[data-field="ssh_alias"]').value.trim() || '新服务器';
  };
  displayName.addEventListener('input', refreshEditorName);
  editor.querySelector('[data-field="ssh_alias"]').addEventListener('input', refreshEditorName);
  refreshEditorName();
  const keyModeName = `ssh-key-mode-${++serverEditorSequence}`;
  editor.querySelectorAll('[data-key-mode]').forEach(input => { input.name = keyModeName; });
  const keyPrivatePath = editor.querySelector('[data-key-field="private_key_path"]');
  const keyPublicPath = editor.querySelector('[data-key-field="public_key_path"]');
  keyPrivatePath.value = values.identity_file || '';
  const refreshKeyMode = () => {
    const existing = editor.querySelector('[data-key-mode="existing"]').checked;
    editor.querySelector('.existing-key-fields').hidden = !existing;
    editor.querySelector('[data-key-setup-overview]').textContent = existing
      ? '复用现有密钥，部署后自动验证'
      : '生成独立密钥，部署后自动验证';
    clearSshKeySetupResult(editor);
  };
  editor.querySelectorAll('[data-key-mode]').forEach(input => input.addEventListener('change', refreshKeyMode));
  keyPrivatePath.addEventListener('input', () => clearSshKeySetupResult(editor));
  keyPublicPath.addEventListener('input', () => clearSshKeySetupResult(editor));
  editor.querySelector('.configure-ssh-key').addEventListener('click', () => void configureServerSshKey(editor));
  refreshKeyMode();
  const refreshCapabilities = () => {
    const backend = editor.querySelector('[data-field="backend"]').value;
    const slurm = backend === 'slurm_ssh' || (backend === 'auto' && values._detected_backend === 'slurm_ssh');
    editor.querySelector('[data-command-summary-help]').textContent = slurm
      ? 'Slurm：显示其他用户的作业名、状态与时间；调度器视图不读取完整 shell 命令。'
      : 'SSH 直连：显示其他用户的 GPU 进程与经本地遮盖、限长的命令摘要。';
  };
  editor.querySelector('[data-field="backend"]').addEventListener('change', refreshCapabilities);
  editor.querySelector('.test-server-connection').addEventListener('click', () => void testServerEditorConnection(editor));
  editor.querySelector('.toggle-server-enabled').addEventListener('click', () => void toggleEditorEnabled(editor));
  setupServerEditorDrag(editor);
  editor.querySelector('.remove-server').addEventListener('click', () => {
    syncVisibleServerDrafts();
    const wasSaved = (currentProfile?.servers || []).some(item => item.id === editor.dataset.originalId);
    const wasImportedCandidate = editor.dataset.importedCandidate === 'true';
    if (wasSaved || wasImportedCandidate) rememberIgnoredSshAlias(editor.dataset.originalSshAlias);
    const draftIndex = serverDraftIndexFromEditor(editor);
    if (draftIndex < 0) return;
    settingsServerDrafts.splice(draftIndex, 1);
    renderServerEditorPage();
  });
  target.appendChild(fragment);
  refreshEditorEnabledState(editor);
  refreshCapabilities();
  if (!options.deferOrder) refreshServerEditorOrder();
  return editor;
}

function populateServerEditors(servers, options = {}) {
  settingsServerDrafts = (servers.length ? servers : (options.allowEmpty ? [] : [{}]))
    .map(server => serverDraftFromValue(server, options));
  settingsServerQuery = '';
  settingsServerPageOffset = 0;
  ui.editorSearch.value = '';
  renderServerEditorPage();
}

function renderServerEditorPage(options = {}) {
  ui.editorList.replaceChildren();
  const indices = filteredServerDraftIndices();
  const maximumOffset = Math.max(0, Math.floor(Math.max(0, indices.length - 1) / SERVER_EDITOR_PAGE_SIZE) * SERVER_EDITOR_PAGE_SIZE);
  settingsServerPageOffset = Math.min(settingsServerPageOffset, maximumOffset);
  const visibleIndices = indices.slice(settingsServerPageOffset, settingsServerPageOffset + SERVER_EDITOR_PAGE_SIZE);
  const target = document.createDocumentFragment();
  const usedIds = new Set(settingsServerDrafts.map(server => String(server.id || '').trim()).filter(Boolean));
  visibleIndices.forEach(draftIndex => addServerEditor(settingsServerDrafts[draftIndex], {
    target,
    usedIds,
    draftIndex,
    deferOrder: true,
  }));
  ui.editorList.appendChild(target);
  ui.editorToolbar.hidden = (settingsMode === 'onboarding' && onboardingStep !== 3)
    || (settingsServerDrafts.length <= SERVER_EDITOR_PAGE_SIZE && !settingsServerQuery);
  const first = indices.length ? settingsServerPageOffset + 1 : 0;
  const last = Math.min(settingsServerPageOffset + SERVER_EDITOR_PAGE_SIZE, indices.length);
  ui.editorPageStatus.textContent = settingsServerQuery
    ? `匹配 ${number(indices.length)} 台 · ${number(first)}–${number(last)}`
    : `${number(first)}–${number(last)} / ${number(settingsServerDrafts.length)}`;
  ui.editorPreviousPage.disabled = settingsServerPageOffset === 0;
  ui.editorNextPage.disabled = settingsServerPageOffset + SERVER_EDITOR_PAGE_SIZE >= indices.length;
  refreshServerEditorOrder();
  if (Number.isInteger(options.focusDraftIndex)) {
    const editor = ui.editorList.querySelector(`[data-draft-index="${options.focusDraftIndex}"]`);
    if (editor && !options.focusDragHandle) editor.open = true;
    editor?.scrollIntoView({behavior: 'smooth', block: 'center'});
    editor?.querySelector(options.focusDragHandle ? '.server-drag-handle' : '[data-field="display_name"]')?.focus({preventScroll: true});
  }
}

function invalidServerDraft() {
  syncVisibleServerDrafts();
  const seenIds = new Set();
  for (let index = 0; index < settingsServerDrafts.length; index += 1) {
    const draft = settingsServerDrafts[index];
    if (!String(draft.display_name || '').trim()) return {index, field: 'display_name', message: '显示名称不能为空'};
    const serverId = String(draft.id || '').trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(serverId)) {
      return {index, field: 'id', message: '服务器 ID 格式不正确'};
    }
    const serverIdKey = serverId.toLowerCase();
    if (seenIds.has(serverIdKey)) return {index, field: 'id', message: '服务器 ID 与前面的服务器重复（不区分大小写）'};
    seenIds.add(serverIdKey);
    if (!String(draft.ssh_alias || '').trim() && !String(draft.host || '').trim()) {
      return {index, field: 'ssh_alias', message: 'OpenSSH 别名与主机地址至少填写一个'};
    }
    const port = Number(draft.port || 22);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return {index, field: 'port', message: '端口必须在 1 到 65535 之间'};
  }
  return null;
}

function revealInvalidServerDraft(problem) {
  settingsServerQuery = '';
  ui.editorSearch.value = '';
  settingsServerPageOffset = Math.floor(problem.index / SERVER_EDITOR_PAGE_SIZE) * SERVER_EDITOR_PAGE_SIZE;
  renderServerEditorPage({focusDraftIndex: problem.index});
  const editor = ui.editorList.querySelector(`[data-draft-index="${problem.index}"]`);
  const input = editor?.querySelector(`[data-field="${problem.field}"]`);
  if (input?.closest('.server-editor-more')) input.closest('.server-editor-more').open = true;
  input?.focus({preventScroll: true});
}

function addAndFocusServerEditor() {
  syncVisibleServerDrafts();
  const usedIds = new Set(settingsServerDrafts.map(server => String(server.id || '').trim()));
  let generatedIndex = 1;
  while (usedIds.has(`server-${generatedIndex}`)) generatedIndex += 1;
  const draftIndex = settingsServerDrafts.length;
  settingsServerDrafts.push(serverDraftFromValue({id: `server-${generatedIndex}`}));
  settingsServerQuery = '';
  ui.editorSearch.value = '';
  settingsServerPageOffset = Math.floor(draftIndex / SERVER_EDITOR_PAGE_SIZE) * SERVER_EDITOR_PAGE_SIZE;
  renderServerEditorPage({focusDraftIndex: draftIndex});
}

function setDetailsOpen(details, open) {
  details.forEach(detail => {
    if (detail.open === open) return;
    detail.dataset.bulkDisclosure = 'true';
    detail.open = open;
  });
}

function applyDashboardDisclosureMode() {
  if (dashboardDisclosureMode === 'default' || ui.dashboardContent.hidden) return;
  setDetailsOpen(ui.dashboardContent.querySelectorAll('details'), false);
}

function collapseDashboardDisclosure() {
  dashboardDisclosureMode = 'collapsed';
  openClusters.clear();
  openTaskGroups.clear();
  openDirectoryNodes.clear();
  openContextNotes.clear();
  directoryTrees.forEach((_state, serverId) => repaintDirectory(serverId));
  applyDashboardDisclosureMode();
  showToast('已一键收起当前页面的全部内容');
}

function collapseSettingsDisclosure() {
  const visibleDetails = [...ui.dialog.querySelectorAll('.dialog-content details')]
    .filter(detail => !detail.closest('[hidden]'));
  setDetailsOpen(visibleDetails, false);
  showToast('已收起设置中的全部说明与高级选项');
}

function prepareSettingsDisclosure() {
  const details = [...ui.dialog.querySelectorAll('.dialog-content details')]
    .filter(detail => !detail.closest('[hidden]'));
  setDetailsOpen(details, false);
  setDetailsOpen(details.filter(detail => detail.hasAttribute('data-default-open')), true);
}

function invalidateServerDiscovery() {
  serverDiscoveryGeneration += 1;
  ui.discoverServerConfig.disabled = false;
}

function setOnboardingStep(step) {
  const nextStep = Math.max(1, Math.min(3, Number(step) || 1));
  const onboarding = settingsMode === 'onboarding';
  if (onboarding && onboardingStep === 2 && nextStep !== 2) {
    invalidateServerDiscovery();
    onboardingDiscoveryStarted = false;
  }
  onboardingStep = nextStep;
  ui.onboardingProgress.hidden = !onboarding;
  ui.onboardingWelcome.hidden = !onboarding || onboardingStep !== 1;
  ui.importPanel.hidden = onboarding && onboardingStep !== 2;
  ui.profileSettings.hidden = onboarding && onboardingStep !== 3;
  ui.serverSettingsHeading.hidden = onboarding && onboardingStep !== 3;
  ui.editorList.hidden = onboarding && onboardingStep !== 3;
  ui.editorToolbar.hidden = (onboarding && onboardingStep !== 3)
    || (settingsServerDrafts.length <= SERVER_EDITOR_PAGE_SIZE && !settingsServerQuery);
  ui.onboardingBack.hidden = !onboarding || onboardingStep === 1;
  ui.onboardingNext.hidden = !onboarding || onboardingStep === 3;
  ui.onboardingLater.hidden = !onboarding;
  ui.saveSettings.hidden = onboarding && onboardingStep !== 3;
  ui.settingsDisclosureTools.hidden = onboarding && onboardingStep === 1;
  ui.onboardingNext.textContent = onboardingStep === 1 ? '开始设置' : '手动填写或检查';
  if (onboarding && onboardingStep === 2) ui.importPanel.open = true;
  if (onboarding && onboardingStep === 3) {
    ui.profileSettings.open = true;
    const firstEditor = ui.editorList.querySelector('.server-editor');
    if (firstEditor) firstEditor.open = true;
  }
  [...ui.onboardingProgress.querySelectorAll('[data-onboarding-marker]')].forEach(marker => {
    const markerStep = Number(marker.dataset.onboardingMarker);
    marker.classList.toggle('current', markerStep === onboardingStep);
    marker.classList.toggle('complete', markerStep < onboardingStep);
    if (markerStep === onboardingStep) marker.setAttribute('aria-current', 'step');
    else marker.removeAttribute('aria-current');
  });
  ui.dialog.querySelector('.dialog-content').scrollTop = 0;
  if (onboarding && onboardingStep === 2 && !onboardingDiscoveryStarted) {
    onboardingDiscoveryStarted = true;
    void discoverServerConfig();
  }
}

function setSettingsMode(mode) {
  settingsMode = mode;
  const onboarding = mode === 'onboarding';
  ui.dialog.classList.toggle('onboarding-mode', onboarding);
  ui.dialogKicker.textContent = onboarding ? '首次使用引导' : '设置与服务器';
  ui.dialogTitle.textContent = onboarding ? '欢迎使用显存雷达' : '本地配置';
  ui.dialogDescription.textContent = onboarding
    ? '按三个简单步骤完成第一台服务器配置，详细教程随时可以展开。'
    : '先用自动发现完成常见设置，需要时再展开登录与高级选项。';
  ui.settingsDisclosureTools.hidden = onboarding;
  ui.closeSettings.hidden = onboarding;
  ui.cancelSettings.hidden = onboarding;
  if (onboarding) {
    onboardingDiscoveryStarted = false;
    setOnboardingStep(1);
  } else {
    ui.onboardingProgress.hidden = true;
    ui.onboardingWelcome.hidden = true;
    ui.profileSettings.hidden = false;
    ui.importPanel.hidden = false;
    ui.serverSettingsHeading.hidden = false;
    ui.editorList.hidden = false;
    ui.editorToolbar.hidden = settingsServerDrafts.length <= SERVER_EDITOR_PAGE_SIZE && !settingsServerQuery;
    ui.onboardingLater.hidden = true;
    ui.onboardingBack.hidden = true;
    ui.onboardingNext.hidden = true;
    ui.saveSettings.hidden = false;
    prepareSettingsDisclosure();
  }
}

function openSettings(options = {}) {
  const onboarding = options?.onboarding === true || (!currentProfile?.servers?.length && options?.forceNormal !== true);
  ui.settingsError.hidden = true;
  ui.profileName.value = currentProfile?.display_name || '我的 GPU';
  ui.refreshSeconds.value = currentProfile?.refresh_seconds || 15;
  ui.language.value = currentProfile?.ui_language === 'en' ? 'en' : 'zh-CN';
  ui.closeBehavior.value = currentProfile?.close_behavior === 'exit' ? 'exit' : 'tray';
  ui.favoriteAlertEnabled.checked = currentProfile?.favorite_alert_enabled !== false;
  ui.favoriteAlertMinMemory.value = Number(currentProfile?.favorite_alert_min_memory_gib) > 0
    ? String(currentProfile.favorite_alert_min_memory_gib)
    : '';
  ui.favoriteAlertMinMemory.disabled = !ui.favoriteAlertEnabled.checked;
  ui.taskCompletionAlertEnabled.checked = currentProfile?.task_completion_alert_enabled !== false;
  renderTaskCompletionWatchList();
  ui.serverConfigPath.value = currentProfile?.server_config_path || '';
  ui.autoSyncServers.checked = Boolean(currentProfile?.auto_sync_servers);
  pendingIgnoredSshAliases = new Set(currentProfile?.ignored_ssh_aliases || []);
  ui.importStatus.hidden = true;
  populateServerEditors(currentProfile?.servers || []);
  setSettingsMode(onboarding ? 'onboarding' : 'settings');
  ui.dialog.showModal();
}

async function discoverServerConfig() {
  const generation = ++serverDiscoveryGeneration;
  const onboardingRequest = settingsMode === 'onboarding';
  const requestIsCurrent = () => generation === serverDiscoveryGeneration && (
    !onboardingRequest || (settingsMode === 'onboarding' && onboardingStep === 2 && ui.dialog.open)
  );
  ui.discoverServerConfig.disabled = true;
  ui.importStatus.hidden = false;
  ui.importStatus.textContent = '正在本地查找用户、系统、编辑器和便携式 SSH 配置…';
  try {
    const result = await api.discover_server_config();
    if (!requestIsCurrent()) return;
    if (!result.ok) {
      ui.importStatus.textContent = result.message;
      return;
    }
    const imported = await api.import_server_config(result.paths || [result.path]);
    if (!requestIsCurrent()) return;
    if (!imported.ok) throw new Error(imported.error);
    applyImportedServerConfig(imported);
  } catch (error) {
    if (requestIsCurrent()) ui.importStatus.textContent = error.message || String(error);
  } finally {
    if (generation === serverDiscoveryGeneration) ui.discoverServerConfig.disabled = false;
  }
}

function applyImportedServerConfig(result) {
  const paths = result.paths || (result.path ? [result.path] : []);
  ui.serverConfigPath.value = paths.length === 1 ? paths[0] : '';
  ui.autoSyncServers.checked = Boolean(result.auto_sync && paths.length === 1);
  const ignoredAliasKeys = new Set([...pendingIgnoredSshAliases].map(sshAliasKey));
  const visibleCandidates = result.servers.filter(server => !ignoredAliasKeys.has(sshAliasKey(server.ssh_alias)));
  populateServerEditors(visibleCandidates, {importedCandidate: true, allowEmpty: true});
  const pendingRemovalCount = result.servers.length - visibleCandidates.length;
  const warning = result.warnings.length ? `；${result.warnings.join('；')}` : '';
  const sourceSummary = paths.length > 1 ? `（合并 ${paths.length} 个来源）` : '';
  const syncSummary = paths.length > 1 ? '；多来源导入不会绑定单一文件自动同步' : '';
  const removalSummary = pendingRemovalCount ? `；已保留 ${pendingRemovalCount} 台本次移除项` : '';
  ui.importStatus.textContent = `已解析 ${visibleCandidates.length} 台服务器候选${sourceSummary}；尚未保存，尚未连接验证${removalSummary}${syncSummary}${warning}`;
  if (settingsMode === 'onboarding' && visibleCandidates.length) setOnboardingStep(3);
}

async function importServerConfig() {
  ui.importServerConfig.disabled = true;
  ui.importStatus.hidden = false;
  ui.importStatus.textContent = '正在读取并解析本地配置…';
  try {
    const result = await api.import_server_config(ui.serverConfigPath.value.trim());
    if (!result.ok) throw new Error(result.error);
    applyImportedServerConfig(result);
  } catch (error) {
    ui.importStatus.textContent = error.message || String(error);
  } finally {
    ui.importServerConfig.disabled = false;
  }
}

function collectProfile() {
  syncVisibleServerDrafts();
  const servers = settingsServerDrafts.map(draft => {
    const value = field => String(draft[field] ?? '').trim();
    const existingId = draft._original_id || value('id');
    const existing = currentProfile?.servers?.find(item => item.id === existingId);
    const selectedBackend = value('backend');
    const server = {
      id: value('id'), display_name: value('display_name'), backend: selectedBackend === 'auto' ? (draft._detected_backend || existing?.backend || 'direct_ssh') : selectedBackend, ssh_alias: value('ssh_alias'),
      host: value('host'), port: Number(value('port') || 22), username: value('username'), identity_file: value('identity_file'),
      ssh_config_file: value('ssh_config_file'),
      port_override: Boolean(draft.port_override),
      enabled: draft.enabled !== false,
      connect_timeout_seconds: Number(existing?.connect_timeout_seconds || 10),
      show_other_user_commands: Boolean(draft.show_other_user_commands),
      auto_detect_backend: selectedBackend === 'auto',
    };
    if (draft.default_work_directory || existing?.default_work_directory) server.default_work_directory = draft.default_work_directory || existing.default_work_directory;
    if (draft.prefer_identity_auth || existing?.prefer_identity_auth) server.prefer_identity_auth = true;
    if (server.backend === 'slurm_ssh') server.gpu_memory_gib = draft.gpu_memory_gib || {};
    return server;
  });
  const activeAliasKeys = new Set(servers.map(server => sshAliasKey(server.ssh_alias)).filter(Boolean));
  const ignoredSshAliases = [...pendingIgnoredSshAliases]
    .filter(alias => !activeAliasKeys.has(sshAliasKey(alias)));
  return {
    schema_version: 1,
    profile_revision: currentProfile.profile_revision,
    id: currentProfile.id,
    display_name: ui.profileName.value.trim(),
    refresh_seconds: Number(ui.refreshSeconds.value),
    server_config_path: ui.serverConfigPath.value.trim(),
    auto_sync_servers: ui.autoSyncServers.checked,
    ignored_ssh_aliases: ignoredSshAliases,
    navigator_side: serverNavigatorSide,
    close_behavior: ui.closeBehavior.value,
    ui_language: ui.language.value,
    favorite_alert_enabled: ui.favoriteAlertEnabled.checked,
    favorite_alert_min_memory_gib: Number(ui.favoriteAlertMinMemory.value || 0),
    task_completion_alert_enabled: ui.taskCompletionAlertEnabled.checked,
    task_completion_watches: (currentProfile.task_completion_watches || []).map(item => ({...item})),
    servers,
  };
}

function collectPasswordUpdates() {
  syncVisibleServerDrafts();
  const updates = {};
  settingsServerDrafts.forEach(draft => {
    const serverId = String(draft.id || '').trim();
    const enteredValue = draft._password || '';
    const clearRequested = Boolean(draft._clear_password);
    if (enteredValue) updates[serverId] = enteredValue;
    else if (clearRequested) updates[serverId] = null;
  });
  return updates;
}

function collectServerRenames() {
  syncVisibleServerDrafts();
  const renames = {};
  settingsServerDrafts.forEach(draft => {
    const oldId = draft._original_id || '';
    const newId = String(draft.id || '').trim();
    if (oldId && newId && oldId !== newId) renames[newId] = oldId;
  });
  return renames;
}

function directoryConnectionSignature(server) {
  if (!server) return '';
  return JSON.stringify([
    server.id,
    server.backend,
    Boolean(server.auto_detect_backend),
    server.ssh_alias,
    server.host,
    Number(server.port || 22),
    Boolean(server.port_override),
    server.username,
    server.identity_file,
    server.ssh_config_file,
    server.default_work_directory,
  ]);
}

function invalidateChangedServerCaches(previousProfile, nextProfile) {
  const previous = new Map((previousProfile?.servers || []).map(server => [server.id, server]));
  const next = new Map((nextProfile?.servers || []).map(server => [server.id, server]));
  const changedIds = new Set();
  previous.forEach((server, serverId) => {
    if (directoryConnectionSignature(server) !== directoryConnectionSignature(next.get(serverId))) {
      changedIds.add(serverId);
    }
  });
  next.forEach((server, serverId) => {
    if (!previous.has(serverId)) changedIds.add(serverId);
  });
  changedIds.forEach(serverId => {
    invalidateDirectoryRequests(serverId);
    directoryTrees.delete(serverId);
    clusterNodePages.delete(serverId);
    clusterNodeRequestGenerations.delete(serverId);
    renderedServerCardSignatures.delete(serverId);
    [...openDirectoryNodes].forEach(key => {
      if (key.startsWith(`${serverId}:`)) openDirectoryNodes.delete(key);
    });
  });
}

async function saveSettings(event) {
  event.preventDefault();
  if (settingsSaveInFlight) return;
  settingsSaveInFlight = true;
  ui.settingsError.hidden = true;
  const controls = [...ui.form.querySelectorAll('button, input, select')];
  const disabledStates = controls.map(control => control.disabled);
  controls.forEach(control => { control.disabled = true; });
  try {
    const previousProfile = currentProfile;
    const invalidDraft = invalidServerDraft();
    if (invalidDraft) {
      revealInvalidServerDraft(invalidDraft);
      throw new Error(`第 ${invalidDraft.index + 1} 台服务器：${invalidDraft.message}`);
    }
    const proposedProfile = collectProfile();
    const result = await api.save_profile(proposedProfile, collectPasswordUpdates(), collectServerRenames());
    if (!result.ok) {
      if (result.code === 'profile_changed') {
        const latest = result.profile || await api.get_profile();
        acceptProfile(latest);
        throw new Error('配置已在其他操作中更新；最新版本已载入，当前表单未覆盖，请核对后重新保存。');
      }
      throw new Error(result.error);
    }
    invalidateChangedServerCaches(previousProfile, result.profile);
    acceptProfile(result.profile);
    renderSavedViews();
    ui.dialog.close();
    const syncWarning = (result.warnings || [])[0];
    showToast(syncWarning ? `配置已保存并同步；${syncWarning}` : '配置已保存，正在连接服务器');
    const automaticServers = (currentProfile.servers || []).filter(
      server => server.enabled !== false && server.auto_detect_backend,
    );
    if (automaticServers.length && api?.test_connection) {
      let needsManualReview = 0;
      showToast(`配置已保存，正在自动识别 ${automaticServers.length} 台服务器`);
      for (const server of automaticServers) {
        const validation = await api.test_connection(server.id);
        if (validation?.profile) acceptProfile(validation.profile);
        if (!validation?.ok) needsManualReview += 1;
      }
      if (needsManualReview) showToast(`${needsManualReview} 台服务器未能自动识别，请在设置中手动确认连接类型或私钥路径`);
      else showToast('服务器连接类型已自动验证');
    }
    scheduleRefresh();
    await refresh(true);
  } catch (error) {
    ui.settingsError.textContent = error.message || String(error);
    ui.settingsError.hidden = false;
  } finally {
    controls.forEach((control, index) => { control.disabled = disabledStates[index]; });
    settingsSaveInFlight = false;
  }
}

async function loadApplication() {
  acceptProfile(await api.get_profile());
  renderSavedViews();
  applyServerNavigatorSide(currentProfile.navigator_side);
  const hasServers = currentProfile.servers.length > 0;
  ui.firstRunHome.hidden = hasServers;
  ui.dashboardContent.hidden = !hasServers;
  ui.refresh.hidden = !hasServers;
  ui.refreshClock.hidden = !hasServers;
  if (hasServers) {
    ui.list.innerHTML = '<div class="loading-skeleton" aria-label="正在读取服务器"><div class="skeleton-line"></div><div class="skeleton-line"></div></div>';
  }
  scheduleRefresh();
  await refresh(false);
  if (!hasServers) openSettings({onboarding: true});
}

function scheduleUpdateCheck(delayMilliseconds) {
  clearTimeout(updateCheckTimer);
  updateCheckTimer = setTimeout(() => {
    void checkForUpdates();
  }, delayMilliseconds);
}

function showUpdateCheckFailure(message = '暂时无法连接 GitHub', interactive = false) {
  if (!updateAvailableShown) {
    ui.updateNotice.replaceChildren();
    ui.updateNotice.hidden = true;
  }
  if (interactive) {
    ui.updateCheckStatus.textContent = '检查失败，可稍后重试；服务器监控不受影响';
    showToast(message || '暂时无法检查更新');
  }
}

async function checkForUpdates({interactive = false} = {}) {
  if (!api?.check_for_updates) return;
  if (updateCheckInFlight) {
    if (interactive) showToast('正在检查更新');
    return;
  }
  updateCheckInFlight = true;
  if (interactive) {
    ui.checkForUpdates.disabled = true;
    ui.updateCheckStatus.textContent = '正在检查更新…';
  }
  lastUpdateCheckAt = Date.now();
  try {
    const result = await api.check_for_updates();
    if (!result?.ok) {
      showUpdateCheckFailure(result?.error, interactive);
      scheduleUpdateCheck(UPDATE_CHECK_RETRY_MS);
      return;
    }
    if (!result.update_available) {
      updateAvailableShown = false;
      ui.updateNotice.classList.remove('update-check-failed');
      ui.updateNotice.hidden = true;
      if (interactive) showToast(`当前已是最新版本 ${result.current_version}`);
      if (interactive) {
        ui.updateCheckStatus.textContent = `已是最新版本 ${result.current_version}`;
        window.setTimeout(() => {
          if (!updateCheckInFlight) ui.updateCheckStatus.textContent = '无更新时保持静默，仅在发现新版本后显示顶部提示';
        }, 4000);
      }
      scheduleUpdateCheck(UPDATE_CHECK_INTERVAL_MS);
      return;
    }
    updateAvailableShown = true;
    latestUpdateAction = result.update_action || 'browser';
    ui.updateNotice.classList.remove('update-check-failed');
    const actionLabel = latestUpdateAction === 'one_click'
      ? '安全一键更新'
      : latestUpdateAction === 'verified_download' ? '下载并校验' : '下载更新';
    const actionCopy = latestUpdateAction === 'one_click'
      ? '确认后将下载官方安装包、校验 SHA-256，安装成功后自动重启。'
      : latestUpdateAction === 'verified_download'
        ? '更新包会先校验 SHA-256，再在 Finder 中显示。'
        : '当前版本需要从 GitHub Release 手动安装。';
    const releaseTitle = result.replacement_available
      ? `发现 VRAM Radar ${escapeHtml(result.latest_version)} 的修复构建`
      : `发现 VRAM Radar ${escapeHtml(result.latest_version)}`;
    ui.updateNotice.innerHTML = `<div><div class="notice-title">${releaseTitle}</div><div class="notice-copy">当前版本 ${escapeHtml(result.current_version)}。${actionCopy}</div></div><button class="button primary install-latest-update" type="button">${actionLabel}</button>`;
    ui.updateNotice.hidden = false;
    if (interactive) ui.updateCheckStatus.textContent = `发现新版本 ${result.latest_version}`;
    scheduleUpdateCheck(UPDATE_CHECK_INTERVAL_MS);
  } catch (error) {
    showUpdateCheckFailure(error?.message, interactive);
    scheduleUpdateCheck(UPDATE_CHECK_RETRY_MS);
  } finally {
    updateCheckInFlight = false;
    if (interactive) ui.checkForUpdates.disabled = false;
  }
}

async function installLatestUpdate(button) {
  if (latestUpdateAction === 'browser') {
    await api.open_latest_release();
    return;
  }
  const explanation = latestUpdateAction === 'one_click'
    ? '将从官方 GitHub Release 下载并校验安装包。校验成功后应用会关闭、安装并自动重启；失败时保留当前版本。是否继续？'
    : '将从官方 GitHub Release 下载并校验更新包。校验成功后会在 Finder 中显示，仍需你手动替换应用。是否继续？';
  if (!window.confirm(explanation)) return;
  button.disabled = true;
  const previousLabel = button.textContent;
  button.textContent = '正在下载并校验…';
  try {
    const result = await api.install_latest_update();
    showToast(result?.message || result?.error || '更新操作未完成');
    if (!result?.ok) button.disabled = false;
  } catch (error) {
    showToast(error?.message || '更新失败，当前版本未被修改');
    button.disabled = false;
  } finally {
    if (!button.disabled) button.textContent = previousLabel;
  }
}

async function toggleTaskCompletionWatch(button) {
  if (!api?.set_task_completion_watch) return;
  const nextWatched = button.classList.contains('remove-task-watch')
    ? false
    : button.getAttribute('aria-pressed') !== 'true';
  button.disabled = true;
  try {
    const result = await api.set_task_completion_watch(
      button.dataset.serverId,
      button.dataset.taskKey,
      button.dataset.taskKind,
      button.dataset.taskId,
      button.dataset.taskLabel,
      nextWatched,
      button.dataset.taskOwner || '',
      button.dataset.taskOwnerScope || 'unknown',
    );
    if (!result?.ok) throw new Error(result?.error || '提醒设置保存失败');
    acceptProfile(result.profile);
    renderTaskCompletionWatchList();
    if (currentSnapshot) render(currentSnapshot);
    showToast(nextWatched ? '已单独关注该任务，结束时会提醒' : '已取消该任务的单独提醒');
  } catch (error) {
    showToast(error.message || String(error));
    button.disabled = false;
  }
}

async function clearTaskCompletionWatches(button) {
  if (!api?.clear_task_completion_watches) return;
  button.disabled = true;
  try {
    const result = await api.clear_task_completion_watches();
    if (!result?.ok) throw new Error(result?.error || '无法移除关注任务');
    acceptProfile(result.profile);
    renderTaskCompletionWatchList();
    if (currentSnapshot) render(currentSnapshot);
    showToast('已移除全部单独关注任务');
  } catch (error) {
    showToast(error.message || String(error));
    button.disabled = false;
  }
}

async function markAllNotificationsRead() {
  if (!currentSnapshot?.notifications) return;
  if (api?.mark_notifications_read) await api.mark_notifications_read();
  else if (api?.mark_task_completion_alerts_read) await api.mark_task_completion_alerts_read();
  currentSnapshot.notifications.unread_count = 0;
  currentSnapshot.notifications.read_sequence = currentSnapshot.notifications.latest_sequence || 0;
  if (currentSnapshot.task_completion_alerts) currentSnapshot.task_completion_alerts.unread_count = 0;
  renderTaskAlertIndicator(currentSnapshot);
}

async function setNotificationCenterOpen(open) {
  ui.notificationCenter.hidden = !open;
  ui.taskAlertIndicator.setAttribute('aria-expanded', String(open));
  if (open) {
    renderNotificationCenter(currentSnapshot);
    if (Number(currentSnapshot?.notifications?.unread_count || 0) > 0) {
      await markAllNotificationsRead();
    }
  }
}

async function initialize() {
  api = window.pywebview.api;
  void checkForUpdates();
  try {
    await loadApplication();
  } catch (error) {
    ui.notice.hidden = false;
    ui.notice.textContent = `应用初始化失败：${error.message || String(error)}`;
  }
}

function scheduleRefresh() {
  clearInterval(refreshTimer);
  const seconds = currentProfile?.refresh_seconds || 15;
  refreshTimer = setInterval(() => {
    if (document.hidden && api?.request_background_refresh) {
      refreshDeferredWhileHidden = true;
      void api.request_background_refresh().catch(() => {});
      return;
    }
    void refresh(false);
  }, seconds * 1000);
}

document.addEventListener('click', event => {
  const serverNavigatorFilterButton = event.target.closest('[data-server-navigator-filter]');
  if (serverNavigatorFilterButton) {
    serverNavigatorFilter = serverNavigatorFilterButton.dataset.serverNavigatorFilter;
    if (currentSnapshot) renderServerNavigator(currentSnapshot.servers);
  }
  const serverNavigatorItem = event.target.closest('.server-navigator-item');
  if (serverNavigatorItem) navigateToServer(serverNavigatorItem.dataset.serverId);
  const serverCard = event.target.closest('.server-card');
  if (serverCard) setActiveServer(serverCard.dataset.serverId);
  const copyButton = event.target.closest('.copy-command');
  if (copyButton) void copyCommand(copyButton);
  const setupTerminal = event.target.closest('.open-setup-terminal');
  if (setupTerminal) void openSetupTerminal(setupTerminal.dataset.platform);
  const contextCopy = event.target.closest('[data-copy-value]');
  if (contextCopy) void copyContextValue(contextCopy);
  const copySsh = event.target.closest('.copy-server-ssh');
  if (copySsh) void copyServerSshCommand(copySsh.dataset.serverId);
  const favorite = event.target.closest('.favorite-server');
  if (favorite) void setFavoriteServer(favorite.dataset.serverId);
  const toggleServer = event.target.closest('.toggle-server-monitoring');
  if (toggleServer) void setServerEnabled(toggleServer.dataset.serverId, !serverIsEnabled(toggleServer.dataset.serverId));
  const openTerminal = event.target.closest('.open-terminal');
  if (openTerminal) void openServerTerminal(openTerminal.dataset.serverId);
  const copyDiagnostics = event.target.closest('.copy-server-diagnostics');
  if (copyDiagnostics) void copyRedactedDiagnostics(copyDiagnostics.dataset.serverId);
  if (event.target.closest('.open-logs')) void openLogsDirectory();
  const loadCluster = event.target.closest('.load-cluster-nodes');
  if (loadCluster) void loadClusterNodes(loadCluster.dataset.serverId, 0);
  const clusterPage = event.target.closest('.cluster-node-page');
  if (clusterPage) void loadClusterNodes(clusterPage.dataset.serverId, Number(clusterPage.dataset.pageOffset));
  const savedView = event.target.closest('[data-apply-saved-view]');
  if (savedView) {
    const entry = savedViewEntries().find(view => view.name === savedView.dataset.applySavedView);
    if (entry) applyResourceCriteria(entry.criteria);
  }
  const deleteView = event.target.closest('[data-delete-saved-view]');
  if (deleteView) void deleteSavedView(deleteView.dataset.deleteSavedView);
  if (event.target.closest('.open-settings')) openSettings({onboarding: !currentProfile?.servers?.length});
  const installUpdate = event.target.closest('.install-latest-update');
  if (installUpdate) void installLatestUpdate(installUpdate);
  const taskWatch = event.target.closest('.task-watch-toggle, .remove-task-watch');
  if (taskWatch) void toggleTaskCompletionWatch(taskWatch);
  const clearTaskWatches = event.target.closest('.clear-task-watches');
  if (clearTaskWatches) void clearTaskCompletionWatches(clearTaskWatches);
  const retry = event.target.closest('.retry-server');
  if (retry) refresh(true, retry.dataset.serverId);
  const retryDirectory = event.target.closest('.retry-directory');
  if (retryDirectory) void loadDirectoryTree(retryDirectory.dataset.serverId, true);
  const refreshDirectory = event.target.closest('.refresh-directory');
  if (refreshDirectory) {
    void loadDirectoryTree(
      refreshDirectory.dataset.serverId,
      true,
      refreshDirectory.dataset.directoryPath || null,
    );
  }
  const loadDirectoryMore = event.target.closest('.load-directory-more');
  if (loadDirectoryMore) void loadDirectoryTree(loadDirectoryMore.dataset.serverId, false, loadDirectoryMore.dataset.directoryPath);
  const pinDirectory = event.target.closest('.pin-directory');
  if (pinDirectory) void pinDefaultDirectory(pinDirectory.dataset.serverId, pinDirectory.dataset.directoryPath);
  const resetDirectory = event.target.closest('.reset-directory-default');
  if (resetDirectory) void resetDefaultDirectory(resetDirectory.dataset.serverId);
  const expandDirectories = event.target.closest('.expand-loaded-directories');
  if (expandDirectories) expandLoadedDirectories(expandDirectories.dataset.serverId);
  const dismissNoticeButton = event.target.closest('.dismiss-notice');
  if (dismissNoticeButton) void dismissNotice(dismissNoticeButton.dataset.noticeCode);
  if (event.target.closest('.retry-all')) refresh(true);
  if (
    !ui.notificationCenter.hidden
    && !event.target.closest('#notification-center')
    && !event.target.closest('#task-alert-indicator')
  ) void setNotificationCenterOpen(false);
});
document.addEventListener('focusin', event => {
  const serverCard = event.target.closest?.('.server-card');
  if (serverCard) setActiveServer(serverCard.dataset.serverId);
});
document.addEventListener('toggle', event => {
  if (event.target.dataset?.bulkDisclosure === 'true') {
    delete event.target.dataset.bulkDisclosure;
    return;
  }
  if (ui.dashboardContent.contains(event.target)) dashboardDisclosureMode = 'default';
  const cluster = event.target.closest?.('.cluster-module');
  if (cluster && event.target === cluster) {
    const key = `${cluster.dataset.serverId}:${cluster.dataset.module || 'module'}`;
    if (cluster.open) {
      openClusters.add(key);
      openClusters.delete(`${key}:closed`);
    } else {
      openClusters.delete(key);
      openClusters.add(`${key}:closed`);
    }
    if (cluster.open && cluster.dataset.module === 'account-directory') {
      void loadDirectoryTree(cluster.dataset.serverId);
    }
    if (cluster.open && cluster.dataset.module === 'cluster-nodes') {
      const page = clusterNodePageState(cluster.dataset.serverId);
      if (page.status === 'idle') void loadClusterNodes(cluster.dataset.serverId, 0);
    }
  }
  const group = event.target.matches?.('[data-task-group]') ? event.target : null;
  if (group && event.target === group) {
    const key = `${group.dataset.serverId}:${group.dataset.taskModule || 'cluster-tasks'}:${group.dataset.taskGroup}`;
    if (group.open) {
      openTaskGroups.add(key);
      openTaskGroups.delete(`${key}:closed`);
    } else {
      openTaskGroups.delete(key);
      openTaskGroups.add(`${key}:closed`);
    }
  }
  const directoryNode = event.target.matches?.('[data-directory-path]') ? event.target : null;
  if (directoryNode && event.target === directoryNode) {
    const key = `${directoryNode.dataset.serverId}:${directoryNode.dataset.directoryPath}`;
    if (directoryNode.open) {
      openDirectoryNodes.add(key);
      const state = directoryTrees.get(directoryNode.dataset.serverId);
      if (state?.loadedRoots?.has(directoryNode.dataset.directoryPath)) {
        void loadDirectoryTree(
          directoryNode.dataset.serverId,
          false,
          directoryNode.dataset.directoryPath,
        );
      }
    } else openDirectoryNodes.delete(key);
    repaintDirectory(directoryNode.dataset.serverId);
  }
  const contextNote = event.target.matches?.('[data-context-note]') ? event.target : null;
  if (contextNote && event.target === contextNote) {
    const key = `${contextNote.dataset.serverId}:${contextNote.dataset.contextNote}`;
    if (contextNote.open) openContextNotes.add(key);
    else openContextNotes.delete(key);
  }
}, true);
document.addEventListener('submit', event => {
  const form = event.target.closest?.('.cluster-node-filters');
  if (!form) return;
  event.preventDefault();
  const values = new FormData(form);
  void loadClusterNodes(form.dataset.serverId, 0, {
    query: String(values.get('query') || '').trim(),
    gpuType: String(values.get('gpuType') || '').trim(),
    partition: String(values.get('partition') || '').trim(),
    onlyAvailable: values.get('onlyAvailable') === 'on',
    issuesOnly: values.get('issuesOnly') === 'on',
    revision: null,
  });
});
ui.refresh.addEventListener('click', () => refresh(true));
ui.monitoringToggle.addEventListener('click', toggleMonitoringPaused);
ui.recommend.addEventListener('click', updateRecommendation);
[ui.requiredGpuCount, ui.requiredMemory, ui.preferredGpu, ui.preferredPartition].forEach(control => {
  control.addEventListener('input', () => {
    clearRecommendation();
    scheduleResourceWatchEvaluation();
  });
});
[ui.requireSameNode, ui.recommendationLimit].forEach(control => {
  control.addEventListener('change', () => {
    clearRecommendation();
    scheduleResourceWatchEvaluation();
  });
});
ui.resourceWatchEnabled.addEventListener('change', () => {
  resourceWatchCriteriaRevision += 1;
  resourceWatchMatched = false;
  clearTimeout(resourceWatchDebounceTimer);
  if (ui.resourceWatchEnabled.checked) {
    ui.resourceWatchStatus.textContent = '正在检查条件';
    void evaluateResourceWatch();
  } else {
    ui.resourceWatchStatus.textContent = '';
  }
});
ui.saveView.addEventListener('click', saveCurrentView);
ui.copyDiagnostics.addEventListener('click', () => copyRedactedDiagnostics());
ui.openLogsDirectory.addEventListener('click', openLogsDirectory);
ui.checkForUpdates.addEventListener('click', () => void checkForUpdates({interactive: true}));
ui.taskAlertIndicator.addEventListener('click', () => {
  void setNotificationCenterOpen(ui.notificationCenter.hidden);
});
ui.markNotificationsRead.addEventListener('click', () => void markAllNotificationsRead());
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !ui.notificationCenter.hidden) {
    void setNotificationCenterOpen(false);
    ui.taskAlertIndicator.focus();
  }
});
ui.language.addEventListener('change', () => {
  window.VRAMRadarI18n?.setLanguage(ui.language.value);
  if (currentSnapshot) renderNotificationCenter(currentSnapshot);
});
ui.favoriteAlertEnabled.addEventListener('change', () => {
  ui.favoriteAlertMinMemory.disabled = !ui.favoriteAlertEnabled.checked;
});
ui.editorSearch.addEventListener('input', () => {
  syncVisibleServerDrafts();
  settingsServerQuery = ui.editorSearch.value;
  settingsServerPageOffset = 0;
  renderServerEditorPage();
});
ui.editorPreviousPage.addEventListener('click', () => {
  syncVisibleServerDrafts();
  settingsServerPageOffset = Math.max(0, settingsServerPageOffset - SERVER_EDITOR_PAGE_SIZE);
  renderServerEditorPage();
});
ui.editorNextPage.addEventListener('click', () => {
  syncVisibleServerDrafts();
  settingsServerPageOffset += SERVER_EDITOR_PAGE_SIZE;
  renderServerEditorPage();
});
ui.serverListPreviousPage.addEventListener('click', () => changeServerFleetPage(-1));
ui.serverListNextPage.addEventListener('click', () => changeServerFleetPage(1));
ui.serverNavigatorSearch.addEventListener('input', () => {
  serverNavigatorQuery = ui.serverNavigatorSearch.value.trim().toLocaleLowerCase();
  scheduleServerNavigatorSearchRender();
});
ui.serverNavigatorDrag.addEventListener('pointerdown', beginServerNavigatorDrag);
ui.serverNavigatorDrag.addEventListener('pointermove', moveServerNavigatorDrag);
ui.serverNavigatorDrag.addEventListener('pointerup', event => finishServerNavigatorDrag(event));
ui.serverNavigatorDrag.addEventListener('pointercancel', event => finishServerNavigatorDrag(event, true));
ui.serverNavigatorDrag.addEventListener('lostpointercapture', event => finishServerNavigatorDrag(event, true));
ui.serverNavigatorDrag.addEventListener('click', () => {
  if (suppressServerNavigatorDragClick) return;
  void persistServerNavigatorSide(serverNavigatorSide === 'left' ? 'right' : 'left');
});
ui.previousServer.addEventListener('click', () => navigateRelativeServer(-1));
ui.nextServer.addEventListener('click', () => navigateRelativeServer(1));
ui.settings.addEventListener('click', () => openSettings({onboarding: !currentProfile?.servers?.length}));
ui.startOnboarding.addEventListener('click', () => openSettings({onboarding: true}));
ui.collapseDashboard.addEventListener('click', collapseDashboardDisclosure);
document.getElementById('collapse-settings').addEventListener('click', collapseSettingsDisclosure);
ui.closeSettings.addEventListener('click', () => ui.dialog.close());
ui.cancelSettings.addEventListener('click', () => ui.dialog.close());
ui.onboardingLater.addEventListener('click', () => ui.dialog.close());
ui.dialog.addEventListener('close', () => {
  // Chromium may deliver a close event after callers have already reopened
  // the same dialog.  Never let that stale event discard the new session's
  // drafts or invalidate its discovery request.
  if (ui.dialog.open) return;
  window.VRAMRadarI18n?.setLanguage(currentProfile?.ui_language || 'zh-CN');
  invalidateServerDiscovery();
  ui.editorList.replaceChildren();
  settingsServerDrafts = [];
  settingsServerQuery = '';
  settingsServerPageOffset = 0;
});
ui.onboardingBack.addEventListener('click', () => setOnboardingStep(onboardingStep - 1));
ui.onboardingNext.addEventListener('click', () => setOnboardingStep(onboardingStep + 1));
document.getElementById('add-server').addEventListener('click', addAndFocusServerEditor);
ui.discoverServerConfig.addEventListener('click', discoverServerConfig);
ui.importServerConfig.addEventListener('click', importServerConfig);
ui.form.addEventListener('invalid', event => {
  const collapsedSection = event.target.closest?.('details:not([open])');
  if (collapsedSection) collapsedSection.open = true;
}, true);
ui.form.addEventListener('submit', saveSettings);
window.addEventListener('scroll', scheduleServerNavigationSync, {passive: true});
window.addEventListener('resize', scheduleServerNavigationSync, {passive: true});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    scheduleDirectoryFreshnessValidation();
    if (refreshDeferredWhileHidden) {
      refreshDeferredWhileHidden = false;
      void refresh(false);
    }
  }
});
window.addEventListener('focus', () => {
  if (Date.now() - lastUpdateCheckAt >= UPDATE_CHECK_ON_FOCUS_AFTER_MS) {
    void checkForUpdates();
  }
});
window.addEventListener('pywebviewready', initialize, {once: true});
