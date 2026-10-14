"""
A small web app with the dynamic-value patterns real systems use, for
end-to-end correlation tests. Every value below changes per session and is
validated server-side, so a script only replays if all of them are correlated:

  GET  /login              HTML form: hidden csrf_token, <meta name="csrf-token">,
                           a nonce inside inline JavaScript
  POST /login              checks csrf_token → 302 /callback?code=…&state=… (single-use code)
  GET  /callback           exchanges the code → JSON {access_token: JWT}; sets XSRF-TOKEN cookie
  GET  /api/orders         Bearer → list of orders with random ids (select by name)
  POST /api/orders/{id}/checkout
                           Bearer + X-XSRF-TOKEN header (double-submit cookie) +
                           X-Nonce header + JSON body {orderId, ts}
                           → {"orderNo": 7-digit number}
  GET  /api/receipt/{orderNo}
"""

import base64
import json
import random
import secrets
import uuid

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI()

_csrf: dict[str, str] = {}          # session id -> csrf token
_nonce: dict[str, str] = {}         # session id -> js nonce
_codes: dict[str, str] = {}         # auth code -> session id (single use)
_tokens: dict[str, dict] = {}       # access token -> state
_orders: dict[str, str] = {}        # order number -> token


def _jwt(sub: str) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': sub, 'jti': secrets.token_hex(8)})}.{secrets.token_urlsafe(16)}"


@app.get("/login", response_class=HTMLResponse)
def login_page():
    sid = secrets.token_hex(12)
    _csrf[sid] = secrets.token_urlsafe(24)
    _nonce[sid] = secrets.token_hex(10)
    html = f"""<!doctype html><html><head>
<meta name="csrf-token" content="{_csrf[sid]}">
<script>window.APP = {{ nonce: "{_nonce[sid]}" }};</script></head>
<body><form method="post" action="/login">
<input type="hidden" name="csrf_token" value="{_csrf[sid]}">
<input name="username"><input name="password" type="password">
</form></body></html>"""
    resp = HTMLResponse(html)
    resp.set_cookie("sid", sid)
    return resp


@app.post("/login")
def do_login(request: Request, csrf_token: str = Form(...), username: str = Form(...), password: str = Form(...)):
    sid = request.cookies.get("sid", "")
    if not sid or _csrf.get(sid) != csrf_token:
        raise HTTPException(403, "bad csrf token")
    code = secrets.token_urlsafe(18)
    _codes[code] = sid
    return RedirectResponse(f"/callback?code={code}&state=st{random.randint(1000, 9999)}", status_code=302)


@app.get("/callback")
def callback(code: str, state: str):
    sid = _codes.pop(code, None)
    if sid is None:
        raise HTTPException(400, "invalid or reused code")
    token = _jwt("user-42")
    xsrf = secrets.token_urlsafe(20) + "/+="          # needs URL-encoding in the cookie
    _tokens[token] = {"xsrf": xsrf, "orders": {}, "nonce": _nonce.get(sid)}
    resp = JSONResponse({"access_token": token, "token_type": "Bearer", "expires_in": 3600})
    from urllib.parse import quote
    resp.set_cookie("XSRF-TOKEN", quote(xsrf, safe=""))
    return resp


def _auth(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer")
    state = _tokens.get(authorization[7:])
    if state is None:
        raise HTTPException(401, "unknown token")
    return state


@app.get("/api/orders")
def orders(authorization: str | None = Header(None)):
    state = _auth(authorization)
    items = [{"name": n, "id": str(uuid.uuid4())} for n in random.sample(["Gold", "Silver", "Bronze"], 3)]
    state["orders"] = {i["id"]: i["name"] for i in items}
    return {"items": items, "count": len(items)}


@app.post("/api/orders/{order_id}/checkout")
async def checkout(order_id: str, request: Request, authorization: str | None = Header(None),
                   x_xsrf_token: str | None = Header(None), x_nonce: str | None = Header(None)):
    state = _auth(authorization)
    if x_xsrf_token != state["xsrf"]:
        raise HTTPException(403, "bad xsrf header")
    if x_nonce != state["nonce"]:
        raise HTTPException(403, "bad nonce")
    if state["orders"].get(order_id) != "Silver":
        raise HTTPException(404, "unknown order")
    body = await request.json()
    if body.get("orderId") != order_id:
        raise HTTPException(400, "body/order mismatch")
    order_no = str(random.randint(1_000_000, 9_999_999))
    _orders[order_no] = authorization
    return {"orderNo": int(order_no), "status": "placed"}


@app.get("/api/receipt/{order_no}")
def receipt(order_no: str, authorization: str | None = Header(None)):
    _auth(authorization)
    if _orders.get(order_no) != authorization:
        raise HTTPException(404, "no such order")
    return {"orderNo": int(order_no), "total": 42.5}
