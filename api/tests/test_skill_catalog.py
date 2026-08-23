import asyncio

import pytest

from vibecanvas_api.services import skill_catalog


class _DummyClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _entries(count: int) -> list[dict[str, object]]:
    return [
        {
            "type": "blob",
            "path": f"skills/.curated/skill-{index}/SKILL.md",
            "size": 100,
        }
        for index in range(count)
    ]


def _item(source_id: str) -> dict[str, object]:
    return {
        "source": "openai",
        "source_label": "OpenAI Curated Skills",
        "source_id": source_id,
        "name": source_id,
        "description": "Test skill",
        "version": 1,
        "allowed_tools": [],
        "homepage": "https://example.test/skill",
        "revision": "revision",
        "files": [],
        "skill_md": "---\nname: test\ndescription: Test skill\n---\n",
        "body": "",
    }


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    skill_catalog._cache.clear()
    yield
    skill_catalog._cache.clear()


@pytest.mark.asyncio
async def test_blank_catalog_loads_only_one_page_plus_has_more_probe(monkeypatch):
    requested: list[str] = []

    async def fake_tree(_source, *, client=None):
        return "revision", _entries(20)

    async def fake_resolve(*, source, source_id, _client=None):
        requested.append(source_id)
        return _item(source_id)

    monkeypatch.setattr(skill_catalog, "_http_client", _DummyClient)
    monkeypatch.setattr(skill_catalog, "_tree", fake_tree)
    monkeypatch.setattr(skill_catalog, "resolve_skill_catalog_item", fake_resolve)

    result = await skill_catalog.search_skill_catalog(
        source="openai",
        limit=2,
    )

    assert requested == ["skill-0", "skill-1", "skill-10"]
    assert [item["source_id"] for item in result["items"]] == [
        "skill-0",
        "skill-1",
    ]
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_slow_items_do_not_discard_completed_catalog_cards(monkeypatch):
    async def fake_tree(_source, *, client=None):
        return "revision", _entries(3)

    async def fake_resolve(*, source, source_id, _client=None):
        if source_id != "skill-0":
            await asyncio.sleep(1)
        return _item(source_id)

    monkeypatch.setattr(skill_catalog, "_http_client", _DummyClient)
    monkeypatch.setattr(skill_catalog, "_tree", fake_tree)
    monkeypatch.setattr(skill_catalog, "resolve_skill_catalog_item", fake_resolve)
    monkeypatch.setattr(skill_catalog, "_CATALOG_OPERATION_TIMEOUT_S", 0.02)

    result = await skill_catalog.search_skill_catalog(
        source="openai",
        limit=2,
    )

    assert [item["source_id"] for item in result["items"]] == ["skill-0"]
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_catalog_timeout_is_actionable_when_no_card_completes(monkeypatch):
    async def fake_tree(_source, *, client=None):
        return "revision", _entries(2)

    async def fake_resolve(*, source, source_id, _client=None):
        await asyncio.sleep(1)
        return _item(source_id)

    monkeypatch.setattr(skill_catalog, "_http_client", _DummyClient)
    monkeypatch.setattr(skill_catalog, "_tree", fake_tree)
    monkeypatch.setattr(skill_catalog, "resolve_skill_catalog_item", fake_resolve)
    monkeypatch.setattr(skill_catalog, "_CATALOG_OPERATION_TIMEOUT_S", 0.01)

    with pytest.raises(TimeoutError, match="did not return any items"):
        await skill_catalog.search_skill_catalog(source="openai", limit=2)
