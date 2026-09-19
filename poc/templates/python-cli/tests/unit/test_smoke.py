"""模板自带的冒烟测试，agent 可以按需替换。"""

from app.cli import main


def test_main_is_callable() -> None:
    assert callable(main)
