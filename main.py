from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path
from typing import Any

from crawler.browser import BrowserOptions, DouyinBrowser
from crawler.comments import CommentCrawler, CommentSettings
from crawler.video import extract_video_info, is_douyin_url
from utils.checkpoint import Checkpoint
from utils.exporter import export_records
from utils.logger import build_logger
from utils.simple_yaml import load_mapping


def load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    loaded = load_mapping(path)
    if not isinstance(loaded, dict):
        raise ValueError("配置文件根节点必须是对象")
    return loaded


def urls_from_args(args: argparse.Namespace) -> list[str]:
    urls: list[str] = list(args.url or [])
    if args.input:
        for line in Path(args.input).read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                urls.append(value)
    unique = list(dict.fromkeys(urls))
    if not unique:
        raise ValueError("请通过 --url 或 --input 提供至少一个视频链接")
    invalid = [url for url in unique if not is_douyin_url(url)]
    if invalid:
        raise ValueError("仅允许抖音视频链接：" + "，".join(invalid))
    return unique


def anonymize(record: dict[str, Any], salt: str) -> dict[str, Any]:
    name = str(record.get("user_name") or "")
    if name:
        digest = hashlib.sha256((salt + "\x1f" + name).encode("utf-8")).hexdigest()[:16]
        record["user_name"] = f"user_{digest}"
    return record


def write_probe(page: Any, video_id: str, directory: Path, logger: logging.Logger) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{video_id}.html"
    target.write_text(page.locator("body").inner_html(), encoding="utf-8")
    logger.info("已保存当前页面 DOM 探针：%s（请只在其中查找公开可见评论节点）", target)


def crawl(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    browser_cfg = config.get("browser", {})
    crawl_cfg = config.get("crawl", {})
    comment_cfg = config.get("comments", {})
    logger = build_logger(Path(args.data_dir) / "logs", args.verbose)
    urls = urls_from_args(args)
    output = Path(args.output)
    checkpoint = Checkpoint(Path(args.checkpoint) if args.checkpoint else output.with_suffix(".checkpoint.jsonl"))
    settings = CommentSettings(
        item_selector=str(comment_cfg.get("item_selector", '[data-e2e="comment-item"]')),
        reply_item_selector=str(comment_cfg.get("reply_item_selector", "")),
        id_attribute=str(comment_cfg.get("id_attribute", "")),
        user_selector=str(comment_cfg.get("user_selector", "")),
        text_selector=str(comment_cfg.get("text_selector", "")),
        time_selector=str(comment_cfg.get("time_selector", "")),
        like_selector=str(comment_cfg.get("like_selector", "")),
        reply_count_selector=str(comment_cfg.get("reply_count_selector", "")),
        parent_id_selector=str(comment_cfg.get("parent_id_selector", "")),
        parent_id_attribute=str(comment_cfg.get("parent_id_attribute", "")),
        max_stagnant_rounds=int(crawl_cfg.get("max_stagnant_rounds", 3)),
        scroll_wait_ms=int(crawl_cfg.get("scroll_wait_ms", 1200)),
    )
    options = BrowserOptions(
        profile_dir=Path(browser_cfg.get("profile_dir", Path(args.data_dir) / "browser_profile")),
        headless=args.headless,
        slow_mo_ms=int(browser_cfg.get("slow_mo_ms", 0)),
        min_delay=float(crawl_cfg.get("min_delay_seconds", 1.2)),
        max_delay=float(crawl_cfg.get("max_delay_seconds", 2.5)),
    )
    captured = 0
    with DouyinBrowser(options, logger) as browser:
        page = browser.new_page()
        if args.login:
            browser.open(page, urls[0])
            browser.wait_for_manual_login(page, args.login_wait_seconds)
        for url in urls:
            if captured >= args.max_comments:
                break
            logger.info("处理视频：%s", url)
            browser.open(page, url)
            # 实测页面在 DOMContentLoaded 后仍会异步渲染视频与评论；等待正常页面加载，不提速或注入接口。
            page.wait_for_timeout(5_000)
            video = extract_video_info(page, url)
            logger.info("已识别 video_id=%s，标题=%s", video.video_id, video.video_title)
            crawler = CommentCrawler(page, settings, logger)
            if args.probe_comments:
                write_probe(page, video.video_id, Path(args.data_dir) / "dom_probes", logger)
            if crawler.comments_login_required():
                logger.info("页面提示“请先登录后发表评论”；这限制发布评论，不等同于不可读取公开已渲染评论。")
            stagnant = 0
            replies_allowed = True
            while captured < args.max_comments and stagnant < settings.max_stagnant_rounds:
                if args.include_replies and replies_allowed:
                    replies_allowed = crawler.expand_replies()
                visible = crawler.parse_visible(video)
                newly_saved = 0
                for record in visible:
                    if not record["comment_text"]:
                        continue
                    if args.anonymize:
                        record = anonymize(record, args.anonymize_salt)
                    if checkpoint.add(record):
                        captured += 1
                        newly_saved += 1
                        if captured >= args.max_comments:
                            break
                logger.info("当前可见 %s 条，新增 %s 条，累计 %s 条", len(visible), newly_saved, captured)
                stagnant = stagnant + 1 if newly_saved == 0 else 0
                if captured >= args.max_comments:
                    break
                moved = crawler.scroll_comments()
                browser.pause()
                if not moved:
                    stagnant += 1
            logger.info("视频 %s 完成；断点文件已保存到 %s", video.video_id, checkpoint.path)
    paths = export_records(checkpoint.records, output, args.output_format)
    logger.info("已导出 %s 条记录：%s", len(checkpoint.records), "，".join(str(path) for path in paths))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合规采集抖音公开可见评论（浏览器自动化）")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--url", action="append", help="抖音视频链接；可重复传入")
    source.add_argument("--input", help="每行一个视频链接的 UTF-8 文本文件")
    parser.add_argument("--output", default="data/comments.csv", help="输出文件路径（后缀由输出格式决定）")
    parser.add_argument("--output-format", action="append", choices=("csv", "xlsx", "jsonl"), default=[], help="可重复指定")
    parser.add_argument("--max-comments", type=int, default=20, help="本次运行最多新增采集的评论数，默认 20")
    parser.add_argument("--include-replies", action="store_true", help="点击公开可见的展开回复控件")
    parser.add_argument("--headless", action="store_true", help="无界面模式；首次登录时不要使用")
    parser.add_argument("--login", action="store_true", help="在可见 Edge 中由你手动完成一次登录")
    parser.add_argument("--login-wait-seconds", type=int, default=0, help="配合 --login 使用；等待指定秒数而不要求终端按 Enter（供本地操作页使用）")
    parser.add_argument("--anonymize", action="store_true", help="以稳定哈希匿名化评论昵称")
    parser.add_argument("--anonymize-salt", default="", help="匿名化盐；研究项目应自行设置并妥善保管")
    parser.add_argument("--checkpoint", help="断点 JSONL 路径，默认与输出同名")
    parser.add_argument("--config", help="YAML 选择器配置；以 --probe-comments 的实际 DOM 为准")
    parser.add_argument("--probe-comments", action="store_true", help="保存当前页面 DOM，便于登录后确认评论选择器")
    parser.add_argument("--data-dir", default="data", help="浏览器配置、日志和 DOM 探针目录")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not args.output_format:
        suffix = Path(args.output).suffix.lower().lstrip(".")
        args.output_format = [suffix if suffix in {"csv", "xlsx", "jsonl"} else "csv"]
    if args.max_comments <= 0:
        parser.error("--max-comments 必须大于 0")
    if args.login_wait_seconds < 0:
        parser.error("--login-wait-seconds 不能小于 0")
    return args


if __name__ == "__main__":
    # 不带参数时优先提供可视化操作页；保留所有既有 CLI 参数以便批处理和自动化。
    if len(sys.argv) == 1:
        from web_app import main as web_main

        web_main()
        raise SystemExit(0)
    try:
        raise SystemExit(crawl(parse_args()))
    except (ValueError, RuntimeError) as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s | %(message)s")
        logging.error("%s", exc)
        raise SystemExit(2)
