"""Disposable browser fixture. No Drupal or client data."""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if self.path == "/cms" and "fixture=1" not in self.headers.get("Cookie", ""):
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        self.send_response(
            500 if self.path == "/error" else 404 if self.path == "/broken.png" else 200
        )
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if self.path == "/login":
            html = '<form action="/login" method="post"><input name="name"><input name="pass" type="password"><button>Log in</button></form>'
        else:
            color = "red" if self.path == "/changed" else "#123456"
            html = f'<style>body {{background:{color};color:white;font:20px Arial}}</style><h1>Fixture home</h1><nav id="authenticated">Editor navigation</nav><button id="open" onclick="document.querySelector(\'dialog\').showModal()">Edit</button><dialog><label>Title<input id="title"></label><button id="close" onclick="this.closest(\'dialog\').close()">Close</button></dialog>'
            if self.path.startswith("/hidden-image"):
                html += '<img style="display:none" src="/broken.png">'
            if self.path == "/missing-image":
                html += '<img src="/broken.png">'
        self.wfile.write(
            (
                '<!doctype html><html><head><meta charset="utf-8"></head><body>'
                + html
                + "</body></html>"
            ).encode()
        )

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if b"name=fixture" not in body or b"pass=fixture" not in body:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        self.send_response(302)
        self.send_header("Set-Cookie", "fixture=1; Path=/; HttpOnly")
        self.send_header("Location", "/cms")
        self.end_headers()


HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8765"))), Handler).serve_forever()
