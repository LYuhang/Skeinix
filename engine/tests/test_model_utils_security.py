from __future__ import annotations

import pytest

from vibecanvas_engine import public_http
from vibecanvas_engine.model_utils import encode_image


def test_serialized_bytes_are_parsed_without_code_execution():
    assert encode_image("b'\\x41\\x42'", return_base64=False) == b"AB"


def test_serialized_bytes_reject_python_expressions():
    with pytest.raises((SyntaxError, ValueError)):
        encode_image(
            "b'\\x41' + __import__('os').getcwd().encode()",
            return_base64=False,
        )


def test_remote_image_rejects_plaintext_and_private_destinations(monkeypatch):
    with pytest.raises(ValueError, match="public HTTPS"):
        encode_image("http://example.com/image.png", return_base64=False)

    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (public_http.socket.AF_INET, 1, 6, "", ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(ValueError, match="private, local, reserved"):
        encode_image("https://example.com/image.png", return_base64=False)


class _Response:
    def __init__(self, status, headers, body=b""):
        self.status = status
        self.headers = headers
        self._body = body
        self._offset = 0

    def read(self, amount):
        chunk = self._body[self._offset:self._offset + amount]
        self._offset += len(chunk)
        return chunk

    def release_conn(self):
        return None

    def close(self):
        return None


class _Pool:
    def __init__(self, response, calls, address, port, **kwargs):
        self.response = response
        self.calls = calls
        self.calls.append(("connect", address, port, kwargs))

    def request(self, method, target, **kwargs):
        self.calls.append(("request", method, target, kwargs))
        return self.response

    def close(self):
        return None


def test_remote_image_pins_dns_and_validates_each_redirect(monkeypatch):
    dns = {
        "images.example": "93.184.216.34",
        "cdn.example": "142.250.72.14",
    }
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [
            (public_http.socket.AF_INET, 1, 6, "", (dns[host], port))
        ],
    )
    responses = iter([
        _Response(302, {"location": "https://cdn.example/final.png"}),
        _Response(200, {"content-length": "3"}, b"PNG"),
    ])
    calls = []

    def pool_factory(address, port, **kwargs):
        return _Pool(next(responses), calls, address, port, **kwargs)

    monkeypatch.setattr(public_http.urllib3, "HTTPSConnectionPool", pool_factory)

    assert encode_image(
        "https://images.example/start.png", return_base64=False,
    ) == b"PNG"
    connections = [call for call in calls if call[0] == "connect"]
    assert [call[1] for call in connections] == [
        "93.184.216.34",
        "142.250.72.14",
    ]
    requests = [call for call in calls if call[0] == "request"]
    assert requests[0][3]["headers"]["Host"] == "images.example"
    assert requests[1][3]["headers"]["Host"] == "cdn.example"


def test_remote_image_revalidates_redirect_destination(monkeypatch):
    def resolve(host, port, **_kwargs):
        address = "93.184.216.34" if host == "images.example" else "169.254.169.254"
        return [(public_http.socket.AF_INET, 1, 6, "", (address, port))]

    monkeypatch.setattr(public_http.socket, "getaddrinfo", resolve)
    calls = []
    monkeypatch.setattr(
        public_http.urllib3,
        "HTTPSConnectionPool",
        lambda address, port, **kwargs: _Pool(
            _Response(302, {"location": "https://metadata.example/latest"}),
            calls,
            address,
            port,
            **kwargs,
        ),
    )

    with pytest.raises(ValueError, match="must not resolve"):
        encode_image("https://images.example/start.png", return_base64=False)
    assert [call[1] for call in calls if call[0] == "connect"] == [
        "93.184.216.34"
    ]
