(() => {
  let running = false;

  function firstUrl(value) {
    if (typeof value === 'string') return value.trim();
    if (Array.isArray(value)) {
      for (const item of value) {
        const result = firstUrl(item);
        if (result) return result;
      }
    }
    if (value && typeof value === 'object') {
      for (const key of ['url_list', 'url', 'uri']) {
        const result = firstUrl(value[key]);
        if (result) return result;
      }
    }
    return '';
  }

  async function request(keyword, offset, count) {
    const uaVersion = (navigator.userAgent.match(/Chrome\/([0-9.]+)/) || ['', ''])[1];
    const params = new URLSearchParams({
      device_platform: 'webapp', aid: '6383', channel: 'channel_pc_web',
      search_channel: 'aweme_general', keyword, search_source: 'normal_search',
      query_correct_type: '1', is_filter_search: '1', sort_type: '2',
      publish_time: '180', offset: String(offset), count: String(count),
      pc_client_type: '1', version_code: '290100', version_name: '29.1.0',
      cookie_enabled: 'true', browser_language: navigator.language || 'zh-CN',
      browser_platform: navigator.platform || 'MacIntel', browser_name: 'Chrome',
      browser_version: uaVersion, browser_online: String(navigator.onLine),
      engine_name: 'Blink', engine_version: uaVersion, os_name: 'Mac OS',
      os_version: '10.15.7', platform: 'PC',
    });
    const response = await fetch(`/aweme/v1/web/general/search/single/?${params.toString()}`, {credentials: 'include'});
    if (!response.ok) throw new Error(`抖音搜索接口返回 HTTP ${response.status}`);
    return response.json();
  }

  function toItem(keyword, aw) {
    const author = aw.author || {};
    const stats = aw.statistics || {};
    const video = aw.video || {};
    const bitrates = video.bit_rate || [];
    const playUrls = [];
    for (const bitrate of bitrates) {
      for (const url of bitrate?.play_addr?.url_list || []) if (url && !playUrls.includes(url)) playUrls.push(url);
    }
    for (const url of video.play_addr?.url_list || []) if (url && !playUrls.includes(url)) playUrls.push(url);
    const cover = firstUrl(video.cover) || firstUrl(video.origin_cover) || firstUrl(video.dynamic_cover);
    let durationMs = Number(aw.duration || aw.video_duration || aw.duration_ms || video.duration || video.video_duration || video.duration_ms || 0);
    if (durationMs > 0 && durationMs < 1000) durationMs *= 1000;
    return {
      platform: 'douyin', platform_label: '抖音', source_keyword: keyword,
      aweme_id: String(aw.aweme_id), url: `https://www.douyin.com/video/${aw.aweme_id}`,
      desc: aw.desc || '', title: aw.desc || '', create_time: aw.create_time || 0,
      author_nickname: author.nickname || '', author_name: author.nickname || '',
      author_uid: author.uid || '', author_sec_uid: author.sec_uid || '',
      digg_count: Number(stats.digg_count || 0), comment_count: Number(stats.comment_count || 0),
      play_count: Number(stats.play_count || 0), collect_count: Number(stats.collect_count || 0),
      share_count: Number(stats.share_count || 0), duration_ms: durationMs,
      duration_seconds: durationMs / 1000, content_type: 'video', cover_url: cover,
      origin_cover_url: firstUrl(video.origin_cover) || cover,
      dynamic_cover_url: firstUrl(video.dynamic_cover) || cover,
      play_url: playUrls[0] || '', video_url: playUrls[0] || '', play_urls: playUrls,
    };
  }

  async function runSearch(payload) {
    const keyword = String(payload?.keyword || '').trim();
    const limit = Math.max(1, Math.min(Number(payload?.limit || 10), 50));
    if (!keyword) throw new Error('检索关键词不能为空');
    const perKeyword = 20;
    const maxOffset = Math.max(240, limit * 8);
    const items = [];
    const seen = new Set();
    let statusCode = null;
    let statusMessage = '';
    for (let offset = 0; offset < maxOffset && items.length < limit; offset += perKeyword) {
      const response = await request(keyword, offset, perKeyword);
      statusCode = response.status_code ?? statusCode;
      statusMessage = response.status_msg || statusMessage;
      for (const row of response.data || []) {
        const aweme = row.aweme_info;
        if (!aweme?.aweme_id || seen.has(String(aweme.aweme_id))) continue;
        if (!aweme.video?.play_addr) continue;
        seen.add(String(aweme.aweme_id));
        items.push(toItem(keyword, aweme));
        if (items.length >= limit) break;
      }
      if (!response.has_more) break;
    }
    items.sort((a, b) => (Number(b.digg_count || 0) - Number(a.digg_count || 0)) || (Number(b.create_time || 0) - Number(a.create_time || 0)));
    return {
      action: 'search', source: 'browser_extension', provider: 'local_browser',
      platform: 'douyin', query: keyword, status_code: statusCode,
      status_msg: statusMessage, items: items.slice(0, limit),
      keyword_stats: [{keyword, sort: '最多点赞', publish_time: '半年内', added: items.length}],
      log: `目标浏览器已使用当前抖音登录态抓取 ${items.length} 条`,
    };
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== 'run-douyin-search' || running) return;
    running = true;
    runSearch(message.payload)
      .then((result) => chrome.runtime.sendMessage({type: 'douyin-search-result', ok: true, result}))
      .catch((error) => chrome.runtime.sendMessage({type: 'douyin-search-result', ok: false, error: error.message}))
      .finally(() => { running = false; });
  });
})();
