#!/usr/bin/env python3
"""云推分发系统 v1 - 短链跳转 + 日志统计 + 看板 (纯标准库, 无需安装依赖)

用法:
  python3 app.py            # 自带 HTTP 服务, 默认监听 0.0.0.0:8080 (PORT 环境变量可改)
  # PythonAnywhere 等只支持 WSGI 的免费托管: 入口文件 wsgi.py, 它调用本文件的 handle()

目录结构:
  config.json        配置 (端口/token/转存估算系数/下一批)
  data/links.json    短链表: code -> {target, title, channel, account}
  data/tasks.json    各线今日任务: [{id, name, quota, done}]
  logs/api-access.log  JSON 行日志 (按 /r/ 路径 + 302 计数)
  dashboard.html     看板页面
"""
import gzip
import hashlib
import json
import os
import random
import re
import string
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
LINKS_PATH = os.path.join(BASE, "data", "links.json")
TASKS_PATH = os.path.join(BASE, "data", "tasks.json")
LOG_PATH = os.path.join(BASE, "logs", "api-access.log")
DASHBOARD_PATH = os.path.join(BASE, "dashboard.html")

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    TZ = timezone(timedelta(hours=8))

LOCK = threading.Lock()
HEALTH_CACHE = {"ts": None, "data": {}}


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config():
    return load_json(CONFIG_PATH, {
        "port": 8080,
        "token": "change-me",
        "save_rate": 0.041,  # 预估转存 = 今日点击 PV x 系数, 定期用真实转存数校准
        "next_batch": "18:40 微博·截流互动·第 4 轮（纪录片求资源回复）",
    })


def get_token():
    # 管理 token 优先读环境变量 ADMIN_TOKEN (托管平台上用), 否则读 config.json
    # 部署到公开托管时不要把真实 token 提交进仓库, 在平台的环境变量里设置
    return os.environ.get("ADMIN_TOKEN") or load_config().get("token", "")


def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def rotate_log_if_needed():
    """日志超过 5MB 则 gzip 轮转, 与截图里的 api-access.log 及轮转 gz 保持一致."""
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 5 * 1024 * 1024:
            ts = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
            gz_path = LOG_PATH + "." + ts + ".gz"
            with open(LOG_PATH, "rb") as fin, gzip.open(gz_path, "wb") as fout:
                fout.write(fin.read())
            open(LOG_PATH, "w").close()
    except Exception:
        pass


def append_log(entry):
    with LOCK:
        rotate_log_if_needed()
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def iter_log_entries():
    """读当前日志 + 全部轮转 gz, 逐行解析 JSON."""
    paths = []
    logdir = os.path.dirname(LOG_PATH)
    if os.path.isdir(logdir):
        for name in sorted(os.listdir(logdir)):
            if name.startswith("api-access.log"):
                paths.append(os.path.join(logdir, name))
    for path in paths:
        try:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except Exception:
            continue


def compute_stats():
    """今日统计: PV / UV(全局去重) / 被点开的短链条数 / 渠道拆分 / 预估转存."""
    cfg = load_config()
    today = today_str()
    pv = 0
    uv_set = set()
    codes = set()
    channels = {}
    per_code = {}
    for e in iter_log_entries():
        ts = e.get("ts", "")
        if not ts.startswith(today):
            continue
        pv += 1
        uv_set.add(hashlib.md5((e.get("ip", "") + "|" + e.get("ua", "")).encode()).hexdigest())
        codes.add(e.get("code", ""))
        ch = e.get("c", "未知渠道") or "未知渠道"
        channels[ch] = channels.get(ch, 0) + 1
        per_code[e.get("code", "")] = per_code.get(e.get("code", ""), 0) + 1
    links = load_json(LINKS_PATH, {})
    dead = [c for c, v in HEALTH_CACHE["data"].items() if v.get("alive") is False]
    return {
        "date": today,
        "pv": pv,
        "uv": len(uv_set),
        "opened_links": len(codes),
        "est_saves": round(pv * float(cfg.get("save_rate", 0.041))),
        "save_rate": cfg.get("save_rate", 0.041),
        "channels": channels,
        "per_code": per_code,
        "dead_count": len(dead),
        "dead_codes": dead,
        "next_batch": cfg.get("next_batch", ""),
        "total_links": len(links),
    }


def check_health():
    """逐条 curl 落地页验活, 结果缓存 10 分钟."""
    now = datetime.now(TZ)
    if HEALTH_CACHE["ts"] and (now - HEALTH_CACHE["ts"]).total_seconds() < 600:
        return HEALTH_CACHE["data"]
    links = load_json(LINKS_PATH, {})
    result = {}
    for code, v in links.items():
        target = v.get("target", "")
        alive, status = False, None
        try:
            req = urllib.request.Request(target, method="HEAD",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                alive = 200 <= resp.status < 400
        except Exception:
            try:
                req = urllib.request.Request(target, method="GET",
                                             headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    status = resp.status
                    alive = 200 <= resp.status < 400
            except Exception as e:
                status = str(e)[:80]
        result[code] = {"alive": alive, "status": status, "target": target,
                        "title": v.get("title", ""), "channel": v.get("channel", ""),
                        "account": v.get("account", "")}
    HEALTH_CACHE["ts"] = now
    HEALTH_CACHE["data"] = result
    return result


def gen_code(n=6):
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


def _json_body(obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8",
                    "Content-Length": str(len(body))}, body


def _text_body(text, status=200):
    body = text.encode("utf-8")
    return status, {"Content-Type": "text/plain; charset=utf-8",
                    "Content-Length": str(len(body))}, body


def handle(method, path, query, headers, body, client_ip):
    """与传输层无关的核心路由, 供自带 HTTP 服务和 wsgi.py 共用.

    method: 'GET' / 'POST'; path: URL 路径 (如 /r/abc123);
    query: parse_qs() 的结果 {name: [values]};
    headers: {Name: value}; body: 请求体 bytes; client_ip: 客户端 IP.
    返回 (status, {header: value}, body_bytes).
    """
    ua = headers.get("User-Agent", "") or headers.get("user-agent", "")

    if method == "GET":
        # ---- 短链跳转: /r/<code>?c=<渠道> ----
        m = re.match(r"^/r/([A-Za-z0-9_-]+)$", path)
        if m:
            code = m.group(1)
            links = load_json(LINKS_PATH, {})
            item = links.get(code)
            if not item:
                return _text_body("短链不存在", 404)
            append_log({
                "ts": datetime.now(TZ).isoformat(timespec="seconds"),
                "code": code,
                "c": query.get("c", [item.get("channel", "")])[0],
                "ip": client_ip,
                "ua": ua,
            })
            return 302, {"Location": item["target"], "Content-Length": "0"}, b""

        if path == "/":
            try:
                with open(DASHBOARD_PATH, "rb") as f:
                    page = f.read()
            except Exception:
                page = "<h1>看板文件缺失 dashboard.html</h1>".encode("utf-8")
            return 200, {"Content-Type": "text/html; charset=utf-8",
                         "Content-Length": str(len(page))}, page

        if path == "/api/stats":
            return _json_body(compute_stats())
        if path == "/api/links":
            return _json_body(load_json(LINKS_PATH, {}))
        if path == "/api/tasks":
            return _json_body(load_json(TASKS_PATH, []))
        if path == "/api/health":
            return _json_body(check_health())
        if path == "/api/logs":
            return _json_body(list(iter_log_entries())[-50:])

        return _text_body("not found", 404)

    if method == "POST":
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            data = {}

        if query.get("token", [""])[0] != get_token():
            return _json_body({"ok": False, "error": "token 不对"}, 403)

        # ---- 新建短链 ----
        if path == "/api/links":
            target = (data.get("target") or "").strip()
            if not target.startswith(("http://", "https://")):
                return _json_body({"ok": False, "error": "target 必须是 http(s) 链接"}, 400)
            with LOCK:
                links = load_json(LINKS_PATH, {})
                code = (data.get("code") or "").strip() or gen_code()
                if code in links:
                    return _json_body({"ok": False, "error": "短链码已存在"}, 400)
                links[code] = {
                    "target": target,
                    "title": data.get("title", ""),
                    "channel": data.get("channel", ""),
                    "account": data.get("account", ""),
                    "created": today_str(),
                }
                save_json(LINKS_PATH, links)
            return _json_body({"ok": True, "code": code, "short": "/r/" + code})

        # ---- 任务 +1 (完成一条) / -1 (点错了回退) ----
        if path in ("/api/tasks/done", "/api/tasks/undo"):
            tid = str(data.get("id", ""))
            delta = 1 if path.endswith("/done") else -1
            with LOCK:
                tasks = load_json(TASKS_PATH, [])
                for t in tasks:
                    if str(t.get("id")) == tid:
                        done = int(t.get("done", 0)) + delta
                        t["done"] = max(0, min(done, int(t.get("quota", 0))))
                        save_json(TASKS_PATH, tasks)
                        return _json_body({"ok": True, "task": t})
            return _json_body({"ok": False, "error": "任务不存在"}, 404)

        # ---- 更新下一批 ----
        if path == "/api/next_batch":
            with LOCK:
                cfg = load_config()
                cfg["next_batch"] = data.get("next_batch", "")
                save_json(CONFIG_PATH, cfg)
            return _json_body({"ok": True})

        return _json_body({"ok": False, "error": "unknown"}, 404)

    return _text_body("method not allowed", 405)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items()}
        status, resp_headers, resp_body = handle(
            method, parsed.path, parse_qs(parsed.query), headers, raw,
            self.client_address[0])
        self.send_response(status)
        for k, v in resp_headers.items():
            self.send_header(k, v)
        self.end_headers()
        if resp_body:
            self.wfile.write(resp_body)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def main():
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    cfg = load_config()
    # Render / 免费托管会通过 PORT 环境变量指定端口, 优先用它
    port = int(os.environ.get("PORT", cfg.get("port", 8080)))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"云推分发系统已启动: http://localhost:{port}/  (token 见 config.json)")
    server.serve_forever()


if __name__ == "__main__":
    main()
