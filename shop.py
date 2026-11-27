#!/usr/bin/env python3
"""轻量小店 v1: 商品 + 订单 + 卡密库存 + USDT 手动收款 (纯标准库)

挂在云推分发系统同一个 app 上 (app.py 开头按 /shop* 与 /api/shop* 转发过来),
跑在同一个 PythonAnywhere 免费 Web App 里, 不用再租服务器。

流程 (验证期先走手动确认, 跑通后再接自动核销):
  买家: 选商品 -> 下单 (留微信/QQ) -> 按订单页显示的 USDT 金额付款
        -> 提交交易哈希 (或联系客服微信/支付宝转账) -> 等确认
  店主: 管理页看到订单 -> 核对收款 -> 点“确认收款”, 有卡密库存就自动发码
        (无库存时订单变“已付款等货”, 到货后在管理页点“发货”补发)

数据文件 (data/):
  shop_products.json   商品: id -> {name, desc, price, cost, stock_type, active}
  shop_orders.json     订单: order_no -> {product_id, product_name, price, contact,
                        status, tx_hash, code, note, created, ...}
  shop_inventory.json  卡密库存: product_id -> [未使用的码, ...]
订单状态: pending_pay (待付款) -> pending_confirm (已提交哈希待确认)
          -> paid (已确认收款, 代充类等人工发货) / waiting_stock (已付款等货)
          -> fulfilled (已发货, 卡密已给买家) / cancelled (已取消)
收款配置在 config.json 的 "shop" 里: name / usdt_address / usdt_network /
usdt_rate (人民币兑 USDT, 默认 7.2) / contact (客服微信/QQ) / notice (须知)。
"""
import json
import os
import random
import string
import threading
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
PRODUCTS_PATH = os.path.join(BASE, "data", "shop_products.json")
ORDERS_PATH = os.path.join(BASE, "data", "shop_orders.json")
INVENTORY_PATH = os.path.join(BASE, "data", "shop_inventory.json")
SHOP_HTML_PATH = os.path.join(BASE, "shop.html")
SHOP_ADMIN_HTML_PATH = os.path.join(BASE, "shop_admin.html")

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    TZ = timezone(timedelta(hours=8))

LOCK = threading.Lock()

SHOP_DEFAULTS = {
    "name": "AI 补给站",
    "usdt_address": "",
    "usdt_network": "BEP20 (BSC)",
    "usdt_rate": 7.2,
    "contact": "",
    "notice": "卡密售出后不支持退款；下单前请确认账号符合商品要求（无订阅、未进 TEAM）。付款后请提交交易哈希，确认收款后自动发码。",
}


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
    return load_json(CONFIG_PATH, {})


def get_token():
    return os.environ.get("ADMIN_TOKEN") or load_config().get("token", "")


def shop_config():
    cfg = load_config().get("shop", {})
    out = dict(SHOP_DEFAULTS)
    out.update({k: v for k, v in cfg.items() if v != "" or k in ("contact", "usdt_address")})
    return out


def _now():
    return datetime.now(TZ)


def _today():
    return _now().strftime("%Y-%m-%d")


def _ts():
    return _now().isoformat(timespec="seconds")


def _json_body(obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8",
                    "Content-Length": str(len(body))}, body


def _text_body(text, status=200):
    body = text.encode("utf-8")
    return status, {"Content-Type": "text/plain; charset=utf-8",
                    "Content-Length": str(len(body))}, body


def _html_file(path):
    try:
        with open(path, "rb") as f:
            page = f.read()
    except Exception:
        page = "<h1>页面文件缺失</h1>".encode("utf-8")
    return 200, {"Content-Type": "text/html; charset=utf-8",
                 "Content-Length": str(len(page))}, page


def _gen_order_no():
    for _ in range(10):
        no = "S" + _now().strftime("%Y%m%d") + "".join(
            random.choice(string.digits) for _ in range(4))
        if no not in load_json(ORDERS_PATH, {}):
            return no
    return "S" + _now().strftime("%Y%m%d%H%M%S") + "".join(
        random.choice(string.ascii_uppercase) for _ in range(3))


def _public_products():
    products = load_json(PRODUCTS_PATH, {})
    inv = load_json(INVENTORY_PATH, {})
    out = []
    for pid, p in products.items():
        if not p.get("active", True):
            continue
        item = {"id": pid, "name": p.get("name", ""),
                "desc": p.get("desc", ""), "price": p.get("price", 0),
                "stock_type": p.get("stock_type", "cdk")}
        if item["stock_type"] == "cdk":
            item["stock"] = len(inv.get(pid, []))
        out.append(item)
    return out


def _require_token(query):
    return query.get("token", [""])[0] == get_token() and get_token() != ""


def handle_shop(method, path, query, headers, body, client_ip):
    """处理全部 /shop* 与 /api/shop* 请求; 不是小店的路径返回 None。"""
    if not (path == "/shop" or path.startswith("/shop/")
            or path.startswith("/api/shop")):
        return None

    # ---------- GET ----------
    if method == "GET":
        if path == "/shop":
            return _html_file(SHOP_HTML_PATH)
        if path == "/shop/admin":
            return _html_file(SHOP_ADMIN_HTML_PATH)
        if path == "/api/shop/products":
            return _json_body({"shop": shop_config(),
                               "products": _public_products()})
        if path.startswith("/api/shop/orders/"):
            no = path[len("/api/shop/orders/"):]
            order = load_json(ORDERS_PATH, {}).get(no)
            if not order:
                return _json_body({"ok": False, "error": "订单不存在"}, 404)
            view = {"ok": True, "order_no": no,
                    "product_name": order.get("product_name", ""),
                    "price": order.get("price", 0),
                    "usdt_amount": order.get("usdt_amount"),
                    "status": order.get("status", ""),
                    "created": order.get("created", ""),
                    "tx_hash": order.get("tx_hash", ""),
                    "note": order.get("note", "")}
            if order.get("status") == "fulfilled":
                view["code"] = order.get("code", "")
            return _json_body(view)
        if path == "/api/shop/admin/orders":
            if not _require_token(query):
                return _json_body({"ok": False, "error": "token 不对"}, 403)
            orders = load_json(ORDERS_PATH, {})
            inv = load_json(INVENTORY_PATH, {})
            products = load_json(PRODUCTS_PATH, {})
            return _json_body({
                "ok": True,
                "orders": sorted(orders.values(),
                                 key=lambda o: o.get("created", ""), reverse=True),
                "stock": {pid: len(codes) for pid, codes in inv.items()},
                "products": products,
                "shop": shop_config(),
            })
        if path == "/api/shop/admin/inventory":
            if not _require_token(query):
                return _json_body({"ok": False, "error": "token 不对"}, 403)
            inv = load_json(INVENTORY_PATH, {})
            return _json_body({"ok": True,
                               "stock": {pid: len(c) for pid, c in inv.items()}})
        return _json_body({"ok": False, "error": "unknown"}, 404)

    # ---------- POST ----------
    if method == "POST":
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            data = {}

        # ---- 公开下单 (不用 token) ----
        if path == "/api/shop/orders":
            pid = str(data.get("product_id", ""))
            contact = (data.get("contact") or "").strip()
            if len(contact) < 2 or len(contact) > 64:
                return _json_body({"ok": False, "error": "请留微信号或 QQ 号"}, 400)
            with LOCK:
                products = load_json(PRODUCTS_PATH, {})
                p = products.get(pid)
                if not p or not p.get("active", True):
                    return _json_body({"ok": False, "error": "商品不存在或已下架"}, 404)
                if p.get("stock_type", "cdk") == "cdk":
                    inv = load_json(INVENTORY_PATH, {})
                    if len(inv.get(pid, [])) <= 0:
                        return _json_body({"ok": False, "error": "暂时缺货，请联系客服"}, 400)
                cfg = shop_config()
                rate = float(cfg.get("usdt_rate") or 7.2)
                price = float(p.get("price", 0))
                no = _gen_order_no()
                orders = load_json(ORDERS_PATH, {})
                orders[no] = {
                    "order_no": no, "product_id": pid,
                    "product_name": p.get("name", ""), "price": price,
                    "usdt_amount": round(price / rate, 2) if rate else None,
                    "usdt_rate": rate, "contact": contact,
                    "status": "pending_pay", "tx_hash": "", "code": "",
                    "note": "", "created": _ts(),
                }
                save_json(ORDERS_PATH, orders)
            return _json_body({"ok": True, "order_no": no, "price": price,
                               "product_name": orders[no]["product_name"],
                               "usdt_amount": orders[no]["usdt_amount"],
                               "usdt_address": cfg.get("usdt_address", ""),
                               "usdt_network": cfg.get("usdt_network", ""),
                               "contact": cfg.get("contact", "")})

        # ---- 买家点「我已付款」: 人民币微信/支付宝转账, 无交易哈希 ----
        if path.startswith("/api/shop/orders/") and path.endswith("/paid"):
            no = path[len("/api/shop/orders/"):-len("/paid")].strip("/")
            m = (data.get("method") or "cny").strip()
            with LOCK:
                orders = load_json(ORDERS_PATH, {})
                o = orders.get(no)
                if not o:
                    return _json_body({"ok": False, "error": "订单不存在"}, 404)
                if o.get("status") != "pending_pay":
                    return _json_body({"ok": False, "error": "该订单当前状态不能提交付款凭证"}, 400)
                o["status"] = "pending_confirm"
                o["payment_method"] = m
                if m == "cny":
                    o["note"] = (data.get("note") or "微信/支付宝付款").strip()
                o["paid_at"] = _ts()
                save_json(ORDERS_PATH, orders)
            return _json_body({"ok": True, "status": "pending_confirm"})

        # ---- 买家提交交易哈希 (不用 token) ----
        if path.startswith("/api/shop/orders/") and path.endswith("/paytx"):
            no = path[len("/api/shop/orders/"):-len("/paytx")].strip("/")
            tx = (data.get("tx_hash") or "").strip()
            if len(tx) < 10:
                return _json_body({"ok": False, "error": "交易哈希不对，请复制完整哈希"}, 400)
            with LOCK:
                orders = load_json(ORDERS_PATH, {})
                o = orders.get(no)
                if not o:
                    return _json_body({"ok": False, "error": "订单不存在"}, 404)
                if o.get("status") != "pending_pay":
                    return _json_body({"ok": False, "error": "该订单当前状态不能提交付款凭证"}, 400)
                o["tx_hash"] = tx
                o["status"] = "pending_confirm"
                o["paid_at"] = _ts()
                save_json(ORDERS_PATH, orders)
            return _json_body({"ok": True, "status": "pending_confirm"})

        # ---- 以下管理接口都要 token ----
        if not _require_token(query):
            return _json_body({"ok": False, "error": "token 不对"}, 403)

        # ---- 加卡密库存 ----
        if path == "/api/shop/admin/inventory":
            pid = str(data.get("product_id", ""))
            codes = data.get("codes") or []
            if isinstance(data.get("text"), str):
                codes = codes + data["text"].splitlines()
            with LOCK:
                products = load_json(PRODUCTS_PATH, {})
                if pid not in products:
                    return _json_body({"ok": False, "error": "商品不存在"}, 404)
                inv = load_json(INVENTORY_PATH, {})
                cur = inv.get(pid, [])
                added = 0
                for c in codes:
                    c = str(c).strip()
                    if c and c not in cur:
                        cur.append(c)
                        added += 1
                inv[pid] = cur
                save_json(INVENTORY_PATH, inv)
            return _json_body({"ok": True, "added": added, "total": len(cur)})

        # ---- 确认收款 (有库存自动发码, 无库存变等货) ----
        if path.startswith("/api/shop/admin/orders/") and path.endswith("/confirm"):
            no = path[len("/api/shop/admin/orders/"):-len("/confirm")].strip("/")
            with LOCK:
                orders = load_json(ORDERS_PATH, {})
                o = orders.get(no)
                if not o:
                    return _json_body({"ok": False, "error": "订单不存在"}, 404)
                if o.get("status") not in ("pending_pay", "pending_confirm"):
                    return _json_body({"ok": False, "error": "该订单当前状态不能确认收款"}, 400)
                o["status"] = "paid"
                o["confirmed_at"] = _ts()
                if load_json(PRODUCTS_PATH, {}).get(o.get("product_id", ""), {}).get("stock_type") == "cdk":
                    inv = load_json(INVENTORY_PATH, {})
                    codes = inv.get(o["product_id"], [])
                    if codes:
                        o["code"] = codes.pop(0)
                        inv[o["product_id"]] = codes
                        save_json(INVENTORY_PATH, inv)
                        o["status"] = "fulfilled"
                        o["fulfilled_at"] = _ts()
                    else:
                        o["status"] = "waiting_stock"
                save_json(ORDERS_PATH, orders)
            return _json_body({"ok": True, "order": o})

        # ---- 发货 (人工代充 / 等货到后补发) ----
        if path.startswith("/api/shop/admin/orders/") and path.endswith("/fulfill"):
            no = path[len("/api/shop/admin/orders/"):-len("/fulfill")].strip("/")
            with LOCK:
                orders = load_json(ORDERS_PATH, {})
                o = orders.get(no)
                if not o:
                    return _json_body({"ok": False, "error": "订单不存在"}, 404)
                if o.get("status") not in ("paid", "waiting_stock"):
                    return _json_body({"ok": False, "error": "该订单当前状态不能发货"}, 400)
                code = (data.get("code") or "").strip()
                if not code:
                    inv = load_json(INVENTORY_PATH, {})
                    codes = inv.get(o["product_id"], [])
                    if codes:
                        code = codes.pop(0)
                        inv[o["product_id"]] = codes
                        save_json(INVENTORY_PATH, inv)
                o["code"] = code
                if data.get("note"):
                    o["note"] = str(data["note"])
                o["status"] = "fulfilled"
                o["fulfilled_at"] = _ts()
                save_json(ORDERS_PATH, orders)
            return _json_body({"ok": True, "order": o})

        # ---- 取消订单 ----
        if path.startswith("/api/shop/admin/orders/") and path.endswith("/cancel"):
            no = path[len("/api/shop/admin/orders/"):-len("/cancel")].strip("/")
            with LOCK:
                orders = load_json(ORDERS_PATH, {})
                o = orders.get(no)
                if not o:
                    return _json_body({"ok": False, "error": "订单不存在"}, 404)
                if o.get("status") not in ("pending_pay", "pending_confirm"):
                    return _json_body({"ok": False, "error": "该订单当前状态不能取消"}, 400)
                o["status"] = "cancelled"
                save_json(ORDERS_PATH, orders)
            return _json_body({"ok": True})

        # ---- 商品新增 / 修改 ----
        if path == "/api/shop/admin/products":
            pid = str(data.get("id") or ("p" + "".join(
                random.choice(string.ascii_lowercase + string.digits)
                for _ in range(6))))
            with LOCK:
                products = load_json(PRODUCTS_PATH, {})
                cur = products.get(pid, {})
                cur.update({
                    "id": pid,
                    "name": data.get("name", cur.get("name", "")),
                    "desc": data.get("desc", cur.get("desc", "")),
                    "price": float(data.get("price", cur.get("price", 0))),
                    "cost": float(data.get("cost", cur.get("cost", 0))),
                    "stock_type": data.get("stock_type", cur.get("stock_type", "cdk")),
                    "active": bool(data.get("active", cur.get("active", True))),
                    "created": cur.get("created", _today()),
                })
                if not cur["name"]:
                    return _json_body({"ok": False, "error": "商品名不能为空"}, 400)
                products[pid] = cur
                save_json(PRODUCTS_PATH, products)
            return _json_body({"ok": True, "product": cur})

        # ---- 收款设置 ----
        if path == "/api/shop/admin/config":
            patch = data.get("shop") or {}
            with LOCK:
                cfg = load_config()
                shop = dict(cfg.get("shop", {}))
                for k in ("name", "usdt_address", "usdt_network", "usdt_rate",
                          "contact", "notice"):
                    if k in patch:
                        shop[k] = patch[k]
                cfg["shop"] = shop
                save_json(CONFIG_PATH, cfg)
            return _json_body({"ok": True, "shop": shop_config()})

        return _json_body({"ok": False, "error": "unknown"}, 404)

    return None
