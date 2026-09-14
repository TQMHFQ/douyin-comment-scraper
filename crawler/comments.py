from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from playwright.sync_api import Locator, Page

from crawler.classifier import KeywordRuleClassifier
from crawler.video import VideoInfo


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def _text_from_html(html: str) -> list[str]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.parts


def _clean_lines(lines: list[str]) -> list[str]:
    ignored = {"回复", "分享", "删除", "举报", "赞", "展开", "收起"}
    return [line for line in lines if line not in ignored and not re.fullmatch(r"\d+", line)]


@dataclass
class CommentSettings:
    item_selector: str = '[data-e2e="comment-item"]'
    reply_item_selector: str = ""
    id_attribute: str = ""
    user_selector: str = ""
    text_selector: str = ""
    time_selector: str = ""
    like_selector: str = ""
    reply_count_selector: str = ""
    parent_id_selector: str = ""
    parent_id_attribute: str = ""
    max_stagnant_rounds: int = 3
    scroll_wait_ms: int = 1200


class CommentCrawler:
    """解析已在浏览器中可见的评论；不请求、伪造或破解评论接口。"""

    def __init__(self, page: Page, settings: CommentSettings, logger: logging.Logger) -> None:
        self.page = page
        self.settings = settings
        self.logger = logger
        self.classifier = KeywordRuleClassifier()

    @staticmethod
    def _read_optional(root: Locator, selector: str) -> str:
        if not selector:
            return ""
        node = root.locator(selector)
        return node.first.inner_text(timeout=1_500).strip() if node.count() else ""

    def comments_login_required(self) -> bool:
        return self.page.get_by_text("请先登录后发表评论", exact=True).count() > 0

    def expand_replies(self) -> bool:
        # 只点击当前公开可见、文本确实表示展开回复的控件；不存在时不做任何猜测性点击。
        buttons = self.page.get_by_text(re.compile(r"^展开.*回复$"))
        allowed = True
        for index in range(min(buttons.count(), 50)):
            try:
                buttons.nth(index).click(timeout=1_500)
                self.page.wait_for_timeout(300)
            except Exception as exc:
                if "intercepts pointer events" in str(exc):
                    self.logger.warning("回复展开被站点登录层阻止；不会使用强制点击或绕过方式。手动登录后可重试。")
                    return False
                self.logger.debug("展开回复失败：%s", exc)
                allowed = False
        return allowed

    def _configured_cards(self) -> list[tuple[str, Locator, str]]:
        if not self.settings.item_selector:
            return []
        cards = self.page.locator(self.settings.item_selector)
        result = [(f"configured-{index}", cards.nth(index), "一级评论") for index in range(cards.count())]
        if self.settings.reply_item_selector:
            replies = self.page.locator(self.settings.reply_item_selector)
            result.extend((f"reply-{index}", replies.nth(index), "回复") for index in range(replies.count()))
        return result

    def _fallback_card_html(self) -> list[tuple[str, str]]:
        """无配置时仅利用公开可见“回复”控件反向定位卡片，供探测和保守采集。"""
        replies = self.page.get_by_text("回复", exact=True)
        output: dict[str, str] = {}
        for index in range(replies.count()):
            try:
                html = replies.nth(index).evaluate(
                    """node => {
                      let current = node;
                      for (let depth = 0; current && depth < 9; depth += 1, current = current.parentElement) {
                        const text = (current.innerText || '').trim();
                        const lines = text.split(/\\n+/).map(x => x.trim()).filter(Boolean);
                        if (lines.length >= 3 && text.length <= 1200 && !text.includes('全部评论') && current.querySelectorAll('button,[role=button]').length <= 6) {
                          return current.outerHTML;
                        }
                      }
                      return node.parentElement ? node.parentElement.outerHTML : node.outerHTML;
                    }"""
                )
                key = hashlib.sha1(html.encode("utf-8")).hexdigest()
                output[key] = html
            except Exception as exc:
                self.logger.debug("回退卡片定位失败：%s", exc)
        return list(output.items())

    def _base_record(self, video: VideoInfo) -> dict[str, Any]:
        return {
            "video_url": video.video_url,
            "video_id": video.video_id,
            "video_title": video.video_title,
            "author_name": video.author_name,
            "video_category": self.classifier.classify_video(video.video_title, video.author_name),
            "comment_id": "",
            "comment_text": "",
            "comment_category": "其他",
            "comment_time": "",
            "like_count": "",
            "reply_count": "",
            "parent_comment_id": "",
            "comment_level": "一级评论",
            "user_name": "",
            "crawl_time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }

    def _parse_configured(self, root: Locator, video: VideoInfo, level: str) -> dict[str, Any]:
        if root.get_attribute("data-e2e") == "comment-item":
            return self._parse_verified_douyin_item(root, video, level)
        record = self._base_record(video)
        record.update({
            "comment_id": root.get_attribute(self.settings.id_attribute) or "" if self.settings.id_attribute else "",
            "user_name": self._read_optional(root, self.settings.user_selector),
            "comment_text": self._read_optional(root, self.settings.text_selector),
            "comment_time": self._read_optional(root, self.settings.time_selector),
            "like_count": self._read_optional(root, self.settings.like_selector),
            "reply_count": self._read_optional(root, self.settings.reply_count_selector),
            "comment_level": level,
        })
        if level == "回复" and self.settings.parent_id_selector and self.settings.parent_id_attribute:
            parent = root.locator(self.settings.parent_id_selector)
            if parent.count():
                record["parent_comment_id"] = parent.first.get_attribute(self.settings.parent_id_attribute) or ""
        record["comment_category"] = self.classifier.classify_comment(record["comment_text"])
        return record

    def _parse_verified_douyin_item(self, root: Locator, video: VideoInfo, level: str) -> dict[str, Any]:
        """解析 2026-09-10 实测的 data-e2e=comment-item 卡片。

        仅依赖 data-e2e / data-click-from 等语义属性；正文、时间与点赞通过该卡片
        已验证的相邻可见叶子节点读取，避免写死会变动的散列 CSS 类。
        """
        values = root.evaluate(
            """node => {
              const visibleText = el => (el.innerText || el.textContent || '').trim();
              const leaves = Array.from(node.querySelectorAll('span'))
                .filter(el => !el.querySelector('span') && visibleText(el))
                .map(el => ({el, text: visibleText(el)}));
              const timeRe = /(?:刚刚|昨天|前天|\\d+\\s*(?:秒|分|小?时|天|周|月|年)前|\\d{1,2}[-/]\\d{1,2}|\\d{4}[-/]\\d{1,2})/;
              const timeIndex = leaves.findIndex(item => timeRe.test(item.text));
              const time = timeIndex >= 0 ? leaves[timeIndex].text : '';
              let text = '';
              if (timeIndex >= 0) {
                const holder = leaves[timeIndex].el.parentElement;
                const previous = holder && holder.previousElementSibling;
                text = previous ? visibleText(previous) : (leaves[timeIndex - 1]?.text || '');
              }
              const user = node.querySelector('[data-click-from="title"]');
              const like = timeIndex >= 0 ? (leaves[timeIndex + 1]?.text || '') : '';
              const all = visibleText(node);
              const replies = all.match(/(?:展开)?\\s*(\\d+)\\s*条?回复/);
              return {user: user ? visibleText(user) : '', text, time, like, replies: replies ? replies[1] : ''};
            }"""
        )
        record = self._base_record(video)
        record.update({
            "user_name": values["user"],
            "comment_text": values["text"],
            "comment_time": values["time"],
            "like_count": values["like"],
            "reply_count": values["replies"],
            "comment_level": level,
        })
        record["comment_category"] = self.classifier.classify_comment(record["comment_text"])
        return record

    def _parse_fallback(self, html: str, video: VideoInfo) -> dict[str, Any]:
        record = self._base_record(video)
        lines = _clean_lines(_text_from_html(html))
        if lines:
            record["user_name"] = lines[0]
        # 时间通常落在评论正文之后；未识别时保留为空，避免把 UI 文本错误写入数据集。
        time_at = next((i for i, line in enumerate(lines) if re.search(r"(?:刚刚|分钟前|小时前|昨天|\d{1,2}[-/]\d{1,2}|\d{4}[-/]\d{1,2})", line)), len(lines))
        body = lines[1:time_at]
        record["comment_text"] = " ".join(body).strip()
        if time_at < len(lines):
            record["comment_time"] = lines[time_at]
        reply_match = re.search(r"(\d+)\s*条?回复", " ".join(lines))
        record["reply_count"] = reply_match.group(1) if reply_match else ""
        record["comment_category"] = self.classifier.classify_comment(record["comment_text"])
        return record

    def parse_visible(self, video: VideoInfo) -> list[dict[str, Any]]:
        configured = self._configured_cards()
        if configured:
            return [self._parse_configured(card, video, level) for _, card, level in configured]
        return [self._parse_fallback(html, video) for _, html in self._fallback_card_html()]

    def scroll_comments(self) -> bool:
        """从已实测的“全部评论”标题向上寻找滚动容器；找不到则滚动普通页面。"""
        moved = self.page.evaluate(
            """() => {
              const heading = Array.from(document.querySelectorAll('*')).find(
                el => (el.textContent || '').trim() === '全部评论'
              );
              for (let node = heading; node; node = node.parentElement) {
                if (node.scrollHeight > node.clientHeight + 20) {
                  const before = node.scrollTop;
                  node.scrollBy({top: Math.max(350, node.clientHeight * 0.8), behavior: 'instant'});
                  return node.scrollTop !== before;
                }
              }
              const before = window.scrollY;
              window.scrollBy({top: Math.max(500, window.innerHeight * 0.8), behavior: 'instant'});
              return window.scrollY !== before;
            }"""
        )
        self.page.wait_for_timeout(self.settings.scroll_wait_ms)
        return bool(moved)
