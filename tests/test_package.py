"""Smoke test for the scaffold. Real coverage lands with the client in Prompt 1."""

import segment_mcp


def test_package_has_a_version() -> None:
    assert segment_mcp.__version__
