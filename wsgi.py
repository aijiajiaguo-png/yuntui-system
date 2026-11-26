"""WSGI 入口, 供 PythonAnywhere 等只支持 WSGI 的免费托管使用.

PythonAnywhere 配置方法:
  1. 把本目录传到 PythonAnywhere (Files 页上传 yuntui-deploy.zip 后解压,
     或用 git clone), 记下路径, 如 /home/<用户名>/yuntui-system
  2. Web 页新建免费 Web App, Python 版本选 3.12, 框架选 Manual configuration
  3. 打开 WSGI configuration file, 把原内容清空, 改成:
         import sys
         sys.path.insert(0, '/home/<用户名>/yuntui-system')
         from wsgi import application
  4. Web 页 Environment variables 里加一条: ADMIN_TOKEN=<你自己定的管理密码>
     (看板第一次打开会问这个密码; 不要用 config.json 里的占位值)
  5. 点 Reload, 打开 https://<用户名>.pythonanywhere.com 就是看板
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import handle  # noqa: E402
from urllib.parse import parse_qs  # noqa: E402

_REASONS = {200: "OK", 302: "Found", 400: "Bad Request", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/") or "/"
    query = parse_qs(environ.get("QUERY_STRING", ""))
    headers = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            headers[key[5:].replace("_", "-").title()] = value
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    body = environ["wsgi.input"].read(length) if length > 0 else b""
    forwarded = environ.get("HTTP_X_FORWARDED_FOR", "")
    client_ip = (forwarded.split(",")[0].strip()
                 if forwarded else environ.get("REMOTE_ADDR", ""))

    status, resp_headers, resp_body = handle(method, path, query, headers,
                                            body, client_ip)
    reason = _REASONS.get(status, "")
    start_response(f"{status} {reason}",
                   [(k, v) for k, v in resp_headers.items()])
    return [resp_body]
