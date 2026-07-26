import unittest

from exhibitflow_lite.render import _pycaps_transcription


class PyCapsTranscriptTest(unittest.TestCase):
    def test_chinese_sentence_is_split_into_character_ranges(self):
        data = _pycaps_transcription([
            {"text": "欢迎来到新能源展", "start": 1.0, "end": 3.0},
        ])
        words = data["segments"][0]["lines"][0]["words"]
        self.assertEqual("".join(item["text"] for item in words), "欢迎来到新能源展")
        self.assertEqual(words[0]["time"]["start"], 1.0)
        self.assertEqual(words[-1]["time"]["end"], 3.0)
        self.assertTrue(all(item["time"]["end"] > item["time"]["start"] for item in words))

    def test_whitespace_does_not_create_empty_caption_tokens(self):
        data = _pycaps_transcription([
            {"text": "第一句  第二句", "start": 0.0, "end": 2.0},
        ])
        words = data["segments"][0]["lines"][0]["words"]
        self.assertEqual([item["text"] for item in words], list("第一句第二句"))


if __name__ == "__main__":
    unittest.main()
