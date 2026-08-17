# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Synthetic-archive coverage for fragment-anchored EPUB TOCs."""

import logging
from types import SimpleNamespace
import zipfile

import pytest


CONTAINER_XML = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

NCX_WITH_FRAGMENTS = b"""<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint><content src="chapter.xhtml#one"/></navPoint>
    <navPoint><content src="chapter.xhtml"/></navPoint>
    <navPoint><content src="chapter.xhtml#two"/></navPoint>
  </navMap>
</ncx>
"""

NCX_WITHOUT_FRAGMENTS = NCX_WITH_FRAGMENTS.replace(
    b"chapter.xhtml#one", b"chapter-one.xhtml"
).replace(
    b"chapter.xhtml#two", b"chapter-two.xhtml"
)

NAV_WITH_FRAGMENTS = b"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter.xhtml#one">One</a></li>
        <li><a href="chapter.xhtml">Whole document</a></li>
        <li><a href="#local-position">Local position</a></li>
      </ol>
    </nav>
    <nav epub:type="landmarks">
      <a href="chapter.xhtml#not-a-toc-entry">Body</a>
    </nav>
  </body>
</html>
"""


def _matching_dual_tocs(target_count):
    ncx_targets = "\n".join(
        '<navPoint><content src="chapters/chapter.xhtml#anchor-{0:03d}"/></navPoint>'.format(index)
        for index in range(target_count)
    )
    nav_targets = "\n".join(
        '<li><a href="../chapters/chapter.xhtml#anchor-{0:03d}">{0}</a></li>'.format(index)
        for index in range(target_count)
    )
    ncx = (
        '<?xml version="1.0"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        + ncx_targets
        + '</navMap></ncx>'
    ).encode()
    nav = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        '<nav epub:type="toc"><ol>'
        + nav_targets
        + '</ol></nav></body></html>'
    ).encode()
    return ncx, nav


def _opf(*manifest_items, version="3.0", spine_toc="", spine_ids=()):
    items = "\n".join(manifest_items)
    toc_attribute = f' toc="{spine_toc}"' if spine_toc else ""
    itemrefs = "".join('<itemref idref="{}"/>'.format(item_id) for item_id in spine_ids)
    return f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}">
  <manifest>{items}</manifest>
  <spine{toc_attribute}>{itemrefs}</spine>
</package>
""".encode()


def _write_epub(path, opf, members=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OPS/book.opf", opf)
        for name, content in members:
            archive.writestr(name, content)
    return path


def _count(path):
    from cps.services.kepub_package_normalizer import count_fragment_anchored_toc_targets

    return count_fragment_anchored_toc_targets(path)


def _normalize(path):
    from cps.services.kepub_package_normalizer import normalize_kepub_package

    return normalize_kepub_package(path)


def _ncx(nav_targets=(), page_targets=(), nav_list_targets=()):
    nav_points = "".join(
        '<navPoint id="n{0}"><content src="{1}"/></navPoint>'.format(index, target)
        for index, target in enumerate(nav_targets)
    )
    pages = "".join(
        '<pageTarget id="p{0}"><content src="{1}"/></pageTarget>'.format(index, target)
        for index, target in enumerate(page_targets)
    )
    nav_targets_outside_map = "".join(
        '<navTarget id="l{0}"><content src="{1}"/></navTarget>'.format(index, target)
        for index, target in enumerate(nav_list_targets)
    )
    return (
        '<?xml version="1.0"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
        '<navMap>{}</navMap><pageList>{}</pageList><navList>{}</navList></ncx>'.format(
            nav_points, pages, nav_targets_outside_map)
    ).encode()


def _fragment_epub(
        tmp_path, name, nav_targets, chapters, page_targets=(), nav_list_targets=()):
    manifest = [
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    ]
    members = [("OPS/toc.ncx", _ncx(nav_targets, page_targets, nav_list_targets))]
    spine_ids = []
    for index, (chapter_path, chapter_bytes) in enumerate(chapters.items()):
        item_id = "chapter{}".format(index)
        spine_ids.append(item_id)
        manifest.append(
            '<item id="{}" href="{}" media-type="application/xhtml+xml"/>'.format(
                item_id, chapter_path.replace(" ", "%20")))
        members.append(("OPS/" + chapter_path, chapter_bytes))
    return _write_epub(
        tmp_path / name,
        _opf(*manifest, version="2.0", spine_toc="ncx", spine_ids=spine_ids),
        members,
    )


def _ncx_sources(path):
    from lxml import etree

    with zipfile.ZipFile(path) as archive:
        document = etree.fromstring(archive.read("OPS/toc.ncx"))
    return document.xpath("//*[local-name()='content']/@src")


@pytest.mark.unit
def test_ncx_only_toc_counts_fragment_anchored_targets(tmp_path):
    package = _write_epub(
        tmp_path / "ncx-fragments.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            version="2.0",
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", NCX_WITH_FRAGMENTS)],
    )

    assert _count(package) == 2


@pytest.mark.unit
def test_single_fragment_at_first_rendered_position_is_stripped_safely(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<section><h1 id="ch1">Chapter</h1></section>tail after the anchor ancestor'
        b'</body></html>'
    )
    package = _fragment_epub(
        tmp_path, "top-anchor.kepub", ["chapter.xhtml#ch1"],
        {"chapter.xhtml": chapter})
    with zipfile.ZipFile(package) as archive:
        chapter_before = archive.read("OPS/chapter.xhtml")

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["chapter.xhtml"]
    with zipfile.ZipFile(package) as archive:
        assert archive.read("OPS/chapter.xhtml") == chapter_before

    first_rewrite = package.read_bytes()
    assert _normalize(package) is False
    assert package.read_bytes() == first_rewrite


@pytest.mark.unit
@pytest.mark.parametrize(
    "prefix",
    [
        b"<p>Rendered before the anchor</p>",
        b'<img src="cover.jpg" alt=""/>',
    ],
    ids=["preceding-text", "preceding-image"],
)
def test_rendered_content_before_anchor_prevents_stripping(tmp_path, prefix):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>' + prefix
        + b'<h1 id="ch1">Chapter</h1></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "rendered-predecessor.kepub", ["chapter.xhtml#ch1"],
        {"chapter.xhtml": chapter})
    before = package.read_bytes()

    assert _normalize(package) is False
    assert package.read_bytes() == before
    assert _ncx_sources(package) == ["chapter.xhtml#ch1"]


@pytest.mark.unit
def test_two_distinct_fragments_into_one_document_are_both_preserved(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="one">One</h1><h2 id="two">Two</h2></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "multiple-fragments.kepub",
        ["chapter.xhtml#one", "chapter.xhtml#two"],
        {"chapter.xhtml": chapter})

    assert _normalize(package) is False
    assert _ncx_sources(package) == ["chapter.xhtml#one", "chapter.xhtml#two"]


@pytest.mark.unit
def test_missing_anchor_is_preserved(tmp_path):
    chapter = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter</h1></body></html>'
    package = _fragment_epub(
        tmp_path, "missing-anchor.kepub", ["chapter.xhtml#absent"],
        {"chapter.xhtml": chapter})

    assert _normalize(package) is False
    assert _ncx_sources(package) == ["chapter.xhtml#absent"]


@pytest.mark.unit
def test_legacy_named_anchor_at_top_is_stripped(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<a name="legacy"></a><p>Chapter</p></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "legacy-anchor.kepub", ["chapter.xhtml#legacy"],
        {"chapter.xhtml": chapter})

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["chapter.xhtml"]


@pytest.mark.unit
def test_percent_encoded_and_spaced_targets_resolve_to_the_same_anchor(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="ch 1">Chapter</h1></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "encoded-spaces.kepub",
        ["Chapter%2001.xhtml#ch%201", "Chapter 01.xhtml#ch 1"],
        {"Chapter 01.xhtml": chapter})

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["Chapter%2001.xhtml", "Chapter 01.xhtml"]


@pytest.mark.unit
def test_page_list_and_nav_target_fragments_are_ignored(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="chapter">Chapter</h1><a id="page1"></a><p>Page one</p></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "page-list.kepub", ["chapter.xhtml#chapter"],
        {"chapter.xhtml": chapter},
        page_targets=["chapter.xhtml#page1"],
        nav_list_targets=["chapter.xhtml#page1"])

    assert _count(package) == 1
    assert _normalize(package) is True
    assert _ncx_sources(package) == [
        "chapter.xhtml", "chapter.xhtml#page1", "chapter.xhtml#page1"]


@pytest.mark.unit
def test_qualifying_targets_are_stripped_independently_within_a_book(tmp_path):
    package = _fragment_epub(
        tmp_path, "partial-safe-rewrite.kepub",
        ["top.xhtml#top", "middle.xhtml#middle"],
        {
            "top.xhtml": (
                b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                b'<h1 id="top">Top</h1></body></html>'),
            "middle.xhtml": (
                b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                b'<p>Earlier</p><h1 id="middle">Middle</h1></body></html>'),
        },
    )

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["top.xhtml", "middle.xhtml#middle"]


@pytest.mark.unit
def test_epub3_doc_toc_is_rewritten_but_landmarks_are_not(tmp_path):
    nav = b"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <nav role="doc-toc"><a href="chapter.xhtml#top">Chapter</a></nav>
  <nav role="doc-landmarks"><a href="chapter.xhtml#top">Landmark</a></nav>
</body></html>"""
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="top">Top</h1></body></html>'
    )
    package = _write_epub(
        tmp_path / "epub3-nav.kepub",
        _opf(
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
        ),
        [("OPS/nav.xhtml", nav), ("OPS/chapter.xhtml", chapter)],
    )

    assert _normalize(package) is True
    with zipfile.ZipFile(package) as archive:
        rewritten = archive.read("OPS/nav.xhtml")
    assert rewritten.count(b'href="chapter.xhtml"') == 1
    assert rewritten.count(b'href="chapter.xhtml#top"') == 1


@pytest.mark.unit
def test_non_zip_counter_and_normalizer_never_raise(tmp_path):
    package = tmp_path / "truncated.kepub"
    package.write_bytes(b"PK\x03\x04truncated")

    assert _count(package) == 0
    assert _normalize(package) is None


@pytest.mark.unit
def test_toc_without_fragments_reports_zero(tmp_path):
    package = _write_epub(
        tmp_path / "ncx-clean.epub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            version="2.0",
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", NCX_WITHOUT_FRAGMENTS)],
    )

    assert _count(package) == 0


@pytest.mark.unit
def test_nav_only_toc_counts_fragments_but_not_landmarks(tmp_path):
    package = _write_epub(
        tmp_path / "nav-fragments.epub",
        _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'),
        [("OPS/nav.xhtml", NAV_WITH_FRAGMENTS)],
    )

    assert _count(package) == 2


@pytest.mark.unit
def test_matching_ncx_and_nav_targets_are_counted_once_per_package(tmp_path):
    ncx, nav = _matching_dual_tocs(42)
    package = _write_epub(
        tmp_path / "dual-toc-fragments.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="nav" href="nav/toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", ncx), ("OPS/nav/toc.xhtml", nav)],
    )

    assert _count(package) == 42


@pytest.mark.unit
@pytest.mark.parametrize("toc_state", ["absent", "malformed"])
def test_absent_or_malformed_toc_never_raises(tmp_path, toc_state):
    if toc_state == "absent":
        opf = _opf('<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>')
        members = []
    else:
        opf = _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        members = [("OPS/nav.xhtml", b"<html><nav")]
    package = _write_epub(tmp_path / f"{toc_state}.epub", opf, members)

    assert _count(package) == 0


@pytest.mark.unit
def test_conversion_diagnostic_names_book_and_fragment_count(tmp_path, caplog, monkeypatch):
    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.tasks import convert

    book_path = tmp_path / "affected"
    (tmp_path / "affected.epub").write_bytes(b"source")
    book = SimpleNamespace(
        id=42,
        title="Synthetic Fragment Book",
        path="Synthetic/Fragment Book",
        data=[SimpleNamespace(name="affected")],
    )

    class Query:
        def filter(self, *_args):
            return self

        def one_or_none(self):
            return None

    class Session:
        def query(self, *_args):
            return Query()

        def merge(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class LocalDB:
        def __init__(self, **_kwargs):
            self.session = Session()

        def get_book(self, _book_id):
            return book

        def get_book_format(self, *_args):
            return None

    def convert_package(*_args):
        _write_epub(
            tmp_path / "affected.kepub",
            _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'),
            [("OPS/nav.xhtml", NAV_WITH_FRAGMENTS)],
        )
        return 0, None

    monkeypatch.setattr(convert.db, "CalibreDB", LocalDB)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(convert.helper, "mark_book_modified", lambda *_args, **_kwargs: None)
    task = convert.TaskConvert(
        str(book_path), 42, "convert",
        {"old_book_format": "EPUB", "new_book_format": "KEPUB"}, None,
    )
    monkeypatch.setattr(task, "_convert_kepubify", convert_package)
    monkeypatch.setattr(task, "_handleSuccess", lambda: None)

    with caplog.at_level(logging.WARNING):
        assert task._convert_ebook_format() == "affected.kepub"

    message = caplog.text
    assert "Synthetic Fragment Book" in message
    assert "42" in message
    assert "2 fragment-anchored TOC targets" in message
    assert "highlights" in message.lower()
