"""The scan mirror: gentle, resumable, first scans first, metadata stripped, and it stops at the first sign of an outage."""
import io
import json

from PIL import Image

import mirror_scans

SOURCE = "https://edisondigital.rutgers.edu/files/original/{volume}/au{n}.jpg"


def _jpeg(width=800, height=600, exif=False):
    out = io.BytesIO()
    image = Image.new("RGB", (width, height), (200, 190, 170))
    if exif:
        data = image.getexif()
        data[0x010F] = "SecretCamera"
        image.save(out, "JPEG", exif=data.tobytes())
    else:
        image.save(out, "JPEG")
    return out.getvalue()


class FakeResponse:
    def __init__(self, url, body=b"", status=200, content_type="image/jpeg", history=()):
        self.url, self.content, self.status_code = url, body, status
        self.headers = {"Content-Type": content_type}
        self.history = list(history)


class FakeSession:
    """Answers each address from a table, and records what was asked for."""

    def __init__(self, answers=None, default=None):
        self.headers, self.asked, self.answers, self.default = {}, [], answers or {}, default

    def get(self, url, timeout):
        self.asked.append(url)
        answer = self.answers.get(url, self.default)
        return answer(url) if callable(answer) else answer or FakeResponse(url, _jpeg())


def _dataset(tmp_path, volumes=(("A200", 2), ("A234A", 2))):
    manifest = {"volumes": {}, "withdrawn": []}
    for volume_id, count in volumes:
        openings = [{"n": n, "source": SOURCE.format(volume=volume_id, n=n)} for n in range(1, count + 1)]
        (tmp_path / "volumes" / volume_id).mkdir(parents=True)
        (tmp_path / "volumes" / volume_id / "volume.json").write_text(json.dumps({"id": volume_id, "openings": openings}), "utf-8")
        manifest["volumes"][volume_id] = {"openings": count}
    (tmp_path / "volumes" / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    return tmp_path


def _fetcher(session):
    return mirror_scans.Fetcher("https://example.org/about", session=session, sleep=lambda seconds: None)


def test_every_books_first_scan_is_copied_before_any_books_later_scans(tmp_path):
    session = FakeSession()
    mirror_scans.mirror(_dataset(tmp_path), _fetcher(session))
    assert session.asked[:2] == [SOURCE.format(volume="A200", n=1), SOURCE.format(volume="A234A", n=1)]


def test_a_mirrored_scan_keeps_no_camera_metadata(tmp_path):
    dataset = _dataset(tmp_path, volumes=(("A200", 1),))
    mirror_scans.mirror(dataset, _fetcher(FakeSession(default=FakeResponse("", _jpeg(exif=True)))))
    assert b"SecretCamera" not in (dataset / "scans" / "A200" / "full" / "0001.jpg").read_bytes()


def test_each_mirrored_scan_gets_a_thumbnail_no_wider_than_the_site_needs(tmp_path):
    dataset = _dataset(tmp_path, volumes=(("A200", 1),))
    mirror_scans.mirror(dataset, _fetcher(FakeSession()))
    with Image.open(dataset / "scans" / "A200" / "thumb" / "0001.jpg") as thumb:
        assert thumb.width == mirror_scans.THUMB_WIDTH


def test_a_scan_already_mirrored_from_the_same_source_is_not_fetched_again(tmp_path):
    dataset = _dataset(tmp_path)
    mirror_scans.mirror(dataset, _fetcher(FakeSession()))
    session = FakeSession()
    mirror_scans.mirror(dataset, _fetcher(session))
    assert session.asked == []


def test_a_scan_whose_source_changed_is_fetched_again(tmp_path):
    dataset = _dataset(tmp_path, volumes=(("A200", 1),))
    mirror_scans.mirror(dataset, _fetcher(FakeSession()))
    path = dataset / "volumes" / "A200" / "volume.json"
    path.write_text(json.dumps({"id": "A200", "openings": [{"n": 1, "source": SOURCE.format(volume="A200", n=9)}]}), "utf-8")
    session = FakeSession()
    mirror_scans.mirror(dataset, _fetcher(session))
    assert session.asked == [SOURCE.format(volume="A200", n=9)]


def test_an_unavailable_replacement_does_not_leave_the_old_scan_public(tmp_path):
    dataset = _dataset(tmp_path, volumes=(("A200", 1),))
    mirror_scans.mirror(dataset, _fetcher(FakeSession()))
    path = dataset / "volumes" / "A200" / "volume.json"
    volume = json.loads(path.read_text("utf-8"))
    volume["openings"][0]["source"] = SOURCE.format(volume="A200", n=9)
    path.write_text(json.dumps(volume), "utf-8")
    mirror_scans.mirror(dataset, _fetcher(FakeSession(default=FakeResponse("", status=404))))
    assert not any(path.exists() for path in mirror_scans.scan_paths(dataset, "A200", 1).values())


def test_a_truncated_jpeg_is_skipped_and_the_next_scan_is_still_mirrored(tmp_path):
    bad = SOURCE.format(volume="A200", n=1)
    session = FakeSession({bad: FakeResponse(bad, b"\xff\xd8\xff")})
    result = mirror_scans.mirror(_dataset(tmp_path, volumes=(("A200", 2),)), _fetcher(session))
    assert (result["skipped"], result["copied"]) == (1, 1)


def test_the_outage_page_stops_the_run_without_asking_for_more(tmp_path):
    outage = FakeResponse("https://sorry.rutgers.edu/", b"<html>", content_type="text/html")
    session = FakeSession(default=outage)
    mirror_scans.mirror(_dataset(tmp_path), _fetcher(session))
    assert len(session.asked) == 1


def test_progress_made_before_an_outage_is_kept_for_the_next_run(tmp_path):
    dataset = _dataset(tmp_path)
    second = SOURCE.format(volume="A234A", n=1)
    session = FakeSession(answers={second: FakeResponse("https://sorry.rutgers.edu/", content_type="text/html")})
    mirror_scans.mirror(dataset, _fetcher(session))
    assert json.loads((dataset / mirror_scans.STATE).read_text("utf-8")).keys() == {"A200/1"}


def test_a_page_that_is_not_a_jpeg_scan_is_skipped_rather_than_stored(tmp_path):
    dataset = _dataset(tmp_path, volumes=(("A200", 1),))
    mirror_scans.mirror(dataset, _fetcher(FakeSession(default=FakeResponse("", b"<html>", content_type="text/html"))))
    assert not (dataset / "scans" / "A200").exists()


def test_a_source_on_any_other_host_is_never_fetched(tmp_path):
    dataset = _dataset(tmp_path, volumes=(("A200", 1),))
    path = dataset / "volumes" / "A200" / "volume.json"
    path.write_text(json.dumps({"id": "A200", "openings": [{"n": 1, "source": "https://example.com/au1.jpg"}]}), "utf-8")
    session = FakeSession()
    mirror_scans.mirror(dataset, _fetcher(session))
    assert session.asked == []


def test_scans_of_a_book_no_longer_published_are_removed(tmp_path):
    dataset = _dataset(tmp_path)
    mirror_scans.mirror(dataset, _fetcher(FakeSession()))
    manifest = json.loads((dataset / "volumes" / "manifest.json").read_text("utf-8"))
    del manifest["volumes"]["A234A"]
    (dataset / "volumes" / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    mirror_scans.mirror(dataset, _fetcher(FakeSession()))
    assert not (dataset / "scans" / "A234A").exists()


def test_the_run_stops_starting_scans_once_its_time_is_spent(tmp_path):
    ticks = iter(range(0, 10_000, 400))
    session = FakeSession()
    mirror_scans.mirror(_dataset(tmp_path), _fetcher(session), minutes=10, clock=lambda: next(ticks))
    assert len(session.asked) == 1


def test_a_pause_of_at_least_a_second_separates_requests(tmp_path):
    pauses = []
    fetcher = mirror_scans.Fetcher("https://example.org/about", session=FakeSession(), sleep=pauses.append)
    mirror_scans.mirror(_dataset(tmp_path), fetcher)
    assert pauses and min(pauses) >= 1.0
