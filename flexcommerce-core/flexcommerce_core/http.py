"""Tiny HTTP client (stdlib only) used by payment gateways and notification providers."""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request


class HTTPClientError(Exception):
    """Network-level failure (DNS, TLS, timeout...). HTTP error statuses are returned, not raised."""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


def request_json(method, url, headers=None, payload=None, timeout=30, form=None, auth=None):
    """
    Send a request and return ``(status_code, decoded_json_or_{"raw": text})``.

    ``payload`` is sent as JSON; ``form`` as ``application/x-www-form-urlencoded``;
    ``auth`` is a ``(username, password)`` tuple for HTTP Basic auth.
    """
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise HTTPClientError(f"Refusing non-HTTP URL: {url}")
    data = None
    request = urllib.request.Request(url, method=method.upper())  # noqa: S310 - scheme checked above
    request.add_header("Accept", "application/json")
    if payload is not None:
        data = json.dumps(payload).encode()
        request.add_header("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    if auth is not None:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    request.data = data
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked above
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read() if exc.fp else b"")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPClientError(f"Could not reach {urllib.parse.urlparse(url).netloc}: {exc}") from exc


def _decode(raw):
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"raw": raw[:500].decode("utf-8", "replace")}
