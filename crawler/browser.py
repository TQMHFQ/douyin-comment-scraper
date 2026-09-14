from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

T = TypeVar("T")


@dataclass
class BrowserOptions:
    profile_dir: Path
    headless: bool
    slow_mo_ms: int = 0
    min_delay: float = 1.2
    max_delay: float = 2.5


class DouyinBrowser:
    """仅驱动本机正常浏览器；不调用未公开接口，也不处理验证码/风控。"""

    def __init__(self, options: BrowserOptions, logger: logging.Logger) -> None:
        self.options = options
        self.logger = logger
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None

    def __enter__(self) -> "DouyinBrowser":
        self.options.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.options.profile_dir),
            channel="msedge",
            headless=self.options.headless,
            slow_mo=self.options.slow_mo_ms,
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
        )
        return self

    def __exit__(self, *_: object) -> None:
        if self.context:
            self.context.close()
        if self._playwright:
            self._playwright.stop()

    def new_page(self) -> Page:
        if not self.context:
            raise RuntimeError("浏览器尚未启动")
        return self.context.new_page()

    def pause(self) -> None:
        """使用随机、低频等待，避免高频页面操作。"""
        time.sleep(random.uniform(self.options.min_delay, self.options.max_delay))

    def retry(self, description: str, operation: Callable[[], T]) -> T:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                return operation()
            except Exception as exc:  # Playwright 的网络/超时错误均在此处理
                last_error = exc
                if attempt == 3:
                    break
                backoff = 2 ** (attempt - 1) + random.uniform(0, 0.8)
                self.logger.warning("%s 失败（第 %s/3 次）：%s；%.1f 秒后重试", description, attempt, exc, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"{description} 已重试 3 次仍失败") from last_error

    def open(self, page: Page, url: str) -> None:
        def navigate() -> None:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1_500)
        self.retry(f"打开 {url}", navigate)
        self.pause()

    def wait_for_manual_login(self, page: Page, wait_seconds: int = 0) -> None:
        if self.options.headless:
            raise RuntimeError("--login 不能与 --headless 一起使用；请在可见浏览器中完成登录。")
        self.logger.info("请在打开的浏览器中按抖音正常流程完成登录；本程序不会代填验证码或绕过访问控制。")
        if wait_seconds > 0:
            self.logger.info("等待 %s 秒供你完成手动登录。", wait_seconds)
            page.wait_for_timeout(wait_seconds * 1_000)
            return
        input("完成登录后回到此终端按 Enter 继续（若页面提示风控，请停止并稍后再试）：")
        page.wait_for_timeout(1_000)
