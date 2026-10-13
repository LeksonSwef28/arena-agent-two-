import base64
from pathlib import Path

from arena.mcp import tool_fs


class _Ctx:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def app_config(self):
        return {"root": str(self.root)}

    def under_root(self, resolved, home):
        return resolved.is_relative_to(self.root)


def test_fs_write_base64_roundtrip(tmp_path):
    ctx = _Ctx(tmp_path)
    payload = base64.b64encode(b"hello-binary-\x00\x01\xff").decode("ascii")
    target = tmp_path / "out.bin"
    res = tool_fs.handle_fs_tool(
        "fs.write_base64",
        {"path": str(target), "base64": payload},
        ctx=ctx,
    )
    assert res and not res.get("isError"), res
    assert target.read_bytes() == b"hello-binary-\x00\x01\xff"


def test_fs_write_base64_rejects_non_base64_gracefully(tmp_path):
    ctx = _Ctx(tmp_path)
    target = tmp_path / "bad.bin"
    res = tool_fs.handle_fs_tool(
        "fs.write_base64",
        {"path": str(target), "base64": "!!!not-base64!!!"},
        ctx=ctx,
    )
    # Must return an error envelope, not raise an unhandled exception.
    assert res.get("isError"), res
    assert not target.exists()
