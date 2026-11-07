"""Exercise the real public HTTP stack, then verify data after a cold restart.

Requires only Python's standard library. Credentials stay in the env file and
are never printed. The small state file contains photo metadata, not secrets.
"""

import argparse
import binascii
import hashlib
import http.cookiejar
import json
from pathlib import Path
import struct
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib


def png(color):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
    pixels = b"".join(b"\0" + bytes(color) * 4 for _ in range(3))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def multipart(title, image, filename="check.png"):
    boundary = "PhotoSmoke" + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\n{title}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            'Content-Type: image/png\r\n\r\n').encode() + image + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def request(self, path, method="GET", body=None, content_type=None, status=200, csrf=True):
        headers = {"Origin": self.base}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        if csrf and self.csrf and method not in ("GET", "HEAD"):
            headers["X-CSRFToken"] = self.csrf
        request = urllib.request.Request(urllib.parse.urljoin(self.base + "/", path),
                                         data=body, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        data = response.read()
        assert response.status == status, f"{method} {path}: expected {status}, got {response.status}: {data[:400]!r}"
        return data, response.headers

    def json(self, path, **kwargs):
        data, _ = self.request(path, **kwargs)
        return json.loads(data)

    def login(self, username, password):
        auth = self.json("/api/auth/")
        self.csrf = auth["csrfToken"]
        result = self.json("/api/auth/", method="POST", body={"username": username, "password": password})
        assert result["authenticated"] is True, "Session login did not authenticate"
        auth = self.json("/api/auth/")
        assert auth["authenticated"] and auth["username"] == username
        self.csrf = auth["csrfToken"]

    def image(self, path):
        data, headers = self.request(path)
        assert headers.get("Content-Type", "").startswith("image/"), f"Unexpected media type: {path}"
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert "sandbox" in headers.get("Content-Security-Policy", ""), "Media must have sandbox CSP"
        assert data, f"Empty image: {path}"
        return hashlib.sha256(data).hexdigest()


def exercise(client, credentials, state_path):
    html, headers = client.request("/")
    assert b'<div id="root"' in html, "Built React page was not served"
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert "script-src 'self'" in headers.get("Content-Security-Policy", "")
    client.json("/api/health/")
    listing = client.json("/api/photos/")
    assert listing["count"] >= 1, "First launch did not load the demo photo"
    for item in listing["results"]:
        client.image(item["image_url"])
        client.image(item["thumbnail_url"])
    client.request("/media/not-allowed.html", status=404)
    client.request("/api/no-such-endpoint/", status=404)
    client.request("/assets/missing.js", status=404)

    image = png((22, 90, 160))
    body, content_type = multipart("Unauthorized", image)
    client.request("/api/photos/", method="POST", body=body, content_type=content_type, status=403)
    client.json("/api/auth/", method="POST", body={"username": credentials["ADMIN_USERNAME"],
                "password": credentials["ADMIN_PASSWORD"]}, csrf=False, status=403)
    client.login(credentials["ADMIN_USERNAME"], credentials["ADMIN_PASSWORD"])
    client.request("/api/photos/", method="POST", body=body, content_type=content_type, csrf=False, status=403)

    bad_body, bad_type = multipart("Invalid image", b"<html><script>alert(1)</script></html>")
    client.json("/api/photos/", method="POST", body=bad_body, content_type=bad_type, status=400)
    title = "CI УРАЛ " + uuid.uuid4().hex[:12]
    body, content_type = multipart(title, image)
    photo = client.json("/api/photos/", method="POST", body=body, content_type=content_type, status=201)
    path = f'/api/photos/{photo["id"]}/'
    assert client.json(path)["title"] == title
    results = client.json("/api/photos/?" + urllib.parse.urlencode({"q": title.lower()}))
    assert results["count"] == 1 and results["results"][0]["id"] == photo["id"], "Case-insensitive Unicode search failed"
    old_image, old_thumbnail = photo["image_url"], photo["thumbnail_url"]
    client.image(old_image)
    client.image(old_thumbnail)

    anonymous = Client(client.base)
    anonymous.request(path, method="PATCH", body={"title": "Forbidden"}, status=403)
    anonymous.request(path, method="DELETE", status=403)
    client.request(path, method="PATCH", body={"title": "No CSRF"}, csrf=False, status=403)
    title += " edited"
    photo = client.json(path, method="PATCH", body={"title": title})
    assert photo["title"] == title, "Title edit was not saved"
    body, content_type = multipart(title, png((199, 24, 64)), "replacement.png")
    photo = client.json(path, method="PATCH", body=body, content_type=content_type)
    assert photo["image_url"] != old_image, "Image replacement did not change the storage key"
    client.request(old_image, status=404)
    if old_thumbnail != photo["thumbnail_url"]:
        client.request(old_thumbnail, status=404)

    body, content_type = multipart("Delete check", image)
    disposable = client.json("/api/photos/", method="POST", body=body, content_type=content_type, status=201)
    client.request(f'/api/photos/{disposable["id"]}/', method="DELETE", status=204)
    client.request(disposable["image_url"], status=404)
    client.request(disposable["thumbnail_url"], status=404)
    state = {"id": photo["id"], "title": title, "image_url": photo["image_url"],
             "thumbnail_url": photo["thumbnail_url"], "image_sha256": client.image(photo["image_url"]),
             "thumbnail_sha256": client.image(photo["thumbnail_url"])}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    client.request("/api/auth/", method="DELETE", status=200)
    assert client.json("/api/auth/")["authenticated"] is False
    client.request(path, method="DELETE", status=403)
    print("HTTP smoke passed: SPA, demo, CSRF/session auth, upload, raster headers, Unicode search, edit, replace, delete, logout.")


def verify(client, credentials, state_path):
    state = json.loads(state_path.read_text(encoding="utf-8"))
    client.json("/api/health/")
    photo = client.json(f'/api/photos/{state["id"]}/')
    assert photo["title"] == state["title"], "Photo metadata was not preserved across restart"
    assert client.image(state["image_url"]) == state["image_sha256"], "Original image changed across restart"
    assert client.image(state["thumbnail_url"]) == state["thumbnail_sha256"], "Thumbnail changed across restart"
    results = client.json("/api/photos/?" + urllib.parse.urlencode({"q": state["title"]}))
    assert results["count"] == 1, "Search data was not preserved across restart"
    client.login(credentials["ADMIN_USERNAME"], credentials["ADMIN_PASSWORD"])
    client.request(f'/api/photos/{state["id"]}/', method="DELETE", status=204)
    client.request(state["image_url"], status=404)
    client.request(state["thumbnail_url"], status=404)
    state_path.unlink()
    print("Cold-restart smoke passed: database, edited metadata, original image, thumbnail, and login persisted.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--env-file", default=".env.docker")
    parser.add_argument("--state-file", type=Path, default=Path(".smoke-state.json"))
    parser.add_argument("--phase", choices=("exercise", "verify"), default="exercise")
    args = parser.parse_args()
    credentials = dict(line.split("=", 1) for line in Path(args.env_file).read_text(encoding="utf-8-sig").splitlines()
                       if line and not line.startswith("#") and "=" in line)
    client = Client(args.base_url)
    (exercise if args.phase == "exercise" else verify)(client, credentials, args.state_file)


if __name__ == "__main__":
    main()
