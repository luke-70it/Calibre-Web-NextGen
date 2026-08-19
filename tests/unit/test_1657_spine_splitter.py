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


# ---------------------------------------------------------------------------
# The post-write validator's own guards.
#
# WHY THESE EXIST. A mutation campaign over this suite found that the splitter's
# BEHAVIOUR was well covered -- reverting the cut container to <body> failed 16
# of 17 tests, mapping every fragment to the first piece failed 6, reversing
# piece order failed 3 -- while EVERY guard inside `_validate_split_archive`
# could be deleted with the whole suite still green:
#
#   deleted guard                              suite result
#   KoboSpan id multiset changed                17 passed
#   spine reading order changed                 17 passed
#   TOC target retains a fragment               17 passed
#   split archive differs from the rewrite plan 17 passed
#   fragment identity occurs in multiple pieces 17 passed
#
# Those guards are the last thing standing between a planning or writing bug and
# a corrupted book on someone's device, and none of them was exercised. Tests
# that only drive the correct path cannot see them, because on the correct path
# they never fire.
#
# So each test below INJECTS the fault the guard exists to catch, and asserts the
# whole rewrite is abandoned: `split_multichapter_documents` returns None, the
# source archive is byte-identical, and no temporary file is left beside it.
# `os.replace` runs only after validation, so "byte-identical" is the real
# user-visible contract, not an implementation detail.
#
# Faults are injected at the two points where they can actually originate:
# `_build_entries` (a bad PLAN) and `_write_archive` (a bad WRITE). Nothing in
# the production module is modified.
#
# After these tests, deleting any one of those four guards fails exactly one test
# each. A FIFTH guard is deliberately not covered here:
#
#     raise ValueError("non-touched ZIP member changed: " + name)
#
# It cannot fire, and no test can make it. `touched` is defined as every name
# whose planned content differs from its source content, so for any name outside
# `touched`, expected == source by construction; the earlier
# `actual != expected_contents` guard has already proven actual == expected, and
# therefore actual == source. It is subsumed, not independent. Left in place
# because it states the intent cheaply, recorded here so nobody spends an
# afternoon trying to write the test that would cover it.
# ---------------------------------------------------------------------------


def _splitter_module():
    from cps.services import kepub_spine_splitter

    return kepub_spine_splitter


def _leftover_temporaries(book):
    return sorted(
        child.name for child in book.parent.iterdir()
        if child.name != book.name and ".spine-split.tmp" in child.name
    )


def _assert_refused(book, before, result):
    """The rewrite was abandoned and the user's file is exactly as it was."""
    assert result is None
    assert book.read_bytes() == before
    assert _leftover_temporaries(book) == []


def _corrupt_entries(monkeypatch, corrupt):
    """Let a test damage the rewrite plan just before it is written."""
    module = _splitter_module()
    original = module._build_entries

    def patched(*args, **kwargs):
        return corrupt(list(original(*args, **kwargs)))

    monkeypatch.setattr(module, "_build_entries", patched)


def _corrupt_written_archive(monkeypatch, corrupt):
    """Let a test damage the bytes actually written, leaving the plan intact."""
    module = _splitter_module()
    original = module._write_archive

    def patched(path, entries, comment):
        return original(path, corrupt(list(entries)), comment)

    monkeypatch.setattr(module, "_write_archive", patched)


def _edit(entries, name, replace):
    found = False
    edited = []
    for info, content in entries:
        if info.filename == name:
            content = replace(content)
            found = True
        edited.append((info, content))
    assert found, "fixture no longer contains {!r}".format(name)
    return edited


@pytest.mark.unit
def test_validator_refuses_a_plan_that_loses_a_kobo_span(tmp_path, monkeypatch):
    """The single most important invariant: a highlight anchors to a KoboSpan id.

    A plan that drops one is self-consistent -- the archive matches the plan
    exactly -- so only the multiset check can catch it.
    """
    book = _book(tmp_path)
    before = book.read_bytes()

    def drop_a_span(entries):
        return _edit(
            entries, "OPS/chapter-split-1.xhtml",
            lambda content: re.sub(
                rb'<span class="koboSpan" id="kobo\.1\.1">First</span>', b"First", content,
                count=1))

    _corrupt_entries(monkeypatch, drop_a_span)
    _assert_refused(book, before, _split(book))


@pytest.mark.unit
def test_validator_refuses_a_plan_that_reorders_the_spine(tmp_path, monkeypatch):
    """Chapters delivered out of order is a silently wrong book, not a crash."""
    book = _book(tmp_path)
    before = book.read_bytes()

    def reverse_spine(entries):
        return _edit(
            entries, "OPS/book.opf",
            lambda content: content.replace(
                b'<itemref idref="chapter"/><itemref idref="chapter-split"/>',
                b'<itemref idref="chapter-split"/><itemref idref="chapter"/>'))

    _corrupt_entries(monkeypatch, reverse_spine)
    _assert_refused(book, before, _split(book))


@pytest.mark.unit
def test_validator_refuses_a_plan_that_leaves_a_fragment_on_a_split_target(
        tmp_path, monkeypatch):
    """A retained #fragment is the whole defect #1657 exists to remove.

    Shipping a split whose TOC still points at an anchor would produce exactly
    the unreachable-chapter identity the split was performed to fix.
    """
    book = _book(tmp_path)
    before = book.read_bytes()

    def restore_a_fragment(entries):
        return _edit(
            entries, "OPS/toc.ncx",
            lambda content: content.replace(
                b'<content src="chapter-split-2.xhtml"/>',
                b'<content src="chapter-split-2.xhtml#ch2"/>'))

    _corrupt_entries(monkeypatch, restore_a_fragment)
    _assert_refused(book, before, _split(book))


@pytest.mark.unit
def test_validator_refuses_bytes_that_do_not_match_the_rewrite_plan(
        tmp_path, monkeypatch):
    """Guards the WRITE rather than the plan: what landed must be what was planned."""
    book = _book(tmp_path)
    before = book.read_bytes()

    def smuggle_bytes_past_the_plan(entries):
        return _edit(
            entries, "OPS/chapter-split-2.xhtml",
            lambda content: content.replace(b"Second", b"Smuggled"))

    _corrupt_written_archive(monkeypatch, smuggle_bytes_past_the_plan)
    _assert_refused(book, before, _split(book))


@pytest.mark.unit
def test_validator_guards_run_on_the_temporary_file_before_the_original_moves(
        tmp_path, monkeypatch):
    """Vacuity guard for the four tests above.

    Each asserts the source is unchanged after a refusal. That assertion would
    also hold if the splitter had simply declined to split this fixture for an
    unrelated reason, which would make all four pass while testing nothing. Pin
    that the same fixture DOES split when no fault is injected, so the byte
    identity above is caused by the refusal and by nothing else.
    """
    book = _book(tmp_path)
    before = book.read_bytes()

    assert _split(book) is True
    assert book.read_bytes() != before
