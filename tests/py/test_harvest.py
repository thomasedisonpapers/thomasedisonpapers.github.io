"""The harvester: documents only, gentle, stops on an outage, resumes, never skips, and never publishes a snapshot that looks wrong."""
import json

import pytest

import harvest


class FakeResponse:
    def __init__(self, rows=None, status=200, total=None, url="https://edisondigital.rutgers.edu/api/items",
                 content_type="application/ld+json", retry_after=None, body_error=False):
        self.status_code = status
        self._rows = rows if rows is not None else []
        self.url = url
        self.history = []
        self.headers = {"Content-Type": content_type}
        self.body_error = body_error
        if total is not None:
            self.headers["Omeka-S-Total-Results"] = str(total)
        if retry_after:
            self.headers["Retry-After"] = retry_after

    def json(self):
        if self.body_error:
            raise ValueError("truncated body")
        return self._rows


class FakeSite:
    """edisondigital in miniature: documents, people and media, answering offset and limit in id order."""

    def __init__(self, documents, people=(), medias=None):
        self.items = sorted(list(documents) + list(people), key=lambda r: r["o:id"])
        self.medias = medias if medias is not None else [media(i["o:id"]) for i in documents]
        self.headers = {}
        self.calls = []
        self.override = {}
        self.before_request = None

    def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        if self.before_request:
            self.before_request(self, params)
        path = url.split(".edu", 1)[1]
        if self.override.get(path):
            return self.override[path].pop(0)
        rows = self.items if path == "/api/items" else self.medias
        if path == "/api/items" and params.get("resource_template_id"):
            rows = [r for r in rows if r.get("o:resource_template", {}).get("o:id") == params["resource_template_id"]]
        offset, limit = params.get("offset", 0), params.get("limit", 100)
        return FakeResponse(rows[offset:offset + limit], total=len(rows))


def item(n, identifier=None, transcription="", template=1):
    row = {"o:id": n, "o:title": f"[D{n:04d}AAA], O\\'Brien's letter {n}", "dcterms:identifier": [{"@value": identifier or f"D{n:04d}AAA"}],
           "o:resource_template": {"o:id": template}, "dcterms:isPartOf": [{"@value": "[D0001-F] Document File Series -- 1880"}],
           "dcterms:date": [{"@value": "1880-01-0%d" % (n % 9 + 1)}], "o:media": [{"o:id": 100000 + n}]}
    if transcription:
        row["scripto:transcription"] = [{"@value": transcription, "type": "literal"}]
    return row


def person(n):
    return {"o:id": n, "o:title": f"Person {n}", "o:resource_template": {"o:id": 9}, "o:media": []}


def media(n):
    return {"o:id": 100000 + n, "o:original_url": f"https://edisondigital.rutgers.edu/files/original/{n}.jpg",
            "o:thumbnail_urls": {"large": f"https://edisondigital.rutgers.edu/files/large/{n}.jpg",
                                 "medium": f"https://edisondigital.rutgers.edu/files/medium/{n}.jpg"}}


def run(tmp_path, site, sleeps=None, **options):
    client = harvest.Client("https://example.org/contact", session=site, sleep=(sleeps.append if sleeps is not None else lambda s: None))
    return harvest.Harvest(client, tmp_path, **options).run()


def records(tmp_path):
    snapshot = json.loads((tmp_path / "edisondigital" / "current.json").read_text())["snapshot"]
    return [json.loads(line) for line in (tmp_path / "edisondigital" / snapshot / "records.jsonl").read_text().splitlines()]


def test_a_harvest_publishes_one_record_per_document(tmp_path):
    assert run(tmp_path, FakeSite([item(1), item(2)]))["records"] == 2


def test_people_and_organisations_are_not_harvested_as_documents(tmp_path):
    assert run(tmp_path, FakeSite([item(1), item(3)], people=[person(2)]))["records"] == 2


def test_the_title_drops_the_archives_bracketed_id_and_escaped_quotes(tmp_path):
    run(tmp_path, FakeSite([item(1)]))
    assert records(tmp_path)[0]["title"] == "O'Brien's letter 1"


def test_an_item_with_a_transcription_is_an_edition():
    assert harvest.item_record(item(1, transcription="Dear Sir"), {})["status"] == "edition"


def test_the_folder_is_parsed_from_its_bracketed_id():
    assert harvest.item_record(item(1), {})["folder"] == {"id": "D0001-F", "title": "Document File Series -- 1880"}


def test_a_placeholder_image_is_not_a_page():
    placeholder = {"original": "https://edisondigital.rutgers.edu/application/asset/img/res/placeholder.png"}
    assert harvest.item_record(item(1), {100001: placeholder})["pages"] == []


def test_a_page_keeps_its_place_number_after_a_skipped_placeholder():
    row = item(1)
    row["o:media"] = [{"o:id": 1}, {"o:id": 2}]
    pages = harvest.item_record(row, {1: {"original": "x/res/placeholder.png"}, 2: {"original": "https://e/2.jpg"}})["pages"]
    assert pages[0]["n"] == 2


def test_an_unsafe_identifier_is_skipped():
    assert harvest.item_record(item(1, identifier="../x"), {}) is None


def test_html_in_a_transcription_becomes_plain_text():
    row = item(1)
    row["scripto:transcription"] = [{"@value": "Dear Sir,<br />Yours &amp; truly", "type": "literal"}]
    assert harvest.item_record(row, {})["transcription"] == "Dear Sir,\nYours & truly"


def test_an_item_with_no_media_says_its_scans_may_be_elsewhere():
    row = item(1)
    row["o:media"] = []
    assert harvest.item_record(row, {})["edisondigital"]["scans_elsewhere"] is True


def test_every_request_after_the_first_waits_at_least_a_second(tmp_path):
    sleeps = []
    run(tmp_path, FakeSite([item(1)]), sleeps)
    assert sleeps and min(sleeps) >= 1.0


def test_the_harvester_says_who_it_is():
    client = harvest.Client("https://example.org/contact", session=FakeSite([]))
    assert "example.org/contact" in client.session.headers["User-Agent"]


def test_only_document_records_are_asked_for(tmp_path):
    site = FakeSite([item(1)])
    run(tmp_path, site)
    assert all(params.get("resource_template_id") == 1 for url, params in site.calls if url.endswith("/api/items"))


def test_the_outage_page_stops_the_run(tmp_path):
    site = FakeSite([item(1)])
    site.override["/api/items"] = [FakeResponse([], url="https://sorry.rutgers.edu/index.html", content_type="text/html")]
    with pytest.raises(harvest.Outage):
        run(tmp_path, site)


def test_a_broken_json_body_is_treated_as_an_outage(tmp_path):
    site = FakeSite([item(1)])
    site.override["/api/items"] = [FakeResponse(total=1, body_error=True)]
    with pytest.raises(harvest.Outage):
        run(tmp_path, site)


def test_a_firewall_403_is_waited_out_then_retried(tmp_path):
    site = FakeSite([item(1)])
    site.override["/api/items"] = [FakeResponse(status=403)]
    assert run(tmp_path, site)["records"] == 1


def test_a_429_honours_its_retry_after(tmp_path):
    site = FakeSite([item(1)])
    site.override["/api/items"] = [FakeResponse(status=429, retry_after="7")]
    sleeps = []
    run(tmp_path, site, sleeps)
    assert any(s >= 7 for s in sleeps)


def test_a_huge_retry_after_is_capped():
    assert harvest._retry_after("999999", 0) == harvest.MAX_WAIT_SECONDS


def test_an_interrupted_harvest_resumes_where_it_stopped(tmp_path):
    documents = [item(n) for n in range(1, 251)]
    site = FakeSite(documents)
    outage = FakeResponse([], url="https://sorry.rutgers.edu/x", content_type="text/html")

    def fail_on_third(fake, params):
        if len(fake.calls) == 3:
            fake.override["/api/items"] = [outage]
    site.before_request = fail_on_third
    with pytest.raises(harvest.Outage):
        run(tmp_path, site)
    site.before_request = None
    assert run(tmp_path, site)["records"] == 250


def test_a_resumed_harvest_does_not_ask_for_pages_it_already_has(tmp_path):
    documents = [item(n) for n in range(1, 251)]
    site = FakeSite(documents)
    outage = FakeResponse([], url="https://sorry.rutgers.edu/x", content_type="text/html")

    def fail_on_third(fake, params):
        if len(fake.calls) == 3:
            fake.override["/api/items"] = [outage]
    site.before_request = fail_on_third
    with pytest.raises(harvest.Outage):
        run(tmp_path, site)
    site.before_request = None
    site.calls.clear()
    run(tmp_path, site)
    assert min(p["offset"] for url, p in site.calls if url.endswith("/api/items")) >= 195


def test_deleting_items_mid_sweep_skips_nothing(tmp_path):
    documents = [item(n) for n in range(1, 301)]
    site = FakeSite(documents)

    def delete_early_items(fake, params):
        if params.get("offset", 0) >= 100 and any(r["o:id"] <= 8 for r in fake.items):
            fake.items = [r for r in fake.items if r["o:id"] > 8]
    site.before_request = delete_early_items
    run(tmp_path, site)
    assert {f"D{n:04d}AAA" for n in range(9, 301)} <= {r["id"] for r in records(tmp_path)}


def test_a_harvest_far_short_of_the_sites_total_is_refused(tmp_path):
    site = FakeSite([item(1), item(2)])
    site.override["/api/items"] = [FakeResponse([item(1), item(2)], total=50)]
    with pytest.raises(harvest.HarvestRefused):
        run(tmp_path, site)


def test_an_empty_first_harvest_is_refused(tmp_path):
    with pytest.raises(harvest.HarvestRefused):
        run(tmp_path, FakeSite([]))


def test_a_harvest_whose_media_never_arrived_is_refused(tmp_path):
    with pytest.raises(harvest.HarvestRefused):
        run(tmp_path, FakeSite([item(1), item(2)], medias=[]))


def test_a_snapshot_that_shrank_keeps_the_previous_one_live(tmp_path):
    good = run(tmp_path, FakeSite([item(n) for n in range(1, 11)]))["snapshot"]
    with pytest.raises(harvest.HarvestRefused):
        run(tmp_path, FakeSite([item(n) for n in range(1, 6)]))
    assert json.loads((tmp_path / "edisondigital" / "current.json").read_text())["snapshot"] == good


def test_a_refused_harvest_is_quarantined_so_the_next_run_starts_afresh(tmp_path):
    run(tmp_path, FakeSite([item(n) for n in range(1, 11)]))
    with pytest.raises(harvest.HarvestRefused):
        run(tmp_path, FakeSite([item(n) for n in range(1, 6)]))
    assert not (tmp_path / "edisondigital" / "work").exists()


def test_a_genuine_shrink_can_be_accepted_on_purpose(tmp_path):
    run(tmp_path, FakeSite([item(n) for n in range(1, 11)]))
    assert run(tmp_path, FakeSite([item(n) for n in range(1, 6)]), accept_shrink=True)["records"] == 5


def test_each_checkpoint_file_is_sent_to_the_mirror(tmp_path):
    pushed = []

    class Mirror:
        def pull(self, work):
            return None

        def push(self, work, paths):
            pushed.extend(p.name for p in paths)

    run(tmp_path, FakeSite([item(1)]), mirror=Mirror())
    assert "000001.json" in pushed
