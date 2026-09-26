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
      .every(value => formatElapsedSeconds(value) === '权限受限');
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
    const beforeUsageState = codexUsageState;
    currentProfile = {...currentProfile, codex_usage_enabled: true};
    openSettings({forceNormal: true});
    ui.extensionsSettings.open = true;
    assertions.codex_settings_compact_by_default = !document.getElementById('quota-usage-options').open
      && !ui.extensionsSettings.querySelector('.local-only-badge')
      && !document.getElementById('quota-usage-dashboard');
    document.getElementById('quota-usage-options').open = true;
    const usageFixture = {state: 'ready', plan: 'Pro', fetched_at: Date.now() / 1000, windows: [
      {name: 'Codex', window_minutes: 300, remaining_percent: 63, resets_at: Date.now() / 1000 + 3600},
      {name: 'Codex', window_minutes: 10080, remaining_percent: null, resets_at: null},
    ]};
    renderCodexUsage(usageFixture);
    assertions.codex_extension_is_peer_and_preserves_unknown =
      ui.extensionsSettings.parentElement === ui.profileSettings.parentElement
      && document.querySelectorAll('#quota-usage-details [role="meter"]').length === 1
      && document.querySelector('#quota-usage-details [role="meter"]').getAttribute('aria-valuenow') === '63'
      && document.getElementById('quota-usage-details').textContent.includes('—');
    assertions.codex_cards_fit_settings = [...document.querySelectorAll('#quota-usage-details .quota-quota-card')]
      .every(node => node.scrollWidth <= node.clientWidth + 1);
    window.VRAMRadarI18n.setLanguage('en');
    renderCodexUsage(usageFixture);
    await wait(50);
    assertions.codex_english_labels = !hasChinese(document.getElementById('quota-usage-details').textContent)
      && document.getElementById('quota-usage-details').textContent.includes('Weekly quota');
    renderCodexUsage({...usageFixture, windows: [{...usageFixture.windows[0], resets_at: 1}]});
    assertions.codex_expired_quota_not_presented_as_current =
      !document.querySelector('#quota-usage-details [role="meter"]')
      && document.getElementById('quota-usage-details').textContent.includes('Awaiting quota update');
    currentProfile = {...currentProfile, codex_usage_enabled: false};
    renderCodexUsage({state: 'disabled', windows: []});
    assertions.codex_disabled_clears_details = !document.getElementById('quota-usage-details').textContent;
    const originalUsageApi = api;
    const savedCalls = [];
    let failUsageSave = false;
    try {
      currentProfile = {...currentProfile, codex_usage_enabled: false, codex_executable: ''};
      api = {
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
      ui.codexExecutable.value = 'unsaved-path';
      ui.codexEnabled.click();
      const lockedDuringSave = ui.codexEnabled.disabled;
      await waitUntil(() => !codexSettingsBusy && !codexUsageBusy, 'Automatic usage save did not finish');
      assertions.codex_toggle_saves_immediately_using_auto_detection = lockedDuringSave
        && savedCalls.length === 1 && savedCalls[0].enabled && savedCalls[0].executable === ''
        && currentProfile.codex_usage_enabled && !ui.codexEnabled.disabled;
      failUsageSave = true;
      ui.codexEnabled.click();
      await waitUntil(() => !codexSettingsBusy, 'Failed usage save did not finish');
      assertions.codex_failed_toggle_restores_saved_state = currentProfile.codex_usage_enabled && ui.codexEnabled.checked;
      failUsageSave = false;
      currentProfile = {...currentProfile, codex_executable: 'previous-custom-path'};
      ui.codexExecutable.value = 'previous-custom-path';
      document.getElementById('auto-quota-settings').click();
      await waitUntil(() => !codexSettingsBusy && !codexUsageBusy, 'Automatic detection reset did not finish');
      assertions.codex_restore_auto_detection_clears_manual_path = currentProfile.codex_executable === ''
        && ui.codexExecutable.value === '' && savedCalls[savedCalls.length-1].executable === '';
    } finally {
      clearTimeout(codexUsageTimer);
      api = originalUsageApi;
    }
    currentProfile = beforeUsageProfile;
    renderCodexUsage(beforeUsageState);
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
    window.__interactionChecks = {ok: Object.values(assertions).every(Boolean), assertions,
      positions: {settledY, refreshY, settledTop, refreshedTop: refreshedCard.getBoundingClientRect().top,
        anchorBeforeTop, anchorAfterTop: anchorAfter.getBoundingClientRect().top, bottom:window.__bottomDebug}, scrollCalls};
  } catch (error) {
    window.__interactionChecks = {ok: false, assertions, error: String(error)};
  } finally {
    window.scrollTo = originalScrollTo;
  }
})();
