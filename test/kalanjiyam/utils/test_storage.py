import io

import pytest

from kalanjiyam.utils.storage import (
    LocalStorage,
    S3Storage,
    editor_image_key,
    get_storage,
    page_image_key,
    pdf_key,
    project_prefix,
)


def test_key_layout_matches_historical_disk_layout():
    assert project_prefix("my-book") == "projects/open-tenant/my-book/"
    assert pdf_key("my-book") == "projects/open-tenant/my-book/pdf/source.pdf"
    assert page_image_key("my-book", "12") == "projects/open-tenant/my-book/pages/12.jpg"
    assert editor_image_key("my-book", "fig_1a2b.png") == (
        "projects/open-tenant/my-book/images/fig_1a2b.png"
    )

    # Test explicit organization slug
    assert project_prefix("my-book", org_slug="ignou") == "projects/ignou/my-book/"
    assert pdf_key("my-book", org_slug="ignou") == "projects/ignou/my-book/pdf/source.pdf"


class TestLocalStorage:
    @pytest.fixture
    def storage(self, tmp_path):
        return LocalStorage(tmp_path)

    def test_save_and_read_bytes(self, storage):
        storage.save("projects/p/pages/1.jpg", b"image-bytes")
        assert storage.read_bytes("projects/p/pages/1.jpg") == b"image-bytes"

    def test_save_from_fileobj_and_path(self, storage, tmp_path):
        storage.save("a/from-fileobj", io.BytesIO(b"one"))
        src = tmp_path / "src.bin"
        src.write_bytes(b"two")
        storage.save("a/from-path", src)
        assert storage.read_bytes("a/from-fileobj") == b"one"
        assert storage.read_bytes("a/from-path") == b"two"

    def test_exists(self, storage):
        assert not storage.exists("missing")
        storage.save("present", b"x")
        assert storage.exists("present")

    def test_list_keys_and_total_size(self, storage):
        storage.save("projects/p/pages/1.jpg", b"aaaa")
        storage.save("projects/p/pdf/source.pdf", b"bb")
        storage.save("projects/other/pages/1.jpg", b"c")
        keys = dict(storage.list_keys("projects/p/"))
        assert keys == {
            "projects/p/pages/1.jpg": 4,
            "projects/p/pdf/source.pdf": 2,
        }
        assert storage.total_size("projects/p/") == 6

    def test_delete_prefix(self, storage):
        storage.save("projects/p/pages/1.jpg", b"x")
        storage.save("projects/p/pages/2.jpg", b"y")
        storage.save("projects/other/pages/1.jpg", b"z")
        assert storage.delete_prefix("projects/p/") == 2
        assert not storage.exists("projects/p/pages/1.jpg")
        assert storage.exists("projects/other/pages/1.jpg")

    def test_local_copy_is_real_path(self, storage):
        storage.save("projects/p/pages/1.jpg", b"x")
        path = storage.local_copy("projects/p/pages/1.jpg")
        assert path.read_bytes() == b"x"

    def test_local_copy_of_missing_key_does_not_exist(self, storage):
        assert not storage.local_copy("projects/p/pages/404.jpg").exists()

    def test_rejects_path_traversal(self, storage):
        with pytest.raises(ValueError):
            storage.save("../escape", b"x")


class TestS3Storage:
    @pytest.fixture
    def storage(self, tmp_path):
        moto = pytest.importorskip("moto")
        with moto.mock_aws():
            yield S3Storage(
                bucket="uploads",
                access_key_id="test",
                secret_access_key="test",
                cache_dir=tmp_path / "cache",
            )

    def test_bucket_is_created_on_first_use(self, storage):
        storage.save("projects/p/pages/1.jpg", b"image-bytes")
        assert storage.exists("projects/p/pages/1.jpg")

    def test_save_and_read_bytes(self, storage):
        storage.save("projects/p/pages/1.jpg", b"image-bytes")
        assert storage.read_bytes("projects/p/pages/1.jpg") == b"image-bytes"

    def test_exists(self, storage):
        assert not storage.exists("missing")
        storage.save("present", b"x")
        assert storage.exists("present")

    def test_list_keys_and_total_size(self, storage):
        storage.save("projects/p/pages/1.jpg", b"aaaa")
        storage.save("projects/p/pdf/source.pdf", b"bb")
        storage.save("projects/other/pages/1.jpg", b"c")
        keys = dict(storage.list_keys("projects/p/"))
        assert keys == {
            "projects/p/pages/1.jpg": 4,
            "projects/p/pdf/source.pdf": 2,
        }
        assert storage.total_size("projects/p/") == 6

    def test_delete_prefix(self, storage):
        storage.save("projects/p/pages/1.jpg", b"x")
        storage.save("projects/p/pages/2.jpg", b"y")
        storage.save("projects/other/pages/1.jpg", b"z")
        assert storage.delete_prefix("projects/p/") == 2
        assert not storage.exists("projects/p/pages/1.jpg")
        assert storage.exists("projects/other/pages/1.jpg")

    def test_local_copy_downloads_to_cache(self, storage):
        storage.save("projects/p/pages/1.jpg", b"image-bytes")
        path = storage.local_copy("projects/p/pages/1.jpg")
        assert path.read_bytes() == b"image-bytes"
        # A second call serves the cached copy.
        assert storage.local_copy("projects/p/pages/1.jpg") == path

    def test_local_copy_of_missing_key_does_not_exist(self, storage):
        assert not storage.local_copy("projects/p/pages/404.jpg").exists()


def test_get_storage_uses_local_backend_in_tests(flask_app):
    with flask_app.app_context():
        storage = get_storage()
        assert isinstance(storage, LocalStorage)
        # The instance is created once and cached on the app.
        assert get_storage() is storage
