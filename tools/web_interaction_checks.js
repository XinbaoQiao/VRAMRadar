// Runs inside the synthetic WebView fixture; no Profile or remote connections.
(async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const assertions = {};
  const originalScrollTo = window.scrollTo;
  let scrollCalls = 0;
  window.scrollTo = function (...args) {
    scrollCalls += 1;
    return originalScrollTo.apply(window, args);
  };
  try {
    await wait(300);
    const cards = [...document.querySelectorAll('.server-card')];
    const card = cards[1];
    if (!card) throw new Error('Synthetic fixture needs two servers');
    const details = card.querySelector('details.cluster-module');
    if (!details) throw new Error('Missing expandable module');
    details.open = false;
    await wait(100);
    originalScrollTo.call(window, {top: scrollY + details.getBoundingClientRect().top - 300});
    await wait(250);
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
    translationProbe.textContent = '已暂停这台服务器';
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
    assertions.language_round_trip_restores_chinese = translationProbe.textContent === '已暂停这台服务器'
      && translationProbe.getAttribute('title') === '文件夹路径';
    translationProbe.remove();
    window.__interactionChecks = {ok: Object.values(assertions).every(Boolean), assertions,
      positions: {settledY, refreshY, settledTop, refreshedTop: refreshedCard.getBoundingClientRect().top,
        anchorBeforeTop, anchorAfterTop: anchorAfter.getBoundingClientRect().top}, scrollCalls};
  } catch (error) {
    window.__interactionChecks = {ok: false, assertions, error: String(error)};
  } finally {
    window.scrollTo = originalScrollTo;
  }
})();
