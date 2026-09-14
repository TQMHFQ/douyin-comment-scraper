import unittest
import uuid
from pathlib import Path

from crawler.classifier import KeywordRuleClassifier
from crawler.video import is_douyin_url, video_id_from_url
from utils.checkpoint import Checkpoint
from utils.exporter import export_records


class CoreTests(unittest.TestCase):
    def artifact_dir(self) -> Path:
        path = Path("tests/.test-output") / uuid.uuid4().hex
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_video_id_and_host_validation(self) -> None:
        self.assertEqual(video_id_from_url("https://www.douyin.com/video/7493742486519680271"), "7493742486519680271")
        self.assertTrue(is_douyin_url("https://v.douyin.com/abc/"))
        self.assertFalse(is_douyin_url("https://example.com/video/7493742486519680271"))

    def test_checkpoint_deduplicates_missing_id_by_fingerprint(self) -> None:
        checkpoint = Checkpoint(self.artifact_dir() / "progress.jsonl")
        record = {"video_id": "1", "user_name": "甲", "comment_text": "谢谢医生", "comment_time": "昨天"}
        self.assertTrue(checkpoint.add(record))
        self.assertFalse(checkpoint.add(record.copy()))
        self.assertEqual(len(Checkpoint(checkpoint.path).records), 1)

    def test_rules_and_export(self) -> None:
        self.assertEqual(KeywordRuleClassifier().classify_comment("谢谢医生，讲得很好"), "感谢医生")
        results = export_records([{"video_id": "1", "comment_text": "样例"}], self.artifact_dir() / "comments.csv", ["csv", "jsonl", "xlsx"])
        self.assertEqual({path.suffix for path in results}, {".csv", ".jsonl", ".xlsx"})
        self.assertTrue(all(path.exists() for path in results))


if __name__ == "__main__":
    unittest.main()
