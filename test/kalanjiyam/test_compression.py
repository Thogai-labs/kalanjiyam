import gzip

from flask import Flask
from flask_compress import Compress


def test_gzip_compression_enabled(client):
    # Request without Accept-Encoding header -> uncompressed
    resp = client.get("/about/")
    assert resp.status_code == 200
    assert "Content-Encoding" not in resp.headers

    # Request with Accept-Encoding: gzip -> compressed with gzip
    resp_gzip = client.get("/about/", headers={"Accept-Encoding": "gzip"})
    assert resp_gzip.status_code == 200
    assert resp_gzip.headers.get("Content-Encoding") == "gzip"
    decompressed = gzip.decompress(resp_gzip.data)
    assert b"Kalanjiyam" in decompressed or b"kalanjiyam" in decompressed.lower()


def test_small_response_not_compressed():
    app = Flask(__name__)
    app.config["COMPRESS_MIN_SIZE"] = 500
    Compress(app)

    @app.route("/small")
    def small():
        return "small string"

    @app.route("/large")
    def large():
        return "large string " * 100

    test_client = app.test_client()
    resp_small = test_client.get("/small", headers={"Accept-Encoding": "gzip"})
    assert resp_small.status_code == 200
    assert "Content-Encoding" not in resp_small.headers

    resp_large = test_client.get("/large", headers={"Accept-Encoding": "gzip"})
    assert resp_large.status_code == 200
    assert resp_large.headers.get("Content-Encoding") == "gzip"
    assert b"large string" in gzip.decompress(resp_large.data)
