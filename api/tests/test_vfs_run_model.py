def test_vfs_run_model_shape():
    from vibecanvas_api.storage.models import VfsRun
    cols = {c.name for c in VfsRun.__table__.columns}
    assert {"run_id", "path", "object_key", "content_type", "size_bytes",
            "tenant_id", "created_at", "last_access"} <= cols
    pk = {c.name for c in VfsRun.__table__.primary_key.columns}
    assert pk == {"run_id", "path"}
    assert "content" not in cols   # bytes live in the ObjectStore, not inline


def test_vfs_run_model_has_wf_id_and_index():
    # UX-10e0: wf_id column (nullable, Text) + a (tenant_id, wf_id) index so the
    # keep-latest purge can find a workflow's run rows cheaply.
    from vibecanvas_api.storage.models import VfsRun
    cols = {c.name for c in VfsRun.__table__.columns}
    assert "wf_id" in cols
    wf_col = VfsRun.__table__.columns["wf_id"]
    assert wf_col.nullable is True
    index_cols = {tuple(c.name for c in ix.columns) for ix in VfsRun.__table__.indexes}
    assert ("tenant_id", "wf_id") in index_cols
