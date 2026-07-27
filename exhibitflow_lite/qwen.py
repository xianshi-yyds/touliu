from __future__ import annotations

import base64
import json
import math
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI

from .config import settings
from .storage import manifest_path, write_json


def post_no_proxy(*args, **kwargs):
    session = requests.Session()
    session.trust_env = False
    try:
        return session.post(*args, **kwargs)
    finally:
        session.close()


def post_json_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: tuple[int, int] = (8, 45),
    attempts: int = 3,
) -> requests.Response:
    """Call an OpenAI-compatible endpoint with bounded retries.

    Authentication/validation errors are returned immediately. Transient
    network, timeout, rate-limit and 5xx failures are retried so a background
    task does not fail on the first short connection fluctuation.
    """
    errors: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = post_no_proxy(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            errors.append(f"第 {attempt} 次请求：{type(exc).__name__}: {exc}")
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
                continue
            raise RuntimeError(
                "模型服务网络连接失败（已重试 "
                f"{attempts} 次）：{' | '.join(errors[-3:])}"
            ) from exc

        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt < attempts:
            errors.append(f"第 {attempt} 次请求：HTTP {response.status_code}")
            time.sleep(min(2 ** (attempt - 1), 4))
            continue
        return response
    raise RuntimeError("模型服务请求失败")


def require_key() -> str:
    if not settings.dashscope_api_key:
        raise RuntimeError("Missing DASHSCOPE_API_KEY. Put it in .env or environment variables.")
    return settings.dashscope_api_key


def require_text_llm_key() -> str:
    if not settings.text_llm_api_key:
        raise RuntimeError(
            "Missing TEXT_LLM_API_KEY (or DASHSCOPE_API_KEY). Put it in .env or environment variables."
        )
    return settings.text_llm_api_key


def openai_client() -> OpenAI:
    return OpenAI(api_key=require_key(), base_url=settings.dashscope_base_url)


VOICEOVER_CHARS_PER_SECOND = 3.8
_UNSUPPORTED_COPY_CLAIMS = (
    "全国最大", "行业第一", "头部平台", "行业巨头", "黄金展位", "稀缺名额",
    "免费住宿", "保证成交", "必然成交", "真实买家", "有采购权", "现场爆满",
    "齐聚", "云集", "采购方在场", "买家在场",
)
_DIRECTION_WORDS = (
    "镜头",
    "画面",
    "运镜",
    "特写",
    "推近",
    "拉远",
    "航拍",
    "字幕",
    "音效",
    "配乐",
    "背景音乐",
    "转场",
    "场景",
)


def clean_voiceover_copy(text: str) -> str:
    """Keep only words that can be read aloud by the narrator.

    The prompt is the primary guardrail, but model output occasionally still
    contains Markdown fences, a ``口播稿：`` label, or a parenthesized shot
    direction.  Remove those wrappers before the text is passed to TTS,
    subtitles, and video rendering.
    """
    value = str(text or "").strip()
    if not value:
        return ""
    value = re.sub(r"^```(?:text|markdown|纯口播稿)?\s*|\s*```$", "", value, flags=re.I).strip()
    value = re.sub(r"^(?:最终)?(?:口播稿|文案|正文)\s*[:：]\s*", "", value).strip()
    kept: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip().lstrip("-•* ").strip()
        if not line:
            continue
        direction_line = re.match(
            r"^(?:镜头|画面|运镜|字幕|音效|配乐|背景音乐|旁白|场景|转场|BGM)\s*[:：]",
            line,
            flags=re.I,
        )
        bracketed_direction = (
            len(line) >= 2
            and line[0] in "（([{【"
            and line[-1] in "）)]}】"
            and any(word in line for word in _DIRECTION_WORDS)
        )
        if direction_line or bracketed_direction:
            continue
        # Remove inline parenthetical directions while preserving the spoken
        # sentence around them.  Only act when the bracket contains an
        # unambiguous production keyword, so ordinary spoken parentheses stay.
        line = re.sub(
            r"[（(\[【][^）)\]】\n]{0,120}(?:镜头|画面|运镜|字幕|音效|配乐|特写|推近|拉远|航拍|转场)[^）)\]】\n]{0,120}[）)\]】]",
            "",
            line,
        )
        line = re.sub(r"^(?:口播稿|文案|正文)\s*[:：]\s*", "", line).strip()
        if line:
            kept.append(line)
    cleaned = "\n".join(kept).strip()
    return cleaned or value


def copy_quality_issues(text: str, topic: str, min_chars: int, max_chars: int) -> list[str]:
    """Return actionable quality issues for one generated voice-over draft."""
    value = str(text or "").strip()
    compact = re.sub(r"[\s，。！？!?；;：:、,.]+", "", value)
    issues: list[str] = []
    if len(compact) < max(24, round(min_chars * 0.82)):
        issues.append(f"正文偏短，当前约 {len(compact)} 字")
    if len(compact) > round(max_chars * 1.18):
        issues.append(f"正文偏长，当前约 {len(compact)} 字")
    sentences = [part.strip() for part in re.findall(r"[^。！？!?；;]+[。！？!?；;]?", value) if part.strip()]
    minimum_sentences = max(3, math.ceil(min_chars / 16))
    if len(sentences) < minimum_sentences:
        issues.append(f"句子过少，需要至少 {minimum_sentences} 个独立短句")
    if any(len(re.sub(r"\s+", "", sentence)) > 26 for sentence in sentences):
        issues.append("存在超过 26 字的长句，不适合一句一屏字幕")
    unsupported = [claim for claim in _UNSUPPORTED_COPY_CLAIMS if claim in value and claim not in topic]
    if unsupported:
        issues.append("包含输入未提供的承诺或事实：" + "、".join(unsupported))
    if re.search(r"(?:镜头|运镜|特写|推近|拉远|航拍|字幕|音效|配乐|背景音乐|转场|画面显示|镜头切换)", value):
        issues.append("包含制作或镜头说明")
    if sum(value.count(word) for word in ("精准", "高效", "锁定", "直击", "聚焦", "汇聚")) > 4:
        issues.append("营销套话密度过高")
    if value.count("不是") > 1 or value.count("而是") > 1:
        issues.append("对比句式重复")
    if re.search(r"(点击|立即|马上).{0,8}(报名|预约|咨询|链接)", value):
        issues.append("正文提前写入行动按钮文案，系统会统一追加 CTA")
    if re.search(r"[專業體驗實現獲鏈條]", value):
        issues.append("夹杂繁体字")
    return issues


def _fallback_viral_logic(topic: str, samples: list[dict[str, Any]], platform: str) -> dict[str, Any]:
    """Return a usable structure when the model response is not valid JSON."""
    first = samples[0] if samples else {}
    title = str(first.get("title") or first.get("desc") or topic or "参考案例").strip()
    source_ids = [
        str(item.get("aweme_id") or item.get("note_id") or item.get("id") or "").strip()
        for item in samples
    ]
    source_ids = [item for item in source_ids if item]
    return {
        "version": "1.0",
        "analysis_type": "viral_logic_timeline",
        "topic": str(topic or "").strip(),
        "platform": str(platform or "").strip(),
        "evidence_level": "metadata",
        "evidence_summary": "当前基于公开标题、描述和互动数据生成结构模板，未读取原视频的完整音频与画面。",
        "summary": f"围绕“{title[:40]}”提取开头钩子、痛点展开、价值说明和行动收束四段结构。",
        "audience": "需要快速判断内容价值的目标受众",
        "core_tension": "用户注意力有限，需要先看到与自身相关的问题或结果。",
        "timeline": [
            {"start_sec": 0, "end_sec": 3, "phase": "hook", "goal": "在前三秒让目标受众意识到这和自己有关", "content_role": "问题或结果先行", "voiceover_pattern": "如果你正在面对某个具体问题……", "visual_role": "直接呈现与主题相关的真实场景或对象"},
            {"start_sec": 3, "end_sec": 8, "phase": "pain", "goal": "把抽象痛点落到决策阻力", "content_role": "补充一个具体顾虑", "voiceover_pattern": "真正难的不是……而是……", "visual_role": "展示问题细节、对比或现场证据"},
            {"start_sec": 8, "end_sec": 18, "phase": "value", "goal": "给出可验证的解决路径，而非空泛承诺", "content_role": "说明可以比较、沟通或验证什么", "voiceover_pattern": "到现场可以重点了解……", "visual_role": "展示产品、展位、交流或行业场景"},
            {"start_sec": 18, "end_sec": 24, "phase": "action", "goal": "让用户知道下一步应该做什么", "content_role": "自然收束并连接行动", "voiceover_pattern": "如果这正是你在找的……", "visual_role": "回到展会主题、品牌或报名入口"},
        ],
        "reuse_rules": ["保留段落节奏，不复制原案例的具体句子。", "先讲受众正在面对的问题，再讲展会可以提供的验证路径。", "每个画面只服务一个口播句意。"],
        "avoid_rules": ["不虚构规模、买家、订单、排名或效果数据。", "不把标题中的情绪词直接改写成行业事实。", "不照搬原视频文案和品牌表达。"],
        "source_sample_ids": source_ids,
    }


def _normalise_viral_logic(raw: Any, topic: str, samples: list[dict[str, Any]], platform: str) -> dict[str, Any]:
    """Constrain a model-produced logic object to the frontend contract."""
    fallback = _fallback_viral_logic(topic, samples, platform)
    if not isinstance(raw, dict):
        return fallback
    result = dict(fallback)
    for key in ("version", "analysis_type", "evidence_level", "evidence_summary", "summary", "audience", "core_tension"):
        if str(raw.get(key) or "").strip():
            result[key] = str(raw[key]).strip()
    for key in ("topic", "platform"):
        result[key] = str(raw.get(key) or result[key] or "").strip()
    timeline = raw.get("timeline")
    if isinstance(timeline, list):
        cleaned: list[dict[str, Any]] = []
        for index, item in enumerate(timeline[:8]):
            if not isinstance(item, dict):
                continue
            start = item.get("start_sec", item.get("start", 0))
            end = item.get("end_sec", item.get("end", 0))
            try:
                start_value = max(0, round(float(start), 1))
                end_value = max(start_value, round(float(end), 1))
            except (TypeError, ValueError):
                continue
            cleaned.append({
                "start_sec": start_value,
                "end_sec": end_value,
                "phase": str(item.get("phase") or item.get("stage") or f"segment_{index + 1}").strip(),
                "goal": str(item.get("goal") or item.get("purpose") or "").strip(),
                "content_role": str(item.get("content_role") or item.get("content") or "").strip(),
                "voiceover_pattern": str(item.get("voiceover_pattern") or item.get("narrative") or "").strip(),
                "visual_role": str(item.get("visual_role") or item.get("visual") or "").strip(),
            })
        if cleaned:
            result["timeline"] = cleaned
    for key in ("reuse_rules", "avoid_rules", "source_sample_ids"):
        value = raw.get(key)
        if isinstance(value, list):
            result[key] = [str(item).strip() for item in value[:8] if str(item).strip()]
    result["topic"] = str(topic or result.get("topic") or "").strip()
    result["platform"] = str(platform or result.get("platform") or "").strip()
    return result


def generate_viral_logic(
    samples: list[dict[str, Any]] | None = None,
    *,
    topic: str = "",
    platform: str = "",
) -> dict[str, Any]:
    """Reverse-engineer selected public samples into a reusable timeline."""
    samples = [item for item in (samples or []) if isinstance(item, dict)][:8]
    snapshots = []
    for index, item in enumerate(samples, start=1):
        snapshots.append({
            "index": index,
            "id": item.get("aweme_id") or item.get("note_id") or item.get("id") or "",
            "title": str(item.get("title") or item.get("desc") or "").strip()[:180],
            "author": str(item.get("author") or item.get("nickname") or "").strip()[:80],
            "likes": item.get("digg_count") or item.get("like_count") or item.get("likes") or 0,
            "comments": item.get("comment_count") or item.get("comments") or 0,
            "shares": item.get("share_count") or item.get("shares") or 0,
            "url": str(item.get("url") or item.get("share_url") or "").strip()[:300],
        })
    if not snapshots:
        return _fallback_viral_logic(topic, [], platform)
    system_prompt = """
你是短视频内容洞察员工，负责把用户选中的公开视频案例拆成可复用的“爆款逻辑时间线”。
你只能根据输入的公开标题、描述和互动数据做结构推断；没有提供的音频、字幕、画面和事实不能假装已经识别。
你的结果供另一个生视频员工使用，必须是结构参考，不能复制原视频文案、品牌和具体承诺。
""".strip()
    prompt = f"""
请分析以下 {len(snapshots)} 条参考案例，输出一个 JSON 对象，不要输出 Markdown、解释或思考过程。

当前业务主题：{str(topic or '').strip()}
检索平台：{str(platform or '').strip()}
参考案例：
{json.dumps(snapshots, ensure_ascii=False, indent=2)}

JSON 必须符合以下结构：
{{
  "version": "1.0",
  "analysis_type": "viral_logic_timeline",
  "evidence_level": "metadata|partial",
  "evidence_summary": "说明依据和缺失数据",
  "summary": "一句话总结这批案例共同的表达逻辑",
  "audience": "主要受众",
  "core_tension": "受众的核心决策阻力",
  "timeline": [
    {{
      "start_sec": 0,
      "end_sec": 3,
      "phase": "hook",
      "goal": "这一段要完成什么",
      "content_role": "应该表达的内容角色，不要写原文",
      "voiceover_pattern": "可泛化的句式方向，不要照抄",
      "visual_role": "画面应该证明或承载什么"
    }}
  ],
  "reuse_rules": ["可复用原则"],
  "avoid_rules": ["不可照搬或容易误导的内容"],
  "source_sample_ids": ["来源 ID"]
}}

要求：
1. timeline 按视频时间顺序输出 4-6 段，时间范围从 0 秒开始并保持递增。
2. 重点拆解开头钩子、痛点/冲突、价值说明、证据/比较、行动收束等功能，不要把它写成某个展会的成稿。
3. 如果只有标题和互动数据，evidence_level 必须是 metadata，并在 evidence_summary 中明确说明没有完整音视频转写。
4. 不要输出原案例的完整句子，不要虚构播放量、客户、订单、买家、品牌或政策信息。
5. 所有字段使用简体中文，timeline 中 phase 使用 hook、pain、value、proof、action 等英文枚举。
""".strip()
    url = settings.text_llm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.text_llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "enable_thinking": settings.text_llm_enable_thinking,
        "temperature": 0.2,
        "max_tokens": 1800,
    }
    response = post_json_with_retry(
        url,
        headers={"Authorization": f"Bearer {require_text_llm_key()}", "Content-Type": "application/json"},
        payload=payload,
        timeout=(8, 60),
        attempts=3,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Qwen viral logic analysis failed: HTTP {response.status_code} {response.text[:800]}")
    try:
        content = response.json()["choices"][0]["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Qwen viral logic analysis failed: invalid response {response.text[:800]}") from exc
    content = str(content).strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I).strip()
    try:
        start = content.find("{")
        end = content.rfind("}")
        raw = json.loads(content[start:end + 1]) if start >= 0 and end > start else None
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = None
    return _normalise_viral_logic(raw, topic, samples, platform)


def generate_copy(
    topic: str,
    sample: dict[str, Any] | None = None,
    *,
    target_duration_seconds: int = 30,
    creative_direction: str = "",
    reference_logic: dict[str, Any] | None = None,
) -> str:
    sample = sample or {}
    target_seconds = max(10, min(120, int(target_duration_seconds or 30)))
    # The configured CTA is appended later by the video pipeline. Reserve its
    # reading time here so the final narration still fits the selected length.
    min_chars = max(24, round(target_seconds * 3.2) - 12)
    max_chars = max(min_chars + 8, round(target_seconds * 4.0) - 10)
    min_sentences = max(3, math.ceil(min_chars / 16))
    max_sentences = max(min_sentences + 2, math.ceil(max_chars / 11))
    logic_text = json.dumps(reference_logic, ensure_ascii=False, indent=2)[:8000] if isinstance(reference_logic, dict) else ""
    direction_label = {
        "impact": "现场冲击型：先抛出与客户、订单、资源或现场热度直接相关的结果，再说明为什么值得来",
        "trend": "行业趋势型：先指出行业机会或变化，再讲专业价值和适合谁来",
        "conversion": "招商转化型：先讲目标人群能获得什么，再给出资源、机会和明确行动引导",
    }.get(str(creative_direction or "").strip(), str(creative_direction or "").strip())
    system_prompt = """
你是“生视频员工”中的资深会展增长文案策划。
你擅长根据不同行业、不同参展人群和不同采购决策链，提炼真实痛点，并把展会表达成解决问题的路径，而不是保证结果。
你必须使用简体中文。你的输出不是分镜脚本、拍摄脚本或制作说明，只能是可直接朗读的口播正文。
""".strip()
    prompt = f"""
请基于下面的业务信息，生成一段约 {target_seconds} 秒的中文短视频口播稿。
中文有效字数目标：{min_chars}-{max_chars} 字（不含标点和空格）。
建议拆成 {min_sentences}-{max_sentences} 个独立短句，每句单独一行。

业务主题：{topic}
创意方向（只用于确定表达策略，不要把它写成制作指令）：{direction_label}
参考标题：{sample.get('title') or sample.get('desc') or ''}
参考数据：点赞 {sample.get('digg_count') or 0}，评论 {sample.get('comment_count') or 0}，转发 {sample.get('share_count') or 0}
参考爆款逻辑（只借鉴结构，不复制原文）：
{logic_text or '未选择结构参考，请根据业务信息独立判断。'}

写作要求：
1. 先识别这类展会真正的目标人群、业务阻力和决策顾虑，再选择最合适的叙事结构：
   - 现场冲击型：痛点场景 → 继续拖延的代价 → 可验证的解决路径 → 行动。
   - 行业趋势型：行业变化 → 旧方法为什么不够 → 来现场比较和判断什么 → 行动。
   - 招商转化型：目标结果 → 当前卡点 → 展会能提供的连接或验证路径 → 行动。
   不要把三种结构混成固定模板。
2. 每一句只表达一个意思，优先 8-18 个汉字，最多不超过 26 个汉字。每个句子单独一行，句末必须使用中文标点；不要只用逗号串成一整段，方便后续做到“一句一屏”的字幕节奏。
3. 痛点必须能逐条对应输入内容，并且符合该行业的决策方式。不能把“关注合规”放大成“合规门槛正在提高”，不能为了制造冲突自行添加“订单上涨、利润下滑、政策收紧”等背景。输入信息较少时，可以推导行业常见顾虑，但必须写成“如果你正在……”“是否适配……”这类条件句或问题，不能当成已发生事实。解决方案只能表达为“现场比较、集中了解、直接沟通、验证适配、连接相关角色”等过程价值，不能写成必然成交、必然获客或必然拿到订单。
4. 面向展商、采购者、专业观众或渠道商时，要分别调整利益点和行动方式。不能不加判断地把所有受众都写成“抢展位”。
5. 只使用输入信息中明确提供的日期、地点、规模、数量、价格、福利、参会角色和承诺。输入里的“面向、连接、目标受众”只代表展会定位，不代表这些人已经确认到场；禁止改写成“齐聚、云集、采购方在场”。没有依据时禁止添加“真实买家、有采购权、头部平台、行业巨头、黄金展位、稀缺名额、全国最大、爆满、免费住宿、2000+工厂”等信息。
6. 语言要像专业招商人员自然说话：具体、克制、有节奏。避免“炸场、错过等一年、闭眼冲、全网最、史无前例、颠覆、赋能、生态闭环、合作商机、窗口期”等空泛词。
7. 避免套话复用。“精准、高效、锁定、直击、聚焦、汇聚”每个词整条最多出现一次；“不是……而是……”最多一次，也可以完全不用。不要连续罗列超过三个卖点。
8. 正文中自然提及一次输入里的展会名称或主题主语，避免全文只写“这里、本次活动”。最后一句只负责自然收束“为什么值得进一步了解”。不要写“点击、立即报名、预约、咨询、提交需求”等行动按钮文案，系统会在后续统一追加用户配置的 CTA。
9. 如果提供了参考爆款逻辑，只复用 timeline 的节奏和信息功能；必须替换成当前展会、当前受众和当前痛点，不能照搬句式、标题、品牌或具体事实。
10. 输出前在内部检查：行业痛点是否专属、解决路径是否对应、是否虚构事实、是否像另一类展会换名套写。检查完成后只输出正文，不要输出检查过程。
11. 只输出配音员要说的话。禁止输出标题、分析、解释、分镜、画面描述、镜头运动、字幕、音效、配乐、转场、场景说明、角色名或 Markdown。
12. 禁止使用括号或方括号标注动作，例如“（镜头推进）”“【画面显示】”；不要出现“镜头切换、推近、拉远、特写、航拍、字幕出现”等制作词。
""".strip()
    url = settings.text_llm_base_url.rstrip("/") + "/chat/completions"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    payload = {
        "model": settings.text_llm_model,
        "messages": messages,
        "stream": False,
        "enable_thinking": settings.text_llm_enable_thinking,
        "temperature": 0.55,
        "max_tokens": max(400, min(1200, max_chars * 2)),
    }
    last_copy = ""
    for generation_attempt in range(2):
        try:
            response = post_json_with_retry(
                url,
                headers={"Authorization": f"Bearer {require_text_llm_key()}", "Content-Type": "application/json"},
                payload=payload,
                timeout=(8, 45),
                attempts=3,
            )
        except RuntimeError as exc:
            raise RuntimeError(f"Qwen text copy generation failed: {exc}") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"Qwen text copy generation failed: HTTP {response.status_code} {response.text[:800]}")
        try:
            data = response.json()
            last_copy = clean_voiceover_copy(data["choices"][0]["message"].get("content") or "")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Qwen text copy generation failed: invalid response {response.text[:800]}") from exc
        issues = copy_quality_issues(last_copy, topic, min_chars, max_chars)
        if not issues or generation_attempt == 1:
            return last_copy
        messages.extend([
            {"role": "assistant", "content": last_copy},
            {
                "role": "user",
                "content": "上一版存在这些问题：" + "；".join(issues) + "。请保留行业相关性并彻底重写，只输出新版纯口播正文。",
            },
        ])
        payload["messages"] = messages
        payload["temperature"] = 0.45
    return last_copy


VISUAL_ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "venue": {
        "ratio": 0.30,
        "terms": ["trade show venue", "exhibition hall crowd", "business exhibition booth"],
    },
    "industry": {
        "ratio": 0.30,
        "terms": ["industrial technology display", "product demonstration expo", "manufacturing exhibition"],
    },
    "business": {
        "ratio": 0.25,
        "terms": ["trade fair negotiation", "business buyers networking", "product presentation meeting"],
    },
    "atmosphere": {
        "ratio": 0.15,
        "terms": ["conference venue exterior", "modern exhibition center", "business event entrance"],
    },
}

VISUAL_EMOTION_BLOCKLIST = {
    "anxiety", "anxious", "stress", "stressed", "depression", "depressed",
    "sad", "sadness", "worried", "worry", "frustrated", "frustration",
    "fear", "lonely", "overwhelmed", "crying", "headache", "pain",
    "office worker stress", "customer loss", "missed opportunity",
}


def _fallback_visual_search_plan(context: str) -> dict[str, Any]:
    value = str(context or "").lower()
    industry = "综合产业"
    industry_terms = list(VISUAL_ROLE_DEFAULTS["industry"]["terms"])
    rules = [
        (("新能源", "电动车", "汽车", "充电", "光伏", "储能", "vehicle"), "新能源产业", ["renewable energy exhibition", "electric vehicle expo", "solar panel trade show", "energy storage technology"]),
        (("食品", "餐饮", "饮料", "茶", "food", "beverage"), "食品饮料", ["food industry exhibition", "food product display", "beverage trade show", "food sampling expo"]),
        (("机器人", "人工智能", "ai", "智能制造", "科技"), "人工智能与智能制造", ["robot exhibition", "artificial intelligence expo", "smart manufacturing display", "technology trade show"]),
        (("医疗", "医药", "健康", "medical", "health"), "医疗健康", ["medical equipment exhibition", "healthcare technology expo", "medical product display", "health conference"]),
        (("家居", "家具", "建材", "装饰"), "家居建材", ["furniture trade show", "interior design exhibition", "building materials expo", "home product display"]),
        (("外贸", "跨境", "进出口", "采购商"), "国际贸易", ["international trade show", "export product exhibition", "global business buyers", "trade negotiation expo"]),
    ]
    for needles, label, terms in rules:
        if any(needle in value for needle in needles):
            industry = label
            industry_terms = terms
            break
    groups = []
    for role, defaults in VISUAL_ROLE_DEFAULTS.items():
        groups.append({
            "role": role,
            "ratio": defaults["ratio"],
            "terms": industry_terms if role == "industry" else list(defaults["terms"]),
        })
    return {
        "industry": industry,
        "video_type": "展会推广短视频",
        "visual_tone": "专业、繁忙、可信、产业化",
        "forbidden_scenes": ["焦虑人物特写", "悲伤人物", "居家场景", "普通办公室压力", "与行业无关的电脑操作"],
        "groups": groups,
        "source": "fallback",
        "error": "",
    }


def _clean_visual_term(value: Any, role: str) -> str:
    term = re.sub(r"[^a-zA-Z0-9 -]+", " ", str(value or "")).strip().lower()
    term = re.sub(r"\s+", " ", term)
    if not term or any(blocked in term for blocked in VISUAL_EMOTION_BLOCKLIST):
        return ""
    words = term.split()
    if len(words) < 2 or len(words) > 6:
        return ""
    anchors = {
        "venue": ("expo", "exhibition", "trade show", "conference", "venue", "booth"),
        "business": ("expo", "exhibition", "trade", "buyer", "networking", "meeting", "negotiation"),
        "atmosphere": ("expo", "exhibition", "conference", "venue", "event", "entrance"),
    }
    if role in anchors and not any(anchor in term for anchor in anchors[role]):
        suffix = " trade show" if role in {"venue", "business"} else " conference venue"
        term = f"{term}{suffix}"
    return " ".join(term.split()[:6])


def _normalize_visual_search_plan(raw_plan: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    raw_groups = raw_plan.get("groups") if isinstance(raw_plan.get("groups"), list) else []
    by_role = {str(item.get("role") or "").strip().lower(): item for item in raw_groups if isinstance(item, dict)}
    groups: list[dict[str, Any]] = []
    for fallback_group in fallback["groups"]:
        role = fallback_group["role"]
        candidate = by_role.get(role) or {}
        raw_terms = candidate.get("terms") if isinstance(candidate.get("terms"), list) else []
        terms: list[str] = []
        for raw_term in [*raw_terms, *fallback_group["terms"]]:
            term = _clean_visual_term(raw_term, role)
            if term and term not in terms:
                terms.append(term)
            if len(terms) >= 4:
                break
        groups.append({"role": role, "ratio": float(fallback_group["ratio"]), "terms": terms})
    forbidden = raw_plan.get("forbidden_scenes") if isinstance(raw_plan.get("forbidden_scenes"), list) else []
    return {
        "industry": str(raw_plan.get("industry") or fallback["industry"])[:80],
        "video_type": str(raw_plan.get("video_type") or fallback["video_type"])[:80],
        "visual_tone": str(raw_plan.get("visual_tone") or fallback["visual_tone"])[:160],
        "forbidden_scenes": list(dict.fromkeys([*(str(item) for item in forbidden if str(item).strip()), *fallback["forbidden_scenes"]]))[:10],
        "groups": groups,
        "source": "model",
        "error": "",
    }


def generate_visual_search_plan(topic: str, script: str) -> dict[str, Any]:
    """Create a stock-footage plan that describes physical exhibition scenes.

    Narrative pain points stay in the voiceover. Search terms are constrained to
    camera-visible business, venue and industry objects so words such as
    ``焦虑`` never become close-ups of distressed people.
    """
    fallback = _fallback_visual_search_plan(f"{topic} {script}")
    prompt = f"""
You are the visual director for a professional trade-show promotional video.
Create a stock-video retrieval plan from the subject and narration.

Critical rules:
1. Search only camera-visible physical scenes, objects and business actions.
2. Never translate emotional pain points into emotional people. Do not use anxiety, stress, worried, sad, frustrated, fear, crying, customer loss or missed opportunity as search concepts.
3. The footage must look like an exhibition, industry showcase, product display, buyer visit, negotiation, networking or venue atmosphere.
4. Return four groups exactly: venue, industry, business, atmosphere.
5. Each group has 2-4 concrete English search terms, each 2-6 words.
6. Do not invent attendance, orders, brands or event facts.

Return JSON only:
{{
  "industry": "...",
  "video_type": "trade show promotion",
  "visual_tone": "...",
  "forbidden_scenes": ["..."],
  "groups": [
    {{"role":"venue","terms":["..."]}},
    {{"role":"industry","terms":["..."]}},
    {{"role":"business","terms":["..."]}},
    {{"role":"atmosphere","terms":["..."]}}
  ]
}}

Subject: {topic}
Narration: {script}
""".strip()
    try:
        response = post_json_with_retry(
            settings.text_llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {require_text_llm_key()}", "Content-Type": "application/json"},
            payload={
                "model": settings.text_llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "enable_thinking": settings.text_llm_enable_thinking,
                "temperature": 0.25,
            },
            timeout=(8, 45),
            attempts=3,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        raw = str(data["choices"][0]["message"].get("content") or "")
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise ValueError("模型返回内容不是 JSON 对象")
        plan = json.loads(match.group(0))
        if not isinstance(plan, dict):
            raise ValueError("模型返回检索计划格式不正确")
        return _normalize_visual_search_plan(plan, fallback)
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback


def generate_search_terms_with_meta(topic: str, script: str, amount: int = 5) -> dict[str, Any]:
    """Backward-compatible flattened view of the structured visual plan."""
    plan = generate_visual_search_plan(topic, script)
    terms = [term for group in plan["groups"] for term in group["terms"]]
    return {"terms": terms[:amount], "source": plan["source"], "error": plan.get("error") or "", "visual_plan": plan}


def generate_search_terms(topic: str, script: str, amount: int = 5) -> list[str]:
    return list(generate_search_terms_with_meta(topic, script, amount).get("terms") or [])


def data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "video/mp4"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def analyze_video(video_path: str, prompt: str = "分析这段展会短视频的镜头结构、内容节奏和可复刻点。") -> dict[str, Any]:
    path = Path(video_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(video_path)
    if path.stat().st_size > 7 * 1024 * 1024:
        raise RuntimeError("OpenAI compatible base64 video should be under 7MB. Compress or upload to OSS first.")
    completion = openai_client().chat.completions.create(
        model=settings.qwen_vl_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": data_url(path)}, "fps": 2},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    result = {
        "video_path": str(path),
        "model": settings.qwen_vl_model,
        "result": completion.choices[0].message.content or "",
    }
    out = manifest_path("qwen-vl", "local", path.stem)
    result["_manifest_path"] = str(write_json(out, result))
    return result


def _synthesize_speech_streaming(payload: dict[str, Any], output_path: Path) -> Path:
    """Use DashScope SSE audio chunks when the non-streaming OSS URL is unreachable.

    The normal request below follows the documented non-streaming curl shape
    and returns a signed audio URL. Some local/VPN routes can reach the API but
    cannot reach the returned OSS host. SSE returns ordered base64 WAV chunks,
    so it is a reliable local fallback and does not change the public payload.
    """
    session = requests.Session()
    session.trust_env = False
    chunks: list[bytes] = []
    errors: list[str] = []
    try:
        response = session.post(
            settings.dashscope_multimodal_url,
            headers={
                "Authorization": f"Bearer {require_key()}",
                "Content-Type": "application/json",
                "X-DashScope-SSE": "enable",
            },
            json=payload,
            timeout=(8, 120),
            stream=True,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:500]}")
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
            if not line.startswith("data:"):
                continue
            raw_data = line[5:].strip()
            if not raw_data or raw_data == "[DONE]":
                continue
            try:
                event = json.loads(raw_data)
            except json.JSONDecodeError as exc:
                errors.append(f"invalid SSE JSON: {exc}")
                continue
            output = event.get("output") or {}
            audio = (output.get("audio") or {}) if isinstance(output, dict) else {}
            audio_data = audio.get("data") if isinstance(audio, dict) else ""
            if audio_data:
                try:
                    chunks.append(base64.b64decode(audio_data))
                except (ValueError, base64.binascii.Error) as exc:
                    raise RuntimeError(f"invalid SSE audio data: {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"SSE request failed: {type(exc).__name__}: {exc}") from exc
    finally:
        session.close()
    if not chunks:
        detail = f" ({'; '.join(errors[-2:])})" if errors else ""
        raise RuntimeError(f"SSE response contained no audio data{detail}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"".join(chunks))
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("SSE audio file is empty")
    return output_path


def synthesize_speech(text: str, voice: str = "", output_name: str = "qwen-tts.mp3") -> Path:
    api_key = require_key()
    out = settings.storage_dir / "tts" / output_name
    out.parent.mkdir(parents=True, exist_ok=True)
    selected_voice = (voice or settings.qwen_tts_voice or "Serena").strip()
    payload = {
        "model": settings.qwen_tts_model,
        "input": {
            "text": text,
            "voice": selected_voice,
            "language_type": "Chinese",
        },
    }
    response = post_json_with_retry(
        settings.dashscope_multimodal_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        payload=payload,
        timeout=(8, 120),
        attempts=3,
    )
    if response.status_code >= 400:
        raise RuntimeError(response.text)
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Qwen TTS response is not JSON: {response.text[:500]}") from exc
    audio = (
        data.get("output", {})
        .get("audio", {})
        or data.get("output", {})
        .get("choices", [{}])[0]
        .get("message", {})
        .get("audio", {})
    )
    audio_url = audio.get("url") if isinstance(audio, dict) else ""
    audio_data = audio.get("data") if isinstance(audio, dict) else ""
    if audio_data:
        out.write_bytes(base64.b64decode(audio_data))
    elif audio_url:
        download_errors: list[str] = []
        # DashScope may return a signed OSS URL whose route differs from the
        # API route. Try the direct path first, then the user's configured
        # proxy path; this keeps local/VPN deployments working in both cases.
        for trust_env in (False, True):
            session = requests.Session()
            session.trust_env = trust_env
            try:
                media = session.get(audio_url, timeout=120)
                media.raise_for_status()
                out.write_bytes(media.content)
                break
            except requests.RequestException as exc:
                download_errors.append(f"proxy={trust_env}: {type(exc).__name__}")
            finally:
                session.close()
        if download_errors and not out.is_file():
            # The SSE chunks are WAV, so keep the correct extension instead of
            # returning a WAV file with an MP3 suffix to the browser.
            streaming_out = out.with_name(f"{out.stem}-streaming.wav")
            try:
                return _synthesize_speech_streaming(payload, streaming_out)
            except Exception as exc:
                raise RuntimeError(
                    "Qwen TTS audio download failed: "
                    + " | ".join(download_errors)
                    + f"; streaming fallback failed: {exc}"
                ) from exc
    else:
        raise RuntimeError(f"Qwen TTS response missing audio: {data}")
    return out
