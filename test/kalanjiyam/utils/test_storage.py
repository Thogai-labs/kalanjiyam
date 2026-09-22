import io

import pytest

from kalanjiyam.utils.storage import (
    LocalStorage,
    MemoryStorage,
    MultiTenantStorage,
    S3Storage,
    _extract_org_slug_from_key,
    editor_image_key,
    get_storage,
    page_image_key,
    pdf_key,
    project_prefix,
    sanitize_bucket_name,
)


def test_key_layout_matches_historical_disk_layout():
    assert project_prefix("my-book") == "projects/open-tenant/my-book/"
    assert pdf_key("my-book") == "projects/open-tenant/my-book/pdf/source.pdf"
    assert (
        page_image_key("my-book", "12") == "projects/open-tenant/my-book/pages/12.jpg"
    )
    assert editor_image_key("my-book", "fig_1a2b.png") == (
        "projects/open-tenant/my-book/images/fig_1a2b.png"
    )

    # Test explicit organization slug
    assert project_prefix("my-book", org_slug="ignou") == "projects/ignou/my-book/"
    assert (
        pdf_key("my-book", org_slug="ignou") == "projects/ignou/my-book/pdf/source.pdf"
    )


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

    def test_serve_applies_caching_defaults(self, storage, flask_app):
        storage.save("projects/p/pages/1.jpg", b"image-content")
        with flask_app.test_request_context():
            response = storage.serve("projects/p/pages/1.jpg")
            assert response.status_code == 200
            assert response.cache_control.max_age == 604800
            assert "ETag" in response.headers

        etag = response.headers.get("ETag")
        with flask_app.test_request_context(headers={"If-None-Match": etag}):
            res304 = storage.serve("projects/p/pages/1.jpg")
            assert res304.status_code == 304


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


# -------------------------------------------------------------------------
# Multi-tenant storage helpers
# -------------------------------------------------------------------------


class TestSanitizeBucketName:
    def test_simple_slug(self):
        assert sanitize_bucket_name("anna-univ") == "org-anna-univ"

    def test_underscores_converted_to_dashes(self):
        assert sanitize_bucket_name("tamil_dept") == "org-tamil-dept"

    def test_uppercase_lowered(self):
        assert sanitize_bucket_name("IGNOU") == "org-ignou"

    def test_special_chars_stripped(self):
        name = sanitize_bucket_name("my org (test)!")
        assert name == "org-my-org-test"

    def test_length_capped_at_63(self):
        long_slug = "a" * 100
        assert len(sanitize_bucket_name(long_slug)) <= 63


class TestExtractOrgSlugFromKey:
    def test_standard_project_key(self):
        assert _extract_org_slug_from_key("projects/anna-univ/book-1/pages/1.jpg") == "anna-univ"

    def test_open_tenant_key(self):
        assert _extract_org_slug_from_key("projects/open-tenant/book/pdf/source.pdf") == "open-tenant"

    def test_docx_key_returns_none(self):
        assert _extract_org_slug_from_key("docx/uploads/abc.docx") is None

    def test_short_key_returns_none(self):
        assert _extract_org_slug_from_key("projects/") is None

    def test_empty_key_returns_none(self):
        assert _extract_org_slug_from_key("") is None


class TestMultiTenantStorage:
    """Test MultiTenantStorage routing using in-memory backends."""

    @pytest.fixture
    def default_backend(self):
        return MemoryStorage()

    @pytest.fixture
    def org_backend(self):
        return MemoryStorage()

    @pytest.fixture
    def mt_storage(self, default_backend, org_backend, monkeypatch):
        """MultiTenantStorage that routes 'custom-org' to org_backend."""
        from kalanjiyam.utils import storage as storage_mod

        def mock_get_org_storage(org_slug):
            if org_slug == "custom-org":
                return org_backend
            return None

        monkeypatch.setattr(storage_mod, "get_org_storage", mock_get_org_storage)
        return MultiTenantStorage(default_backend)

    def test_default_org_uses_default_backend(self, mt_storage, default_backend):
        mt_storage.save("projects/open-tenant/book/pages/1.jpg", b"default-data")
        assert default_backend.exists("projects/open-tenant/book/pages/1.jpg")
        assert mt_storage.read_bytes("projects/open-tenant/book/pages/1.jpg") == b"default-data"

    def test_custom_org_routes_to_org_backend(self, mt_storage, default_backend, org_backend):
        mt_storage.save("projects/custom-org/book/pages/1.jpg", b"org-data")
        assert org_backend.exists("projects/custom-org/book/pages/1.jpg")
        assert not default_backend.exists("projects/custom-org/book/pages/1.jpg")
        assert mt_storage.read_bytes("projects/custom-org/book/pages/1.jpg") == b"org-data"

    def test_non_project_keys_use_default(self, mt_storage, default_backend):
        mt_storage.save("docx/uploads/abc.docx", b"docx-data")
        assert default_backend.exists("docx/uploads/abc.docx")
        assert mt_storage.read_bytes("docx/uploads/abc.docx") == b"docx-data"

    def test_exists_routes_correctly(self, mt_storage, org_backend):
        org_backend.save("projects/custom-org/book/pdf/source.pdf", b"pdf")
        assert mt_storage.exists("projects/custom-org/book/pdf/source.pdf")
        assert not mt_storage.exists("projects/custom-org/book/pdf/missing.pdf")

    def test_delete_routes_correctly(self, mt_storage, org_backend):
        org_backend.save("projects/custom-org/book/pages/1.jpg", b"data")
        assert mt_storage.delete("projects/custom-org/book/pages/1.jpg")
        assert not org_backend.exists("projects/custom-org/book/pages/1.jpg")

    def test_list_keys_routes_correctly(self, mt_storage, org_backend):
        org_backend.save("projects/custom-org/book/pages/1.jpg", b"a")
        org_backend.save("projects/custom-org/book/pages/2.jpg", b"bb")
        keys = dict(mt_storage.list_keys("projects/custom-org/"))
        assert keys == {
            "projects/custom-org/book/pages/1.jpg": 1,
            "projects/custom-org/book/pages/2.jpg": 2,
        }

    def test_size_routes_correctly(self, mt_storage, org_backend):
        org_backend.save("projects/custom-org/book/pages/1.jpg", b"1234")
        assert mt_storage.size("projects/custom-org/book/pages/1.jpg") == 4

    def test_delete_prefix_routes_correctly(self, mt_storage, default_backend, org_backend):
        org_backend.save("projects/custom-org/book/pages/1.jpg", b"a")
        org_backend.save("projects/custom-org/book/pages/2.jpg", b"b")
        default_backend.save("projects/open-tenant/book/pages/1.jpg", b"c")
        assert mt_storage.delete_prefix("projects/custom-org/") == 2
        assert not org_backend.exists("projects/custom-org/book/pages/1.jpg")
        assert default_backend.exists("projects/open-tenant/book/pages/1.jpg")
