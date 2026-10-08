// Runs inside the synthetic WebView fixture; no Profile or remote connections.
(async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const assertions = {};
  const originalScrollTo = window.scrollTo;
  const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
  const waitUntil = async (predicate, label) => {
    const deadline = performance.now() + 5000;
    while (!predicate()) {
      if (performance.now() >= deadline) throw new Error(label);
      await wait(25);
    }
  };
  let scrollCalls = 0;
  window.scrollTo = function (...args) {
    scrollCalls += 1;
    return originalScrollTo.apply(window, args);
  };
  try {
    // Native show() is asynchronous on Cocoa. Timers can run before the
    // compositor resumes; issuing scrolls then samples an unshown viewport.
    // Await actual visibility and painted frames before exercising scroll.
    await waitUntil(() => !document.hidden, 'Native window did not become visible');
    await nextFrame();
    await nextFrame();
    const cards = [...document.querySelectorAll('.server-card')];
    const card = cards[1];
    if (!card) throw new Error('Synthetic fixture needs two servers');
    const details = card.querySelector('details.cluster-module');
    if (!details) throw new Error('Missing expandable module');
    details.open = false;
    await wait(100);
    const setupTop = Math.max(0, Math.min(
      scrollY + details.getBoundingClientRect().top - 300,
      document.documentElement.scrollHeight - innerHeight,
    ));
    originalScrollTo.call(window, {top: setupTop, behavior: 'auto'});
    await waitUntil(() => Math.abs(scrollY - setupTop) <= 2, 'Initial viewport scroll did not complete');
    await nextFrame();
    await nextFrame();
    scrollCalls = 0;
    details.querySelector(':scope > summary').click();
    await wait(350);
    const settledY = scrollY;
    const settledTop = card.getBoundingClientRect().top;
    const anchorBefore = cards.find(node => {
      const rect = node.getBoundingClientRect();
      return rect.top <= cachedTitlebarHeightPx && rect.bottom > cachedTitlebarHeightPx;
    }) || cards.find(node => node.getBoundingClientRect().bottom > 0);
    const anchorBeforeTop = anchorBefore.getBoundingClientRect().top;
    const settledServer = activeServerId;
    await wait(450);
    assertions.expand_scroll_is_bounded = scrollCalls <= 1;
    assertions.expansion_settles_without_jitter = Math.abs(scrollY - settledY) <= 2;
    assertions.idle_keeps_selected_server = activeServerId === settledServer;

    // Replacing an open card emits native toggle events. These are not clicks.
    scrollCalls = 0;
    const next = structuredClone(currentSnapshot);
    next.servers.forEach(server => { server.display_name += ' refreshed'; });
    render(next);
    await wait(450);
    const refreshY = scrollY;
    await wait(350);
    assertions.refresh_keeps_selected_server = activeServerId === settledServer;
    assertions.refresh_settles_without_follow_scroll = Math.abs(scrollY - refreshY) <= 2;
    const refreshedCard = document.getElementById(serverCardAnchor(card.dataset.serverId));
    const anchorAfter = document.getElementById(serverCardAnchor(anchorBefore.dataset.serverId));
    assertions.refresh_preserves_viewport = Math.abs(anchorAfter.getBoundingClientRect().top - anchorBeforeTop) <= 3;

    let staleCallbackRan = false;
    afterExpandLayout(() => { staleCallbackRan = true; });
    window.dispatchEvent(new WheelEvent('wheel', {deltaY: 10}));
    await wait(100);
    assertions.user_scroll_cancels_pending_adjustment = !staleCallbackRan;
    assertions.missing_elapsed_is_not_zero = [null, undefined, '', ' ', -1, NaN]
      .every(value => formatElapsedSeconds(value) === '不可用');
    assertions.valid_elapsed_is_preserved = formatElapsedSeconds(65) !== formatElapsedSeconds(0);
    const abnormalTiming = renderProcessElapsed({timing_status: 'unavailable', observed_running_seconds: 90});
    assertions.abnormal_timing_is_distinct_from_observed_lower_bound =
      abnormalTiming.includes('运行时长不可用') && abnormalTiming.includes('已观测运行至少') && !abnormalTiming.includes('0秒');
    assertions.normal_timing_display_is_unchanged = renderProcessElapsed({elapsed_seconds: 86400}) === '1天';
    window.VRAMRadarI18n.setLanguage('en');
    const translationProbe = document.createElement('div');
    translationProbe.textContent = '已取消收藏 GPU';
    translationProbe.setAttribute('title', '文件夹路径');
    translationProbe.setAttribute('aria-label', '工作目录路径');
    translationProbe.setAttribute('alt', '复制 SSH 命令');
    translationProbe.setAttribute('data-label', '运行时长');
    document.body.append(translationProbe);
    showToast('已恢复监控');
    await wait(100);
    const hasChinese = value => /[一-鿿]/u.test(value);
    assertions.dynamic_english_text_and_attributes_are_translated =
      [translationProbe.textContent, ui.toast.textContent, ...['title', 'aria-label', 'alt', 'data-label'].map(name => translationProbe.getAttribute(name))].every(value => !hasChinese(value));
    assertions.command_redaction_marker_is_english = renderProcessName({pid: 1,
      name: 'train', command_preview: 'python train.py --token [已隐藏]'}).includes('[redacted]');
    translationProbe.textContent = '已暂停监控这台服务器';
    await wait(50);
    assertions.dynamic_english_updates_are_translated = !hasChinese(translationProbe.textContent);
    const originalConfirm = window.confirm;
    const originalUpdateAction = latestUpdateAction;
    let confirmationText = '';
    try {
      window.confirm = message => { confirmationText = message; return false; };
      latestUpdateAction = 'one_click';
      await installLatestUpdate(document.createElement('button'));
    } finally {
      window.confirm = originalConfirm;
      latestUpdateAction = originalUpdateAction;
    }
    assertions.native_update_confirmation_is_english = confirmationText.includes('GitHub') && !hasChinese(confirmationText);
    window.VRAMRadarI18n.setLanguage('zh-CN');
    assertions.language_round_trip_restores_chinese = translationProbe.textContent === '已暂停监控这台服务器'
      && translationProbe.getAttribute('title') === '文件夹路径';
    translationProbe.remove();
    const profileBeforeImport = currentProfile;
    openSettings({forceNormal: true});
    const importedChoice = {
      id: 'fixture-choice', group_key: 'machine:fixture-a|fixture-b',
      reason: 'same_destination', kept_server_id: 'fixture-a', default_alias: 'fixture-a',
      aliases: ['fixture-a', 'fixture-b'],
      routes: ['fixture-a', 'fixture-b'].map(alias => ({
        primary_alias: alias, aliases: [alias], summary: alias, ssh_config_file: `${alias}.conf`,
      })),
    };
    applyImportedServerConfig({
      paths: ['fixture-a.conf', 'fixture-b.conf'], auto_sync: false, warnings: [],
      servers: [{id: 'fixture-a', display_name: 'Fixture', backend: 'direct_ssh',
        ssh_alias: 'fixture-a', ssh_config_file: 'fixture-a.conf', enabled: true}],
      pending_alias_choices: [importedChoice],
    });
    const importedProfile = collectProfile();
    assertions.multi_source_import_preserves_choice_before_save =
      !importedProfile.auto_sync_servers
      && importedProfile.pending_alias_choices.some(choice => choice.id === importedChoice.id)
      && importedProfile.pending_alias_choices[0].routes[1].ssh_config_file === 'fixture-b.conf';
    ui.dialog.close();
    await wait(50);
    currentProfile = profileBeforeImport;
    openSettings({forceNormal: true});
    assertions.cancelled_import_does_not_leak_choices_into_next_editor =
      importAliasChoiceDrafts.length === 0 && !document.getElementById('import-alias-choices');
    ui.dialog.close();
    const beforeUsageProfile = currentProfile;
    const beforeUsageState = quotaMonitorState;
    currentProfile = {...currentProfile, codex_usage_enabled: true};
    openSettings({forceNormal: true});
    ui.extensionsSettings.open = true;
    const extensionBody = ui.extensionsSettings.querySelector('.quota-extension-body');
    assertions.quota_extension_is_single_switch =
      ui.extensionsSettings.parentElement === ui.profileSettings.parentElement
      && extensionBody.querySelectorAll('input[type="checkbox"]').length === 1
      && !extensionBody.querySelector('details, button, [role="meter"], .quota-quota-card')
      && !document.getElementById('quota-usage-options') && !document.getElementById('refresh-quota-usage')
      && !ui.extensionsSettings.querySelector('.local-only-badge');
    const monitorFixture = {primary: {fetched_at: Date.now() / 1000}, providers: {
      codex: {installed: true}, kimi: {installed: true, fetched_at: Date.now() / 1000 - 60},
      grok: {installed: true}, glm: {installed: false}}};
    renderQuotaMonitor(monitorFixture);
    const statusNode = document.getElementById('quota-usage-status');
    assertions.quota_status_counts_detected_apps = statusNode.textContent.startsWith('已检测到 3 个 AI 应用')
      && statusNode.textContent.includes('上次读取') && !statusNode.textContent.includes('Codex');
    window.VRAMRadarI18n.setLanguage('en');
    renderQuotaMonitor(monitorFixture);
    await wait(50);
    assertions.quota_switch_english = !hasChinese(extensionBody.textContent)
      && extensionBody.textContent.includes('Quota monitoring')
      && statusNode.textContent.startsWith('3 AI apps detected');
    renderQuotaMonitor({providers: {kimi: {installed: true}}, primary: null});
    assertions.quota_status_singular = statusNode.textContent === '1 AI app detected';
    currentProfile = {...currentProfile, codex_usage_enabled: false};
    renderQuotaMonitor(monitorFixture);
    assertions.quota_status_off = statusNode.textContent === 'Quota monitoring is off';
    window.VRAMRadarI18n.setLanguage('zh-CN');
    const originalUsageApi = api;
    const savedCalls = [];
    let failUsageSave = false;
    try {
      currentProfile = {...currentProfile, codex_usage_enabled: false, codex_executable: 'previous-custom-path'};
      api = {
        get_usage_providers: async () => ({enabled: currentProfile.codex_usage_enabled, selected: ['codex', 'kimi'],
          providers: {codex: {installed: true}, kimi: {installed: true}}}),
        get_codex_usage: async () => ({enabled: currentProfile.codex_usage_enabled, state: currentProfile.codex_usage_enabled ? 'ready' : 'disabled', windows: []}),
        save_codex_usage_settings: async (enabled, executable, revision) => {
          savedCalls.push({enabled, executable});
          await wait(20);
          if (failUsageSave) return {ok: false, error: 'Synthetic save failure'};
          return {ok: true, profile: {...currentProfile, codex_usage_enabled: enabled, codex_executable: executable,
            profile_revision: Number(revision || 0) + 1}, usage: {enabled, state: enabled ? 'ready' : 'disabled', windows: []}};
        },
      };
      ui.codexEnabled.checked = false;
      ui.codexEnabled.click();
      const lockedDuringSave = ui.codexEnabled.disabled;
      await waitUntil(() => !codexSettingsBusy && !quotaMonitorBusy, 'Automatic usage save did not finish');
      assertions.quota_switch_saves_immediately_and_keeps_path = lockedDuringSave
        && savedCalls.length === 1 && savedCalls[0].enabled && savedCalls[0].executable === 'previous-custom-path'
        && currentProfile.codex_usage_enabled && !ui.codexEnabled.disabled
        && document.getElementById('quota-usage-status').textContent.startsWith('已检测到 2 个 AI 应用');
      failUsageSave = true;
      ui.codexEnabled.click();
      await waitUntil(() => !codexSettingsBusy, 'Failed usage save did not finish');
      assertions.quota_failed_toggle_restores_saved_state = currentProfile.codex_usage_enabled && ui.codexEnabled.checked;
      // The count matches the strip: selected apps that are detected (not every installed app).
      renderQuotaMonitor({providers: {codex: {installed: true}, kimi: {installed: true}, grok: {installed: true}},
        selected: ['codex', 'kimi'], primary: null});
      assertions.quota_status_counts_selected_only =
        document.getElementById('quota-usage-status').textContent.startsWith('已检测到 2 个 AI 应用');
      // The 15 s status refresh runs only while Settings is open; closing it stops the timer
      // and later profile updates do not poll in the background.
      await waitUntil(() => !quotaMonitorBusy, 'Quota status poll did not settle');
      const timerWhileOpen = quotaMonitorTimer !== null;
      ui.dialog.close();
      await wait(30);
      const timerAfterClose = quotaMonitorTimer;
      let polledWhileClosed = 0;
      const countingApi = api;
      api = {...countingApi, get_usage_providers: async (...args) => {
        polledWhileClosed += 1;
        return countingApi.get_usage_providers(...args);
      }};
      await pollQuotaMonitor();
      assertions.quota_poll_only_while_settings_open = timerWhileOpen && timerAfterClose === null
        && polledWhileClosed === 0 && quotaMonitorTimer === null;
    } finally {
      clearTimeout(quotaMonitorTimer);
      api = originalUsageApi;
    }
    currentProfile = beforeUsageProfile;
    renderQuotaMonitor(beforeUsageState);
    window.VRAMRadarI18n.setLanguage('zh-CN');
    ui.dialog.close();
    const sizeApi = api, sizeProfile = currentProfile;
    const grip = ui.serverNavigator.querySelector('[data-resize="sw"]');
    const oldCapture = grip.setPointerCapture;
    grip.setPointerCapture = () => {};
    try {
      let savedSize = null;
      api = {...api, set_navigator_size: async (width, height) => {
        savedSize = [width, height];
        return {ok: true, profile: {...currentProfile, navigator_width: width, navigator_height: height,
          profile_revision: (Number(currentProfile.profile_revision) || 0)+1}};
      }};
      beginNavigatorResize({target: grip, pointerId: 77, button: 0, clientX: 500, clientY: 400, preventDefault() {}});
      moveNavigatorResize({pointerId: 77, clientX: 430, clientY: 465, preventDefault() {}});
      await finishNavigatorResize({pointerId: 77});
      assertions.navigator_edges_resize_and_save = savedSize?.[0] >= 190 && savedSize?.[1] >= 240
        && ui.serverNavigator.classList.contains('has-custom-size')
        && getComputedStyle(grip).cursor === 'nesw-resize';
      const beforeCancel = ui.serverNavigator.style.getPropertyValue('--navigator-height');
      beginNavigatorResize({target: grip, pointerId: 78, button: 0, clientX: 500, clientY: 400, preventDefault() {}});
      moveNavigatorResize({pointerId: 78, clientX: 380, clientY: 580, preventDefault() {}});
      await finishNavigatorResize({pointerId: 78}, true);
      assertions.navigator_resize_cancel_restores_saved_dimensions = ui.serverNavigator.style.getPropertyValue('--navigator-height') === beforeCancel;
    } finally {
      grip.setPointerCapture = oldCapture;
      api = sizeApi; currentProfile = sizeProfile;
      applyNavigatorSize(sizeProfile.navigator_width || 0, sizeProfile.navigator_height || 0);
    }
    const bottomCard = document.querySelector('.server-card:last-child');
    const bottomHead = bottomCard?.querySelector('.server-head');
    if (bottomHead) {
      reserveServerHeadSpace(bottomCard, bottomHead, false);
      const beforeCollapse = document.documentElement.scrollHeight;
      reserveServerHeadSpace(bottomCard, bottomHead, true);
      assertions.sticky_header_preserves_document_height = Math.abs(document.documentElement.scrollHeight-beforeCollapse) <= 2;
      originalScrollTo.call(window, 0, document.documentElement.scrollHeight);
      const samples = [];
      const headPositions = [];
      window.__bottomDebug = [];
      for (let index = 0; index < 24; index++) {
        updateStuckChrome(); await nextFrame();
        window.__bottomDebug.push({height:document.documentElement.scrollHeight, y:window.scrollY, head:bottomHead.getBoundingClientRect().top,
          headHeight:bottomHead.getBoundingClientRect().height, stuck:bottomHead.classList.contains('is-stuck'),
          sentinel:bottomCard.querySelector('.server-head-sentinel')?.getBoundingClientRect().top,
          spacer:bottomCard.querySelector('.server-head-spacer')?.style.height});
        if (index >= 8) { samples.push(document.documentElement.scrollHeight); headPositions.push(bottomHead.getBoundingClientRect().top); }
      }
      assertions.bottom_scroll_height_stays_stable = Math.max(...samples)-Math.min(...samples) <= 2;
      assertions.bottom_server_header_stays_stable = Math.max(...headPositions)-Math.min(...headPositions) <= 2;
    }
    openSettings({forceNormal: true});
    pendingIgnoredSshAliases = new Set(['retired-a', 'retired-b']);
    const restoreFixture = [
      {id: 'restore-a', display_name: '<b>Removed A</b>', ssh_alias: 'retired-a', backend: 'direct_ssh'},
      {id: 'restore-b', display_name: 'Removed B', ssh_alias: 'retired-b', backend: 'direct_ssh'},
    ];
    renderRemovedServerChoices(restoreFixture);
    const restorePanel = document.getElementById('removed-server-choices');
    assertions.removed_servers_default_collapsed = restorePanel.tagName === 'DETAILS' && !restorePanel.open && restorePanel.querySelector('.removed-server-count').textContent === '2';
    restorePanel.querySelector('summary').click();
    assertions.removed_servers_can_expand = restorePanel.open;
    assertions.removed_servers_default_unselected = restorePanel.querySelectorAll('input:checked').length === 0 && restorePanel.querySelector('button').disabled;
    assertions.removed_server_labels_are_escaped = !restorePanel.querySelector('b');
    restorePanel.querySelector('input').click();
    restorePanel.querySelector('summary').click();
    assertions.removed_servers_can_collapse = !restorePanel.open;
    restorePanel.querySelector('summary').click();
    assertions.removed_servers_selection_survives_collapse = restorePanel.querySelector('input').checked;
    restorePanel.querySelector('button').click();
    assertions.only_selected_removed_server_restored = settingsServerDrafts.some(s => s.ssh_alias === 'retired-a') && !settingsServerDrafts.some(s => s.ssh_alias === 'retired-b');
    assertions.unselected_server_remains_ignored = pendingIgnoredSshAliases.has('retired-b') && !pendingIgnoredSshAliases.has('retired-a');
    const restoredProfile = collectProfile();
    assertions.restored_draft_save_retains_other_removal = restoredProfile.ignored_ssh_aliases.includes('retired-b') && !restoredProfile.ignored_ssh_aliases.includes('retired-a');
    ui.dialog.close();
    openSettings({forceNormal: true});
    assertions.cancel_discards_removed_server_restore = !settingsServerDrafts.some(s => s.ssh_alias === 'retired-a') && !document.getElementById('removed-server-choices');
    ui.dialog.close();
    const invisibleProcess = {pid:'1234', name:'GPU 进程', owner_scope:'unknown', command_visibility:'unavailable', metadata_reason:'pid_not_visible'};
    const missingDetails = renderProcessName(invisibleProcess, 'synthetic-root');
    assertions.invisible_pid_is_not_reported_as_permission_denied = missingDetails.includes('当前进程视图中找不到') && !missingDetails.includes('权限受限');
    assertions.missing_process_time_is_not_permission_evidence = formatElapsedSeconds(null) === '不可用';
    const namespaceProcess = {...invisibleProcess, pid: '2349206', metadata_reason: 'pid_namespace_unmapped', memory_used_gib: 4.83, allocations: [{gpu_index: 0, memory_used_gib: 4.83}]};
    const namespaceServer = {
      server_id: 'synthetic-namespace', connection: {state: 'online'},
      processes: {supported: true, current_user: 'owner', pid_view: 'isolated', active: [namespaceProcess], local_gpu_access_supported: true,
        local_gpu_access: [{pid: '15109', user: 'owner', owner_scope: 'mine', name: 'train.py', command_preview: 'python train.py', elapsed_seconds: 998}]},
    };
    const namespaceFixture = document.createElement('section');
    namespaceFixture.innerHTML = renderDirectProcessModule(namespaceServer);
    document.body.appendChild(namespaceFixture);
    namespaceFixture.querySelectorAll('details').forEach(detail => { detail.open = true; });
    const gpuRow = namespaceFixture.querySelector('.process-unknown tbody tr');
    const localTable = namespaceFixture.querySelector('.local-gpu-access-table');
    assertions.isolated_gpu_owner_remains_unknown_without_mapping = gpuRow.innerText.includes('2349206') && !gpuRow.querySelector('.self-user-tag');
    assertions.namespace_explanation_distinguishes_ssh_permissions = gpuRow.innerText.includes('独立的 PID 视图') && gpuRow.innerText.includes('不代表 SSH 账号权限不足');
    assertions.local_device_users_show_their_verified_owner = localTable.innerText.includes('15109') && localTable.querySelector('.task-user.self .self-user-tag')?.innerText === '我';
    assertions.local_device_users_have_no_inferred_vram_or_completion_watch = !localTable.innerText.includes('GiB') && !localTable.innerText.includes('显存') && !localTable.innerText.includes('提醒');
    assertions.gpu_memory_survives_namespace_isolation = gpuRow.innerText.includes('4.83 GiB');
    const mappedPid = renderProcessPid({pid: '2349206', visible_pid: '15109'});
    assertions.proven_mapping_keeps_both_distinct_pids = mappedPid.includes('2349206') && mappedPid.includes('15109') && mappedPid.includes('当前环境 PID');
    namespaceServer.connection.state = 'stale';
    assertions.stale_device_users_are_labelled_as_previous_data = renderLocalGpuAccess(namespaceServer, true).includes('上次环境') && renderLocalGpuAccess(namespaceServer, true).includes('不能据此判断');
    window.VRAMRadarI18n.setLanguage('en');
    assertions.namespace_ui_is_translated = !/[\u3400-\u9fff]/u.test(namespaceFixture.textContent) && namespaceFixture.textContent.includes('PID in this environment');
    window.VRAMRadarI18n.setLanguage('zh-CN');
    namespaceFixture.remove();
    window.__interactionChecks = {ok: Object.values(assertions).every(Boolean), assertions,
      positions: {settledY, refreshY, settledTop, refreshedTop: refreshedCard.getBoundingClientRect().top,
        anchorBeforeTop, anchorAfterTop: anchorAfter.getBoundingClientRect().top, bottom:window.__bottomDebug}, scrollCalls};
  } catch (error) {
    window.__interactionChecks = {ok: false, assertions, error: String(error)};
  } finally {
    window.scrollTo = originalScrollTo;
  }
})();
