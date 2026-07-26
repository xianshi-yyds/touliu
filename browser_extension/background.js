const DEFAULT_API_BASE = 'https://employee.xianshi.icu';
const WORKER_ID_KEY = 'exhibitflow_worker_id';
const ACTIVE_JOB_KEY = 'exhibitflow_active_job';

let workerIdPromise;

function apiBase(value) {
  const raw = String(value || DEFAULT_API_BASE).replace(/\/$/, '');
  return /^https?:\/\//i.test(raw) ? raw : DEFAULT_API_BASE;
}

async function workerId() {
  if (!workerIdPromise) {
    workerIdPromise = chrome.storage.local.get(WORKER_ID_KEY).then(async (data) => {
      let value = data[WORKER_ID_KEY];
      if (!value) {
        value = `chrome-${crypto.randomUUID()}`;
        await chrome.storage.local.set({[WORKER_ID_KEY]: value});
      }
      return value;
    });
  }
  return workerIdPromise;
}

async function workerInfo() {
  return {
    worker_id: await workerId(),
    browser: 'Chrome',
    extension_version: chrome.runtime.getManifest().version,
  };
}

async function postJson(base, path, body) {
  const response = await fetch(`${apiBase(base)}${path}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {}; }
  if (!response.ok) throw new Error(data.error || `服务器请求失败（HTTP ${response.status}）`);
  return data;
}

async function saveJob(job) {
  if (job) await chrome.storage.local.set({[ACTIVE_JOB_KEY]: job});
  else await chrome.storage.local.remove(ACTIVE_JOB_KEY);
}

async function getJob() {
  const data = await chrome.storage.local.get(ACTIVE_JOB_KEY);
  return data[ACTIVE_JOB_KEY] || null;
}

async function sendSearchCommand(job, attempt = 0) {
  if (!job?.tab_id) return;
  try {
    await chrome.tabs.sendMessage(job.tab_id, {type: 'run-douyin-search', payload: job.payload});
  } catch (error) {
    if (attempt < 8) {
      setTimeout(() => sendSearchCommand(job, attempt + 1), 1500);
      return;
    }
    await reportResult(job, 'failed', null, `抖音页面脚本未连接：${error.message}`);
  }
}

async function startLocalSearch(message) {
  const info = await workerInfo();
  const base = apiBase(message.api_base);
  const taskId = String(message.task_id || '').trim();
  if (!taskId) throw new Error('缺少任务 ID');
  const previous = await getJob();
  if (previous && previous.task_id && previous.task_id !== taskId) {
    throw new Error('当前目标浏览器仍有一个抓取任务正在执行');
  }
  const claim = await postJson(base, '/api/browser-workers/claim', {
    ...info,
    worker_id: info.worker_id,
    task_id: taskId,
  });
  if (!claim.claimed) throw new Error('服务器没有找到可领取的本机抓取任务');
  const taskPayload = claim.task?.payload || message.payload || {};
  const keyword = encodeURIComponent(taskPayload.keyword || '展会素材');
  const tab = await chrome.tabs.create({
    url: `https://www.douyin.com/search/${keyword}?type=video`,
    active: true,
  });
  const job = {
    task_id: taskId,
    api_base: base,
    worker_id: info.worker_id,
    tab_id: tab.id,
    payload: taskPayload,
    started_at: new Date().toISOString(),
  };
  await saveJob(job);
  return {ok: true, worker_id: info.worker_id, task_id: taskId, tab_id: tab.id};
}

async function reportResult(job, status, result, error = '') {
  if (!job?.task_id) return;
  try {
    await postJson(job.api_base, '/api/browser-workers/result', {
      task_id: job.task_id,
      worker_id: job.worker_id,
      status,
      result: result || {},
      error,
    });
  } finally {
    await saveJob(null);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'worker-info') {
    workerInfo().then(sendResponse);
    return true;
  }
  if (message?.type === 'start-local-search') {
    startLocalSearch(message).then(sendResponse).catch((error) => sendResponse({ok: false, error: error.message}));
    return true;
  }
  if (message?.type === 'douyin-search-result') {
    getJob().then(async (job) => {
      if (!job || (sender.tab?.id && job.tab_id !== sender.tab.id)) return;
      await reportResult(job, message.ok === false ? 'failed' : 'succeeded', message.result, message.error || '');
    });
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status !== 'complete') return;
  getJob().then((job) => {
    if (job?.tab_id === tabId) setTimeout(() => sendSearchCommand(job), 1800);
  });
});

chrome.tabs.onRemoved.addListener((tabId) => {
  getJob().then((job) => {
    if (job?.tab_id === tabId) reportResult(job, 'failed', null, '目标浏览器关闭了抖音抓取页面');
  });
});
