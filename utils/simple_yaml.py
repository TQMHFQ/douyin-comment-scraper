from __future__ import annotations

"""本项目示例配置所需的安全 YAML 子集解析器（映射、缩进、标量）。"""

from pathlib import Path
from typing import Any


def _value(raw: str) -> Any:
    value = raw.strip()
    if value in {"\"\"", "''"}:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def load_mapping(path: str | Path) -> dict[str, Any]:
    root: dict[str, Any] = {}
    # (缩进层级, 当前映射)
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_no, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        raw = raw_line.split("#", 1)[0].rstrip()
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        if ":" not in content or content.startswith("-"):
            raise ValueError(f"配置第 {line_no} 行不在支持的 YAML 子集内")
        key, value = (part.strip() for part in content.split(":", 1))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"配置第 {line_no} 行缩进无效")
        container = stack[-1][1]
        if not value:
            child: dict[str, Any] = {}
            container[key] = child
            stack.append((indent, child))
        else:
            container[key] = _value(value)
    return root
