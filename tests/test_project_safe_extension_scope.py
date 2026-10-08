from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_CHAT_MATCHES = {
    "https://chat.openai.com/*",
    "https://chatgpt.com/*",
    "https://chat.deepseek.com/*",
    "https://chat.qwen.ai/*",
}
ALLOWED_HOSTS = ALLOWED_CHAT_MATCHES | {
    "http://127.0.0.1:8765/*",
    "http://localhost:8765/*",
}


def _manifest(folder: str) -> dict:
    return json.loads((ROOT / folder / "manifest.json").read_text(encoding="utf-8"))


def test_project_safe_extensions_do_not_run_on_all_urls():
    for folder in ("chat_extension", "chat_extension_firefox"):
        manifest = _manifest(folder)
        assert set(manifest["host_permissions"]) == ALLOWED_HOSTS
        assert set(manifest["content_scripts"][0]["matches"]) == ALLOWED_CHAT_MATCHES
        assert set(manifest["web_accessible_resources"][0]["matches"]) == ALLOWED_CHAT_MATCHES
        assert "<all_urls>" not in json.dumps(manifest)
