import pytest

import pydantic

from murdock.config import GlobalSettings


def test_invalid_script_dir(tmpdir):
    test_dir = str(tmpdir.join("not_exist").realpath())
    with pytest.raises(pydantic.error_wrappers.ValidationError) as exc_info:
        _ = GlobalSettings(scripts_dir=test_dir)
    message = f"""1 validation error for GlobalSettings
scripts_dir
  Scripts dir doesn't exist ({test_dir}) (type=value_error)"""
    assert str(exc_info.value) == message

    with pytest.raises(pydantic.error_wrappers.ValidationError) as exc_info:
        _ = GlobalSettings(work_dir=test_dir)
    message = f"""1 validation error for GlobalSettings
work_dir
  Work dir doesn't exist ({test_dir}) (type=value_error)"""
    assert str(exc_info.value) == message
