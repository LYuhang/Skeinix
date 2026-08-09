import base64
import hashlib
import pytest
from vibecanvas_api.browser.commands import Observation
from vibecanvas_api.browser import media as media_mod


class FakeVfsStore:
    def __init__(self):
        self.writes = []

    def upsert_artifact_bytes(self, *, wf_id, path, data, content_type, abstract=""):
        self.writes.append((wf_id, path, content_type, len(data), abstract))
        return False


def test_screenshot_bytes_become_a_chat_workspace_data_path(monkeypatch):
    store = FakeVfsStore()
    monkeypatch.setattr(media_mod, "PostgresVfsStore", lambda: store)
    raw = base64.b64encode(b"PNGDATA").decode()
    obs = Observation(id="c1", ok=True, target_id="T0", error=None,
        data={"ok": True, "target_id": "T0",
              "media": [{"slot": "screenshot", "b64": raw, "ext": "png", "mime": "image/png"}]},
        media=[{"slot": "screenshot", "b64": raw, "ext": "png", "mime": "image/png"}])
    out = media_mod.write_observation_media(obs, workspace_scope_id="chat_ws_1", tenant_id="t1")
    h = hashlib.sha256(b"PNGDATA").hexdigest()
    expected = f"/data/browser-media/{h}.png"
    assert out.data["screenshot"] == expected
    assert out.data["media"] == [{
        "slot": "screenshot",
        "path": expected,
        "bytes_len": 7,
        "mime": "image/png",
    }]
    assert "b64" not in out.data["media"][0]                # bytes never survive
    assert store.writes[0][0] == "chat_ws_1"
    assert store.writes[0][1] == expected and store.writes[0][2] == "image/png"


def test_save_path_overrides_default_browser_media_path(monkeypatch):
    store = FakeVfsStore()
    monkeypatch.setattr(media_mod, "PostgresVfsStore", lambda: store)
    raw = base64.b64encode(b"PDF").decode()
    obs = Observation(id="c1", ok=True, target_id="T0", error=None,
        data={"ok": True, "target_id": "T0",
              "media": [{"slot": "resource", "b64": raw, "ext": "pdf", "mime": "application/pdf"}]},
        media=[{"slot": "resource", "b64": raw, "ext": "pdf", "mime": "application/pdf"}])
    out = media_mod.write_observation_media(
        obs,
        workspace_scope_id="chat_ws_1",
        tenant_id="t1",
        save_path="/data/downloads/report.pdf",
    )
    assert out.data["resource"] == "/data/downloads/report.pdf"
    assert store.writes[0][0] == "chat_ws_1"
    assert store.writes[0][1] == "/data/downloads/report.pdf"
    assert store.writes[0][2] == "application/pdf"


@pytest.mark.parametrize("bad_path", [
    "/run/x.png",
    "/chat/c1/data/x.png",
    "/mount/x.png",
    "/data/",
    "/data/../x.png",
])
def test_save_path_must_stay_under_chat_data(monkeypatch, bad_path):
    store = FakeVfsStore()
    monkeypatch.setattr(media_mod, "PostgresVfsStore", lambda: store)
    raw = base64.b64encode(b"X").decode()
    obs = Observation(id="c1", ok=True, target_id="T0", error=None,
        data={"ok": True, "target_id": "T0",
              "media": [{"slot": "resource", "b64": raw, "ext": "png", "mime": "image/png"}]},
        media=[{"slot": "resource", "b64": raw, "ext": "png", "mime": "image/png"}])
    with pytest.raises(ValueError):
        media_mod.write_observation_media(
            obs,
            workspace_scope_id="chat_ws_1",
            tenant_id="t1",
            save_path=bad_path,
        )
    assert store.writes == []


def test_frames_become_a_list_of_paths(monkeypatch):
    store = FakeVfsStore()
    monkeypatch.setattr(media_mod, "PostgresVfsStore", lambda: store)
    b = base64.b64encode(b"f").decode()
    media = [{"slot": "frames", "b64": b, "ext": "jpg", "mime": "image/jpeg"},
             {"slot": "frames", "b64": b, "ext": "jpg", "mime": "image/jpeg"}]
    obs = Observation(id="c1", ok=True, target_id="T0", error=None,
        data={"ok": True, "target_id": "T0", "media": media}, media=media)
    out = media_mod.write_observation_media(obs, workspace_scope_id="chat_ws_1", tenant_id="t1")
    # content-hash dedup: identical frames → one write, but two path entries
    assert isinstance(out.data["frames"], list) and len(out.data["frames"]) == 2
    assert len(store.writes) == 1                            # dedup by hash


def test_host_media_writer_binds_chat_workspace_scope(monkeypatch):
    store = FakeVfsStore()
    monkeypatch.setattr(media_mod, "PostgresVfsStore", lambda: store)
    raw = base64.b64encode(b"X").decode()
    media = [{"slot": "image", "b64": raw, "ext": "png", "mime": "image/png"}]
    obs = Observation(id="c1", ok=True, target_id="T0", error=None,
        data={"ok": True, "target_id": "T0", "media": media}, media=media)
    writer = media_mod.host_media_writer(workspace_scope_id="chat_ws_1", tenant_id="t1")
    out = writer(obs, transport_id="t1:b1")  # host calls with transport_id kwarg
    h = hashlib.sha256(b"X").hexdigest()
    assert out.data["image"] == f"/data/browser-media/{h}.png"
    assert store.writes[0][0] == "chat_ws_1"


def test_no_media_passthrough(monkeypatch):
    store = FakeVfsStore()
    monkeypatch.setattr(media_mod, "PostgresVfsStore", lambda: store)
    obs = Observation(id="c1", ok=True, target_id="T0", error=None,
        data={"ok": True, "target_id": "T0", "text": "hi"}, media=[])
    out = media_mod.write_observation_media(obs, workspace_scope_id="chat_ws_1", tenant_id="t1")
    assert out is obs and store.writes == []
