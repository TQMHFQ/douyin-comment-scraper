from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.sync_api import Page

VIDEO_ID_RE = re.compile(r"(?:/video/|modal_id=)(\d{8,})")


@dataclass(frozen=True)
class VideoInfo:
    video_url: str
    video_id: str
    video_title: str
    author_name: str


def video_id_from_url(url: str) -> str:
    match = VIDEO_ID_RE.search(url)
    if not match:
        raise ValueError(f"无法从链接识别视频 ID：{url}")
    return match.group(1)


def is_douyin_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "douyin.com" or host.endswith(".douyin.com") or host.endswith("iesdouyin.com")


def extract_video_info(page: Page, original_url: str) -> VideoInfo:
    """基于实测的 h1 与 data-e2e=user-info，不依赖易变的散列 class。"""
    final_url = page.url
    video_id = video_id_from_url(final_url or original_url)
    title = ""
    h1 = page.locator("h1")
    if h1.count():
        title = h1.first.inner_text(timeout=5_000).strip()
    if not title:
        title = page.title().removesuffix(" - 抖音").strip()

    author = ""
    user_info = page.locator('[data-e2e="user-info"]')
    if user_info.count():
        # 实测作者卡片内存在 data-click-from=title；优先读取其可见文本的首行。
        title_node = user_info.first.locator('[data-click-from="title"]')
        if title_node.count():
            author = title_node.first.evaluate(
                """node => {
                  const leaves = Array.from(node.querySelectorAll('span'))
                    .filter(el => !el.querySelector('span') && (el.innerText || '').trim());
                  return leaves.length ? leaves[0].innerText.trim() : (node.innerText || '').trim();
                }"""
            )
    return VideoInfo(original_url, video_id, title, author)
