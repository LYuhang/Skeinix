import pytest


@pytest.mark.asyncio
async def test_workflows_route_requires_auth(client):
    r = await client.get("/api/v1/workflows")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_workflows_route_isolates_by_tenant(client):
    # user A creates a workflow
    ta = (await client.post("/api/v1/auth/register",
          json={"email": "ta@example.com", "username": "Test User", "password": "pw12345678"})).json()["session_token"]
    await client.post("/api/v1/workflows",
                      json={"name": "A wf", "description": "", "tags": []},
                      headers={"Authorization": f"Bearer {ta}"})
    # user B sees none of A's workflows
    tb = (await client.post("/api/v1/auth/register",
          json={"email": "tb@example.com", "username": "Test User", "password": "pw12345678"})).json()["session_token"]
    lst = await client.get("/api/v1/workflows",
                           headers={"Authorization": f"Bearer {tb}"})
    items = lst.json().get("items", lst.json())
    assert items == []
