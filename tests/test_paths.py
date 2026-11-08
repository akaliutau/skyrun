import pytest

from skyrun.paths import atomic_write, inside, relative_path, validate_run_id


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "..",
        "../x",
        "a/../x",
        "/tmp/x",
        "a//b",
        "a/./b",
        "a/",
        "a\\b",
        "C:/x",
        "a\x00b",
        ".env",
    ],
)
def test_unsafe_paths_are_rejected(path):
    with pytest.raises(ValueError):
        relative_path(path)


@pytest.mark.parametrize("value", ["../a", "/a", "a/b", "a.b", "", "x" * 129])
def test_unsafe_run_id(value):
    with pytest.raises(ValueError):
        validate_run_id(value)


def test_parent_symlink_is_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        inside(linked / "child", "file")


def test_interrupted_atomic_write_preserves_original(tmp_path):
    target = tmp_path / "result"
    target.write_bytes(b"old")

    def failing():
        yield b"partial"
        raise OSError("interrupted")

    with pytest.raises(OSError):
        atomic_write(target, failing())
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]


def test_unique_temporary_files_do_not_follow_predictable_symlink(tmp_path):
    target = tmp_path / "result"
    secret = tmp_path / "secret"
    secret.write_bytes(b"safe")
    target.with_name("result.download").symlink_to(secret)
    atomic_write(target, [b"new"])
    assert target.read_bytes() == b"new"
    assert secret.read_bytes() == b"safe"
