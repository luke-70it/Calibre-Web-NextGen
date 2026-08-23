"""Regression coverage for #1372's download-time KEPUB metadata rewrite."""

from datetime import datetime, timezone
from types import SimpleNamespace
from zipfile import ZipFile

from lxml import etree

from cps import helper
from cps.services import parallel
from tests.fixtures.kepub_fixture import build_calibre_epub3_series_kepub


def _book(series_name="Verify Series"):
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=1372,
        uuid="issue-1372-real-shape",
        identifiers=[],
        title="Fixture Title",
        authors=[SimpleNamespace(name="Fixture Author")],
        author_sort="Author, Fixture",
        pubdate=now,
        comments=[SimpleNamespace(text="Library description")],
        publishers=[SimpleNamespace(name="Library Publisher")],
        languages=[SimpleNamespace(lang_code="eng")],
        tags=[SimpleNamespace(name="regression")],
        series=[] if series_name is None else [SimpleNamespace(name=series_name)],
        series_index=3,
        ratings=[],
        timestamp=now,
        sort="Fixture Title",
    )


def _run_download_rewrite(monkeypatch, tmp_path, book):
    source = build_calibre_epub3_series_kepub(tmp_path / "source.kepub")

    class Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return []

    monkeypatch.setattr(
        helper.calibre_db,
        "session",
        SimpleNamespace(query=lambda *_args: Query()),
    )
    monkeypatch.setattr(helper, "current_user", SimpleNamespace(locale="en"))
    monkeypatch.setattr(helper, "_", lambda value: value)
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(helper, "uuid4", lambda: "served-copy")
    monkeypatch.setattr(parallel, "run_blocking", lambda job: job())

    output_dir, output_name = helper.do_kepubify_metadata_replace(book, str(source))
    return source, tmp_path / f"{output_name}.kepub"


def _package(archive):
    container = etree.fromstring(archive.read("META-INF/container.xml"))
    package_name = container.xpath(
        'string(//*[local-name()="rootfile"]/@full-path)'
    )
    return package_name, etree.fromstring(archive.read(package_name))


def _series_collections(package):
    metas = package.xpath('//*[local-name()="meta"]')
    series_ids = {
        element.get("refines", "").removeprefix("#")
        for element in metas
        if element.get("property") == "collection-type"
        and "".join(element.itertext()).strip().lower() == "series"
    }
    return [
        element
        for element in metas
        if element.get("property") == "belongs-to-collection"
        and element.get("id") in series_ids
    ]


def test_kepub_download_keeps_library_series_in_epub3_collection_metadata(
    monkeypatch, tmp_path
):
    source, served = _run_download_rewrite(monkeypatch, tmp_path, _book())

    with ZipFile(source) as source_zip, ZipFile(served) as served_zip:
        package_name, package = _package(served_zip)
        collections = _series_collections(package)
        assert ["".join(element.itertext()).strip() for element in collections] == [
            "Verify Series"
        ]
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#%s"]'
            '[@property="group-position"])' % collections[0].get("id")
        ) == "3"

        # The old whole-metadata replacement silently discarded these EPUB3
        # elements even though the download rewrite did not author them.
        assert package.xpath(
            'string(//*[local-name()="meta"][@property="dcterms:modified"])'
        ) == "2026-08-23T12:00:00Z"
        assert package.xpath(
            'string(//*[local-name()="meta"][@name="cover"]/@content)'
        ) == "cover-image"
        assert package.xpath(
            'string(//*[local-name()="meta"][@property="belongs-to-collection"]'
            '[.="Fixture Set"])'
        ) == "Fixture Set"
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#creator"]'
            '[@property="file-as"])'
        ) == "Author, Fixture"
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#creator"]'
            '[@property="role"])'
        ) == "aut"
        unique_identifier = package.get("unique-identifier")
        assert unique_identifier == "bookid"
        assert len(
            package.xpath(
                '//*[local-name()="identifier"][@id=$identifier]',
                identifier=unique_identifier,
            )
        ) == 1

        assert set(source_zip.namelist()) == set(served_zip.namelist())
        assert served_zip.comment == source_zip.comment
        for member in source_zip.namelist():
            if member != package_name:
                assert served_zip.read(member) == source_zip.read(member), member


def test_kepub_download_clears_only_series_collection_metadata(monkeypatch, tmp_path):
    _source, served = _run_download_rewrite(
        monkeypatch, tmp_path, _book(series_name=None)
    )

    with ZipFile(served) as served_zip:
        _package_name, package = _package(served_zip)
        assert _series_collections(package) == []
        assert package.xpath(
            'string(//*[local-name()="meta"][@property="belongs-to-collection"]'
            '[.="Fixture Set"])'
        ) == "Fixture Set"
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#id-6"]'
            '[@property="collection-type"])'
        ) == "set"
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#title"]'
            '[@property="title-type"])'
        ) == "main"
