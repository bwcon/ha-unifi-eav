"""Mock UniFi Network console exposing the reverse-engineered Pro AV endpoints.

    python tools/mock_unifi.py [--port 8443] [--tls CERT KEY] [--expire-after SECONDS]
                               [--user admin] [--password password]

Serves both UniFi OS (/proxy/network + /api/auth/login) and legacy (no prefix +
/api/login) shapes. Every request is logged; write bodies are logged too.
Default credentials: admin / password.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import secrets
import ssl
import time
import uuid

from aiohttp import web

LOG = logging.getLogger("mock_unifi")

TX1, TX2 = "f4:e2:c6:00:00:01", "f4:e2:c6:00:00:02"
RX1, RX2, RX3 = "f4:e2:c6:00:00:11", "f4:e2:c6:00:00:12", "f4:e2:c6:00:00:13"


def _client(mac: str, mode: str) -> dict:
    return {
        "mac": mac,
        "client_type": "VIDEO",
        "mode": mode,
        "resolution": "AUTO",
        "color_format": "RGB",
        "audio_enabled": True,
        "audio_mode": "PCM",
        "enabled": True,
    }


def _device(mac: str, name: str | None) -> dict:
    d = {"mac": mac, "model": "EAV-Bridge", "type": "eav", "state": 1, "adopted": True, "disabled": False}
    if name:
        d["name"] = name
    return d


INITIAL = {
    "hosts": [TX1, TX2],
    "clients": [RX1, RX2, RX3],
    "groups": [
        {
            "id": "3f0c9d1e-5b1a-4c8e-9f2d-7a6b5c4d3e2f",
            "channel": 1,
            "name": "Apple TV",
            "type": "VIDEO",
            "host": {"mac": TX1, "host_type": "VIDEO", "refresh_rate": "AUTO", "enabled": True},
            "clients": [_client(RX1, "MULTICAST"), _client(RX2, "MULTICAST")],
        }
    ],
    "devices": [
        _device(TX1, "Apple TV"),
        _device(TX2, "Cable Box"),
        _device(RX1, "Living Room TV"),
        _device(RX2, "Kitchen TV"),
        _device(RX3, None),  # un-named bridge -> clients fall back to MAC
    ],
    "channel": 1,
}


def make_app(user: str, password: str, expire_after: float | None) -> web.Application:
    state = copy.deepcopy(INITIAL)
    sessions: dict[str, dict] = {}  # TOKEN -> {"csrf", "issued"}

    @web.middleware
    async def auth(request: web.Request, handler):
        if request.path in ("/api/auth/login", "/api/login"):
            return await handler(request)
        token = request.cookies.get("TOKEN")
        sess = sessions.get(token)
        if sess and expire_after and time.time() - sess["issued"] > expire_after:
            sessions.pop(token, None)
            sess = None
        if not sess:
            return web.json_response({"code": "AUTHENTICATION_FAILED_INVALID_TOKEN"}, status=401)
        if request.method != "GET" and request.headers.get("X-CSRF-Token") != sess["csrf"]:
            return web.json_response({"code": "CSRF_TOKEN_MISMATCH"}, status=403)
        return await handler(request)

    async def login(request: web.Request):
        body = await request.json()
        if (body.get("username"), body.get("password")) != (user, password):
            return web.json_response({"code": "AUTHENTICATION_FAILED_INVALID_CREDENTIALS"}, status=401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[token] = {"csrf": csrf, "issued": time.time()}
        resp = web.json_response({"unique_id": str(uuid.uuid4()), "username": user}, headers={"X-CSRF-Token": csrf})
        resp.set_cookie("TOKEN", token, httponly=True)
        return resp

    async def matrix(request: web.Request):
        return web.json_response(
            {
                "bridges": state["hosts"] + state["clients"],
                "hosts": state["hosts"],
                "clients": state["clients"],
                "host_candidates": [],
                "client_candidates": [],
                "groups": state["groups"],
            }
        )

    async def device_basic(request: web.Request):
        return web.json_response({"meta": {"rc": "ok"}, "data": state["devices"]})

    def validate(group: dict, exclude_id: str | None) -> str | None:
        others = [g for g in state["groups"] if g["id"] != exclude_id]
        host = group.get("host", {}).get("mac")
        if host not in state["hosts"]:
            return f"unknown host {host}"
        if any(g["host"]["mac"] == host for g in others):
            return f"host {host} already has a group"
        for c in group.get("clients", []):
            if c.get("mac") not in state["clients"]:
                return f"unknown client {c.get('mac')}"
            if any(c["mac"] == oc["mac"] for g in others for oc in g["clients"]):
                return f"client {c['mac']} is already in another group"
        return None

    def bad(msg: str):
        LOG.info("  -> 400 %s", msg)
        return web.json_response({"code": "InvalidPayload", "message": msg}, status=400)

    async def create_group(request: web.Request):
        body = await request.json()
        LOG.info("  body: %s", json.dumps(body))
        if err := validate(body, None):
            return bad(err)
        state["channel"] += 1
        group = {**body, "id": str(uuid.uuid4()), "channel": state["channel"]}
        state["groups"].append(group)
        return web.json_response(group, status=201)

    async def update_group(request: web.Request):
        gid = request.match_info["id"]
        body = await request.json()
        LOG.info("  body: %s", json.dumps(body))
        idx = next((i for i, g in enumerate(state["groups"]) if g["id"] == gid), None)
        if idx is None:
            return web.json_response({"code": "NotFound"}, status=404)
        if err := validate(body, gid):
            return bad(err)
        old = state["groups"][idx]
        state["groups"][idx] = {**body, "id": gid, "channel": old["channel"]}
        return web.json_response(state["groups"][idx])

    async def delete_group(request: web.Request):
        gid = request.match_info["id"]
        before = len(state["groups"])
        state["groups"] = [g for g in state["groups"] if g["id"] != gid]
        if len(state["groups"]) == before:
            return web.json_response({"code": "NotFound"}, status=404)
        return web.json_response({})

    app = web.Application(middlewares=[auth])
    app.router.add_post("/api/auth/login", login)
    app.router.add_post("/api/login", login)
    for prefix in ("/proxy/network", ""):
        av = f"{prefix}/v2/api/site/{{site}}/proav/video"
        app.router.add_get(f"{av}/matrix", matrix)
        app.router.add_post(f"{av}/group", create_group)
        app.router.add_put(f"{av}/group/{{id}}", update_group)
        app.router.add_delete(f"{av}/group/{{id}}", delete_group)
        app.router.add_get(f"{prefix}/api/s/{{site}}/stat/device-basic", device_basic)
    return app


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8443)
    p.add_argument("--tls", nargs=2, metavar=("CERT", "KEY"), help="serve HTTPS with this cert/key pair")
    p.add_argument("--expire-after", type=float, metavar="SECONDS", help="sessions expire after N seconds (forces re-login)")
    p.add_argument("--user", default="admin")
    p.add_argument("--password", default="password")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ssl_ctx = None
    if args.tls:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(*args.tls)
    LOG.info("mock UniFi console on %s://0.0.0.0:%d (user=%s)", "https" if ssl_ctx else "http", args.port, args.user)
    web.run_app(
        make_app(args.user, args.password, args.expire_after),
        port=args.port,
        ssl_context=ssl_ctx,
        access_log_format='%a "%r" %s',
        print=None,
    )


if __name__ == "__main__":
    main()
