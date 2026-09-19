"""模板自带的冒烟测试，agent 可以按需替换。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_build_script_exists() -> None:
    assert (ROOT / "scripts" / "build.py").is_file()
