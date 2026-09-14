from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class Checkpoint:
    """JSONL 追加式断点：单条评论落盘后即可在下一次运行中恢复。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seen: set[str] = set()
        self.records: list[dict[str, Any]] = []
        self._load()

    @staticmethod
    def key(record: dict[str, Any]) -> str:
        comment_id = str(record.get("comment_id") or "").strip()
        if comment_id:
            return f"id:{comment_id}"
        # 页面未公开 comment_id 时，用稳定指纹做去重，但不把指纹伪装成 comment_id。
        source = "\x1f".join(
            str(record.get(name) or "")
            for name in ("video_id", "parent_comment_id", "user_name", "comment_text", "comment_time")
        )
        return "fp:" + hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = self.key(record)
                if key not in self.seen:
                    self.seen.add(key)
                    self.records.append(record)

    def add(self, record: dict[str, Any]) -> bool:
        key = self.key(record)
        if key in self.seen:
            return False
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
        self.seen.add(key)
        self.records.append(record)
        return True
