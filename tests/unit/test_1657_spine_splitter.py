# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Focused coverage for fragment-addressed KEPUB spine splitting."""

from collections import Counter
import re
import zipfile

from lxml import etree
import pytest


CONTAINER = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _chapter(span_ids=("kobo.1.1", "kobo.1.2", "kobo.2.1", "kobo.2.2")):
    return (
        '<?xml version="1.0"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head><body>'
        '<div id="book-columns"><div id="book-inner">'
        '<p class="preface">Before chapter one</p>'
        '<section id="ch1"><h1>One</h1>'
        '<p><span class="koboSpan" id="{}">First</span>'
        '<a href="#ch2"><span class="koboSpan" id="{}">next</span></a></p></section>'
        '<section id="ch2"><h1>Two</h1>'
        '<p><span id="{}" class="other koboSpan tail">Second</span>'
        '<span class="koboSpan" id="{}">end</span></p></section>'
        '</div></div></body></html>'
    ).format(*span_ids).encode()


def _ncx(targets):
    points = "".join(
        '<navPoint id="n{}"><navLabel><text>{}</text></navLabel>'
        '<content src="{}"/></navPoint>'.format(index, index, target)
        for index, target in enumerate(targets)
    )
    return (
        '<?xml version="1.0"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        + points + '</navMap></ncx>'
    ).encode()


def _opf(properties=' properties="scripted"', guide=True):
    guide_xml = (
        '<guide><reference type="text" title="Second" '
        'href="chapter.xhtml#ch2"/></guide>' if guide else ""
    )
    return (
        '<?xml version="1.0"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<manifest>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="chapter" href="chapter.xhtml" '
        'media-type="application/xhtml+xml"{}/>'
        '</manifest><spine toc="ncx"><itemref idref="chapter"/></spine>{}'
        '</package>'
    ).format(properties, guide_xml).encode()


def _book(tmp_path, *, targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2"),
          chapter=None, ncx=None, extra_members=(), properties=' properties="scripted"'):
    path = tmp_path / "book.kepub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OPS/book.opf", _opf(properties=properties))
        archive.writestr("OPS/toc.ncx", _ncx(targets) if ncx is None else ncx)
        archive.writestr("OPS/chapter.xhtml", _chapter() if chapter is None else chapter)
        for name, content in extra_members:
            archive.writestr(name, content)
    return path


def _add_following_document(book, following):
    with zipfile.ZipFile(book) as archive:
        members = [(info, archive.read(info)) for info in archive.infolist()]
    package_index = next(
        index for index, (info, _content) in enumerate(members)
        if info.filename == "OPS/book.opf")
    package_info, package = members[package_index]
    package = package.replace(
        b"</manifest>",
        b'<item id="following" href="following.xhtml" '
        b'media-type="application/xhtml+xml"/></manifest>',
    ).replace(
        b"</spine>", b'<itemref idref="following"/></spine>')
    members[package_index] = package_info, package
    with zipfile.ZipFile(book, "w") as archive:
        for info, content in members:
            archive.writestr(info, content)
        archive.writestr("OPS/following.xhtml", following)


def _split(path):
    from cps.services.kepub_spine_splitter import split_multichapter_documents

    return split_multichapter_documents(path)


def _span_ids(contents):
    return Counter(re.findall(
        rb'<span\b(?=[^>]*\bclass=["\'][^"\']*\bkoboSpan\b)[^>]*'
        rb'\bid=["\']([^"\']+)["\']',
        b"".join(contents),
    ))


def _kobo_span_lexemes(contents):
    return Counter(re.findall(
        rb'<span\b(?=[^>]*\bclass=["\'][^"\']*\bkoboSpan\b)[^>]*>'
        rb'[^<]*</span\s*>',
        b"".join(contents),
    ))


def _package_state(path):
    with zipfile.ZipFile(path) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    package = etree.fromstring(contents["OPS/book.opf"])
    manifest = {
        item.get("id"): (item.get("href"), item.get("properties"))
        for item in package.xpath("//*[local-name()='manifest']/*[local-name()='item']")
    }
    spine = package.xpath("//*[local-name()='spine']/*[local-name()='itemref']/@idref")
    toc = etree.fromstring(contents["OPS/toc.ncx"])
    targets = toc.xpath("//*[local-name()='navMap']//*[local-name()='content']/@src")
    return contents, manifest, spine, targets


@pytest.mark.unit
def test_two_chapters_split_in_spine_order_and_toc_fragments_are_removed(tmp_path):
    book = _book(tmp_path)
    untouched_before = b"unchanged auxiliary bytes\x00\xff"
    with zipfile.ZipFile(book, "a") as archive:
        archive.writestr("OPS/untouched.bin", untouched_before)

    assert _split(book) is True

    contents, manifest, spine, targets = _package_state(book)
    assert "OPS/chapter.xhtml" not in contents
    assert spine == ["chapter", "chapter-split"]
    assert manifest["chapter"] == ("chapter-split-1.xhtml", "scripted")
    assert manifest["chapter-split"] == ("chapter-split-2.xhtml", "scripted")
    assert targets == ["chapter-split-1.xhtml", "chapter-split-2.xhtml"]
    assert contents["OPS/untouched.bin"] == untouched_before
    assert b"Before chapter one" in contents["OPS/chapter-split-1.xhtml"]
    assert b'id="ch2"' not in contents["OPS/chapter-split-1.xhtml"]
    assert contents["OPS/chapter-split-2.xhtml"].count(b'id="ch2"') == 1


@pytest.mark.unit
def test_kobo_span_id_multiset_is_preserved_exactly(tmp_path):
    book = _book(tmp_path)
    with zipfile.ZipFile(book) as archive:
        before = _span_ids([archive.read(name) for name in archive.namelist()])

    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        after = _span_ids([archive.read(name) for name in archive.namelist()])
    assert sum(before.values()) == 4
    assert sum(after.values()) == 4
    assert after == before


@pytest.mark.unit
def test_real_kepub_shape_preserves_every_kobo_span_lexeme_byte_exact(tmp_path):
    chapter = _chapter().replace(
        b'<span class="koboSpan" id="kobo.1.1">First</span>',
        b"<span  data-extra='kept' class='lead koboSpan' id='kobo.1.1'>First</span>",
    )
    book = _book(tmp_path, chapter=chapter)
    with zipfile.ZipFile(book) as archive:
        before = _kobo_span_lexemes([archive.read(name) for name in archive.namelist()])

    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        after = _kobo_span_lexemes([archive.read(name) for name in archive.namelist()])
    assert len(before) == 4
    assert after == before


@pytest.mark.unit
def test_shared_cut_element_keeps_anchors_together_without_aborting_other_splits(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body class="calibre">'
        b'<div id="book-columns"><div id="book-inner">'
        b'<div class="chapter-shell"><h1 id="ch1">One</h1>'
        b'<p><span class="koboSpan" id="kobo.1.1">one</span></p>'
        b'<h2 id="ch1b">One, continued</h2>'
        b'<p><span class="koboSpan" id="kobo.1.2">continued</span></p></div>'
        b'<div class="chapter-shell"><h1 id="ch2">Two</h1>'
        b'<p><span class="koboSpan" id="kobo.2.1">two</span></p></div>'
        b'</div></div></body></html>'
    )
    book = _book(
        tmp_path,
        targets=(
            "chapter.xhtml#ch1",
            "chapter.xhtml#ch1b",
            "chapter.xhtml#ch2",
        ),
        chapter=chapter,
    )

    assert _split(book) is True

    contents, _manifest, spine, targets = _package_state(book)
    assert spine == ["chapter", "chapter-split"]
    assert targets == [
        "chapter-split-1.xhtml",
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
    ]
    first = contents["OPS/chapter-split-1.xhtml"]
    second = contents["OPS/chapter-split-2.xhtml"]
    assert b'id="ch1"' in first and b'id="ch1b"' in first
    assert b'id="ch2"' not in first
    assert b'id="ch2"' in second


@pytest.mark.unit
def test_non_one_based_kobo_span_ids_remain_byte_exact(tmp_path):
    ids = ("kobo.0.0", "kobo.0.7", "kobo.8.0", "kobo.8.19")
    book = _book(tmp_path, chapter=_chapter(ids))

    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        after = _span_ids([archive.read(name) for name in archive.namelist()])
    assert after == Counter(span_id.encode() for span_id in ids)


@pytest.mark.unit
def test_single_fragment_document_is_a_byte_identical_noop(tmp_path):
    book = _book(tmp_path, targets=("chapter.xhtml#ch1",))
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_missing_target_anchor_is_a_byte_identical_noop(tmp_path):
    book = _book(
        tmp_path, targets=("chapter.xhtml#ch1", "chapter.xhtml#missing"))
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_unparseable_toc_is_left_untouched(tmp_path):
    book = _book(tmp_path, ncx=b"<ncx><navMap><broken></ncx>")
    before = book.read_bytes()

    assert _split(book) in (False, None)
    assert book.read_bytes() == before


@pytest.mark.unit
def test_second_call_is_a_byte_identical_noop(tmp_path):
    book = _book(tmp_path)
    assert _split(book) is True
    after_first = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == after_first


@pytest.mark.unit
def test_normalizer_default_does_not_split_multichapter_documents(tmp_path):
    import inspect

    from cps.services.kepub_package_normalizer import normalize_kepub_package

    book = _book(tmp_path)
    before = book.read_bytes()

    split_parameter = inspect.signature(normalize_kepub_package).parameters["split_chapters"]
    assert split_parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert split_parameter.default is False
    assert normalize_kepub_package(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_normalizer_explicit_split_opt_in_splits_multichapter_documents(tmp_path):
    from cps.services.kepub_package_normalizer import normalize_kepub_package

    book = _book(tmp_path)

    assert normalize_kepub_package(book, split_chapters=True) is True

    _contents, _manifest, spine, targets = _package_state(book)
    assert spine == ["chapter", "chapter-split"]
    assert targets == ["chapter-split-1.xhtml", "chapter-split-2.xhtml"]


@pytest.mark.unit
def test_opted_in_split_failure_is_a_byte_identical_nonfatal_result(
        tmp_path, monkeypatch):
    from cps.services import kepub_spine_splitter
    from cps.services.kepub_package_normalizer import normalize_kepub_package

    book = _book(tmp_path)
    before = book.read_bytes()
    monkeypatch.setattr(
        kepub_spine_splitter,
        "split_multichapter_documents",
        lambda _path: None,
    )

    assert normalize_kepub_package(book, split_chapters=True) is None
    assert book.read_bytes() == before


@pytest.mark.unit
def test_guide_and_cross_piece_internal_links_continue_to_resolve(tmp_path):
    book = _book(tmp_path)
    following = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<a href="chapter.xhtml#ch2">Back to two</a></body></html>')
    _add_following_document(book, following)

    assert _split(book) is True

    contents, _manifest, _spine, _targets = _package_state(book)
    assert b'href="chapter-split-2.xhtml#ch2"' in contents["OPS/chapter-split-1.xhtml"]
    assert b'href="chapter-split-2.xhtml#ch2"' in contents["OPS/following.xhtml"]
    package = etree.fromstring(contents["OPS/book.opf"])
    assert package.xpath("string(//*[local-name()='guide']/*/@href)") == (
        "chapter-split-2.xhtml#ch2")


@pytest.mark.unit
def test_css_url_reference_to_split_fragment_continues_to_resolve(tmp_path):
    stylesheet = b".chapter-link{background:url('chapter.xhtml#ch2')}"
    book = _book(
        tmp_path,
        extra_members=(("OPS/book.css", stylesheet),),
    )

    assert _split(book) is True

    contents, _manifest, _spine, _targets = _package_state(book)
    assert contents["OPS/book.css"] == (
        b".chapter-link{background:url('chapter-split-2.xhtml#ch2')}")


@pytest.mark.unit
def test_unsupported_reference_attribute_causes_a_byte_identical_noop(tmp_path):
    book = _book(tmp_path)
    following = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<video poster="chapter.xhtml#ch2"/></body></html>')
    _add_following_document(book, following)
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_nested_boundary_that_cannot_form_valid_documents_is_left_untouched(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><div id="ch1">'
        b'<span class="koboSpan" id="kobo.1.1">one</span>'
        b'<section id="ch2"><span class="koboSpan" id="kobo.2.1">two</span>'
        b'</section></div></body></html>'
    )
    book = _book(tmp_path, chapter=chapter)
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_existing_piece_name_collision_is_avoided(tmp_path):
    book = _book(
        tmp_path, extra_members=(("OPS/chapter-split-1.xhtml", b"occupied"),))

    assert _split(book) is True

    contents, _manifest, _spine, targets = _package_state(book)
    assert contents["OPS/chapter-split-1.xhtml"] == b"occupied"
    assert targets == ["chapter-split-2.xhtml", "chapter-split-3.xhtml"]
