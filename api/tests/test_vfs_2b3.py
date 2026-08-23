"""VFS 2b-3 — ref-store deletion guards."""
def test_read_ref_and_register_blob_gone():
    from vibecanvas_api.agents.tools import build_tools
    names = {t.name for t in build_tools({"workflow"})}
    assert "read_ref" not in names
    # register_blob retired in the industry-standard FS redesign — pasted data
    # is now saved with write_file('/memory/…').
    assert "register_blob" not in names
    # The industry-standard FS surface is present (ls/glob retired — directory
    # listing → bash, content search → grep).
    assert {"read_file", "write_file", "edit_file", "grep"} <= names
    assert "ls" not in names and "glob" not in names
    # Workflow projection reads are cross-Runtime Platform MCP tools, not
    # LangChain-private registrations.
    assert "get_workflow" not in names
    # Platform capabilities intentionally no longer inflate this private
    # registry; behavior is pinned by the explicit essential-name assertions.


def test_agentcontext_has_no_ref_repo():
    from vibecanvas_api.agent import AgentContext
    assert "ref_repo" not in AgentContext.model_fields


def test_package_imports_clean():
    import vibecanvas_api
    import vibecanvas_api.app  # builds the FastAPI app — exercises route wiring
    assert vibecanvas_api.app is not None


def test_ref_store_modules_deleted():
    import importlib
    for mod in ("vibecanvas_api.storage.ref_repo",
                "vibecanvas_api.storage.ref_resolve",
                "vibecanvas_api.storage.ref_helpers",
                "vibecanvas_api.agents.middleware.ref_strip",
                "vibecanvas_api.routes.refs",
                "vibecanvas_api.schemas.ref"):
        try:
            importlib.import_module(mod)
            assert False, f"{mod} should be deleted"
        except ModuleNotFoundError:
            pass
