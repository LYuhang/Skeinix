import json, pytest
from vibecanvas_api.browser.envelope import encode, decode

def test_encode_decode_roundtrip():
    raw = encode("playwright_relay", id="c1", channel="chat:1", transport="t:b", data={"action": "request"})
    d = decode(raw)
    assert d == {"v": 1, "kind": "playwright_relay", "id": "c1", "channel": "chat:1",
                 "transport": "t:b", "data": {"action": "request"}, "producer": None}

def test_decode_rejects_malformed():
    with pytest.raises(ValueError):
        decode("not json")
    with pytest.raises(ValueError):
        decode(json.dumps({"kind": "x"}))  # missing required id/channel/transport
