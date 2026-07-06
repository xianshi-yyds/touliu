#!/usr/bin/env python3
"""Search logged-in Xiaohongshu pages and download video notes."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


BASE_DIR = Path(__file__).resolve().parent
PLATFORM = "xiaohongshu"
PUBLIC_FIELDS = [
    "platform",
    "rank",
    "source_keyword",
    "aweme_id",
    "note_id",
    "url",
    "author_nickname",
    "author_uid",
    "author_id",
    "author_sec_uid",
    "publish_time",
    "create_time",
    "title",
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
class XhsSearchResult:
    items: list[dict[str, Any]]
    status_code: int | None
    status_msg: str
    keyword_stats: list[dict[str, Any]]


@dataclass(frozen=True)
class XhsSearchPlan:
    sort_label: str
    time_label: str
    boost: float


def sanitize_filename(filename: str, max_length: int = 100) -> str:
    filename = re.sub(r'[<>"/\\|?*:\n\r\t]', "_", filename)
    filename = filename.strip(" .")
    if len(filename) > max_length:
        filename = filename[:max_length]
    return filename or "untitled"


def split_keywords(value: str) -> list[str]:
    parts = re.split(r"[,，\n]+", value or "")
    return [part.strip() for part in parts if part.strip()] or ["车展"]


def expand_related_keywords(keywords: list[str]) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()
    suffixes = ["", " 视频", " 现场", " 实拍", " vlog"]
    for keyword in keywords:
        for suffix in suffixes:
            value = f"{keyword}{suffix}".strip()
            if value and value not in seen:
                seen.add(value)
                variants.append(value)
    return variants


def xhs_search_plans() -> list[XhsSearchPlan]:
    return [
        XhsSearchPlan("最多点赞", "半年内", 1.0),
    ]


def build_filter_js(plan: XhsSearchPlan) -> str:
    options = json.dumps(
        {
            "sort": plan.sort_label,
            "time": plan.time_label,
            "type": "视频",
        },
        ensure_ascii=False,
    )
    return f"""
(function () {{
  var options = {options};
  function clean(value) {{ return String(value || "").replace(/\\s+/g, " ").trim(); }}
  function visible(node) {{
    if (!node) return false;
    var rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }}
  function fire(node) {{
    if (!node) return false;
    var rect = node.getBoundingClientRect();
    var x = rect.left + rect.width / 2;
    var y = rect.top + rect.height / 2;
    var target = document.elementFromPoint(x, y) || node;
    ["pointerdown", "mousedown", "mouseup", "click"].forEach(function (type) {{
      target.dispatchEvent(new MouseEvent(type, {{
        bubbles: true,
        cancelable: true,
        view: window,
        clientX: x,
        clientY: y
      }}));
    }});
    return true;
  }}
  var filter = document.querySelector(".filter") || Array.from(document.querySelectorAll("*")).find(function (node) {{
    return visible(node) && clean(node.innerText || node.textContent) === "筛选";
  }});
  if (filter && !document.querySelector(".filter-panel")) fire(filter);
  setTimeout(function () {{
    function clickGroup(groupLabel, optionLabel) {{
      if (!optionLabel) return false;
      var groups = Array.from(document.querySelectorAll(".filter-panel .filters"));
      var group = groups.find(function (node) {{ return clean(node.innerText).indexOf(groupLabel) >= 0; }});
      if (!group) return false;
      var tags = Array.from(group.querySelectorAll(".tags"));
      var tag = tags.find(function (node) {{ return clean(node.innerText) === optionLabel; }});
      if (!tag) return false;
      if (!/active/.test(String(tag.className || ""))) fire(tag);
      return true;
    }}
    clickGroup("排序依据", options.sort);
    clickGroup("笔记类型", options.type);
    clickGroup("发布时间", options.time);
  }}, 300);
  return "queued";
}})()
"""


def compact_int(value: Any) -> int | str:
    text = str(value or "").strip()
    if not text or text in {"赞", "评论", "收藏", "分享", "回复"}:
        return ""
    text = text.replace(",", "")
    match = re.search(r"([\d.]+)\s*万", text)
    if match:
        return int(float(match.group(1)) * 10000)
    match = re.search(r"([\d.]+)\s*k", text, re.I)
    if match:
        return int(float(match.group(1)) * 1000)
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else ""


def duration_to_ms(value: str | float | int) -> int:
    if isinstance(value, (int, float)):
        return int(float(value) * 1000)
    text = str(value or "").strip()
    if not text:
        return 0
    parts = [int(part) for part in re.findall(r"\d+", text)]
    if len(parts) >= 2:
        return (parts[-2] * 60 + parts[-1]) * 1000
    if len(parts) == 1:
        return parts[0] * 1000
    return 0


def metric_number(value: Any) -> int:
    parsed = compact_int(value)
    return parsed if isinstance(parsed, int) else 0


def parse_publish_datetime(value: Any, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    text = re.sub(r"^编辑于\s*", "", str(value or "").strip())
    if not text:
        return None
    if "刚刚" in text:
        return now
    match = re.search(r"(\d+)\s*分钟前", text)
    if match:
        return now - timedelta(minutes=int(match.group(1)))
    match = re.search(r"(\d+)\s*小时前", text)
    if match:
        return now - timedelta(hours=int(match.group(1)))
    match = re.search(r"(\d+)\s*天前", text)
    if match:
        return now - timedelta(days=int(match.group(1)))
    match = re.search(r"昨天\s*(\d{1,2}):(\d{2})", text)
    if match:
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if match:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4) or 0),
            int(match.group(5) or 0),
        )
    match = re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if match:
        candidate = datetime(
            now.year,
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
            int(match.group(4) or 0),
        )
        if candidate > now + timedelta(days=7):
            candidate = candidate.replace(year=now.year - 1)
        return candidate
    return None


def search_score(row: dict[str, Any], keywords: list[str]) -> float:
    now = datetime.now()
    likes = metric_number(row.get("digg_count"))
    collects = metric_number(row.get("collect_count"))
    comments = metric_number(row.get("comment_count"))
    shares = metric_number(row.get("share_count"))
    engagement = likes + collects * 0.45 + comments * 0.3 + shares * 0.25
    engagement_score = math.log1p(engagement) * 120

    published_at = parse_publish_datetime(row.get("publish_time"), now)
    if published_at:
        age_days = max(0.0, (now - published_at).total_seconds() / 86400)
        recency_score = max(0.0, 1.0 - min(age_days, 180.0) / 180.0) * 180
        if age_days <= 7:
            recency_score += 90
    else:
        recency_score = 0.0

    text = f"{row.get('title') or ''} {row.get('desc') or ''}".lower()
    relevance_score = 0.0
    for keyword in keywords:
        keyword_text = keyword.lower()
        if keyword_text and keyword_text in text:
            relevance_score += 80

    source_filter = row.get("source_filter") or {}
    filter_boost = float(source_filter.get("boost") or 1.0)
    return (engagement_score + recency_score + relevance_score) * filter_boost


def rank_by_search_score(rows: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: search_score(row, keywords), reverse=True)
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return ranked


def rank_by_likes_with_half_year(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now()
    cutoff = now - timedelta(days=180)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        published_at = parse_publish_datetime(row.get("publish_time"), now)
        if published_at and published_at < cutoff:
            continue
        filtered.append(row)

    def sort_key(row: dict[str, Any]) -> tuple[int, float]:
        published_at = parse_publish_datetime(row.get("publish_time"), now)
        timestamp = published_at.timestamp() if published_at else 0.0
        return metric_number(row.get("digg_count")), timestamp

    ranked = sorted(filtered, key=sort_key, reverse=True)
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return ranked


def row_text(row: dict[str, Any]) -> str:
    return f"{row.get('title') or ''} {row.get('desc') or ''}".lower()


def has_keyword_text(row: dict[str, Any], keywords: list[str]) -> bool:
    text = row_text(row)
    for keyword in keywords:
        keyword = keyword.strip().lower()
        if not keyword:
            continue
        if keyword in text:
            return True
        if keyword.endswith("展") and len(keyword) > 2:
            stem = keyword[:-1]
            if len(stem) >= 2 and stem in text:
                return True
        if keyword in {"人工智能展", "ai展", "aigc展"} and any(term in text for term in ["人工智能", "ai", "aigc", "机器人", "waic"]):
            return True
        if keyword in {"车展", "汽车展", "汽车展会"} and any(term in text for term in ["车展", "汽车", "新能源车", "新车", "车型"]):
            return True
    return False


def is_obvious_keyword_mismatch(row: dict[str, Any], keywords: list[str]) -> bool:
    """Drop clear false positives such as searching 车展 and getting 台球展."""
    text = row_text(row)
    if not text or has_keyword_text(row, keywords):
        return False
    for keyword in keywords:
        keyword = keyword.strip().lower()
        if keyword in {"车展", "汽车展", "汽车展会"} and re.search(r"(台球|珠宝|家居|家具|宠物|婚博|动漫|漫展|游戏|摄影|美妆|食品|茶|花卉|服装|艺术)展", text):
            return True
    return False


def note_id_from_url(url: str) -> str:
    match = re.search(r"/(?:explore|search_result)/([0-9a-fA-F]+)", url)
    if match:
        return match.group(1)
    return ""


def run_osascript(script: str, timeout: int = 120) -> str:
    completed = subprocess.run(
        ["osascript"],
        input=script,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout.strip() or "AppleScript 执行失败")
    return completed.stdout.strip()


def apple_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_xhs_search_prepare_tab(keyword: str) -> None:
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&source=web_search_result_notes&type=51"
    script = f'''
tell application "Google Chrome"
  activate
  set targetTab to missing value
  repeat with w in windows
    repeat with i from 1 to count of tabs of w
      set t to tab i of w
      if (URL of t) contains "xiaohongshu.com/search_result" then
        set targetTab to t
        set active tab index of w to i
        exit repeat
      end if
    end repeat
    if targetTab is not missing value then exit repeat
  end repeat
  if targetTab is missing value then
    set targetTab to make new tab at end of tabs of window 1
    set active tab index of window 1 to (count of tabs of window 1)
  end if
  set URL of targetTab to {apple_string(search_url)}
end tell
'''
    try:
        run_osascript(script, timeout=30)
    except Exception:
        pass


def collect_existing_filtered_xhs_search(keywords: list[str], limit: int) -> XhsSearchResult:
    """Read the user's already-filtered Xiaohongshu search tab in page order."""
    keyword_set = {keyword.strip().lower() for keyword in keywords if keyword.strip()}
    collect_js = r"""
(function () {
  function clean(value) { return String(value || "").replace(/\s+/g, " ").trim(); }
  function compactInt(value) {
    var text = String(value || "").replace(/,/g, "");
    var match = text.match(/([\d.]+)\s*万/);
    if (match) return Math.round(parseFloat(match[1]) * 10000);
    match = text.match(/\d+/);
    return match ? parseInt(match[0], 10) : 0;
  }
  var bodyText = clean(document.body ? document.body.innerText : "");
  var currentKeyword = "";
  try {
    currentKeyword = decodeURIComponent(new URL(location.href).searchParams.get("keyword") || "");
  } catch (err) {}
  var covers = Array.from(document.querySelectorAll("a.cover[href*='/search_result/'], a.cover[href*='/explore/']"));
  var rows = covers.map(function (cover) {
    var card = cover.closest(".note-item") || cover.closest("section") || cover.parentElement || cover;
    var rect = card.getBoundingClientRect();
    var titleNode = card.querySelector(".title") || card.querySelector("[class*='title']");
    var authorNode = card.querySelector(".author .name") || card.querySelector("[class*='author'] [class*='name']");
    var timeNode = card.querySelector(".author .time") || card.querySelector("[class*='time']");
    var likeNode = card.querySelector(".like-wrapper .count") || card.querySelector("[class*='like'] [class*='count']");
    var html = cover.innerHTML || "";
    var hasVideoBadge =
      !!cover.querySelector(".play-icon, .video-icon, [class*='play'], [class*='video']") ||
      /#play-|play|video|播放|▶/.test(html);
    var likeText = likeNode ? clean(likeNode.innerText) : "";
    return {
      url: cover.href || cover.getAttribute("href") || "",
      title: titleNode ? clean(titleNode.innerText) : "",
      author_nickname: authorNode ? clean(authorNode.innerText) : "",
      publish_time_text: timeNode ? clean(timeNode.innerText) : "",
      digg_count_text: likeText,
      digg_count: compactInt(likeText),
      has_video_badge: hasVideoBadge,
      rect_top: Math.round(rect.top + window.scrollY),
      rect_left: Math.round(rect.left + window.scrollX),
      rect_width: Math.round(rect.width),
      rect_height: Math.round(rect.height)
    };
  }).filter(function (row) { return row.url && row.rect_width > 80 && row.rect_height > 100; });
  rows.sort(function (a, b) {
    return (a.rect_top - b.rect_top) || (a.rect_left - b.rect_left);
  });
  var seen = {};
  rows = rows.filter(function (row) {
    var id = (row.url.match(/\/(?:search_result|explore)\/([0-9a-fA-F]+)/) || [])[1] || row.url;
    if (seen[id]) return false;
    seen[id] = true;
    return true;
  });
  return JSON.stringify({
    url: location.href,
    title: document.title,
    keyword: currentKeyword,
    is_filtered: bodyText.indexOf("已筛选") >= 0,
    is_video_tab: /[?&]type=51(?:&|$)/.test(location.href) || bodyText.indexOf("视频") >= 0,
    rows: rows.slice(0, LIMIT_NUMBER)
  });
})()
"""
    collect_js = collect_js.replace("LIMIT_NUMBER", str(limit))
    list_script = '''
set outText to ""
tell application "Google Chrome"
  repeat with w in windows
    repeat with i from 1 to count of tabs of w
      set t to tab i of w
      set u to URL of t
      if u contains "xiaohongshu.com/search_result" then
        set isActive to ((active tab index of w) = i)
        set outText to outText & (((id of w) as text) & "," & (i as text) & "," & (isActive as text) & "@@XHS@@" & u & linefeed)
      end if
    end repeat
  end repeat
end tell
return outText
'''
    with tempfile.NamedTemporaryFile("w", suffix=".applescript", encoding="utf-8", delete=False) as handle:
        handle.write(list_script)
        list_script_path = Path(handle.name)
    try:
        completed = subprocess.run(
            ["osascript", str(list_script_path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
    finally:
        list_script_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout.strip() or "AppleScript 执行失败")
    tab_lines = completed.stdout.splitlines()

    candidates: list[dict[str, Any]] = []
    for line in tab_lines:
        if not line.strip() or "@@XHS@@" not in line:
            continue
        meta, _url = line.split("@@XHS@@", 1)
        window_id, tab_index, is_active = (meta.split(",", 2) + ["false"])[:3]
        try:
            payload = chrome_execute_existing_tab(int(window_id), int(tab_index), collect_js)
            data = json.loads(payload)
        except Exception:
            continue
        tab_keyword = str(data.get("keyword") or "").strip().lower()
        title = str(data.get("title") or "").lower()
        if keyword_set and tab_keyword not in keyword_set and not any(keyword in title for keyword in keyword_set):
            continue
        rows = list(data.get("rows") or [])
        if not data.get("is_filtered") or not data.get("is_video_tab") or not rows:
            continue
        candidates.append(
            {
                "window_id": window_id,
                "tab_index": tab_index,
                "is_active": is_active == "true",
                "keyword": tab_keyword,
                "rows": rows,
                "url": data.get("url") or "",
            }
        )

    if not candidates:
        keyword = keywords[0] if keywords else "车展"
        open_xhs_search_prepare_tab(keyword)
        return XhsSearchResult(
            [],
            1,
            "没有找到已筛选的小红书搜索页。我已帮你打开小红书搜索页，请在 Chrome 里选择「视频 / 最多点赞 / 半年内」，看到页面显示「已筛选」后再回到本页面点击开始爬取。",
            [],
        )

    candidates.sort(key=lambda item: (item["is_active"], len(item["rows"])), reverse=True)
    chosen = candidates[0]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in chosen["rows"]:
        note_id = note_id_from_url(item.get("url") or "")
        if not note_id or note_id in seen:
            continue
        seen.add(note_id)
        item["note_id"] = note_id
        item["source_keyword"] = chosen["keyword"] or (keywords[0] if keywords else "")
        item["source_filter"] = {
            "sort": "最多点赞",
            "time": "半年内",
            "source": "existing_filtered_tab",
        }
        rows.append(item)
        if len(rows) >= limit:
            break
    stats = [
        {
            "keyword": chosen["keyword"] or (keywords[0] if keywords else ""),
            "sort": "最多点赞",
            "time": "半年内",
            "source": "xiaohongshu_existing_filtered_page",
            "added": len(rows),
            "tab": f"{chosen['window_id']}/{chosen['tab_index']}",
        }
    ]
    return XhsSearchResult(rows, 0, "", stats)


class XhsChromeSession:
    def __init__(self) -> None:
        self.marker = f"xhs-crawler-{uuid.uuid4().hex}"
        self.closed = False
        init_js = f"window.name = {json.dumps(self.marker)}"
        script = f'''
tell application "Google Chrome"
  set workTab to make new tab at end of tabs of window 1 with properties {{URL:"about:blank"}}
  set active tab index of window 1 to (count of tabs of window 1)
  execute workTab javascript {apple_string(init_js)}
  return ((id of window 1) as text) & "," & ((active tab index of window 1) as text)
end tell
'''
        result = run_osascript(script, timeout=30)
        window_id, tab_index = result.split(",", 1)
        self.window_id = int(window_id)
        self.tab_index = int(tab_index)

    def execute(self, url: str, js_code: str, delay_seconds: int = 5, scrolls: int = 0) -> str:
        if self.closed:
            raise RuntimeError("Chrome 工作标签页已关闭")
        return chrome_execute_work_tab(
            self.window_id,
            self.tab_index,
            url,
            js_code,
            delay_seconds=delay_seconds,
            scrolls=scrolls,
        )

    def execute_with_filters(
        self,
        url: str,
        js_code: str,
        filter_js_code: str,
        delay_seconds: int = 5,
        scrolls: int = 0,
    ) -> str:
        if self.closed:
            raise RuntimeError("Chrome 工作标签页已关闭")
        return chrome_execute_work_tab(
            self.window_id,
            self.tab_index,
            url,
            js_code,
            delay_seconds=delay_seconds,
            scrolls=scrolls,
            pre_js_code=filter_js_code,
        )

    def execute_filtered_search(
        self,
        url: str,
        js_code: str,
        plan: XhsSearchPlan,
        delay_seconds: int = 5,
        scrolls: int = 0,
    ) -> str:
        if self.closed:
            raise RuntimeError("Chrome 工作标签页已关闭")
        return chrome_execute_work_tab(
            self.window_id,
            self.tab_index,
            url,
            js_code,
            delay_seconds=delay_seconds,
            scrolls=scrolls,
            pre_js_code=build_filter_js(plan),
        )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        script = f'''
tell application "Google Chrome"
  try
    close tab {self.tab_index} of (first window whose id is {self.window_id})
    return "closed"
  on error
    return "missing"
  end try
end tell
'''
        try:
            run_osascript(script, timeout=30)
        except Exception:
            pass

    def __enter__(self) -> "XhsChromeSession":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def chrome_execute_work_tab(
    window_id: int,
    tab_index: int,
    url: str,
    js_code: str,
    delay_seconds: int = 5,
    scrolls: int = 0,
    pre_js_code: str = "",
) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(js_code)
        js_path = Path(handle.name)
    pre_js_path: Path | None = None
    if pre_js_code:
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(pre_js_code)
            pre_js_path = Path(handle.name)
    pre_js_block = ""
    if pre_js_path:
        pre_js_block = f'''
  set preJsCode to read (POSIX file "{pre_js_path}") as «class utf8»
  execute workTab javascript preJsCode
  delay 3
'''
    script = f'''
set jsCode to read (POSIX file "{js_path}") as «class utf8»
tell application "Google Chrome"
  set targetWindow to first window whose id is {window_id}
  set workTab to tab {tab_index} of targetWindow
  set URL of workTab to {apple_string(url)}
  delay {delay_seconds}
{pre_js_block}
  repeat {scrolls} times
    execute workTab javascript "window.scrollBy(0, Math.max(900, Math.floor(window.innerHeight * 0.9)))"
    delay 1
  end repeat
  set resultText to execute workTab javascript jsCode
  return resultText
end tell
'''
    try:
        return run_osascript(script)
    finally:
        js_path.unlink(missing_ok=True)
        if pre_js_path:
            pre_js_path.unlink(missing_ok=True)


def chrome_execute_existing_tab(window_id: int, tab_index: int, js_code: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(js_code)
        js_path = Path(handle.name)
    script = f'''
set jsCode to read (POSIX file "{js_path}") as «class utf8»
tell application "Google Chrome"
  set targetWindow to first window whose id is {window_id}
  set workTab to tab {tab_index} of targetWindow
  set resultText to execute workTab javascript jsCode
  return resultText
end tell
'''
    try:
        return run_osascript(script, timeout=30)
    finally:
        js_path.unlink(missing_ok=True)


SEARCH_JS = r"""
(function () {
  function clean(value) { return String(value || "").replace(/\s+/g, " ").trim(); }
  function firstImageUrl(root) {
    var img = root.querySelector("img");
    if (img) {
      return img.currentSrc || img.src || img.getAttribute("data-src") || img.getAttribute("data-original") || "";
    }
    var bg = "";
    try { bg = window.getComputedStyle(root).backgroundImage || ""; } catch (err) {}
    var match = bg.match(/url\(["']?([^"')]+)["']?\)/);
    return match ? match[1] : "";
  }
  var bodyText = clean(document.body ? document.body.innerText : "");
  var covers = Array.from(document.querySelectorAll("a.cover[href*='/search_result/'], a.cover[href*='/explore/']"));
  var rows = covers.map(function (cover) {
    var card = cover.closest(".note-item") || cover.closest("section") || cover.parentElement || cover;
    var rect = card.getBoundingClientRect();
    var titleNode = card.querySelector(".title") || card.querySelector("[class*='title']");
    var authorNode = card.querySelector(".author .name") || card.querySelector("[class*='author'] [class*='name']");
    var timeNode = card.querySelector(".author .time") || card.querySelector("[class*='time']");
    var likeNode = card.querySelector(".like-wrapper .count") || card.querySelector("[class*='like'] [class*='count']");
    var html = cover.innerHTML || "";
    var hasVideoBadge =
      !!cover.querySelector(".play-icon, .video-icon, [class*='play'], [class*='video']") ||
      /#play-|play|video|播放|▶/.test(html);
    return {
      url: cover.href || cover.getAttribute("href") || "",
      title: titleNode ? clean(titleNode.innerText) : "",
      author_nickname: authorNode ? clean(authorNode.innerText) : "",
      publish_time_text: timeNode ? clean(timeNode.innerText) : "",
      digg_count_text: likeNode ? clean(likeNode.innerText) : "",
      cover_url: firstImageUrl(cover) || firstImageUrl(card),
      has_video_badge: hasVideoBadge,
      rect_top: Math.round(rect.top + window.scrollY),
      rect_left: Math.round(rect.left + window.scrollX),
      rect_width: Math.round(rect.width),
      rect_height: Math.round(rect.height)
    };
  }).filter(function (row) { return row.url && row.rect_width > 80 && row.rect_height > 100; });
  rows.sort(function (a, b) {
    return (a.rect_top - b.rect_top) || (a.rect_left - b.rect_left);
  });
  var seen = {};
  rows = rows.filter(function (row) {
    var id = (row.url.match(/\/(?:search_result|explore)\/([0-9a-fA-F]+)/) || [])[1] || row.url;
    if (seen[id]) return false;
    seen[id] = true;
    return true;
  });
  var isVideoTab = false;
  try {
    isVideoTab = new URL(location.href).searchParams.get("type") === "51";
  } catch (err) {
    isVideoTab = /[?&]type=51(?:&|$)/.test(location.href);
  }
  var videoRows = rows.filter(function (row) { return row.has_video_badge; });
  if (!isVideoTab && videoRows.length > 0) rows = videoRows;
  return JSON.stringify({
    rows: rows,
    url: location.href,
    title: document.title,
    is_filtered: bodyText.indexOf("已筛选") >= 0,
    is_video_tab: isVideoTab
  });
})()
"""


DETAIL_JS = r"""
(function () {
  function clean(value) { return String(value || "").replace(/\s+/g, " ").trim(); }
  function meta(name) {
    var node = document.querySelector('meta[property="' + name + '"]') || document.querySelector('meta[name="' + name + '"]');
    return node ? (node.content || "") : "";
  }
  var videos = Array.from(document.querySelectorAll("video")).map(function (video) {
    return {
      src: video.currentSrc || video.src || "",
      poster: video.poster || "",
      duration: video.duration || 0
    };
  });
  var resourceVideos = performance.getEntriesByType("resource").map(function (entry) {
    return entry.name || "";
  }).filter(function (name) {
    return /\.mp4(\?|$)/.test(name) || name.indexOf("sns-video") >= 0;
  });
  var noteUrl = meta("og:url") || location.href;
  var rawTitle = meta("og:title") || clean((document.querySelector(".title") || {}).innerText);
  var title = rawTitle.replace(/\s*-\s*小红书\s*$/, "");
  var desc = meta("description") || clean((document.querySelector(".desc") || {}).innerText);
  var author = clean((document.querySelector(".author-container .username") || document.querySelector(".author .username") || {}).innerText);
  var date = clean((document.querySelector(".note-content .date") || document.querySelector(".date") || {}).innerText);
  return JSON.stringify({
    url: location.href,
    note_url: noteUrl,
    note_id: (location.href.match(/\/(?:explore|search_result)\/([0-9a-fA-F]+)/) || noteUrl.match(/\/explore\/([0-9a-fA-F]+)/) || [])[1] || "",
    title: title,
    desc: desc,
    author_nickname: author,
    publish_time_text: date,
    digg_count: meta("og:xhs:note_like"),
    collect_count: meta("og:xhs:note_collect"),
    comment_count: meta("og:xhs:note_comment"),
    share_count: meta("og:xhs:note_share") || "",
    duration_text: meta("og:videotime"),
    video_url: meta("og:video") || resourceVideos[0] || "",
    video_tag: videos[0] || {},
    og_type: meta("og:type")
  });
})()
"""


COMMENTS_JS = r"""
(function () {
  function clean(value) { return String(value || "").replace(/\s+/g, " ").trim(); }
  function compactInt(value) {
    var text = String(value || "").replace(/,/g, "");
    var match = text.match(/([\d.]+)\s*万/);
    if (match) return Math.round(parseFloat(match[1]) * 10000);
    match = text.match(/([\d.]+)\s*k/i);
    if (match) return Math.round(parseFloat(match[1]) * 1000);
    match = text.match(/\d+/);
    return match ? parseInt(match[0], 10) : 0;
  }
  function firstText(root, selectors) {
    for (var i = 0; i < selectors.length; i += 1) {
      var node = root.querySelector(selectors[i]);
      var text = clean(node ? node.innerText || node.textContent : "");
      if (text) return text;
    }
    return "";
  }
  function firstAttr(root, selectors, attr) {
    for (var i = 0; i < selectors.length; i += 1) {
      var node = root.querySelector(selectors[i]);
      var value = node ? node.getAttribute(attr) || "" : "";
      if (value) return value;
    }
    return "";
  }
  function looksLikeCommentText(text) {
    if (!text || text.length < 2) return false;
    if (/^(赞|回复|评论|收藏|分享|展开|收起|登录后查看更多评论)$/.test(text)) return false;
    return true;
  }
  var scrollTargets = Array.from(document.querySelectorAll("*")).filter(function (node) {
    var style = window.getComputedStyle(node);
    return /(auto|scroll)/.test(style.overflowY || "") && node.scrollHeight > node.clientHeight + 80;
  }).slice(0, 6);
  scrollTargets.forEach(function (node) { node.scrollTop = node.scrollHeight; });

  var roots = Array.from(document.querySelectorAll(
    ".comment-item, .parent-comment, .comment-inner-container, [class*='comment-item'], [class*='commentItem'], [class*='parent-comment']"
  ));
  if (!roots.length) {
    roots = Array.from(document.querySelectorAll("[class*='comment'] [class*='content'], [class*='comment'] .content")).map(function (node) {
      return node.closest("[class*='comment']") || node;
    });
  }
  var seen = {};
  var rows = [];
  roots.forEach(function (root, index) {
    var rect = root.getBoundingClientRect();
    if (rect.width < 80 || rect.height < 12) return;
    var text = firstText(root, [
      ".content",
      ".comment-content",
      ".note-text",
      "[class*='comment-content']",
      "[class*='content']"
    ]);
    if (!looksLikeCommentText(text)) {
      var raw = clean(root.innerText || root.textContent || "");
      var parts = raw.split(/\s+(?:回复|赞|展开|收起|\d+\s*条回复)\s*/);
      text = clean(parts[0] || raw);
    }
    if (!looksLikeCommentText(text)) return;
    var user = firstText(root, [
      ".user-name",
      ".name",
      ".author",
      "[class*='user-name']",
      "[class*='username']",
      "[class*='name']"
    ]);
    if (user && text.indexOf(user) === 0) text = clean(text.slice(user.length));
    if (!looksLikeCommentText(text)) return;
    var date = firstText(root, [
      ".date",
      ".time",
      "[class*='date']",
      "[class*='time']"
    ]);
    var likeText = firstText(root, [
      ".like .count",
      ".like-wrapper .count",
      "[class*='like'] [class*='count']",
      "[class*='like']",
      "[class*='digg']"
    ]);
    var rawText = clean(root.innerText || "");
    var replyMatch = rawText.match(/(\d+)\s*条回复/);
    var cid = root.getAttribute("id") || root.getAttribute("data-id") || firstAttr(root, ["a[href*='comment']"], "href") || "";
    var key = cid || (user + "::" + text).slice(0, 180);
    if (seen[key]) return;
    seen[key] = true;
    rows.push({
      cid: cid,
      text: text,
      publish_time_text: date,
      digg_count: compactInt(likeText),
      reply_comment_total: replyMatch ? parseInt(replyMatch[1], 10) : 0,
      user_nickname: user,
      raw_index: index + 1
    });
  });
  return JSON.stringify({ comments: rows.slice(0, LIMIT_NUMBER) });
})()
"""


def run_xhs_search(
    session: XhsChromeSession,
    keywords: list[str],
    limit: int,
    plans: list[XhsSearchPlan] | None = None,
) -> XhsSearchResult:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    stats: list[dict[str, Any]] = []
    plans = plans or [XhsSearchPlan("综合", "不限", 1.0)]
    for keyword in keywords:
        for plan in plans:
            before = len(rows)
            url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&source=web_search_result_notes&type=51"
            scrolls = min(max(limit // 6, 3), 18)
            data = json.loads(
                session.execute_filtered_search(
                    url,
                    SEARCH_JS,
                    plan,
                    delay_seconds=5,
                    scrolls=scrolls,
                )
                or "{}"
            )
            if plan.sort_label == "最多点赞" and plan.time_label == "半年内" and not data.get("is_filtered"):
                stats.append(
                    {
                        "keyword": keyword,
                        "sort": plan.sort_label,
                        "time": plan.time_label,
                        "source": "xiaohongshu_auto_filtered_page",
                        "added": 0,
                        "status": "auto_filter_not_confirmed",
                    }
                )
                continue
            for item in data.get("rows") or []:
                note_id = note_id_from_url(item.get("url") or "")
                if not note_id or note_id in seen:
                    continue
                seen.add(note_id)
                item["note_id"] = note_id
                item["source_keyword"] = keyword
                item["source_filter"] = {
                    "sort": plan.sort_label,
                    "time": plan.time_label,
                    "boost": plan.boost,
                }
                rows.append(item)
                if len(rows) >= limit:
                    break
            stats.append(
                {
                    "keyword": keyword,
                    "sort": plan.sort_label,
                    "time": plan.time_label,
                    "source": "xiaohongshu_auto_filtered_page",
                    "added": len(rows) - before,
                    "is_filtered": bool(data.get("is_filtered")),
                }
            )
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break
    if not rows:
        return XhsSearchResult(
            [],
            1,
            "自动筛选小红书搜索页没有生效。请在 Chrome 里确认小红书已登录；如果页面没有显示「已筛选」，先手动选择「视频 / 最多点赞 / 半年内」后再点击开始爬取。",
            stats,
        )
    return XhsSearchResult(rows, 0, "", stats)


def parse_xhs_detail(
    session: XhsChromeSession,
    url: str,
    source_keyword: str = "",
    source_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = url.replace("&xsec_source=", "&xsec_source=pc_search")
    detail = json.loads(session.execute(url, DETAIL_JS, delay_seconds=5, scrolls=0) or "{}")
    note_id = detail.get("note_id") or note_id_from_url(url)
    duration_ms = duration_to_ms(detail.get("duration_text") or (detail.get("video_tag") or {}).get("duration") or 0)
    return {
        "platform": PLATFORM,
        "source_keyword": source_keyword,
        "source_filter": source_filter or {},
        "aweme_id": note_id,
        "note_id": note_id,
        "url": detail.get("note_url") or detail.get("url") or url,
        "author_nickname": detail.get("author_nickname") or "",
        "author_uid": "",
        "author_id": "",
        "author_sec_uid": "",
        "publish_time": detail.get("publish_time_text") or "",
        "create_time": 0,
        "title": detail.get("title") or "",
        "desc": detail.get("desc") or detail.get("title") or "",
        "digg_count": compact_int(detail.get("digg_count")),
        "comment_count": compact_int(detail.get("comment_count")),
        "play_count": "",
        "collect_count": compact_int(detail.get("collect_count")),
        "share_count": compact_int(detail.get("share_count")),
        "duration_ms": duration_ms,
        "content_type": "video" if detail.get("video_url") else "image",
        "play_url": detail.get("video_url") or "",
        "play_urls": [detail.get("video_url")] if detail.get("video_url") else [],
    }


def read_url_file(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def normalize_item(item: dict[str, Any], rank: int) -> dict[str, Any]:
    row = dict(item)
    row["rank"] = rank
    row["platform"] = PLATFORM
    row["aweme_id"] = row.get("aweme_id") or row.get("note_id") or ""
    row["note_id"] = row.get("note_id") or row.get("aweme_id") or ""
    row["title"] = row.get("title") or ""
    row["desc"] = row.get("desc") or row.get("title") or ""
    row["publish_time"] = row.get("publish_time") or row.get("publish_time_text") or ""
    row["digg_count"] = row.get("digg_count") if row.get("digg_count") not in (None, "") else compact_int(row.get("digg_count_text"))
    row["comment_count"] = row.get("comment_count") or 0
    row["collect_count"] = row.get("collect_count") or 0
    row["share_count"] = row.get("share_count") or 0
    row["duration_ms"] = row.get("duration_ms") or 0
    row["content_type"] = row.get("content_type") or "video"
    return {field: row.get(field, "") for field in PUBLIC_FIELDS} | {
        "cover_url": row.get("cover_url") or "",
        "play_url": row.get("play_url") or "",
        "play_urls": row.get("play_urls") or [],
        "source_filter": row.get("source_filter") or {},
    }


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


def parse_comment_time(value: Any) -> tuple[int, str]:
    published_at = parse_publish_datetime(value)
    if not published_at:
        return 0, str(value or "")
    return int(published_at.timestamp()), published_at.strftime("%Y-%m-%d %H:%M:%S")


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


def collect_note_comments(
    session: XhsChromeSession,
    row: dict[str, Any],
    comments_limit: int,
    comments_fetch_limit: int,
    comments_sort: str,
    keywords: list[str],
) -> list[dict[str, Any]]:
    note_id = str(row.get("aweme_id") or row.get("note_id") or "")
    if comments_limit <= 0 or not note_id:
        return []
    fetch_limit = max(comments_limit, comments_fetch_limit)
    js_code = COMMENTS_JS.replace("LIMIT_NUMBER", str(fetch_limit))
    scrolls = min(max(fetch_limit // 25, 8), 30)
    try:
        data = json.loads(session.execute(row.get("url") or "", js_code, delay_seconds=6, scrolls=scrolls) or "{}")
    except Exception as exc:
        print(f"小红书评论抓取失败：{note_id}，错误：{exc}")
        data = {}
    raw_comments: list[dict[str, Any]] = []
    for comment in data.get("comments") or []:
        create_time, publish_time = parse_comment_time(comment.get("publish_time_text"))
        raw_comments.append(
            {
                "cid": comment.get("cid") or "",
                "text": comment.get("text") or "",
                "create_time": create_time,
                "publish_time": publish_time,
                "digg_count": int(comment.get("digg_count") or 0),
                "reply_comment_total": int(comment.get("reply_comment_total") or 0),
                "ip_label": "",
                "user_uid": "",
                "user_sec_uid": "",
                "user_nickname": comment.get("user_nickname") or "",
                "user_unique_id": "",
            }
        )
    ranked_comments = rank_comments(raw_comments, comments_limit, comments_sort, keywords)
    out: list[dict[str, Any]] = []
    for index, comment in enumerate(ranked_comments, 1):
        out.append(
            {
                "rank": row.get("rank") or "",
                "aweme_id": note_id,
                "video_url": row.get("url") or "",
                "comment_rank": index,
                "raw_comment_rank": comment.get("raw_comment_rank") or "",
                "cid": comment.get("cid") or "",
                "text": comment.get("text") or "",
                "create_time": comment.get("create_time") or 0,
                "publish_time": comment.get("publish_time") or "",
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


def filter_comments_for_rows(comments: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank_by_id = {str(row.get("aweme_id") or row.get("note_id") or ""): row.get("rank") for row in rows}
    filtered: list[dict[str, Any]] = []
    for comment in comments:
        note_id = str(comment.get("aweme_id") or "")
        if note_id not in rank_by_id:
            continue
        row = dict(comment)
        row["rank"] = rank_by_id[note_id]
        filtered.append(row)
    return filtered


def file_has_video_stream(path: str) -> bool:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                path,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return "video" in completed.stdout
    except Exception:
        return True


def download_file(url: str, save_path: Path, referer: str) -> bool:
    part_path = Path(str(save_path) + ".part")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Referer": referer or "https://www.xiaohongshu.com/",
        "Accept": "*/*",
        "Accept-Encoding": "identity;q=1, *;q=0",
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }
    save_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, headers=headers, stream=True, timeout=60) as response:
            response.raise_for_status()
            with part_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if part_path.stat().st_size == 0:
            part_path.unlink(missing_ok=True)
            return False
        os.replace(part_path, save_path)
        return True
    except Exception as exc:
        part_path.unlink(missing_ok=True)
        print(f"小红书视频下载失败：{url}，错误：{exc}")
        return False


def download_items(items: list[dict[str, Any]], output_video: Path, desired_count: int) -> list[dict[str, Any]]:
    output_video.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for item in items:
        if len(results) >= desired_count:
            break
        row = dict(item)
        if row.get("content_type") != "video" or not row.get("play_url"):
            continue
        desc = sanitize_filename(row.get("title") or row.get("desc") or row["note_id"], max_length=70)
        file_path = output_video / f"{row['rank']:02d}_{row['note_id']}_{desc}.mp4"
        ok = False
        if file_path.exists() and file_path.stat().st_size > 0 and file_has_video_stream(str(file_path)):
            ok = True
        else:
            file_path.unlink(missing_ok=True)
            ok = download_file(row["play_url"], file_path, row.get("url") or "")
        row["video_stream_ok"] = bool(ok and file_path.exists() and file_has_video_stream(str(file_path)))
        row["download_ok"] = bool(ok and row["video_stream_ok"])
        row["download_path"] = str(file_path.resolve()) if row["download_ok"] else ""
        row["file_size_bytes"] = file_path.stat().st_size if row["download_ok"] and file_path.exists() else 0
        results.append(row)
    for rank, row in enumerate(results, 1):
        row["rank"] = rank
    return results


def write_reports(
    rows: list[dict[str, Any]],
    output_date: Path,
    query: str,
    source: str,
    status_code: int | None,
    status_msg: str,
    keyword_stats: list[dict[str, Any]],
    comments: list[dict[str, Any]] | None = None,
    comments_settings: dict[str, Any] | None = None,
) -> dict[str, Path]:
    output_date.mkdir(parents=True, exist_ok=True)
    public_rows = [{field: row.get(field, "") for field in PUBLIC_FIELDS} for row in rows]
    comment_rows = comments or []
    report = {
        "platform": PLATFORM,
        "query": query,
        "source": source,
        "crawled_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(public_rows),
        "comments_count": len(comment_rows),
        "comments_settings": comments_settings or {},
        "status_code": status_code,
        "status_msg": status_msg,
        "keyword_stats": keyword_stats,
        "items": public_rows,
    }

    json_path = output_date / "douyin_video_metadata.json"
    csv_path = output_date / "douyin_video_metadata.csv"
    md_path = output_date / "douyin_video_metadata.md"
    manifest_path = output_date / "download_manifest.json"
    comments_json_path = output_date / "douyin_video_comments.json"
    comments_csv_path = output_date / "douyin_video_comments.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    comments_json_path.write_text(
        json.dumps(
            {
                "query": query,
                "source": source,
                "crawled_at": report["crawled_at"],
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
    manifest_path.write_text(json.dumps(public_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Xiaohongshu Video Crawl", "", f"- Query: {query}", f"- Count: {len(public_rows)}", ""]
    for row in public_rows:
        lines.append(f"- {row.get('rank')}. {row.get('title') or row.get('desc')} | {row.get('author_nickname')} | {row.get('url')}")
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
    parser = argparse.ArgumentParser(description="Crawl Xiaohongshu video notes and metrics into local output folders.")
    parser.add_argument("--keywords", default="车展", help="Comma/newline separated search keywords.")
    parser.add_argument("--url-file", type=Path, help="Optional file with one Xiaohongshu URL per line.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum videos to download.")
    parser.add_argument("--output-video", type=Path, default=BASE_DIR / "output_video" / PLATFORM)
    parser.add_argument("--output-date", type=Path, default=BASE_DIR / "output_date" / PLATFORM)
    parser.add_argument("--date-folder", action="store_true", help="Write outputs under YYYY-MM-DD subfolders.")
    parser.add_argument("--run-name", default="", help="Optional child folder name under the date folder.")
    parser.add_argument("--no-download", action="store_true", help="Only collect metadata; skip mp4 downloads.")
    parser.add_argument("--list-only", action="store_true", help="When used with --no-download, keep search-list data only and do not open note detail pages.")
    parser.add_argument("--comments-limit", type=int, default=30, help="Maximum selected comments to save per video. Use 0 to disable.")
    parser.add_argument("--comments-fetch-limit", type=int, default=500, help="Maximum raw comments to collect per note before local ranking.")
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
            safe_run_name = sanitize_filename(args.run_name, max_length=50)
            args.output_video = args.output_video / safe_run_name
            args.output_date = args.output_date / safe_run_name

    status_code: int | None = None
    status_msg = ""
    keyword_stats: list[dict[str, Any]] = []
    comment_rows: list[dict[str, Any]] = []
    with XhsChromeSession() as session:
        if args.url_file:
            query = str(args.url_file)
            source = "xiaohongshu_detail_url_file"
            keywords_for_comments = comment_keywords(args.comments_keywords or args.keywords)
            raw = []
            for url in read_url_file(args.url_file):
                detail = parse_xhs_detail(session, url, "url_file")
                raw.append(detail)
                if detail.get("play_url"):
                    comment_rows.extend(
                        collect_note_comments(
                            session,
                            detail,
                            args.comments_limit,
                            args.comments_fetch_limit,
                            args.comments_sort,
                            keywords_for_comments,
                        )
                    )
        else:
            keywords = split_keywords(args.keywords)
            query = " / ".join(keywords)
            keywords_for_comments = comment_keywords(args.comments_keywords or query)
            candidate_limit = min(max(args.limit * 5, args.limit + 20), 100)
            search = collect_existing_filtered_xhs_search(keywords, candidate_limit)
            if search.status_code:
                auto_search = run_xhs_search(session, keywords, candidate_limit, xhs_search_plans())
                if auto_search.status_code:
                    raise SystemExit(auto_search.status_msg)
                search = auto_search
                source = "xiaohongshu_auto_filtered_page"
            else:
                source = "xiaohongshu_existing_filtered_page"
            raw = []
            if args.no_download and args.list_only:
                raw = search.items[: args.limit]
            else:
                video_detail_target = min(max(args.limit * 2, args.limit + 8), len(search.items))
                video_detail_count = 0
                for item in search.items:
                    try:
                        detail = parse_xhs_detail(
                            session,
                            item["url"],
                            item.get("source_keyword") or "",
                            item.get("source_filter") or {},
                        )
                        if is_obvious_keyword_mismatch(detail, keywords):
                            print(f"跳过明显不匹配的小红书候选：{item.get('url')}")
                            continue
                        if item.get("cover_url") and not detail.get("cover_url"):
                            detail["cover_url"] = item["cover_url"]
                        raw.append(detail)
                        if detail.get("play_url"):
                            comment_rows.extend(
                                collect_note_comments(
                                    session,
                                    detail,
                                    args.comments_limit,
                                    args.comments_fetch_limit,
                                    args.comments_sort,
                                    keywords_for_comments,
                                )
                            )
                        if detail.get("play_url"):
                            video_detail_count += 1
                        if video_detail_count >= video_detail_target:
                            break
                    except Exception as exc:
                        print(f"跳过小红书候选：{item.get('url')}，错误：{exc}")
            status_code = search.status_code
            status_msg = search.status_msg
            keyword_stats = search.keyword_stats

    normalized = [normalize_item(item, rank) for rank, item in enumerate(raw, 1)]
    if args.no_download:
        normalized = [row for row in normalized if row.get("content_type") == "video"][: args.limit]
        for rank, row in enumerate(normalized, 1):
            row["rank"] = rank
        for row in normalized:
            row["download_ok"] = False
            row["download_path"] = ""
            row["file_size_bytes"] = 0
    else:
        normalized = download_items(normalized, args.output_video, args.limit)
    comment_rows = filter_comments_for_rows(comment_rows, normalized)

    paths = write_reports(
        normalized,
        args.output_date,
        query=query,
        source=source,
        status_code=status_code,
        status_msg=status_msg,
        keyword_stats=keyword_stats,
        comments=comment_rows,
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
