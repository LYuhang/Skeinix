import json
import re
from pathlib import Path

import pytest
from vibecanvas_api.browser.commands import (
    Cmd, READ_CMDS, ACT_CMDS, MUTATING, CONTROL_CMDS, MEDIA_SLOTS,
    make_command, parse_observation, Observation,
)


def test_enum_is_closed_and_string_valued():
    # every §5.1 command present, string-valued (serializes to the wire `cmd`)
    assert Cmd.NAVIGATE.value == "navigate"
    assert Cmd.READ_FIELDS.value == "read_fields"
    assert Cmd.ACQUIRE_VIDEO.value == "acquire_video"
    assert Cmd("click") is Cmd.CLICK            # value-constructable
    names = {c.value for c in Cmd}
    assert {"navigate", "snapshot", "read_text", "read_fields", "query",
            "screenshot", "get_image", "acquire_video", "click", "type",
            "select", "submit", "press", "scroll", "wait_for",
            "switch_tab", "close_tab", "wait_for_new_tab", "check_login",
            "highlight", "narrate"} <= names
    assert "confirm" not in names  # authorization belongs to the outer Agent Loop


def test_extension_command_enum_is_byte_identical_to_backend_source_of_truth():
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "extension/src/shared/commands.ts").read_text(encoding="utf-8")
    command_block = source.split("export const CMD = {", 1)[1].split("} as const;", 1)[0]
    extension_values = set(
        re.findall(r'^\s+[A-Z][A-Z_]*:\s*"([^"]+)",?$', command_block, re.MULTILINE)
    )

    assert extension_values == {command.value for command in Cmd}


def test_class_partitions_and_mutating_boundary():
    assert Cmd.SNAPSHOT in READ_CMDS and Cmd.GET_ATTRIBUTE in READ_CMDS
    assert Cmd.CLICK in ACT_CMDS and Cmd.SUBMIT in ACT_CMDS
    assert Cmd.NAVIGATE in CONTROL_CMDS and Cmd.START_SESSION in CONTROL_CMDS
    # The side-effect boundary includes form/page writes plus navigation, tab,
    # and session lifecycle operations.
    assert MUTATING == (ACT_CMDS | CONTROL_CMDS)
    assert not (READ_CMDS & MUTATING)


def test_media_slots_declared():
    assert MEDIA_SLOTS[Cmd.SCREENSHOT] == ("screenshot",)
    assert MEDIA_SLOTS[Cmd.ACQUIRE_VIDEO] == ("frames", "video")
    assert Cmd.NAVIGATE not in MEDIA_SLOTS    # no media


def test_make_command_builds_envelope():
    raw = make_command(Cmd.CLICK, id="c1", transport="t:b", channel="chat:1",
                       args={"handle": "h7"}, target_id="tab9", producer="agent")
    d = json.loads(raw)
    assert d["kind"] == "command" and d["id"] == "c1"
    assert d["data"] == {"cmd": "click", "args": {"handle": "h7"}, "target_id": "tab9"}
    assert d["transport"] == "t:b" and d["channel"] == "chat:1" and d["producer"] == "agent"


def test_make_command_optional_target_and_bad_cmd():
    # target_id is optional → empty means "the controlled root tab" (the
    # extension resolves it to sm.knownTargets()[0]).
    import json
    raw = make_command(Cmd.CLICK, id="c1", transport="t:b", channel="chat:1",
                       args={}, target_id=None, producer="agent")
    assert json.loads(raw)["data"]["target_id"] == ""
    # a non-Cmd still raises
    with pytest.raises(ValueError):
        make_command("not_a_cmd", id="c1", transport="t:b", channel="chat:1",  # type: ignore
                     args={}, target_id="tab9", producer="agent")


def test_parse_observation_roundtrip():
    raw = json.dumps({"v": 1, "kind": "observation", "id": "c1", "channel": "chat:1",
                      "transport": "t:b", "producer": None,
                      "data": {"ok": True, "target_id": "tab9", "text": "hi",
                               "media": [{"slot": "screenshot", "bytes_len": 5}]}})
    obs = parse_observation(raw)
    assert isinstance(obs, Observation)
    assert obs.ok and obs.target_id == "tab9"
    assert obs.data["text"] == "hi"
    assert obs.media == [{"slot": "screenshot", "bytes_len": 5}]
    assert obs.error is None


def test_parse_observation_rejects_non_observation():
    raw = json.dumps({"v": 1, "kind": "command", "id": "c1", "channel": "c",
                      "transport": "t", "data": {}, "producer": None})
    with pytest.raises(ValueError):
        parse_observation(raw)
