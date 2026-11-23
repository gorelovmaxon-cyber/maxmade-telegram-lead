"""Bounded, allowlisted Bot API transport metadata. Never log URLs or payloads."""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import re
from pathlib import Path
import socket
import ssl
import time
import urllib.error
import urllib.request

STATE = Path(__file__).resolve().parent / "state"
MAX_BYTES = 2 * 1024 * 1024
METHODS = {"getMe", "getWebhookInfo", "getChat", "getChatMember", "getUpdates",
           "sendMessage", "editMessageText", "answerCallbackQuery"}


class Trace:
    def __init__(self, method: str, timeout: int, backend: str):
        self.started = time.monotonic()
        self.phase_started = self.started
        self.phase = "setup"
        self.timings: dict[str, int] = {}
        self.record = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "pid": os.getpid(), "method": method if method in METHODS else "other",
                       "backend": backend if backend in {"urllib", "curl"} else "other",
                       "timeout_s": timeout}

    def enter(self, phase: str) -> None:
        current = time.monotonic()
        self.timings[self.phase] = self.timings.get(self.phase, 0) + round((current-self.phase_started)*1000)
        self.phase, self.phase_started = phase, current

    def finish(self, outcome: str, exc: BaseException | None = None, **numbers) -> None:
        failed_phase = self.phase
        self.enter("done")
        record = {**self.record, "elapsed_ms": round((time.monotonic()-self.started)*1000),
                  "phase": failed_phase, "timings_ms": self.timings,
                  "outcome": outcome if outcome in {"ok", "network_error", "http_error", "api_error", "invalid_response"} else "other"}
        # No free-form strings from exceptions, headers, process stderr, URLs, or request bodies.
        if exc is not None:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, BaseException):
                for key in ("errno", "winerror"):
                    value = getattr(reason, key, None)
                    if isinstance(value, int):
                        record[key] = value
                if isinstance(reason, ssl.SSLCertVerificationError):
                    category = "tls_certificate"
                elif isinstance(reason, ssl.SSLError):
                    category = "tls"
                elif isinstance(reason, (TimeoutError, socket.timeout)):
                    category = "timeout"
                elif isinstance(reason, ConnectionRefusedError):
                    category = "connection_refused"
                elif isinstance(reason, ConnectionResetError):
                    category = "connection_reset"
                elif isinstance(reason, socket.gaierror):
                    category = "dns"
                elif isinstance(reason, http.client.RemoteDisconnected):
                    category = "remote_closed"
                else:
                    category = "transport_or_response"
            else:
                category = "transport_or_response"
            record["error_category"] = category
        for key in ("http_status", "api_code", "retry_after_s", "curl_exit"):
            if isinstance(numbers.get(key), int):
                record[key] = numbers[key]
        write(record)


def write(record: dict) -> None:
    """One delivery process writes; probes must choose their own directory."""
    try:
        STATE.mkdir(exist_ok=True)
        path = STATE / "delivery-transport.jsonl"
        if path.exists() and path.stat().st_size >= MAX_BYTES:
            (STATE / "delivery-transport.2.jsonl").unlink(missing_ok=True)
            previous = STATE / "delivery-transport.1.jsonl"
            if previous.exists():
                previous.replace(STATE / "delivery-transport.2.jsonl")
            path.replace(previous)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError:
        # Diagnostics cannot change send/retry semantics or expose an exception URL.
        pass


class Connection(http.client.HTTPSConnection):
    def __init__(self, *args, trace: Trace, **kwargs):
        self.trace = trace
        super().__init__(*args, **kwargs)
        create = self._create_connection

        def connect_tcp(*args, **kwargs):
            self.trace.record["via_proxy"] = bool(self._tunnel_host)
            self.trace.enter("proxy_tcp" if self._tunnel_host else "origin_tcp")
            sock = create(*args, **kwargs)
            self.trace.enter("proxy_connect" if self._tunnel_host else "tls")
            return sock

        self._create_connection = connect_tcp

    def _tunnel(self):
        self.trace.enter("proxy_connect")
        try:
            super()._tunnel()
        except OSError as exc:
            match = re.match(r"Tunnel connection failed: (\d{3})(?:\s|$)", str(exc))
            if match:
                self.trace.record["proxy_connect_status"] = int(match[1])
            raise
        self.trace.record["proxy_connect_status"] = 200
        self.trace.enter("tls")

    def connect(self):
        super().connect()
        self.trace.enter("http_request")

    def getresponse(self):
        self.trace.enter("http_headers")
        response = super().getresponse()
        self.trace.record["http_status"] = response.status
        self.trace.enter("http_body")
        return response


class Handler(urllib.request.HTTPSHandler):
    def __init__(self, trace: Trace):
        super().__init__()
        self.trace = trace

    def https_open(self, request):
        def factory(*args, **kwargs):
            return Connection(*args, trace=self.trace, **kwargs)
        return self.do_open(factory, request, context=self._context)


def open_request(request, timeout: int, trace: Trace):
    # Default ProxyHandler retains urllib's normal environment/Windows settings.
    # This private opener does not alter any global handler or other process.
    return urllib.request.build_opener(Handler(trace)).open(request, timeout=timeout)
