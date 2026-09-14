from __future__ import annotations

"""本地操作页：仅监听 127.0.0.1，不向外暴露采集功能。"""

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web" / "static"
TEMPLATES = ROOT / "web" / "templates"
DATA = ROOT / "data"
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _safe_job(job_id: str) -> dict[str, Any] | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        # 不能直接把 Popen 对象序列化或暴露给前端。
        return {key: value for key, value in job.items() if key != "process"}


def _append_log(job_id: str, line: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job["logs"].append(line.rstrip())
            job["logs"] = job["logs"][-500:]


def _run_job(job_id: str, command: list[str], output_base: Path) -> None:
    try:
        child_env = os.environ.copy()
        child_env["PYTHONUTF8"] = "1"
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env,
        )
        with JOBS_LOCK:
            JOBS[job_id]["process"] = process
            JOBS[job_id]["status"] = "running"
        assert process.stdout is not None
        for line in process.stdout:
            _append_log(job_id, line)
        code = process.wait()
        outputs = [
            path.name for path in output_base.parent.glob(output_base.stem + ".*")
            if path.suffix in {".csv", ".xlsx", ".jsonl"}
        ]
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "completed" if code == 0 else "failed"
            JOBS[job_id]["return_code"] = code
            JOBS[job_id]["outputs"] = sorted(outputs)
    except Exception as exc:
        logging.exception("本地任务启动失败")
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["return_code"] = -1
        _append_log(job_id, f"操作页启动任务失败：{exc}")


def _start_job(payload: dict[str, Any]) -> dict[str, Any]:
    raw_urls = str(payload.get("urls", ""))
    urls = [line.strip() for line in raw_urls.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not urls:
        raise ValueError("请至少输入一个抖音视频链接；每行一个。")
    try:
        max_comments = int(payload.get("max_comments", 20))
    except (TypeError, ValueError) as exc:
        raise ValueError("最大评论数必须是正整数。") from exc
    if not 1 <= max_comments <= 100_000:
        raise ValueError("最大评论数请设置在 1 到 100000 之间。")

    formats = payload.get("formats", ["csv"])
    if not isinstance(formats, list) or not formats or any(item not in {"csv", "xlsx", "jsonl"} for item in formats):
        raise ValueError("请至少选择一种有效导出格式。")
    manual_login = bool(payload.get("manual_login", False))
    headless = bool(payload.get("headless", False))
    if manual_login and headless:
        raise ValueError("手动登录时不能启用无界面模式。")
    try:
        login_wait = int(payload.get("login_wait_seconds", 120))
    except (TypeError, ValueError) as exc:
        raise ValueError("登录等待时间必须是整数。") from exc
    if not 30 <= login_wait <= 900:
        raise ValueError("登录等待时间请设置在 30 到 900 秒之间。")

    job_id = uuid.uuid4().hex[:12]
    job_dir = DATA / "web_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / "videos.txt"
    input_path.write_text("\n".join(urls) + "\n", encoding="utf-8")
    export_dir = DATA / "web_exports" / job_id
    output_base = export_dir / "comments.csv"
    command = [
        sys.executable, "main.py", "--input", str(input_path), "--output", str(output_base),
        "--max-comments", str(max_comments),
    ]
    for fmt in formats:
        command.extend(["--output-format", fmt])
    if payload.get("include_replies"):
        command.append("--include-replies")
    if payload.get("anonymize"):
        command.append("--anonymize")
        salt = str(payload.get("anonymize_salt", ""))
        if salt:
            command.extend(["--anonymize-salt", salt])
    if manual_login:
        command.extend(["--login", "--login-wait-seconds", str(login_wait)])
    elif headless:
        command.append("--headless")

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id, "status": "queued", "return_code": None, "logs": [], "outputs": [],
            "output_dir": str(export_dir),
        }
    thread = threading.Thread(target=_run_job, args=(job_id, command, output_base), daemon=True)
    thread.start()
    return _safe_job(job_id) or {}


class AppHandler(BaseHTTPRequestHandler):
    server_version = "DouyinCrawlerLocalUI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("操作页 | " + fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: Any) -> None:
        self._send(status, _json_bytes(value), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        if request.path == "/":
            self._send(HTTPStatus.OK, (TEMPLATES / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if request.path.startswith("/static/"):
            filename = request.path.removeprefix("/static/")
            if filename not in {"app.js", "style.css"}:
                self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
                return
            content_type = "text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8"
            self._send(HTTPStatus.OK, (STATIC / filename).read_bytes(), content_type)
            return
        if request.path.startswith("/api/jobs/"):
            job_id = request.path.removeprefix("/api/jobs/")
            job = _safe_job(job_id)
            if not job:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "任务不存在或已过期"})
                return
            self._send_json(HTTPStatus.OK, job)
            return
        if request.path == "/download":
            params = parse_qs(request.query)
            job_id = params.get("job", [""])[0]
            filename = params.get("file", [""])[0]
            if not job_id or filename not in {"comments.csv", "comments.xlsx", "comments.jsonl"}:
                self._send(HTTPStatus.BAD_REQUEST, b"Bad download request", "text/plain")
                return
            target = DATA / "web_exports" / job_id / filename
            if not target.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"File not found", "text/plain")
                return
            types = {".csv": "text/csv; charset=utf-8", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".jsonl": "application/x-ndjson; charset=utf-8"}
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", types[target.suffix])
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()
            with target.open("rb") as handle:
                self.wfile.write(handle.read())
            return
        self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/jobs":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 2 <= length <= 1_000_000:
                raise ValueError("请求内容无效")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效")
            self._send_json(HTTPStatus.ACCEPTED, _start_job(payload))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本地抖音评论采集操作页")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="仅启动服务，不自动打开浏览器")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024 到 65535 之间")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), AppHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"本地操作页已启动：{url}")
    print("按 Ctrl+C 停止服务。")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n操作页已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
