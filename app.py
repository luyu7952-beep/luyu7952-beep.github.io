#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import traceback
import uuid
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import weibo_spider
import weibo_topic_spider


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
INDEX_FILE = ROOT / "index.html"
STATIC_DIR = ROOT / "static"

# 强制锁定 0.0.0.0，彻底解决 127.0.0.1 导致 Render 健康检查超时被杀的问题
HOST = "0.0.0.0"
DEFAULT_PORT = 8000
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>微博采集控制台</title>
  <link rel="stylesheet" href="/static/style.css" />
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <h1>微博采集控制台</h1>
        <p>正文搜索、用户内搜索、话题指标采集都在这里启动和下载。</p>
      </div>
      <div class="status-pill" id="serverStatus">就绪</div>
    </header>

    <section class="workspace">
      <nav class="tabs" aria-label="采集类型">
        <button class="tab active" data-tab="posts" type="button">微博正文</button>
        <button class="tab" data-tab="topics" type="button">话题指标</button>
      </nav>

      <section class="panel active" id="posts">
        <form id="postForm" class="form-grid">
          <label class="wide">
            <span>关键词</span>
            <textarea name="keywords" rows="3" placeholder="人工智能 AI 芯片"></textarea>
          </label>
          <label>
            <span>采集页数</span>
            <input name="pages" type="number" min="1" max="50" value="3" />
          </label>
          <label>
            <span>请求间隔秒</span>
            <input name="sleep" type="number" min="0" max="30" step="0.1" value="1.5" />
          </label>
          <label>
            <span>指定用户 UID</span>
            <input name="uid" placeholder="可留空" />
          </label>
          <label>
            <span>导出格式</span>
            <select name="format">
              <option value="csv">CSV</option>
              <option value="both">CSV + JSON</option>
            </select>
          </label>
          <label class="wide">
            <span>微博 Cookie</span>
            <textarea name="cookie" rows="3" placeholder="公开访问失败时再填写；只会发给本机后台脚本"></textarea>
          </label>
          <button class="primary" type="submit">开始采集正文</button>
        </form>
      </section>

      <section class="panel" id="topics">
        <form id="topicForm" class="form-grid">
          <label class="wide">
            <span>话题词</span>
            <textarea name="topics" rows="3" placeholder="人工智能 芯片 科技新闻"></textarea>
          </label>
          <label>
            <span>抽样微博页数</span>
            <input name="post_pages" type="number" min="1" max="50" value="3" />
          </label>
          <label>
            <span>请求间隔秒</span>
            <input name="sleep" type="number" min="0" max="30" step="0.1" value="1.5" />
          </label>
          <label>
            <span>热搜监测轮数</span>
            <input name="monitor_rounds" type="number" min="1" max="200" value="1" />
          </label>
          <label>
            <span>监测间隔秒</span>
            <input name="monitor_interval" type="number" min="1" max="3600" value="60" />
          </label>
          <label>
            <span>导出格式</span>
            <select name="format">
              <option value="csv">CSV</option>
              <option value="both">CSV + JSON</option>
            </select>
          </label>
          <label class="wide">
            <span>微博 Cookie</span>
            <textarea name="cookie" rows="3" placeholder="公开访问失败时再填写"></textarea>
          </label>
          <button class="primary" type="submit">开始采集话题</button>
        </form>
      </section>
    </section>

    <section class="results">
      <div class="results-head">
        <h2>任务结果</h2>
        <button class="ghost" id="clearJobs" type="button">清空列表</button>
      </div>
      <div id="jobs" class="jobs"></div>
    </section>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>
"""


STYLE_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1e293b;
  --muted: #64748b;
  --line: #d9e0e8;
  --accent: #b4232a;
  --accent-dark: #841f25;
  --green: #0f7a58;
  --blue: #2563a9;
  --shadow: 0 12px 28px rgba(24, 39, 75, .08);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}

.shell {
  width: min(1180px, calc(100vw - 32px));
  margin: 24px auto 40px;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
}

h1, h2 { margin: 0; letter-spacing: 0; }
h1 { font-size: 24px; line-height: 1.25; }
h2 { font-size: 18px; }
p { margin: 6px 0 0; color: var(--muted); }

.status-pill {
  min-width: 72px;
  text-align: center;
  padding: 8px 12px;
  border: 1px solid #badbcc;
  border-radius: 999px;
  color: var(--green);
  background: #eefaf4;
  font-size: 14px;
}

.workspace, .results {
  margin-top: 16px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
}

.tabs {
  display: flex;
  gap: 6px;
  padding: 10px;
  border-bottom: 1px solid var(--line);
}

.tab, .ghost, .primary {
  border: 1px solid var(--line);
  border-radius: 6px;
  min-height: 38px;
  padding: 0 14px;
  background: #fff;
  color: var(--text);
  cursor: pointer;
  font-size: 14px;
}

.tab.active {
  border-color: #f0b8bb;
  background: #fff1f2;
  color: var(--accent-dark);
}

.panel { display: none; padding: 18px; }
.panel.active { display: block; }

.form-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

label {
  display: grid;
  gap: 7px;
  color: var(--muted);
  font-size: 13px;
}

.wide { grid-column: 1 / -1; }

input, textarea, select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 11px;
  color: var(--text);
  background: #fff;
  font: inherit;
  min-height: 40px;
}

textarea { resize: vertical; line-height: 1.5; }
input:focus, textarea:focus, select:focus {
  outline: 2px solid #bfd7ff;
  border-color: var(--blue);
}

.primary {
  grid-column: 1 / -1;
  width: fit-content;
  min-width: 148px;
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
  font-weight: 650;
}

.primary:disabled { opacity: .6; cursor: wait; }

.results { padding: 16px 18px; }
.results-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.jobs {
  display: grid;
  gap: 10px;
}

.empty {
  color: var(--muted);
  border: 1px dashed var(--line);
  border-radius: 8px;
  padding: 22px;
  text-align: center;
}

.job {
  display: grid;
  gap: 10px;
  padding: 13px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfe;
}

.job-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.job-title { font-weight: 700; }
.job-meta { color: var(--muted); font-size: 13px; }
.badge {
  padding: 5px 9px;
  border-radius: 999px;
  background: #eef2ff;
  color: #354a8c;
  font-size: 13px;
}
.badge.done { background: #ecfdf3; color: var(--green); }
.badge.error { background: #fff1f2; color: var(--accent-dark); }
.downloads { display: flex; flex-wrap: wrap; gap: 8px; }
.downloads a {
  color: var(--blue);
  border: 1px solid #bdd5f5;
  background: #f3f8ff;
  border-radius: 6px;
  padding: 7px 10px;
  text-decoration: none;
  font-size: 14px;
}
.error-text {
  margin: 0;
  white-space: pre-wrap;
  color: var(--accent-dark);
  background: #fff7f7;
  border: 1px solid #f4c7ca;
  border-radius: 6px;
  padding: 10px;
}

@media (max-width: 760px) {
  .shell { width: min(100vw - 20px, 1180px); margin-top: 10px; }
  .topbar, .job-top { align-items: flex-start; flex-direction: column; }
  .form-grid { grid-template-columns: 1fr; }
  .primary { width: 100%; }
}
"""


APP_JS = """
const tabs = document.querySelectorAll('.tab');
const panels = document.querySelectorAll('.panel');
const jobsEl = document.querySelector('#jobs');
const statusEl = document.querySelector('#serverStatus');
const jobIds = new Set();

tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    tabs.forEach(item => item.classList.remove('active'));
    panels.forEach(item => item.classList.remove('active'));
    tab.classList.add('active');
    document.querySelector('#' + tab.dataset.tab).classList.add('active');
  });
});

document.querySelector('#clearJobs').addEventListener('click', () => {
  jobIds.clear();
  renderJobs([]);
});

document.querySelector('#postForm').addEventListener('submit', event => {
  event.preventDefault();
  submitJob('/api/collect-posts', event.currentTarget);
});

document.querySelector('#topicForm').addEventListener('submit', event => {
  event.preventDefault();
  submitJob('/api/collect-topics', event.currentTarget);
});

function formPayload(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const key of ['pages', 'post_pages', 'monitor_rounds']) {
    if (data[key] !== undefined) data[key] = Number(data[key] || 1);
  }
  for (const key of ['sleep', 'monitor_interval']) {
    if (data[key] !== undefined) data[key] = Number(data[key] || 0);
  }
  return data;
}

async function submitJob(url, form) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  statusEl.textContent = '启动中';
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(formPayload(form))
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '启动失败');
    jobIds.add(data.job_id);
    await pollJobs();
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    statusEl.textContent = '就绪';
  }
}

async function pollJobs() {
  if (jobIds.size === 0) {
    renderJobs([]);
    return;
  }
  const response = await fetch('/api/jobs?ids=' + encodeURIComponent([...jobIds].join(',')));
  const data = await response.json();
  renderJobs(data.jobs || []);
  const hasRunning = (data.jobs || []).some(job => job.status === 'running' || job.status === 'queued');
  statusEl.textContent = hasRunning ? '采集中' : '就绪';
  if (hasRunning) setTimeout(pollJobs, 1500);
}

function renderJobs(jobs) {
  if (!jobs.length) {
    jobsEl.innerHTML = '<div class="empty">还没有任务。填写上面的表单后，结果会出现在这里。</div>';
    return;
  }
  jobsEl.innerHTML = jobs.map(job => {
    const badgeClass = job.status === 'done' ? 'done' : job.status === 'error' ? 'error' : '';
    const statusText = { queued: '排队中', running: '运行中', done: '完成', error: '失败' }[job.status] || job.status;
    const downloads = (job.files || []).map(file =>
      `<a href="/download/${encodeURIComponent(file.name)}" download>${file.label}</a>`
    ).join('');
    const error = job.error ? `<pre class="error-text">${escapeHtml(job.error)}</pre>` : '';
    return `<article class="job">
      <div class="job-top">
        <div>
          <div class="job-title">${escapeHtml(job.title)}</div>
          <div class="job-meta">${escapeHtml(job.created_at)} · ${job.count || 0} 条结果</div>
        </div>
        <span class="badge ${badgeClass}">${statusText}</span>
      </div>
      ${downloads ? `<div class="downloads">${downloads}</div>` : ''}
      ${error}
    </article>`;
  }).join('');
}

function escapeHtml(value) {
  return String(value || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

renderJobs([]);
"""


def split_words(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\s,，;；]+", value or "") if item.strip()]


def safe_name(prefix: str, suffix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    token = uuid.uuid4().hex[:8]
    return f"{prefix}_{stamp}_{token}.{suffix}"


def write_json_rows(rows: list[Any], path: Path) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump([asdict(row) for row in rows], file, ensure_ascii=False, indent=2)


def read_asset(path: Path) -> bytes:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(str(path))
    if resolved != INDEX_FILE.resolve() and STATIC_DIR.resolve() not in resolved.parents:
        raise FileNotFoundError(str(path))
    return resolved.read_bytes()


def create_job(title: str) -> str:
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "title": title,
            "status": "queued",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": 0,
            "files": [],
            "error": "",
        }
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(fields)


def run_post_job(job_id: str, payload: dict[str, Any]) -> None:
    update_job(job_id, status="running")
    try:
        keywords = split_words(payload.get("keywords", "")) or weibo_spider.DEFAULT_KEYWORDS
        pages = max(1, int(payload.get("pages") or 1))
        sleep_seconds = max(0.0, float(payload.get("sleep") or 0))
        cookie = (payload.get("cookie") or "").strip() or None
        uid = (payload.get("uid") or "").strip() or None
        rows = weibo_spider.collect(keywords, pages, sleep_seconds, cookie, uid)

        OUTPUT_DIR.mkdir(exist_ok=True)
        csv_name = safe_name("weibo_posts", "csv")
        csv_path = OUTPUT_DIR / csv_name
        weibo_spider.write_csv(rows, str(csv_path))
        files = [{"name": csv_name, "label": "下载 CSV"}]
        if payload.get("format") == "both":
            json_name = safe_name("weibo_posts", "json")
            write_json_rows(rows, OUTPUT_DIR / json_name)
            files.append({"name": json_name, "label": "下载 JSON"})
        update_job(job_id, status="done", count=len(rows), files=files)
    except Exception:
        update_job(job_id, status="error", error=traceback.format_exc(limit=8))


def run_topic_job(job_id: str, payload: dict[str, Any]) -> None:
    update_job(job_id, status="running")
    try:
        topics = split_words(payload.get("topics", ""))
        if not topics:
            raise ValueError("请至少填写一个话题词")
        post_pages = max(1, int(payload.get("post_pages") or 1))
        sleep_seconds = max(0.0, float(payload.get("sleep") or 0))
        cookie = (payload.get("cookie") or "").strip() or None
        monitor_rounds = max(1, int(payload.get("monitor_rounds") or 1))
        monitor_interval = max(1.0, float(payload.get("monitor_interval") or 1))
        rows = weibo_topic_spider.collect_topics(
            topics=topics,
            post_pages=post_pages,
            sleep_seconds=sleep_seconds,
            cookie=cookie,
            monitor_rounds=monitor_rounds,
            monitor_interval=monitor_interval,
        )

        OUTPUT_DIR.mkdir(exist_ok=True)
        csv_name = safe_name("weibo_topics", "csv")
        csv_path = OUTPUT_DIR / csv_name
        weibo_topic_spider.write_csv(rows, str(csv_path))
        files = [{"name": csv_name, "label": "下载 CSV"}]
        if payload.get("format") == "both":
            json_name = safe_name("weibo_topics", "json")
            weibo_topic_spider.write_json(rows, str(OUTPUT_DIR / json_name))
            files.append({"name": json_name, "label": "下载 JSON"})
        update_job(job_id, status="done", count=len(rows), files=files)
    except Exception:
        update_job(job_id, status="error", error=traceback.format_exc(limit=8))


def start_job(kind: str, payload: dict[str, Any]) -> str:
    title = "微博正文采集" if kind == "posts" else "话题指标采集"
    job_id = create_job(title)
    target = run_post_job if kind == "posts" else run_topic_job
    thread = threading.Thread(target=target, args=(job_id, payload), daemon=True)
    thread.start()
    return job_id


class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_text(self, body: str, content_type: str = "text/html; charset=utf-8", status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, data: dict[str, Any], status: int = 200) -> None:
        self.send_text(json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8", status)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(read_asset(INDEX_FILE), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/static/"):
            relative = parsed.path.removeprefix("/static/")
            path = STATIC_DIR / relative
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_bytes(read_asset(path), content_type)
            return
        if parsed.path == "/api/jobs":
            query = parse_qs(parsed.query)
            ids = [item for item in query.get("ids", [""])[0].split(",") if item]
            with JOBS_LOCK:
                jobs = [JOBS[item] for item in ids if item in JOBS]
            self.send_json({"jobs": jobs})
            return
        if parsed.path.startswith("/download/"):
            name = os.path.basename(parsed.path.removeprefix("/download/"))
            path = OUTPUT_DIR / name
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:
        try:
            payload = self.read_json()
            if self.path == "/api/collect-posts":
                job_id = start_job("posts", payload)
                self.send_json({"job_id": job_id})
                return
            if self.path == "/api/collect-topics":
                job_id = start_job("topics", payload)
                self.send_json({"job_id": job_id})
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


def prepare_static_assets() -> None:
    """在启动服务前，动态在磁盘生成静态资产文件，确保文件系统安全检查顺利通过"""
    STATIC_DIR.mkdir(exist_ok=True)
    INDEX_FILE.write_text(INDEX_HTML, encoding="utf-8")
    (STATIC_DIR / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    (STATIC_DIR / "app.js").write_text(APP_JS, encoding="utf-8")


def main() -> None:
    try:
        # 1. 动态生成实体页面资产，解决安全策略检查问题
        prepare_static_assets()
        
        # 2. 读取 Render 分配的有效端口
        port = int(os.environ.get("PORT", DEFAULT_PORT))
        shown_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
        
        print("正在启动微博采集控制台...", flush=True)
        print(f"工作目录：{ROOT}", flush=True)
        print(f"监听地址：{HOST}:{port}", flush=True)
        print(f"首页文件存在：{INDEX_FILE.exists()} -> {INDEX_FILE}", flush=True)
        print(f"静态目录存在：{STATIC_DIR.exists()} -> {STATIC_DIR}", flush=True)
        
        # 3. 这里的 HOST 已经死锁为 "0.0.0.0"，Render 网关能完美映射进入容器
        server = ThreadingHTTPServer((HOST, port), AppHandler)
        print(f"微博采集控制台已启动：http://{shown_host}:{port}", flush=True)
        server.serve_forever()
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()