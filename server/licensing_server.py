# -*- coding: utf-8 -*-
"""素材授权台 HTTP API（Python 标准库实现，无外部依赖）。

启动:  python3 server/licensing_server.py [--port 8081] [--db server/licensing.db]
前台页面 licenses.html 通过 fetch 调用本服务（已开启 CORS）。
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from licensing_core import (ConflictError, LicensingStore, NotFoundError,
                            ValidationError, parse)

STORE = None  # 在 main() 中初始化


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _dt(s):
    if s is None:
        return None
    try:
        return parse(s)
    except Exception:
        raise ValidationError("时间格式应为 ISO 8601，如 2026-10-01T00:00:00+00:00")


class Handler(BaseHTTPRequestHandler):
    server_version = "LicensingAPI/1.0"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):  # 静默
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            raise ValidationError("请求体不是合法 JSON")

    # ---------- 路由 ----------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        path = unquote(self.path.split("?")[0]).rstrip("/") or "/"
        query = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    query[k] = v
        now = now_utc()
        try:
            if method == "GET" and path == "/api/health":
                return self._json(200, {"ok": True, "now": now.isoformat()})
            if method == "GET" and path == "/api/overview":
                return self._json(200, STORE.overview(now))
            if method == "GET" and path == "/api/assets":
                return self._json(200, STORE.list_assets())
            if method == "POST" and path == "/api/assets":
                b = self._body()
                aid = STORE.register_asset(b["title"], b["content_hash"], b["source_name"], now,
                                           byte_size=b.get("byte_size", 0),
                                           source_ref=b.get("source_ref"))
                return self._json(201, {"id": aid})
            m = re.fullmatch(r"/api/assets/([^/]+)/replace", path)
            if method == "POST" and m:
                b = self._body()
                ver = STORE.replace_asset_file(m.group(1), b["content_hash"],
                                               int(b["expected_version"]), now,
                                               byte_size=b.get("byte_size", 0))
                return self._json(200, {"version": ver})
            if method == "POST" and path == "/api/licenses":
                b = self._body()
                lid = STORE.create_license(b["asset_id"], b["author"], b["uses"],
                                           _dt(b["valid_from"]), now,
                                           valid_until=_dt(b.get("valid_until")),
                                           evidence_type=b.get("evidence_type"),
                                           evidence_uri=b.get("evidence_uri"),
                                           note=b.get("note"))
                return self._json(201, {"id": lid})
            m = re.fullmatch(r"/api/licenses/([^/]+)/renew", path)
            if method == "POST" and m:
                STORE.renew_license(m.group(1), _dt(self._body()["valid_until"]), now)
                return self._json(200, {"ok": True})
            m = re.fullmatch(r"/api/licenses/([^/]+)/evidence", path)
            if method == "POST" and m:
                b = self._body()
                STORE.set_evidence(m.group(1), b.get("evidence_type"), b["evidence_uri"], now)
                return self._json(200, {"ok": True})
            m = re.fullmatch(r"/api/licenses/([^/]+)/revoke_use", path)
            if method == "POST" and m:
                b = self._body()
                impact = STORE.revoke_use(m.group(1), b["use_code"], b.get("reason", ""), now)
                return self._json(200, impact)
            m = re.fullmatch(r"/api/licenses/([^/]+)/impact", path)
            if method == "GET" and m:
                lic = STORE._lic(m.group(1))
                impact = STORE.compute_impact(lic["asset_id"], query.get("use_code", ""), now)
                return self._json(200, impact)
            if method == "POST" and path == "/api/derivatives":
                b = self._body()
                did = STORE.add_derivative(b["parent_asset_id"], b["content_hash"],
                                           b.get("transform", ""), now)
                return self._json(201, {"id": did})
            if method == "GET" and path == "/api/refs":
                return self._json(200, STORE.list_refs())
            if method == "POST" and path == "/api/refs":
                b = self._body()
                rid = STORE.publish_ref(b["page_slug"], b["page_kind"], b["use_code"],
                                        b["target_kind"], b["target_id"], now)
                return self._json(201, {"id": rid})
            m = re.fullmatch(r"/api/refs/([^/]+)/republish", path)
            if method == "POST" and m:
                STORE.republish_ref(m.group(1), now)
                return self._json(200, {"ok": True})
            m = re.fullmatch(r"/api/pages/(.+)/render", path)  # slug 可含层级，如 articles/xxx
            if method == "GET" and m:
                return self._json(200, STORE.render_page(m.group(1), now))
            if method == "GET" and path == "/api/cache":
                return self._json(200, STORE.list_cache(now))
            if method == "POST" and path == "/api/exports":
                b = self._body()
                eid = STORE.start_export(b["kind"], b["items"], now)
                return self._json(201, {"id": eid})
            m = re.fullmatch(r"/api/exports/([^/]+)/finish", path)
            if method == "POST" and m:
                state = STORE.finish_export(m.group(1), now)
                return self._json(200, {"state": state})
            m = re.fullmatch(r"/api/exports/([^/]+)/download", path)
            if method == "POST" and m:
                STORE.record_download(m.group(1), now)
                return self._json(200, {"ok": True})
            if method == "GET" and path == "/api/exports":
                return self._json(200, STORE.list_exports())
            if method == "GET" and path == "/api/tasks":
                return self._json(200, STORE.list_tasks(query.get("state")))
            m = re.fullmatch(r"/api/tasks/([^/]+)/execute", path)
            if method == "POST" and m:
                return self._json(200, {"result": STORE.execute_task(m.group(1), now)})
            if method == "POST" and path == "/api/sweeper/run":
                return self._json(200, STORE.run_sweeper(now))
            if method == "GET" and path == "/api/cdn/purges":
                return self._json(200, STORE.list_purges())
            m = re.fullmatch(r"/api/cdn/purges/([^/]+)/confirm", path)
            if method == "POST" and m:
                STORE.confirm_purge(m.group(1), now)
                return self._json(200, {"ok": True})
            if method == "GET" and path == "/api/audit":
                return self._json(200, STORE.list_audit())
            if method == "GET" and path == "/api/reports/unrecoverable":
                return self._json(200, STORE.unrecoverable_report(now))
            return self._json(404, {"error": "not_found", "path": path})
        except ValidationError as e:
            return self._json(422, {"error": "validation", "message": str(e)})
        except ConflictError as e:
            return self._json(409, {"error": "conflict", "message": str(e)})
        except NotFoundError as e:
            return self._json(404, {"error": "not_found", "message": str(e)})
        except KeyError as e:
            return self._json(400, {"error": "bad_request", "message": "缺少字段 %s" % e})
        except Exception as e:  # noqa
            return self._json(500, {"error": "internal", "message": str(e)})


def main():
    import argparse
    global STORE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--db", default="server/licensing.db")
    args = ap.parse_args()
    STORE = LicensingStore(args.db)
    print("素材授权台 API 已启动: http://localhost:%d  (db=%s)" % (args.port, args.db))
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
