(() => {
  const SOURCE = 'exhibitflow-browser-worker';

  function announce(info) {
    window.postMessage({
      source: SOURCE,
      type: 'worker-ready',
      worker_id: info?.worker_id || '',
      browser: info?.browser || 'Chrome',
      extension_version: info?.extension_version || '',
    }, window.location.origin);
  }

  function requestInfo() {
    chrome.runtime.sendMessage({type: 'worker-info'}, (info) => {
      if (chrome.runtime.lastError) return;
      announce(info || {});
    });
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const message = event.data || {};
    if (message.source !== SOURCE || message.type !== 'start-local-search') return;
    chrome.runtime.sendMessage({
      type: 'start-local-search',
      task_id: message.task_id,
      api_base: message.api_base || window.location.origin,
      payload: message.payload || {},
    }, (result) => {
      if (chrome.runtime.lastError) {
        window.postMessage({source: SOURCE, type: 'worker-error', error: chrome.runtime.lastError.message}, window.location.origin);
        return;
      }
      window.postMessage({source: SOURCE, type: 'worker-task-accepted', result: result || {}}, window.location.origin);
    });
  });

  requestInfo();
  window.setInterval(requestInfo, 5000);
})();
