from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .storage import manifest_path, safe_stem, write_json
from . import rnote, tikhub


PLATFORM_SCRIPTS = {
    "douyin": "crawl_douyin.py",
    "xiaohongshu": "crawl_xiaohongshu.py",
}
PLATFORM_LABELS = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}
PLATFORM_LOGIN_URLS = {
    "douyin": "https://www.douyin.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/explore",
}
SOCIAL_BINDINGS_FILE = settings.storage_dir / "social_accounts.json"


def canonical_platform(platform: str) -> str:
    value = str(platform or "").strip().lower()
    if value in {"xhs", "xiaohongshu"}:
        return "xiaohongshu"
    if value == "douyin":
        return "douyin"
    raise ValueError(f"unsupported platform: {platform}")


def load_bindings() -> dict[str, Any]:
    if not SOCIAL_BINDINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SOCIAL_BINDINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_bindings(bindings: dict[str, Any]) -> None:
    SOCIAL_BINDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_json(SOCIAL_BINDINGS_FILE, bindings)


def public_bindings() -> dict[str, Any]:
    rows = load_bindings()
    return {
        key: {
            "platform": key,
            "status": str(value.get("status") or "unbound"),
            "verified": bool(value.get("verified")),
            "verified_at": value.get("verified_at") or "",
            "last_checked_at": value.get("last_checked_at") or "",
            "account_name": value.get("account_name") or "",
            "page_url": value.get("page_url") or "",
            "reason": value.get("reason") or "",
        }
        for key, value in rows.items()
        if key in PLATFORM_SCRIPTS
    }


def open_local_login_page(platform: str) -> dict[str, Any]:
    """Open the platform login page in the Chrome instance on the API host.

    The social crawler and browser probe both run on the machine hosting the
    API.  Opening the URL from frontend JavaScript would instead open the
    visitor's browser, so this action deliberately stays server-side.
    """
    platform = canonical_platform(platform)
    url = PLATFORM_LOGIN_URLS[platform]
    if sys.platform != "darwin":
        raise RuntimeError("本机浏览器登录只支持运行 API 的 macOS 主机")
    try:
        subprocess.run(
            ["open", "-a", "Google Chrome", url],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("运行 API 的本机未找到 macOS open 命令") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stdout or "").strip()
        raise RuntimeError(detail or "无法在本机打开 Google Chrome") from exc
    return {
        "ok": True,
        "platform": platform,
        "url": url,
        "target": "api_host_google_chrome",
        "message": "已在运行 API 服务的本机 Google Chrome 中打开登录页",
    }


def _browser_probe(platform: str) -> dict[str, Any]:
    """Inspect an existing signed-in Chrome tab without reading cookie values."""
    platform = canonical_platform(platform)
    domain = "xiaohongshu.com" if platform == "xiaohongshu" else "douyin.com"
    if platform == "douyin":
        session_names = ["sessionid", "sessionid_ss", "sid_tt", "uid_tt", "uid_tt_ss"]
        avatar_selectors = [
            "img[class*='avatar']", "[class*='avatar'] img", "[class*='user-info'] img",
            "[class*='userInfo'] img", "a[href*='/user/'] img",
        ]
        name_selectors = ["[class*='user-name']", "[class*='nickname']", "[class*='userInfo']"]
    else:
        session_names = ["web_session"]
        avatar_selectors = [
            "img[class*='avatar']", "[class*='avatar'] img", "[class*='user'] img",
            "a[href*='/user/profile/'] img", "[class*='author'] img",
        ]
        name_selectors = ["[class*='user-name']", "[class*='username']", "[class*='nickname']"]
    js = f"""
(function() {{
  function visible(el) {{
    if (!el) return false;
    var r = el.getBoundingClientRect();
    var s = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  }}
  function clean(v) {{ return String(v || '').replace(/\\s+/g, ' ').trim(); }}
  var cookieNames = (document.cookie || '').split(';').map(function(v) {{ return v.split('=')[0].trim(); }}).filter(Boolean);
  var sessionNames = {json.dumps(session_names, ensure_ascii=False)};
  var sessionSignals = sessionNames.filter(function(name) {{ return cookieNames.indexOf(name) >= 0; }});
  var loginNodes = Array.from(document.querySelectorAll('button,a,span,div')).filter(function(el) {{
    if (!visible(el)) return false;
    var text = clean(el.innerText || el.textContent);
    return text === '登录' || text === '立即登录' || text === '手机号登录';
  }});
  var avatarSelectors = {json.dumps(avatar_selectors, ensure_ascii=False)};
  var avatars = [];
  avatarSelectors.forEach(function(selector) {{
    try {{ avatars = avatars.concat(Array.from(document.querySelectorAll(selector)).filter(visible)); }} catch (err) {{}}
  }});
  var accountName = '';
  var nameSelectors = {json.dumps(name_selectors, ensure_ascii=False)};
  for (var i = 0; i < nameSelectors.length && !accountName; i++) {{
    try {{
      var node = Array.from(document.querySelectorAll(nameSelectors[i])).find(visible);
      var text = clean(node && (node.innerText || node.textContent));
      if (text && text.length <= 60 && text !== '登录') accountName = text;
    }} catch (err) {{}}
  }}
  return JSON.stringify({{
    url: location.href,
    title: document.title || '',
    session_signal_count: sessionSignals.length,
    login_button_count: loginNodes.length,
    avatar_count: avatars.length,
    account_name: accountName
  }});
}})()
""".strip()
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(js)
        js_path = Path(handle.name)
    osa = f'''
set jsCode to read POSIX file "{js_path}" as «class utf8»
tell application "Google Chrome"
  if (count of windows) is 0 then return "NO_CHROME_WINDOW"
  repeat with w in windows
    repeat with i from 1 to count of tabs of w
      set t to tab i of w
      if (URL of t) contains "{domain}" then
        return execute t javascript jsCode
      end if
    end repeat
  end repeat
  return "NO_PLATFORM_TAB"
end tell
'''
    try:
        output = subprocess.check_output(
            ["osascript", "-e", osa],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=30,
        ).strip()
    finally:
        js_path.unlink(missing_ok=True)
    if output in {"NO_CHROME_WINDOW", "NO_PLATFORM_TAB"}:
        return {"verified": False, "reason": "没有找到已打开的平台登录页", "probe": output}
    try:
        probe = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 Chrome 登录状态：{output[:300]}") from exc
    verified = bool(
        int(probe.get("session_signal_count") or 0) > 0
        or (
            int(probe.get("avatar_count") or 0) > 0
            and int(probe.get("login_button_count") or 0) == 0
        )
    )
    reason = "已检测到有效平台会话" if verified else "页面仍显示登录入口，或没有检测到账号会话"
    return {"verified": verified, "reason": reason, **probe}


def verify_browser_login(platform: str) -> dict[str, Any]:
    platform = canonical_platform(platform)
    checked_at = datetime.now().isoformat(timespec="seconds")
    try:
        probe = _browser_probe(platform)
    except Exception as exc:
        probe = {"verified": False, "reason": str(exc), "url": "", "account_name": ""}
    bindings = load_bindings()
    previous = bindings.get(platform) or {}
    row = {
        "platform": platform,
        "status": "verified" if probe.get("verified") else "invalid",
        "verified": bool(probe.get("verified")),
        "verified_at": checked_at if probe.get("verified") else previous.get("verified_at") or "",
        "last_checked_at": checked_at,
        "account_name": probe.get("account_name") or previous.get("account_name") or "",
        "page_url": probe.get("url") or "",
        "reason": probe.get("reason") or "",
        "detector": {
            "session_signal_count": int(probe.get("session_signal_count") or 0),
            "login_button_count": int(probe.get("login_button_count") or 0),
            "avatar_count": int(probe.get("avatar_count") or 0),
        },
    }
    bindings[platform] = row
    save_bindings(bindings)
    return row


def clear_binding(platform: str) -> dict[str, Any]:
    platform = canonical_platform(platform)
    bindings = load_bindings()
    bindings.pop(platform, None)
    save_bindings(bindings)
    return {"ok": True, "platform": platform, "status": "unbound"}


def require_verified_binding(platform: str) -> dict[str, Any]:
    result = verify_browser_login(platform)
    if not result.get("verified"):
        raise RuntimeError(
            f"{PLATFORM_LABELS.get(canonical_platform(platform), platform)}账号没有通过真实登录检测："
            f"{result.get('reason') or '请重新登录'}"
        )
    return result


def python_executable() -> str:
    candidate = settings.crawler_dir / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def run_command(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or f"command failed: {' '.join(cmd)}")
    return result.stdout


def item_matches_platform(item: dict[str, Any], platform: str) -> bool:
    item_platform = str(item.get("platform") or "")
    if item_platform:
        return item_platform == platform
    url = str(item.get("url") or item.get("video_url") or "")
    if platform == "xiaohongshu":
        return "xiaohongshu.com" in url or bool(item.get("note_id"))
    if "xiaohongshu.com" in url or item.get("note_id"):
        return False
    return "douyin.com" in url or bool(item.get("aweme_id")) or not url


def report_path_matches_platform(path: Path, platform: str) -> bool:
    has_xhs_part = "xiaohongshu" in path.parts
    return has_xhs_part if platform == "xiaohongshu" else not has_xhs_part


def provider_for(platform: str, override: str = "") -> str:
    """Select the social search backend without coupling the UI to a crawler."""
    platform = canonical_platform(platform)
    configured_provider = str(override or settings.social_search_provider or "auto").strip().lower()
    if configured_provider in {"browser", "browser_extension", "local_browser", "target_host"}:
        return "browser_extension"
    if configured_provider == "local":
        return "local"
    if configured_provider == "tikhub":
        return "tikhub" if tikhub.configured() else "unavailable"
    if configured_provider == "rnote":
        return "rnote" if platform == "xiaohongshu" and rnote.configured() else "unavailable"
    # auto: use the configured server-side providers without browser login.
    # Keep Rnote ahead of TikHub for installations that explicitly configured
    # it before TikHub added Xiaohongshu support.
    if platform == "douyin" and tikhub.configured():
        return "tikhub"
    if platform == "xiaohongshu" and rnote.configured():
        return "rnote"
    if platform == "xiaohongshu" and tikhub.configured():
        return "tikhub"
    return "local"


def search_available(platform: str = "") -> bool:
    platforms = [canonical_platform(platform)] if platform else list(PLATFORM_SCRIPTS)
    return any(
        provider_for(item_platform) == "tikhub"
        or provider_for(item_platform) == "rnote"
        or (
            provider_for(item_platform) == "local"
            and (settings.crawler_dir / PLATFORM_SCRIPTS[item_platform]).exists()
        )
        for item_platform in platforms
    )


def public_search_config() -> dict[str, Any]:
    return {
        "provider": settings.social_search_provider,
        "douyin_provider": provider_for("douyin"),
        "xiaohongshu_provider": provider_for("xiaohongshu"),
        "tikhub_configured": tikhub.configured(),
        "tikhub_base_url": settings.tikhub_base_url if tikhub.configured() else "",
        "rnote_configured": rnote.configured(),
        "rnote_base_url": settings.rnote_base_url if rnote.configured() else "",
    }


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_crawler_report(platform: str, since: float = 0) -> Path:
    reports = sorted(
        [
            path
            for path in settings.crawler_dir.glob("output_date/**/douyin_video_metadata.json")
            if path.stat().st_mtime >= since and report_path_matches_platform(path, platform)
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in reports:
        try:
            data = load_report(path)
        except Exception:
            continue
        items = data.get("items") or []
        if all(item_matches_platform(item, platform) for item in items):
            return path
    raise RuntimeError(f"{platform} crawler did not write a matching report")


def engagement_score(item: dict[str, Any]) -> float:
    return (
        float(item.get("digg_count") or 0)
        + float(item.get("comment_count") or 0) * 1.5
        + float(item.get("share_count") or 0) * 2
    )


def normalize_items(items: list[dict[str, Any]], platform: str, limit: int) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for item in items:
        if not item_matches_platform(item, platform):
            continue
        key = str(item.get("aweme_id") or item.get("note_id") or item.get("url") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        row = dict(item)
        row["platform"] = platform
        row["platform_label"] = PLATFORM_LABELS.get(platform, platform)
        row["title"] = row.get("title") or row.get("desc") or ""
        row["desc"] = row.get("desc") or row.get("title") or ""
        row["interaction_score"] = round(engagement_score(row), 1)
        rows.append(row)
    rows.sort(key=lambda row: (float(row.get("interaction_score") or 0), -float(row.get("rank") or 9999)), reverse=True)
    for index, row in enumerate(rows[:limit], 1):
        row["rank"] = index
    return rows[:limit]


def normalize_report(report_path: Path, action: str, platform: str, log: str, limit: int) -> dict[str, Any]:
    report = load_report(report_path)
    items = normalize_items(report.get("items") or [], platform, limit)
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "source": "social_video_crawler",
        "platform": platform,
        "platform_label": PLATFORM_LABELS.get(platform, platform),
        "crawler_dir": str(settings.crawler_dir),
        "report_path": str(report_path),
        "query": report.get("query", ""),
        "requested_limit": limit,
        "count": len(items),
        "actual_count": len(items),
        "keyword_stats": report.get("keyword_stats") or [],
        "items": items,
        "log": log,
    }


def public_profile(platform: str, identifier: str, limit: int = 20) -> dict[str, Any]:
    platform = canonical_platform(platform)
    if platform != "xiaohongshu":
        raise ValueError("TikHub 公开账号资料当前只接入小红书")
    if not tikhub.configured():
        raise RuntimeError("小红书 TikHub 接口未配置，请检查 TIKHUB_API_KEY。")
    return tikhub.xiaohongshu_profile(identifier, limit=limit)


def search(
    platform: str,
    keyword: str,
    limit: int,
    deep: bool = False,
    *,
    provider_override: str = "",
) -> dict[str, Any]:
    platform = canonical_platform(platform)
    if platform not in PLATFORM_SCRIPTS:
        raise ValueError(f"unsupported platform: {platform}")
    provider = provider_for(platform, provider_override)
    if provider == "tikhub":
        manifest = (
            tikhub.search_xiaohongshu(keyword, limit, deep=deep)
            if platform == "xiaohongshu"
            else tikhub.search(keyword, limit, deep=deep)
        )
        out = manifest_path("search", platform, keyword)
        manifest["_manifest_path"] = str(write_json(out, manifest))
        if not manifest.get("items"):
            manifest["warning"] = (
                f"{PLATFORM_LABELS.get(platform, platform)}检索已完成，但没有找到匹配视频。"
                "请换一个更宽泛的关键词或调整筛选条件后重试。"
            )
        return manifest
    if provider == "rnote":
        manifest = rnote.search(keyword, limit, deep=deep, note_type=1)
        out = manifest_path("search", platform, keyword)
        manifest["_manifest_path"] = str(write_json(out, manifest))
        if not manifest.get("items"):
            manifest["warning"] = (
                f"{PLATFORM_LABELS.get(platform, platform)}检索已完成，但没有找到匹配视频。"
                "请换一个更宽泛的关键词或调整检索平台筛选后重试。"
            )
        return manifest
    if provider == "unavailable":
        raise RuntimeError("已指定 TikHub 搜索，但没有配置 TIKHUB_API_KEY")
    if not (settings.crawler_dir / PLATFORM_SCRIPTS[platform]).exists():
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "action": "search",
            "source": "missing_optional_crawler",
            "platform": platform,
            "platform_label": PLATFORM_LABELS.get(platform, platform),
            "query": keyword,
            "requested_limit": limit,
            "count": 0,
            "actual_count": 0,
            "items": [],
            "log": (
                f"未找到内置抓取器：{settings.crawler_dir / PLATFORM_SCRIPTS[platform]}。\n"
                "独立版可先使用手动导入链接或上传本地样本；真实平台检索需要配置 SOCIAL_CRAWLER_DIR。"
            ),
        }
        out = manifest_path("search", platform, keyword)
        manifest["_manifest_path"] = str(write_json(out, manifest))
        return manifest
    # A browser tab merely being open is not account binding. Re-check the
    # actual signed-in Chrome session immediately before every real crawl.
    require_verified_binding(platform)
    started_at = time.time() - 1
    cmd = [
        python_executable(),
        PLATFORM_SCRIPTS[platform],
        "--keywords",
        keyword,
        "--limit",
        str(limit),
        "--no-download",
        "--comments-limit",
        "0",
    ]
    if platform == "xiaohongshu" and not deep:
        cmd.append("--list-only")
    cmd.extend(["--date-folder", "--run-name", f"search_{platform}_{safe_stem(keyword)}"])
    log = run_command(cmd, settings.crawler_dir)
    manifest = normalize_report(latest_crawler_report(platform, started_at), "search", platform, log, limit)
    manifest["deep_enriched"] = bool(deep)
    if not manifest.get("items"):
        # A valid browser session can still return zero matches for a narrow
        # keyword.  That is a completed search with an empty result, not a
        # crawler/login failure.  Keep the result in the task record so the
        # UI can show "已完成 / 0 条" and the user can choose another keyword.
        manifest["empty_result"] = True
        manifest["warning"] = (
            f"{PLATFORM_LABELS.get(platform, platform)}检索已完成，但没有找到匹配视频。"
            "请换一个更宽泛的关键词或调整筛选条件后重试。"
        )
    out = manifest_path("search", platform, keyword)
    manifest["_manifest_path"] = str(write_json(out, manifest))
    return manifest


def download_selected(platform: str, keyword: str, urls: list[str], limit: int = 1) -> dict[str, Any]:
    platform = canonical_platform(platform)
    if platform not in PLATFORM_SCRIPTS:
        raise ValueError(f"unsupported platform: {platform}")
    clean_urls = [url.strip() for url in urls if url and url.strip()]
    if not clean_urls:
        raise ValueError("no urls selected")
    if not (settings.crawler_dir / PLATFORM_SCRIPTS[platform]).exists():
        items = [
            {
                "rank": index,
                "platform": platform,
                "platform_label": PLATFORM_LABELS.get(platform, platform),
                "url": url,
                "title": f"{keyword} 参考链接 {index}",
                "download_ok": False,
            }
            for index, url in enumerate(clean_urls[:limit], start=1)
        ]
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "action": "download",
            "source": "missing_optional_crawler",
            "platform": platform,
            "platform_label": PLATFORM_LABELS.get(platform, platform),
            "query": keyword,
            "count": len(items),
            "items": items,
            "downloaded_count": 0,
            "download_dir": "",
            "log": "未配置抓取器，不能从平台链接下载视频。请上传本地样本或配置 SOCIAL_CRAWLER_DIR。",
        }
        out = manifest_path("download", platform, keyword)
        manifest["_manifest_path"] = str(write_json(out, manifest))
        return manifest
    require_verified_binding(platform)
    started_at = time.time() - 1
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        urls_file = Path(handle.name)
        handle.write("\n".join(clean_urls) + "\n")
    try:
        cmd = [
            python_executable(),
            PLATFORM_SCRIPTS[platform],
            "--url-file",
            str(urls_file),
            "--limit",
            str(min(limit, len(clean_urls))),
            "--comments-limit",
            "0",
            "--date-folder",
            "--run-name",
            f"selected_{platform}_{safe_stem(keyword)}",
        ]
        log = run_command(cmd, settings.crawler_dir)
    finally:
        urls_file.unlink(missing_ok=True)
    manifest = normalize_report(latest_crawler_report(platform, started_at), "download-selected", platform, log, limit)
    downloaded = [item for item in manifest["items"] if item.get("download_ok") and item.get("download_path")]
    manifest["downloaded_count"] = len(downloaded)
    manifest["download_dir"] = str(Path(downloaded[0]["download_path"]).parent) if downloaded else ""
    out = manifest_path("download", platform, keyword)
    manifest["_manifest_path"] = str(write_json(out, manifest))
    if not manifest.get("items") or not manifest.get("downloaded_count"):
        raise RuntimeError(
            f"{PLATFORM_LABELS.get(platform, platform)}未能解析并下载选中的视频。"
            "链接可能已失效、作品不是视频，或当前浏览器登录会话无权访问详情页。"
        )
    return manifest


def preview_url(item: dict[str, Any]) -> str:
    for key in ("play_url", "video_url", "download_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("play_urls", "video_urls", "download_urls"):
        for value in item.get(key) or []:
            if value:
                return str(value)
    return ""


def cover_url(item: dict[str, Any]) -> str:
    for key in ("cover_url", "origin_cover_url", "dynamic_cover_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("cover_urls", "origin_cover_urls", "dynamic_cover_urls"):
        for value in item.get(key) or []:
            if value:
                return str(value)
    return ""
