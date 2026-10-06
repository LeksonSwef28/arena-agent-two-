from pathlib import Path

START = (Path(__file__).resolve().parents[1] / "start.bat").read_text(
    encoding="utf-8", errors="replace"
).lower()


def test_windows_launcher_is_project_safe_by_construction():
    assert 'arena_project_safe=1' in START
    assert '--profile cautious' in START
    assert '--bind 127.0.0.1' in START
    assert 'owner-shell' not in START
    assert 'taskkill /f' not in START
    assert 'pip install' not in START
    assert 'winget install' not in START
    assert '--root "%userprofile%"' not in START
