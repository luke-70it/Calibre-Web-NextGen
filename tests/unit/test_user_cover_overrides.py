# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user cover preferences stay isolated from global metadata and files."""
from datetime import datetime, timezone
import io
import inspect
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import zipfile

from flask import Flask
from PIL import Image
import pytest
from sqlalchemy import create_engine, inspect as sa_inspect
from werkzeug.datastructures import FileStorage

from cps import ub
from cps.api.serializers import cover_url_for, serialize_book_detail
from cps.services import user_cover


def _jpeg(color):
    stream = io.BytesIO()
    Image.new("RGB", (12, 18), color).save(stream, "JPEG")
    return stream.getvalue()


def _book(book_id=11):
    return SimpleNamespace(
        id=book_id, title="Public test book", has_cover=1,
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
        series=[], series_index=1, authors=[], data=[], tags=[], comments=[],
        languages=[], publishers=[], identifiers=[], rating=[], pubdate=None,
    )


@pytest.mark.unit
def test_migration_creates_composite_user_book_primary_key(tmp_path):
    engine = create_engine("sqlite:///{}".format(tmp_path / "app.db"))
    ub.User.__table__.create(engine)

    ub.migrate_user_book_cover_table(engine, None)
    ub.migrate_user_book_cover_table(engine, None)

    inspector = sa_inspect(engine)
    assert inspector.has_table("user_book_cover")
    assert inspector.get_pk_constraint("user_book_cover")["constrained_columns"] == [
        "user_id", "book_id",
    ]


@pytest.mark.unit
def test_serializer_selects_only_the_supplied_users_override(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    own = ub.UserBookCover(
        user_id=7, book_id=11,
        updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    other = ub.UserBookCover(
        user_id=8, book_id=11,
        updated_at=datetime(2026, 2, 2, tzinfo=timezone.utc),
    )
    book = _book()

    assert cover_url_for(book, "md", own).startswith(
        "/api/v1/books/11/my-cover/image?c=")
    assert cover_url_for(book, "md", other).startswith(
        "/api/v1/books/11/my-cover/image?c=")
    assert cover_url_for(book, "md") == "/cover/11/md?c=1767225600000000"

    detail = serialize_book_detail(book, cover_override=own)
    assert detail["using_my_cover"] is True
    assert detail["cover_url"].startswith("/api/v1/books/11/my-cover/image")
    assert detail["library_cover_url"] == "/cover/11/md?c=1767225600000000"
    assert detail["cover_srcset"] is None


@pytest.mark.unit
def test_upload_is_normalized_and_not_visible_until_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    raw = io.BytesIO()
    Image.new("RGBA", (10, 14), (20, 40, 60, 120)).save(raw, "PNG")
    raw.seek(0)

    staged, error = user_cover.stage_upload(
        7, 11,
        FileStorage(raw, filename="cover.png", content_type="image/png"),
    )

    assert error is None
    assert staged is not None
    assert not os.path.exists(user_cover.cover_path(7, 11))
    assert staged.publish() == (True, None)
    with Image.open(user_cover.cover_path(7, 11)) as saved:
        assert saved.format == "JPEG"
        assert saved.mode == "RGB"


@pytest.mark.unit
def test_startup_scavenger_removes_only_interrupted_personal_stages(tmp_path, monkeypatch):
    from cps import helper

    config_dir = tmp_path / "config"
    library_dir = tmp_path / "library"
    temp_dir = tmp_path / "temp"
    library_dir.mkdir()
    temp_dir.mkdir()
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(library_dir))
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(temp_dir))

    staged, error = user_cover.stage_upload(
        7, 11,
        FileStorage(
            io.BytesIO(_jpeg("red")),
            filename="cover.jpg",
            content_type="image/jpeg",
        ),
    )
    assert staged is not None and error is None
    unrelated = config_dir / "user-covers" / "7" / ".notes.cwng-test.stage"
    unrelated.write_bytes(b"keep")

    assert helper.scavenge_staged_cover_files() == 1
    assert not os.path.exists(staged.staged_path)
    assert unrelated.read_bytes() == b"keep"
    assert not os.path.exists(user_cover.cover_path(7, 11))


@pytest.mark.unit
def test_set_endpoint_writes_only_current_users_row(tmp_path, monkeypatch):
    from cps.api import actions

    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    app = Flask(__name__)
    upload = (io.BytesIO(_jpeg("red")), "mine.jpg")
    viewer = SimpleNamespace(
        id=7, is_authenticated=True, is_anonymous=False,
        role_browse_global=lambda: True,
    )
    session = MagicMock()
    staged = MagicMock()
    staged.publish.return_value = (True, None)
    book = _book()

    with app.test_request_context(
        "/api/v1/books/11/my-cover", method="PUT",
        data={"file": upload}, content_type="multipart/form-data",
    ), patch.object(actions, "current_user", viewer), \
            patch.object(actions, "_personal_cover_book", return_value=book), \
            patch.object(actions.user_cover, "stage_upload", return_value=(staged, None)), \
            patch.object(actions.user_cover, "row_for_user", return_value=None) as lookup, \
            patch.object(actions.ub, "session", session), \
            patch.object(actions.user_library, "mark_response_user_specific"), \
            patch.object(actions, "remove_synced_book"):
        response = inspect.unwrap(actions.set_my_book_cover)(11)

    body = json.loads(response.get_data())
    assert body["ok"] is True
    assert body["using_my_cover"] is True
    lookup.assert_called_once_with(7, 11)
    row = session.add.call_args.args[0]
    assert (row.user_id, row.book_id) == (7, 11)
    session.commit.assert_called_once()
    staged.publish.assert_called_once()


@pytest.mark.unit
def test_get_and_clear_endpoints_never_address_another_users_cover():
    from cps.api import actions

    app = Flask(__name__)
    viewer = SimpleNamespace(
        id=7, is_authenticated=True, is_anonymous=False,
        role_browse_global=lambda: True,
    )
    row = ub.UserBookCover(
        user_id=7, book_id=11,
        updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    session = MagicMock()
    book = _book()

    with app.test_request_context("/api/v1/books/11/my-cover"), \
            patch.object(actions, "current_user", viewer), \
            patch.object(actions, "_personal_cover_book", return_value=book), \
            patch.object(actions.user_cover, "override_for_user", return_value=row) as get_lookup, \
            patch.object(actions.user_library, "mark_response_user_specific"):
        response = inspect.unwrap(actions.get_my_book_cover)(11)
    assert json.loads(response.get_data())["using_my_cover"] is True
    get_lookup.assert_called_once_with(7, 11)

    with app.test_request_context("/api/v1/books/11/my-cover", method="DELETE"), \
            patch.object(actions, "current_user", viewer), \
            patch.object(actions, "_personal_cover_book", return_value=book), \
            patch.object(actions.user_cover, "row_for_user", return_value=row) as clear_lookup, \
            patch.object(actions.user_cover, "remove_file") as remove_file, \
            patch.object(actions.ub, "session", session), \
            patch.object(actions.user_library, "mark_response_user_specific"), \
            patch.object(actions, "remove_synced_book"):
        response = inspect.unwrap(actions.clear_my_book_cover)(11)
    assert json.loads(response.get_data())["using_my_cover"] is False
    clear_lookup.assert_called_once_with(7, 11)
    session.delete.assert_called_once_with(row)
    remove_file.assert_called_once_with(7, 11)


@pytest.mark.unit
def test_personal_source_routes_do_not_grant_global_cover_write():
    from cps import cover_picker

    app = Flask(__name__)
    viewer = SimpleNamespace(role_edit=lambda: False, role_admin=lambda: False)
    called = MagicMock(return_value="ok")
    protected = cover_picker.cover_source_required(called)

    with app.test_request_context("/book/11/cover/candidates?scope=personal"), \
            patch.object(cover_picker, "current_user", viewer):
        assert protected() == "ok"

    with app.test_request_context("/book/11/cover/candidates"), \
            patch.object(cover_picker, "current_user", viewer), \
            pytest.raises(Exception) as error:
        protected()
    assert getattr(error.value, "code", None) == 403


def _epub(path, cover_bytes):
    container = b'''<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
    </container>'''
    package = b'''<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
      <metadata><meta name="cover" content="cover-image"/></metadata>
      <manifest>
        <item id="cover-image" href="images/cover.jpg" media-type="image/jpeg"/>
        <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
      </manifest><spine><itemref idref="chapter"/></spine>
    </package>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/images/cover.jpg", cover_bytes)
        archive.writestr("OEBPS/chapter.xhtml", b"<p>unchanged</p>")


@pytest.mark.unit
def test_epub_delivery_copy_embeds_only_requesting_users_cover(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path / "config"))
    source = tmp_path / "library.epub"
    global_cover = _jpeg("blue")
    personal_cover = _jpeg("red")
    _epub(source, global_cover)
    os.makedirs(user_cover.cover_directory(7), exist_ok=True)
    with open(user_cover.cover_path(7, 11), "wb") as cover_file:
        cover_file.write(personal_cover)
    row = SimpleNamespace(user_id=7, book_id=11)

    monkeypatch.setattr(
        user_cover, "override_for_user",
        lambda user_id, book_id: row if (user_id, book_id) == (7, 11) else None,
    )
    from cps import helper
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path / "deliveries"))

    delivered = user_cover.materialize_delivery_copy(7, 11, str(source), "epub")
    assert delivered is not None
    delivered_path = os.path.join(delivered[0], delivered[1] + ".epub")
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(delivered_path) as private:
        assert original.read("OEBPS/images/cover.jpg") == global_cover
        assert private.read("OEBPS/images/cover.jpg") != global_cover
        assert private.read("OEBPS/chapter.xhtml") == original.read("OEBPS/chapter.xhtml")
        with Image.open(io.BytesIO(private.read("OEBPS/images/cover.jpg"))) as image:
            red, _green, blue = image.resize((1, 1)).getpixel((0, 0))
            assert red > blue

    assert user_cover.materialize_delivery_copy(8, 11, str(source), "epub") is None


@pytest.mark.unit
def test_kobo_cover_image_id_versions_personal_cover_per_user(monkeypatch):
    from cps import kobo

    book = SimpleNamespace(
        id=11, uuid="12345678-1234-1234-1234-123456789abc",
        path="Author/Book", last_modified=datetime(2026, 1, 1),
    )
    row = SimpleNamespace(
        user_id=7, book_id=11,
        updated_at=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(kobo, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(user_cover, "override_for_user", lambda user_id, book_id: row)
    monkeypatch.setattr(
        kobo, "_current_padding_settings",
        lambda: SimpleNamespace(enabled=False),
    )

    image_id = kobo._get_cover_image_id(book)
    assert image_id.startswith(str(book.uuid) + "-")
    assert image_id != str(book.uuid)
    assert kobo.normalize_cover_uuid(image_id) == str(book.uuid)
