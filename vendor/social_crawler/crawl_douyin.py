#!/usr/bin/env python3
"""Search, collect metrics, and download Douyin videos into local outputs.

This script keeps credentials out of reports. It reads cookies only from the
runtime cookie file and never writes cookie values to output files.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from core.douyin_crawler import DouyinCrawler, _sanitize_filename


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_KEYWORDS = [
    "汽车展会",
    "车展",
    "北京国际车展",
    "北京车展",
    "广州车展",
    "上海车展",
    "粤港澳车展",
    "福州车展",
    "汽车测试展",
    "汽配展会",
]
PUBLIC_FIELDS = [
    "rank",
    "source_keyword",
    "aweme_id",
    "url",
    "author_nickname",
    "author_uid",
    "author_id",
    "author_sec_uid",
    "publish_time",
    "create_time",
    "desc",
    "digg_count",
    "comment_count",
    "play_count",
    "collect_count",
    "share_count",
    "duration_ms",
    "content_type",
    "cover_url",
    "play_url",
    "play_urls",
    "video_stream_ok",
    "download_ok",
    "download_path",
    "file_size_bytes",
]
COMMENT_FIELDS = [
    "rank",
    "aweme_id",
    "video_url",
    "comment_rank",
    "raw_comment_rank",
    "cid",
    "text",
    "create_time",
    "publish_time",
    "digg_count",
    "reply_comment_total",
    "sort_mode",
    "selected_score",
    "hot_score",
    "is_question",
    "sentiment_label",
    "sentiment_score",
    "matched_keywords",
    "ip_label",
    "user_uid",
    "user_sec_uid",
    "user_nickname",
    "user_unique_id",
]
DEFAULT_COMMENT_KEYWORDS = [
    "多少钱",
    "价格",
    "门票",
    "地址",
    "位置",
    "哪里",
    "时间",
    "预约",
    "报名",
    "怎么买",
    "想去",
    "值得",
    "好看",
]
QUESTION_MARKERS = ["?", "？", "吗", "呢", "么", "怎么", "如何", "哪里", "哪儿", "多少", "为什么", "有没有", "能不能", "可以"]
POSITIVE_WORDS = ["喜欢", "想去", "好看", "漂亮", "震撼", "不错", "值得", "安排", "心动", "种草", "厉害", "高级", "舒服", "牛", "帅"]
NEGATIVE_WORDS = ["不好", "失望", "贵", "太贵", "难看", "坑", "垃圾", "无语", "排队", "被骗", "不值", "麻烦", "踩雷", "差"]


@dataclass
class BrowserSearchResult:
    items: list[dict[str, Any]]
    status_code: int | None
    status_msg: str
    keyword_stats: list[dict[str, Any]]


def split_keywords(value: str) -> list[str]:
    if not value:
        return DEFAULT_KEYWORDS
    parts = re.split(r"[,，\n]+", value)
    return [part.strip() for part in parts if part.strip()]


def read_url_file(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def half_year_cutoff_timestamp() -> int:
    return int((datetime.now() - timedelta(days=183)).timestamp())


def run_chrome_same_origin_search(keywords: list[str], limit: int, per_keyword: int = 20) -> BrowserSearchResult:
    """Run Douyin search inside an already logged-in Chrome tab.

    Chrome menu must allow JavaScript from Apple Events:
    View > Developer > Allow JavaScript from Apple Events.
    """
    js_template = r"""
(function(){
  var keywords = KEYWORDS_JSON;
  var limit = LIMIT_NUMBER;
  var perKeyword = PER_KEYWORD_NUMBER;
  var cutoffTime = CUTOFF_TIME_NUMBER;
  var maxOffset = Math.max(240, limit * 8);
  function request(keyword, offset){
    var uaVersion = (navigator.userAgent.match(/Chrome\/([0-9.]+)/)||['',''])[1];
    var params = new URLSearchParams({
      device_platform:'webapp',
      aid:'6383',
      channel:'channel_pc_web',
      search_channel:'aweme_general',
      keyword:keyword,
      search_source:'normal_search',
      query_correct_type:'1',
      is_filter_search:'1',
      sort_type:'2',
      publish_time:'180',
      offset:String(offset),
      count:String(perKeyword),
      pc_client_type:'1',
      version_code:'290100',
      version_name:'29.1.0',
      cookie_enabled:'true',
      browser_language:navigator.language || 'zh-CN',
      browser_platform:navigator.platform || 'MacIntel',
      browser_name:'Chrome',
      browser_version:uaVersion,
      browser_online:String(navigator.onLine),
      engine_name:'Blink',
      engine_version:uaVersion,
      os_name:'Mac OS',
      os_version:'10.15.7',
      platform:'PC'
    });
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/aweme/v1/web/general/search/single/?' + params.toString(), false);
    xhr.withCredentials = true;
    xhr.send(null);
    return JSON.parse(xhr.responseText);
  }
  var out = {status_code:null, status_msg:'', items:[], keyword_stats:[]};
  var seen = {};
  for (var k = 0; k < keywords.length; k++) {
    var keyword = keywords[k];
    var before = out.items.length;
    for (var offset = 0; offset < maxOffset; offset += perKeyword) {
      var res = request(keyword, offset);
      out.status_code = res.status_code;
      out.status_msg = res.status_msg || '';
      var data = res.data || [];
      for (var i = 0; i < data.length; i++) {
        var aw = data[i].aweme_info;
        if (!aw || !aw.aweme_id || seen[aw.aweme_id]) continue;
        if (!aw.video || !aw.video.play_addr) continue;
        if ((aw.create_time || 0) < cutoffTime) continue;
        var author = aw.author || {};
        var st = aw.statistics || {};
        var video = aw.video || {};
        var play = '';
        var playUrls = [];
        var coverUrl = '';
        var durationMs = 0;
        try {
          function firstNumber(values) {
            for (var n = 0; n < values.length; n++) {
              var value = Number(values[n] || 0);
              if (value > 0) return value;
            }
            return 0;
          }
          var br = video.bit_rate || [];
          for (var b = 0; b < br.length; b++) {
            if (br[b].play_addr && br[b].play_addr.url_list && br[b].play_addr.url_list.length) {
              for (var u = 0; u < br[b].play_addr.url_list.length; u++) {
                playUrls.push(br[b].play_addr.url_list[u]);
              }
            }
          }
          if (video.play_addr && video.play_addr.url_list && video.play_addr.url_list.length) {
            for (var p = 0; p < video.play_addr.url_list.length; p++) {
              playUrls.push(video.play_addr.url_list[p]);
            }
          }
          var coverCandidates = [video.cover, video.origin_cover, video.dynamic_cover];
          for (var c = 0; c < coverCandidates.length; c++) {
            var cover = coverCandidates[c] || {};
            if (cover.url_list && cover.url_list.length) {
              coverUrl = cover.url_list[0];
              break;
            }
          }
          durationMs = firstNumber([
            aw.duration,
            aw.video_duration,
            aw.duration_ms,
            video.duration,
            video.video_duration,
            video.duration_ms,
            video.play_addr && video.play_addr.duration,
            br[0] && br[0].duration
          ]);
          if (durationMs > 0 && durationMs < 1000) durationMs = durationMs * 1000;
        } catch(e) {}
        playUrls = playUrls.filter(function(url, index, arr){ return url && arr.indexOf(url) === index; });
        play = playUrls[0] || '';
        seen[aw.aweme_id] = true;
        out.items.push({
          source_keyword: keyword,
          aweme_id: String(aw.aweme_id),
          desc: aw.desc || '',
          create_time: aw.create_time || 0,
          author_nickname: author.nickname || '',
          author_id: author.uid || '',
          author_sec_uid: author.sec_uid || '',
          digg_count: st.digg_count || 0,
          comment_count: st.comment_count || 0,
          play_count: st.play_count || 0,
          collect_count: st.collect_count || 0,
          share_count: st.share_count || 0,
          duration_ms: durationMs,
          content_type: 'video',
          cover_url: coverUrl,
          play_url: play,
          play_urls: playUrls
        });
      }
      if (!res.has_more) break;
    }
    out.keyword_stats.push({keyword: keyword, sort: '最多点赞', publish_time: '半年内', added: out.items.length - before});
  }
  out.items.sort(function(a, b) {
    return (Number(b.digg_count || 0) - Number(a.digg_count || 0)) || (Number(b.create_time || 0) - Number(a.create_time || 0));
  });
  out.items = out.items.slice(0, limit);
  return JSON.stringify(out);
})()
"""
    js = (
        js_template.replace("KEYWORDS_JSON", json.dumps(keywords, ensure_ascii=False))
        .replace("LIMIT_NUMBER", str(limit))
        .replace("PER_KEYWORD_NUMBER", str(per_keyword))
        .replace("CUTOFF_TIME_NUMBER", str(half_year_cutoff_timestamp()))
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(js)
        js_path = handle.name

    osa = f'''
set jsCode to read POSIX file "{js_path}" as «class utf8»
tell application "Google Chrome"
  if (count of windows) is 0 then make new window
  repeat with w in windows
    repeat with i from 1 to count of tabs of w
      set t to tab i of w
      if (URL of t) contains "douyin.com" then
        return execute t javascript jsCode
      end if
    end repeat
  end repeat
  set targetTab to make new tab at end of tabs of window 1 with properties {{URL:"https://www.douyin.com/jingxuan"}}
  delay 6
  return execute targetTab javascript jsCode
end tell
'''
    try:
        output = subprocess.check_output(["osascript", "-e", osa], text=True, stderr=subprocess.STDOUT)
    finally:
        Path(js_path).unlink(missing_ok=True)

    if output.strip() == "NO_DOUYIN_TAB":
        raise RuntimeError("Chrome has no open douyin.com tab. Open https://www.douyin.com/jingxuan first.")

    data = json.loads(output)
    return BrowserSearchResult(
        items=list(data.get("items") or []),
        status_code=data.get("status_code"),
        status_msg=data.get("status_msg") or "",
        keyword_stats=list(data.get("keyword_stats") or []),
    )


def run_chrome_filtered_page_search(keywords: list[str], limit: int) -> BrowserSearchResult:
    """Collect Douyin video links in the same order as the filtered web page."""
    collect_js = r"""
(function(){
  function clean(value) { return String(value || "").replace(/\s+/g, " ").trim(); }
  function compactInt(value) {
    var text = String(value || "").replace(/,/g, "");
    var match = text.match(/([\d.]+)\s*万/);
    if (match) return Math.round(parseFloat(match[1]) * 10000);
    match = text.match(/\d+/);
    return match ? parseInt(match[0], 10) : 0;
  }
  var anchors = Array.from(document.querySelectorAll("a[href*='/video/']"));
  var seen = {};
  var rows = anchors.map(function(anchor) {
    var rect = anchor.getBoundingClientRect();
    var text = clean(anchor.innerText || anchor.textContent || "");
    var id = ((anchor.href || "").match(/\/video\/(\d+)/) || [])[1] || "";
    var likeText = (text.match(/\d{1,2}:\d{2}\s+([\d.]+万|\d+)/) || [])[1] || "";
    return {
      aweme_id: id,
      url: anchor.href || "",
      desc: text,
      digg_count: compactInt(likeText),
      rect_top: Math.round(rect.top + window.scrollY),
      rect_left: Math.round(rect.left + window.scrollX),
      rect_width: Math.round(rect.width),
      rect_height: Math.round(rect.height)
    };
  }).filter(function(row) {
    return row.aweme_id && row.url && row.rect_width > 80 && row.rect_height > 100;
  }).sort(function(a, b) {
    return (a.rect_top - b.rect_top) || (a.rect_left - b.rect_left);
  }).filter(function(row) {
    if (seen[row.aweme_id]) return false;
    seen[row.aweme_id] = true;
    return true;
  });
  return JSON.stringify({status_code: 0, status_msg: "", items: rows.slice(0, LIMIT_NUMBER)});
})()
"""
    collect_js = collect_js.replace("LIMIT_NUMBER", str(limit))
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(collect_js)
        js_path = handle.name

    stats: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for keyword in keywords:
            search_url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
            osa = f'''
set jsCode to read POSIX file "{js_path}" as «class utf8»
tell application "Google Chrome"
  if (count of windows) is 0 then make new window
  set targetTab to missing value
  repeat with w in windows
    repeat with i from 1 to count of tabs of w
      set t to tab i of w
      if (URL of t) contains "douyin.com" then
        set targetTab to t
        exit repeat
      end if
    end repeat
    if targetTab is not missing value then exit repeat
  end repeat
  if targetTab is missing value then
    set targetTab to make new tab at end of tabs of window 1 with properties {{URL:"https://www.douyin.com/jingxuan"}}
    delay 6
  end if
  set URL of targetTab to "{search_url}"
  delay 6
  execute targetTab javascript "(() => {{ function clean(v) {{ return String(v || '').replace(/\\\\s+/g, ' ').trim(); }} function visible(el) {{ const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }} function fire(el) {{ const r = el.getBoundingClientRect(); const x = r.left + r.width / 2; const y = r.top + r.height / 2; const target = document.elementFromPoint(x, y) || el; ['pointerdown','mousedown','mouseup','click'].forEach(type => target.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }}))); }} const nodes = Array.from(document.querySelectorAll('*')).filter(el => visible(el) && clean(el.innerText || el.textContent) === '筛选'); nodes.sort((a,b)=>a.getBoundingClientRect().width-b.getBoundingClientRect().width); const node = nodes[0]; if (node) fire(node); return node ? 'filter-open' : 'filter-missing'; }})()"
  delay 1
  execute targetTab javascript "(() => {{ function clean(v) {{ return String(v || '').replace(/\\\\s+/g, ' ').trim(); }} function visible(el) {{ const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }} function fire(el) {{ const r = el.getBoundingClientRect(); const x = r.left + r.width / 2; const y = r.top + r.height / 2; const target = document.elementFromPoint(x, y) || el; ['pointerdown','mousedown','mouseup','click'].forEach(type => target.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }}))); }} const panels = Array.from(document.querySelectorAll('*')).filter(el => visible(el) && clean(el.innerText || el.textContent).includes('排序依据') && clean(el.innerText || el.textContent).includes('发布时间') && clean(el.innerText || el.textContent).includes('最多点赞') && clean(el.innerText || el.textContent).includes('半年内')); panels.sort((a,b)=>(a.getBoundingClientRect().width*a.getBoundingClientRect().height)-(b.getBoundingClientRect().width*b.getBoundingClientRect().height)); const panel = panels[0] || document.body; function clickText(text) {{ const nodes = Array.from(panel.querySelectorAll('*')).filter(el => visible(el) && clean(el.innerText || el.textContent) === text); nodes.sort((a,b)=>(a.getBoundingClientRect().width*a.getBoundingClientRect().height)-(b.getBoundingClientRect().width*b.getBoundingClientRect().height)); const node = nodes[0]; if (node) fire(node); return !!node; }} clickText('最多点赞'); clickText('半年内'); return 'filters-set'; }})()"
  delay 4
  set resultText to execute targetTab javascript jsCode
  return resultText
end tell
'''
            output = subprocess.check_output(["osascript", "-e", osa], text=True, stderr=subprocess.STDOUT).strip()
            if output == "NO_DOUYIN_TAB":
                raise RuntimeError("Chrome has no open douyin.com tab. Open https://www.douyin.com/jingxuan first.")
            data = json.loads(output)
            before = len(all_items)
            for item in data.get("items") or []:
                aweme_id = str(item.get("aweme_id") or "")
                if not aweme_id or aweme_id in seen:
                    continue
                seen.add(aweme_id)
                item["source_keyword"] = keyword
                all_items.append(item)
                if len(all_items) >= limit:
                    break
            stats.append({"keyword": keyword, "sort": "最多点赞", "publish_time": "半年内", "source": "douyin_web_filtered_page", "added": len(all_items) - before})
            if len(all_items) >= limit:
                break
    finally:
        Path(js_path).unlink(missing_ok=True)

    return BrowserSearchResult(all_items[:limit], 0, "", stats)


def parse_search_candidates(crawler: DouyinCrawler, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            parsed = crawler.parse_video(candidate["url"])
        except Exception as exc:
            print(f"跳过抖音页面候选：{candidate.get('url')}，错误：{exc}")
            continue
        parsed["url"] = candidate.get("url") or parsed.get("url") or ""
        parsed["source_keyword"] = candidate.get("source_keyword") or ""
        if not parsed.get("digg_count") and candidate.get("digg_count"):
            parsed["digg_count"] = candidate["digg_count"]
        items.append(parsed)
    return items


def normalize_item(item: dict[str, Any], rank: int) -> dict[str, Any]:
    aweme_id = str(item.get("aweme_id") or "")
    create_time = int(item.get("create_time") or 0)
    publish_time = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S") if create_time else ""
    normalized = {
        "rank": rank,
        "source_keyword": item.get("source_keyword") or "",
        "aweme_id": aweme_id,
        "url": item.get("url") or f"https://www.douyin.com/video/{aweme_id}",
        "author_nickname": item.get("author_nickname") or "",
        "author_uid": item.get("author_uid") or item.get("author_id") or "",
        "author_id": item.get("author_id") or "",
        "author_sec_uid": item.get("author_sec_uid") or "",
        "publish_time": publish_time,
        "create_time": create_time,
        "desc": item.get("desc") or "",
        "digg_count": int(item.get("digg_count") or 0),
        "comment_count": int(item.get("comment_count") or 0),
        "play_count": int(item.get("play_count") or 0),
        "collect_count": int(item.get("collect_count") or 0),
        "share_count": int(item.get("share_count") or 0),
        "duration_ms": int(item.get("duration_ms") or 0),
        "content_type": item.get("content_type") or "video",
        "cover_url": item.get("cover_url") or "",
        "play_url": item.get("play_url") or "",
        "play_urls": item.get("play_urls") or [],
    }
    return normalized


def parse_urls(crawler: DouyinCrawler, urls: list[str], limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for url in urls[:limit]:
        parsed = crawler.parse(url)
        parsed["url"] = url
        parsed["source_keyword"] = "url_file"
        if parsed.get("content_type") == "video":
            parsed["duration_ms"] = int(parsed.get("duration_ms") or 0)
        items.append(parsed)
    return items


def download_items(
    crawler: DouyinCrawler,
    items: list[dict[str, Any]],
    output_video: Path,
    overwrite: bool,
    show_progress: bool,
    desired_count: int | None = None,
) -> list[dict[str, Any]]:
    output_video.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for item in items:
        if desired_count is not None and len(results) >= desired_count:
            break
        row = dict(item)
        desc = _sanitize_filename(row.get("desc") or row["aweme_id"], max_length=70)
        file_path = output_video / f"{row['rank']:02d}_{row['aweme_id']}_{desc}.mp4"
        if file_path.exists() and file_path.stat().st_size > 0 and not overwrite:
            if crawler.file_has_video_stream(str(file_path)):
                row["video_stream_ok"] = True
                row["download_ok"] = True
                row["download_path"] = str(file_path.resolve())
                row["file_size_bytes"] = file_path.stat().st_size
                results.append(row)
                continue
            file_path.unlink(missing_ok=True)

        play_urls = []
        for url in row.get("play_urls") or []:
            if url:
                play_urls.append(str(url).replace("playwm", "play"))
        if row.get("play_url"):
            play_urls.insert(0, str(row["play_url"]).replace("playwm", "play"))
        if not play_urls:
            parsed = crawler.parse_video(row["url"])
            play_urls = list(parsed.get("play_urls") or [])
            if parsed.get("play_url"):
                play_urls.insert(0, parsed["play_url"])
        play_urls = list(dict.fromkeys(url for url in play_urls if url))

        ok = False
        for index, play_url in enumerate(play_urls, 1):
            candidate_path = file_path.with_suffix(f".candidate{index}.mp4")
            candidate_path.unlink(missing_ok=True)
            candidate_part = Path(str(candidate_path) + ".part")
            candidate_part.unlink(missing_ok=True)
            if not crawler.download_file(play_url, str(candidate_path), show_progress=show_progress):
                continue
            if crawler.file_has_video_stream(str(candidate_path)):
                shutil.move(str(candidate_path), str(file_path))
                ok = True
                break
            candidate_path.unlink(missing_ok=True)

        row["video_stream_ok"] = bool(ok and file_path.exists() and crawler.file_has_video_stream(str(file_path)))
        row["download_ok"] = bool(ok and row["video_stream_ok"])
        row["download_path"] = str(file_path.resolve()) if row["download_ok"] else ""
        row["file_size_bytes"] = file_path.stat().st_size if row["download_ok"] and file_path.exists() else 0
        if row["download_ok"]:
            results.append(row)
        else:
            print(f"跳过无视频流候选：{row.get('aweme_id')} {row.get('desc', '')[:40]}")
    for rank, row in enumerate(results, 1):
        row["rank"] = rank
    return results


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in PUBLIC_FIELDS}


def comment_keywords(query: str) -> list[str]:
    values = DEFAULT_COMMENT_KEYWORDS[:]
    for keyword in split_keywords(query.replace("/", ",")):
        if keyword and keyword not in values:
            values.append(keyword)
    return values


def comment_sentiment(text: str) -> tuple[str, int]:
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    score = positive - negative
    if score > 0:
        return "positive", score
    if score < 0:
        return "negative", score
    return "neutral", 0


def keyword_matches_text(keyword: str, text: str) -> bool:
    start = text.lower().find(keyword.lower())
    while start >= 0:
        prefix = text[max(0, start - 1) : start]
        if prefix not in {"不", "没", "无", "别", "勿"}:
            return True
        start = text.lower().find(keyword.lower(), start + len(keyword))
    return False


def analyze_comment(comment: dict[str, Any], keywords: list[str]) -> dict[str, Any]:
    row = dict(comment)
    text = str(row.get("text") or "")
    digg_count = int(row.get("digg_count") or 0)
    reply_count = int(row.get("reply_comment_total") or 0)
    create_time = int(row.get("create_time") or 0)
    matched = [keyword for keyword in keywords if keyword and keyword_matches_text(keyword, text)]
    is_question = any(marker in text for marker in QUESTION_MARKERS)
    sentiment_label, sentiment_score = comment_sentiment(text)
    hot_score = digg_count * 3 + reply_count * 5
    recent_score = create_time / 1_000_000_000 if create_time else 0
    row["hot_score"] = hot_score
    row["is_question"] = is_question
    row["sentiment_label"] = sentiment_label
    row["sentiment_score"] = sentiment_score
    row["matched_keywords"] = " / ".join(matched)
    row["keyword_score"] = len(matched) * 20
    row["question_score"] = 25 if is_question else 0
    row["sentiment_abs_score"] = abs(sentiment_score) * 12
    row["recent_score"] = recent_score
    row["analysis_score"] = hot_score + row["keyword_score"] + row["question_score"] + row["sentiment_abs_score"] + recent_score
    return row


def rank_comments(comments: list[dict[str, Any]], limit: int, sort_mode: str, keywords: list[str]) -> list[dict[str, Any]]:
    analyzed = [analyze_comment(comment, keywords) | {"raw_comment_rank": index} for index, comment in enumerate(comments, 1)]
    if sort_mode == "new":
        key = lambda row: (int(row.get("create_time") or 0), int(row.get("hot_score") or 0))
    elif sort_mode == "question":
        key = lambda row: (bool(row.get("is_question")), int(row.get("hot_score") or 0), int(row.get("create_time") or 0))
    elif sort_mode == "keyword":
        key = lambda row: (int(row.get("keyword_score") or 0), int(row.get("hot_score") or 0), int(row.get("create_time") or 0))
    elif sort_mode == "positive":
        key = lambda row: (int(row.get("sentiment_score") or 0), int(row.get("hot_score") or 0), int(row.get("create_time") or 0))
    elif sort_mode == "negative":
        key = lambda row: (-int(row.get("sentiment_score") or 0), int(row.get("hot_score") or 0), int(row.get("create_time") or 0))
    elif sort_mode == "hot":
        key = lambda row: (int(row.get("hot_score") or 0), int(row.get("create_time") or 0))
    else:
        key = lambda row: (float(row.get("analysis_score") or 0), int(row.get("create_time") or 0))
    ranked = sorted(analyzed, key=key, reverse=True)
    for row in ranked:
        row["sort_mode"] = sort_mode
        row["selected_score"] = round(float(row.get("analysis_score") if sort_mode == "analysis" else key(row)[0]) or 0, 3)
    return ranked[:limit]


def collect_video_comments(
    crawler: DouyinCrawler,
    rows: list[dict[str, Any]],
    comments_limit: int,
    comments_fetch_limit: int,
    comments_sort: str,
    keywords: list[str],
) -> list[dict[str, Any]]:
    if comments_limit <= 0:
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        aweme_id = str(row.get("aweme_id") or "")
        if not aweme_id:
            continue
        try:
            comments = crawler.get_video_comments(aweme_id, max(comments_limit, comments_fetch_limit))
        except Exception as exc:
            print(f"评论抓取失败：{aweme_id}，错误：{exc}")
            comments = []
        ranked_comments = rank_comments(comments, comments_limit, comments_sort, keywords)
        for index, comment in enumerate(ranked_comments, 1):
            create_time = int(comment.get("create_time") or 0)
            out.append(
                {
                    "rank": row.get("rank") or "",
                    "aweme_id": aweme_id,
                    "video_url": row.get("url") or f"https://www.douyin.com/video/{aweme_id}",
                    "comment_rank": index,
                    "raw_comment_rank": comment.get("raw_comment_rank") or "",
                    "cid": comment.get("cid") or "",
                    "text": comment.get("text") or "",
                    "create_time": create_time,
                    "publish_time": datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S") if create_time else "",
                    "digg_count": comment.get("digg_count") or 0,
                    "reply_comment_total": comment.get("reply_comment_total") or 0,
                    "sort_mode": comment.get("sort_mode") or comments_sort,
                    "selected_score": comment.get("selected_score") or 0,
                    "hot_score": comment.get("hot_score") or 0,
                    "is_question": comment.get("is_question") or False,
                    "sentiment_label": comment.get("sentiment_label") or "neutral",
                    "sentiment_score": comment.get("sentiment_score") or 0,
                    "matched_keywords": comment.get("matched_keywords") or "",
                    "ip_label": comment.get("ip_label") or "",
                    "user_uid": comment.get("user_uid") or "",
                    "user_sec_uid": comment.get("user_sec_uid") or "",
                    "user_nickname": comment.get("user_nickname") or "",
                    "user_unique_id": comment.get("user_unique_id") or "",
                }
            )
    return out


def write_reports(
    rows: list[dict[str, Any]],
    output_date: Path,
    query: str,
    source: str,
    status_code: int | None = None,
    status_msg: str = "",
    keyword_stats: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
    comments_settings: dict[str, Any] | None = None,
) -> dict[str, Path]:
    output_date.mkdir(parents=True, exist_ok=True)
    crawled_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    public_rows = [public_row(row) for row in rows]
    comment_rows = comments or []
    report = {
        "query": query,
        "source": source,
        "crawled_at": crawled_at,
        "status_code": status_code,
        "status_msg": status_msg,
        "count": len(public_rows),
        "comments_count": len(comment_rows),
        "comments_settings": comments_settings or {},
        "play_count_note": "play_count may be 0/unavailable in Douyin web responses; do not treat 0 as the real playback count.",
        "keyword_stats": keyword_stats or [],
        "items": public_rows,
    }

    json_path = output_date / "douyin_video_metadata.json"
    md_path = output_date / "douyin_video_metadata.md"
    csv_path = output_date / "douyin_video_metadata.csv"
    manifest_path = output_date / "download_manifest.json"
    comments_json_path = output_date / "douyin_video_comments.json"
    comments_csv_path = output_date / "douyin_video_comments.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps({"items": public_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    comments_json_path.write_text(
        json.dumps(
            {
                "query": query,
                "source": source,
                "crawled_at": crawled_at,
                "count": len(comment_rows),
                "settings": comments_settings or {},
                "items": comment_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PUBLIC_FIELDS)
        writer.writeheader()
        writer.writerows(public_rows)
    with comments_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMMENT_FIELDS)
        writer.writeheader()
        writer.writerows(comment_rows)

    lines = [
        "# Douyin Video Metadata",
        "",
        f"- Crawled at: {crawled_at}",
        f"- Query: {query}",
        f"- Source: {source}",
        f"- Count: {len(public_rows)}",
        "- Note: play_count may be 0/unavailable in Douyin web responses.",
        "",
        "| # | 来源词 | 视频ID | 作者 | 作者UID | 发布时间 | 点赞 | 评论 | 播放 | 收藏 | 转发 | 下载 | 描述 | URL |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in public_rows:
        desc = str(row.get("desc") or "").replace("\n", " ").replace("|", "/")
        if len(desc) > 72:
            desc = desc[:72] + "..."
        lines.append(
            f"| {row.get('rank')} | {row.get('source_keyword')} | {row.get('aweme_id')} | "
            f"{str(row.get('author_nickname') or '').replace('|', '/')} | {row.get('author_uid')} | {row.get('publish_time')} | "
            f"{row.get('digg_count')} | {row.get('comment_count')} | {row.get('play_count')} | "
            f"{row.get('collect_count')} | {row.get('share_count')} | "
            f"{'yes' if row.get('download_ok') else 'no'} | {desc} | {row.get('url')} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "json": json_path,
        "markdown": md_path,
        "csv": csv_path,
        "manifest": manifest_path,
        "comments_json": comments_json_path,
        "comments_csv": comments_csv_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl Douyin videos and metrics into local output folders.")
    parser.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS), help="Comma/newline separated search keywords.")
    parser.add_argument("--url-file", type=Path, help="Optional file with one Douyin URL per line.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum videos to process.")
    parser.add_argument("--cookie-file", type=Path, default=Path("cookie.txt"), help="Runtime cookie file. Do not commit it.")
    parser.add_argument("--output-video", type=Path, default=BASE_DIR / "output_video")
    parser.add_argument("--output-date", type=Path, default=BASE_DIR / "output_date")
    parser.add_argument("--date-folder", action="store_true", help="Write outputs under YYYY-MM-DD subfolders.")
    parser.add_argument("--run-name", default="", help="Optional child folder name under the date folder, e.g. coser_zhan.")
    parser.add_argument("--no-download", action="store_true", help="Only collect metadata; skip mp4 downloads.")
    parser.add_argument("--show-progress", action="store_true", help="Show per-file download progress bars.")
    parser.add_argument("--overwrite", action="store_true", help="Re-download existing video files.")
    parser.add_argument("--search-mode", choices=["browser"], default="browser", help="Use logged-in Chrome same-origin search.")
    parser.add_argument("--comments-limit", type=int, default=30, help="Maximum selected comments to save per video. Use 0 to disable.")
    parser.add_argument("--comments-fetch-limit", type=int, default=500, help="Maximum raw top-level comments to fetch per video before local ranking.")
    parser.add_argument(
        "--comments-sort",
        choices=["analysis", "hot", "new", "question", "keyword", "positive", "negative"],
        default="analysis",
        help="Comment selection mode: analysis blends interaction, question, keyword, sentiment, and recency.",
    )
    parser.add_argument("--comments-keywords", default="", help="Comma/newline separated comment keywords for keyword/analysis ranking.")
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    if args.comments_limit < 0:
        raise SystemExit("--comments-limit must be >= 0")
    if args.comments_fetch_limit < 0:
        raise SystemExit("--comments-fetch-limit must be >= 0")

    if args.date_folder:
        date_part = datetime.now().strftime("%Y-%m-%d")
        args.output_video = args.output_video / date_part
        args.output_date = args.output_date / date_part
        if args.run_name:
            safe_run_name = _sanitize_filename(args.run_name, max_length=50)
            args.output_video = args.output_video / safe_run_name
            args.output_date = args.output_date / safe_run_name

    crawler = DouyinCrawler.from_cookie_file(str(args.cookie_file))
    raw_items: list[dict[str, Any]]
    source: str
    status_code: int | None = None
    status_msg = ""
    keyword_stats: list[dict[str, Any]] = []
    query: str

    if args.url_file:
        urls = read_url_file(args.url_file)
        raw_items = parse_urls(crawler, urls, args.limit)
        source = "douyin_detail_url_file"
        query = str(args.url_file)
    else:
        keywords = split_keywords(args.keywords)
        # Follow the same web filter order the user sees: video search, most liked, within half a year.
        # Collect a few extra visible cards so failed parses/downloads can fall through to the next page-ranked item.
        candidate_limit = min(max(args.limit * 3, args.limit + 12), 80)
        search = run_chrome_filtered_page_search(keywords, candidate_limit)
        raw_items = parse_search_candidates(crawler, search.items)
        source = "douyin_web_filtered_page"
        status_code = search.status_code
        status_msg = search.status_msg
        keyword_stats = search.keyword_stats
        query = " / ".join(keywords)

    normalized = [normalize_item(item, rank) for rank, item in enumerate(raw_items, 1)]
    if not args.no_download:
        normalized = download_items(
            crawler,
            normalized,
            args.output_video,
            args.overwrite,
            args.show_progress,
            desired_count=args.limit,
        )
    else:
        normalized = normalized[: args.limit]
        for row in normalized:
            row["download_ok"] = False
            row["download_path"] = ""
            row["file_size_bytes"] = 0

    keywords_for_comments = comment_keywords(args.comments_keywords or query)
    comments = collect_video_comments(
        crawler,
        normalized,
        args.comments_limit,
        args.comments_fetch_limit,
        args.comments_sort,
        keywords_for_comments,
    )

    paths = write_reports(
        normalized,
        args.output_date,
        query=query,
        source=source,
        status_code=status_code,
        status_msg=status_msg,
        keyword_stats=keyword_stats,
        comments=comments,
        comments_settings={
            "limit": args.comments_limit,
            "fetch_limit": args.comments_fetch_limit,
            "sort": args.comments_sort,
            "keywords": keywords_for_comments,
        },
    )
    print(f"videos: {args.output_video.resolve()}")
    for label, path in paths.items():
        print(f"{label}: {path.resolve()}")


if __name__ == "__main__":
    main()
