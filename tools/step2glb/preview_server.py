# -*- coding: utf-8 -*-
"""
preview_server.py — 极简静态服务器，用于把 frontend 的数模自检页跑起来（截图 / 人工查看）

映射规则（等价于 Vite 的 public 目录语义）:
    /models/*        -> frontend/public/models/*
    /node_modules/*  -> frontend/node_modules/*
    /<其余>          -> frontend/public/<其余>

用法: python preview_server.py [端口]
"""
import http.server
import os
import socketserver
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "frontend"))
PUBLIC = os.path.join(ROOT, "public")


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = path.split("?", 1)[0].split("#", 1)[0]
        p = p.lstrip("/")
        if p.startswith("node_modules/"):
            return os.path.join(ROOT, p)
        if p.startswith("models/") or p.startswith("assets/"):
            return os.path.join(PUBLIC, p)
        cand = os.path.join(PUBLIC, p)
        if p and os.path.exists(cand):
            return cand
        return os.path.join(ROOT, p)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    with Server(("127.0.0.1", port), Handler) as httpd:
        print(f"serving http://127.0.0.1:{port}/  (root={ROOT})")
        print(f"  数模自检页  http://127.0.0.1:{port}/model-check.html")
        httpd.serve_forever()
