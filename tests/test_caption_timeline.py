from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from exhibitflow_lite.render import caption_sentence_timeline, caption_timeline


def write_timing_file(tmp_path: Path, data: dict) -> Path:
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"not used by caption_timeline")
    audio.with_suffix(".words.json").write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )
    return audio


class CaptionTimelineTest(unittest.TestCase):
    def test_one_caption_block_per_tts_segment(self) -> None:
        script = "欢迎来到华南国际新能源产业展现场！人潮涌动，展位爆满。"
        with tempfile.TemporaryDirectory() as directory:
            audio = write_timing_file(
                Path(directory),
                {
                    "segments": [
                        {"start": 0, "end": 2.4},
                        {"start": 2.4, "end": 4.8},
                    ],
                    "words": [
                        {"text": "欢迎", "start": 0.1, "duration": 0.2, "segment_index": 0},
                        {"text": "来到", "start": 0.4, "duration": 0.2, "segment_index": 0},
                        {"text": "华南", "start": 0.7, "duration": 0.2, "segment_index": 0},
                        {"text": "国际", "start": 1.0, "duration": 0.2, "segment_index": 0},
                        {"text": "新能源", "start": 1.3, "duration": 0.3, "segment_index": 0},
                        {"text": "产业展", "start": 1.7, "duration": 0.3, "segment_index": 0},
                        {"text": "现场", "start": 2.1, "duration": 0.3, "segment_index": 0},
                    ],
                },
            )

            timeline = caption_timeline(script, 4.8, audio)

        self.assertEqual(len(timeline), 2)
        self.assertEqual(timeline[0]["text"], "欢迎来到华南国际新能源产业展现场")
        self.assertEqual(timeline[0]["start"], 0)
        self.assertEqual(timeline[0]["end"], 2.4)
        self.assertTrue(timeline[0]["semantic_unit"])

    def test_qwen_segment_metadata_works_without_word_boundaries(self) -> None:
        script = "第一句内容。第二句内容。"
        with tempfile.TemporaryDirectory() as directory:
            audio = write_timing_file(
                Path(directory),
                {
                    "segments": [
                        {"start": 0, "end": 1.5},
                        {"start": 1.5, "end": 3.0},
                    ],
                    "words": [],
                },
            )
            timeline = caption_timeline(script, 3.0, audio, cta_text="第二句内容")

        self.assertEqual([item["text"] for item in timeline], ["第一句内容", "第二句内容"])
        self.assertTrue(timeline[-1]["cta"])

    def test_semantic_tts_cues_are_not_merged(self) -> None:
        timeline = [
            {"text": "直接连接上下游关键决策角色", "start": 16.8, "end": 19.68},
            {"text": "通过现场沟通验证业务匹配度", "start": 19.68, "end": 22.72},
            {"text": "把不确定性转化为可评估选项", "start": 22.72, "end": 25.44},
        ]

        result = caption_sentence_timeline(timeline)

        self.assertEqual([item["text"] for item in result], [item["text"] for item in timeline])

    def test_historical_short_fragments_are_rejoined(self) -> None:
        result = caption_sentence_timeline([
            {"text": "这是一个", "start": 0, "end": 0.7},
            {"text": "完整句子。", "start": 0.7, "end": 1.6},
        ])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "这是一个完整句子。")

    def test_long_unpunctuated_cue_is_bounded(self) -> None:
        result = caption_sentence_timeline([
            {
                "text": "这是一个没有任何逗号但是长度明显超过字幕单屏限制的完整口播句子",
                "start": 0,
                "end": 5,
                "semantic_unit": True,
            },
        ])

        self.assertTrue(len(result) > 1)
        self.assertTrue(all(len(item["text"]) <= 16 for item in result))
        self.assertEqual(result[0]["start"], 0)
        self.assertEqual(result[-1]["end"], 5)


if __name__ == "__main__":
    unittest.main()
