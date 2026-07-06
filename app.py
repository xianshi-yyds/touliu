from __future__ import annotations

import os
from html import escape
from pathlib import Path

import streamlit as st

from exhibitflow_lite import pipeline, publisher, qwen, social
from exhibitflow_lite.config import settings
from exhibitflow_lite.render import copy_local_samples, import_links
from exhibitflow_lite.storage import ensure_storage, latest_manifest, read_json


ensure_storage()

st.set_page_config(page_title="ExhibitFlow Lite", page_icon="E", layout="wide")

st.markdown(
    """
    <style>
    :root {
      --ink:#17191f; --muted:#858b96; --line:#dfe4ed; --soft:#f6f7f9; --panel:#fff;
      --primary:#17191f; --blue:#3f46ff; --ok:#edf8df; --warn:#fff6d9;
    }
    [data-testid="stAppViewContainer"] { background: var(--soft); }
    .block-container { max-width: 1440px; padding-top: 32px; }
    h1,h2,h3 { letter-spacing: 0; color: var(--ink); }
    .ef-shell { background:#fff; border:1px solid var(--line); border-radius:12px; padding:28px 34px; }
    .ef-brand { display:flex; align-items:center; gap:12px; margin-bottom:24px; }
    .ef-logo { width:32px; height:32px; border-radius:9px; background:#17191f; color:#fff; display:grid; place-items:center; font-weight:800; }
    .ef-brand b { font-size:19px; }
    .ef-badge { border:1px solid #cce9bd; background:#e8f7dc; color:#3a8a19; border-radius:999px; padding:3px 8px; font-size:12px; font-weight:700; }
    .ef-note { border:1px solid var(--line); background:#fbfcff; border-radius:8px; padding:10px 12px; color:#333842; font-size:14px; margin:10px 0; }
    .ef-card-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; }
    .ef-rank { color:var(--muted); font-weight:800; }
    .ef-pill { border:1px solid var(--line); border-radius:999px; padding:2px 9px; font-size:12px; font-weight:800; background:#f8f9fb; }
    .ef-title { margin-top:10px; font-weight:800; color:var(--ink); line-height:1.45; min-height:42px; }
    .ef-meta { color:var(--muted); font-size:13px; line-height:1.5; margin-top:8px; }
    .ef-preview-empty { min-height:190px; border:1px solid var(--line); border-radius:8px; background:#f8f9fb; display:flex; align-items:center; justify-content:center; text-align:center; color:var(--muted); padding:18px; }
    .ef-cover img { width:100%; height:220px; object-fit:cover; border:1px solid var(--line); border-radius:8px; display:block; }
    .ef-video { width:100%; height:220px; border-radius:8px; border:1px solid var(--line); object-fit:cover; background:#000; display:block; }
    div.stButton > button { border-radius:7px; min-height:38px; font-weight:800; }
    </style>
    """,
    unsafe_allow_html=True,
)


def current_manifest() -> dict:
    if st.session_state.get("manifest"):
        return st.session_state["manifest"]
    return latest_manifest(action="search")


def selected_item(items: list[dict]) -> dict:
    selected_url = st.session_state.get("selected_url", "")
    for item in items:
        if item.get("url") == selected_url:
            return item
    return items[0] if items else {}


def render_preview(item: dict) -> None:
    local_path = item.get("download_path") if item.get("download_ok") else ""
    if local_path and os.path.exists(str(local_path)):
        st.video(str(local_path))
        return
    cover = social.cover_url(item)
    if cover:
        st.markdown(f'<div class="ef-cover"><img src="{escape(cover)}" alt="cover"></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="ef-preview-empty">列表里没有封面。可打开原链接确认，或换关键词重新检索。</div>', unsafe_allow_html=True)


def is_xhs_list_only(item: dict) -> bool:
    return item.get("platform") == "xiaohongshu" and not item.get("download_ok") and not social.preview_url(item)


def stat_text(item: dict) -> str:
    author = escape(str(item.get("author_nickname") or "未知"))
    likes = item.get("digg_count") or 0
    if is_xhs_list_only(item):
        return f"作者：{author}<br>赞 {likes}<br>列表页数据，评论/转发/收藏/时长需下载预览后补齐"
    return (
        f"作者：{author}<br>"
        f"赞 {likes} ｜ 评 {item.get('comment_count') or 0} ｜ 转 {item.get('share_count') or 0}<br>"
        f"互动分 {item.get('interaction_score') or 0}"
    )


def render_gallery(manifest: dict) -> None:
    items = manifest.get("items") or []
    if not items:
        st.info("暂无候选。先选择平台并检索。")
        return
    st.markdown(
        f"<div class='ef-note'>当前结果：{escape(Path(manifest.get('_manifest_path', '')).name or '未保存')} ｜ "
        f"{manifest.get('platform_label', '')} ｜ {len(items)} 条 ｜ 检索阶段不下载视频</div>",
        unsafe_allow_html=True,
    )
    cols = st.columns(4, gap="medium")
    for index, item in enumerate(items, 1):
        with cols[(index - 1) % 4]:
            platform_label = item.get("platform_label") or manifest.get("platform_label") or item.get("platform", "")
            checked = item.get("url") == st.session_state.get("selected_url", "")
            st.markdown(
                f"<div class='ef-card-head'><span class='ef-rank'>#{item.get('rank') or index}</span>"
                f"<span class='ef-pill'>{escape(str(platform_label))}</span></div>",
                unsafe_allow_html=True,
            )
            if st.button("当前参考样本" if checked else "设为参考样本", key=f"select-{index}", use_container_width=True):
                st.session_state["selected_url"] = item.get("url", "")
                st.rerun()
            render_preview(item)
            title = str(item.get("title") or item.get("desc") or "未命名视频")
            if len(title) > 64:
                title = title[:64] + "..."
            st.markdown(f"<div class='ef-title'>{escape(title)}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='ef-meta'>{stat_text(item)}</div>", unsafe_allow_html=True)
            if item.get("url"):
                st.link_button("打开原链接", item["url"], use_container_width=True)
            if st.button("下载预览", key=f"download-{index}", use_container_width=True):
                with st.spinner("正在下载选中样本..."):
                    downloaded = social.download_selected(
                        item.get("platform") or manifest.get("platform"),
                        manifest.get("query") or "selected",
                        [item["url"]],
                        limit=1,
                    )
                st.session_state["download_manifest"] = downloaded
                st.success(f"下载完成：{downloaded.get('downloaded_count', 0)} 条")
                st.rerun()


st.markdown("<div class='ef-shell'>", unsafe_allow_html=True)
st.markdown(
    """
    <div class="ef-brand">
      <span class="ef-logo">E</span>
      <b>ExhibitFlow Lite</b>
      <span>展会短视频工作台</span>
      <span class="ef-badge">AI Agent</span>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("配置状态")
    st.write(f"项目目录：`{settings.project_root}`")
    st.write(f"成片引擎：`{settings.render_engine}`")
    st.write(f"素材目录：`{settings.default_material_dir}`")
    st.caption("抓取器/发布器是可选插件；不配置也能手动导入样本并生成成片。")
    st.write(f"可选抓取器：`{settings.crawler_dir}`")
    st.write(f"可选发布器：`{settings.sau_bin}`")
    st.write(f"Qwen 文案：`{settings.qwen_text_model}`")
    st.write(f"Qwen 视觉：`{settings.qwen_vl_model}`")
    st.write(f"TTS：`{settings.qwen_tts_model}`")
    if not settings.dashscope_api_key:
        st.warning("未配置 DASHSCOPE_API_KEY，Qwen 功能不可用。")

st.subheader("1. 检索同行样本")
search_cols = st.columns([1.1, 1.6, 1.0, 1.2], gap="medium")
with search_cols[0]:
    platform_label = st.radio("平台", ["抖音", "小红书"], horizontal=True)
    platform = "xiaohongshu" if platform_label == "小红书" else "douyin"
with search_cols[1]:
    keyword = st.text_input("关键词", value=st.session_state.get("keyword", "车展"))
    st.session_state["keyword"] = keyword
with search_cols[2]:
    limit = st.number_input("数量", min_value=1, max_value=50, value=10, step=1)
with search_cols[3]:
    if st.button("检索", type="primary", use_container_width=True):
        with st.spinner("检索候选中..."):
            manifest = social.search(platform, keyword, int(limit), deep=False)
        st.session_state["manifest"] = manifest
        if manifest.get("items"):
            st.session_state["selected_url"] = manifest["items"][0].get("url", "")
        st.rerun()

if platform == "xiaohongshu":
    st.caption("小红书默认检索只读列表页，不进入单条详情；需要完整数据时点“深度补全当前结果”。")
else:
    st.caption("抖音检索会读取搜索接口返回的封面/播放地址，检索阶段不落本地视频。")

manifest = current_manifest()
if platform == "xiaohongshu" and manifest.get("items") and not manifest.get("deep_enriched"):
    if st.button("深度补全当前结果（会打开详情页，不下载视频）", use_container_width=True):
        with st.spinner("进入详情页补全数据..."):
            enriched = social.search(platform, keyword, int(limit), deep=True)
        st.session_state["manifest"] = enriched
        if enriched.get("items"):
            st.session_state["selected_url"] = enriched["items"][0].get("url", "")
        st.rerun()
render_gallery(manifest)

with st.expander("没有抓取器时：手动导入参考样本", expanded=not bool(manifest.get("items"))):
    manual_links = st.text_area("粘贴抖音/小红书链接，每行一个", height=90)
    manual_cols = st.columns(2)
    with manual_cols[0]:
        if st.button("导入链接为候选", use_container_width=True):
            st.session_state["manifest"] = import_links(platform, keyword, manual_links)
            st.rerun()
    with manual_cols[1]:
        uploaded_samples = st.file_uploader(
            "或上传本地参考视频/图片",
            type=["mp4", "mov", "m4v", "webm", "mkv", "jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
        )
        if st.button("导入上传样本", use_container_width=True, disabled=not bool(uploaded_samples)):
            st.session_state["manifest"] = copy_local_samples(platform, keyword, uploaded_samples or [])
            st.rerun()

items = manifest.get("items") or []
sample = selected_item(items)
download_manifest = st.session_state.get("download_manifest") or latest_manifest(action="download")
downloaded_items = download_manifest.get("items") or []
downloaded = next((item for item in downloaded_items if item.get("download_ok") and item.get("download_path")), {})

st.divider()
st.subheader("2. Qwen 辅助")
qwen_cols = st.columns(3, gap="medium")
with qwen_cols[0]:
    if st.button("基于样本生成文案", use_container_width=True, disabled=not bool(sample)):
        with st.spinner("生成文案..."):
            st.session_state["copy_text"] = qwen.generate_copy(keyword, sample)
with qwen_cols[1]:
    disabled = not bool(downloaded.get("download_path"))
    if st.button("分析已下载视频", use_container_width=True, disabled=disabled):
        with st.spinner("分析视频..."):
            st.session_state["vl_result"] = qwen.analyze_video(downloaded["download_path"])
with qwen_cols[2]:
    copy_text = st.session_state.get("copy_text", "")
    if st.button("文案转语音", use_container_width=True, disabled=not bool(copy_text)):
        with st.spinner("生成语音..."):
            audio_path = qwen.synthesize_speech(copy_text)
        st.session_state["tts_path"] = str(audio_path)

if st.session_state.get("copy_text"):
    st.text_area("文案", value=st.session_state["copy_text"], height=160)
if st.session_state.get("vl_result"):
    st.json(st.session_state["vl_result"])
if st.session_state.get("tts_path") and os.path.exists(st.session_state["tts_path"]):
    st.audio(st.session_state["tts_path"])

st.divider()
st.divider()
st.subheader("3. 成片生成")
render_cols = st.columns([2.2, 2.2, 1.2], gap="medium")
default_competitor_dir = download_manifest.get("download_dir") or ""
with render_cols[0]:
    competitor_dir = st.text_input("参考视频目录（可选）", value=default_competitor_dir)
with render_cols[1]:
    material_dir = st.text_input("素材目录", value=str(settings.default_material_dir))
with render_cols[2]:
    tts_mode = st.selectbox("配音", ["silent", "external"], index=0)
if st.button("生成成片", use_container_width=True, disabled=not bool(material_dir)):
    with st.spinner("调用成片 pipeline..."):
        st.session_state["pipeline_log"] = pipeline.render_video(
            keyword=keyword,
            competitor_dir=competitor_dir,
            material_dir=material_dir,
            name=f"lite-{keyword}",
            tts_mode="silent" if tts_mode == "silent" else "runninghub",
            script=st.session_state.get("copy_text", "") or keyword,
            audio_file=st.session_state.get("tts_path", ""),
        )
if st.session_state.get("pipeline_log"):
    st.code(st.session_state["pipeline_log"][-6000:], language="text")
    try:
        import json

        summary_text = st.session_state["pipeline_log"].split("__SUMMARY_JSON__", 1)[1]
        summary = json.loads(summary_text)
        final_video = summary.get("final_video", "")
        if final_video and os.path.exists(final_video):
            st.video(final_video)
            st.caption(f"成片：{final_video}")
    except Exception:
        pass

st.divider()
st.subheader("4. 发布入口")
pub_cols = st.columns([1.0, 1.2, 2.0], gap="medium")
with pub_cols[0]:
    publish_platform_label = st.radio("发布平台", ["抖音", "小红书"], horizontal=True)
    publish_platform = "xiaohongshu" if publish_platform_label == "小红书" else "douyin"
with pub_cols[1]:
    account = st.text_input("账号标识", value="creator")
with pub_cols[2]:
    publish_title = st.text_input("发布标题", value=(sample.get("title") or sample.get("desc") or keyword)[:40] if sample else keyword)

pub_action_cols = st.columns(2)
with pub_action_cols[0]:
    if st.button("检查账号", use_container_width=True):
        st.code(publisher.check_account(publish_platform, account), language="text")
with pub_action_cols[1]:
    can_publish = bool(downloaded.get("download_path") and os.path.exists(str(downloaded.get("download_path"))))
    if st.button("发布已下载视频", use_container_width=True, disabled=not can_publish):
        st.code(
            publisher.upload_video(
                publish_platform,
                downloaded["download_path"],
                publish_title,
                st.session_state.get("copy_text", ""),
                account,
            ),
            language="text",
        )

st.markdown("</div>", unsafe_allow_html=True)
