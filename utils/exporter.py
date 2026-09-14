from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

FIELDS = [
    "video_url", "video_id", "video_title", "author_name", "video_category",
    "comment_id", "comment_text", "comment_category", "comment_time", "like_count",
    "reply_count", "parent_comment_id", "comment_level", "user_name", "crawl_time",
]


def export_records(records: list[dict[str, Any]], output: Path, formats: list[str]) -> list[Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized_formats = list(dict.fromkeys(formats))
    results: list[Path] = []
    for fmt in normalized_formats:
        target = output.with_suffix(f".{fmt}")
        if fmt == "csv":
            with target.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(records)
        elif fmt == "jsonl":
            with target.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        elif fmt == "xlsx":
            book = Workbook()
            sheet = book.active
            sheet.title = "comments"
            sheet.append(FIELDS)
            for record in records:
                sheet.append([record.get(field, "") for field in FIELDS])
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for column in sheet.columns:
                letter = column[0].column_letter
                width = min(60, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
                sheet.column_dimensions[letter].width = width
            book.save(target)
        else:
            raise ValueError(f"不支持的输出格式：{fmt}")
        results.append(target)
    return results
