from __future__ import annotations

import os

from vibecanvas_api.services import codex_cli


def test_codex_cli_node_runtime_resolves_npm_shebang_interpreter(
    monkeypatch,
    tmp_path,
):
    package = tmp_path / "node_modules" / "@openai" / "codex"
    entrypoint = package / "bin" / "codex.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    entrypoint.chmod(0o755)
    node = tmp_path / "nvm" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"\x7fELF")
    node.chmod(0o755)
    shim = tmp_path / "bin" / "node"
    shim.parent.mkdir()
    shim.symlink_to(node)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: str(shim))

    assert codex_cli.codex_cli_readonly_root(str(entrypoint)) == str(package)
    assert codex_cli.codex_cli_node_runtime(str(entrypoint)) == os.path.realpath(node)


def test_codex_cli_node_runtime_is_not_required_for_native_binary(tmp_path):
    executable = tmp_path / "codex"
    executable.write_bytes(b"\x7fELF")
    executable.chmod(0o755)

    assert codex_cli.codex_cli_node_runtime(str(executable)) is None
