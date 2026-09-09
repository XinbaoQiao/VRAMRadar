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
    window.__interactionChecks = {ok: Object.values(assertions).every(Boolean), assertions,
      positions: {settledY, refreshY, settledTop, refreshedTop: refreshedCard.getBoundingClientRect().top,
        anchorBeforeTop, anchorAfterTop: anchorAfter.getBoundingClientRect().top}, scrollCalls};
  } catch (error) {
    window.__interactionChecks = {ok: false, assertions, error: String(error)};
  } finally {
    window.scrollTo = originalScrollTo;
  }
})();
