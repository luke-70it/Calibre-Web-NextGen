# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral coverage for configurable Magic Shelf custom-column sorting."""

from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker


pytestmark = pytest.mark.unit


class ColumnDefinition:
    def __init__(self, column_id, datatype="int", *, multiple=False, deleted=False, name=None):
        self.id = column_id
        self.name = name or f"Column {column_id}"
        self.datatype = datatype
        self.is_multiple = multiple
        self.mark_for_delete = deleted


@pytest.fixture()
def sortable_library(monkeypatch):
    """A real Books table and the direct-per-book shape Calibre uses for ints."""
    from cps import db

    custom_base = declarative_base()

    class Difficulty(custom_base):
        __tablename__ = "custom_column_2086"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(Integer)

    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    Difficulty.__table__.create(engine)
    with engine.begin() as connection:
        for book_id in range(1, 7):
            connection.execute(
                text(
                    "INSERT INTO books "
                    "(id, title, sort, author_sort, timestamp, pubdate, series_index, "
                    "last_modified, path, has_cover, uuid) "
                    "VALUES (:id, :title, :title, 'Author', :timestamp, :timestamp, "
                    "1.0, :timestamp, '.', 0, :uuid)"
                ),
                {
                    "id": book_id,
                    "title": f"Book {book_id}",
                    "timestamp": f"2026-01-0{book_id} 00:00:00+00:00",
                    "uuid": f"uuid-{book_id}",
                },
            )
        connection.execute(
            Difficulty.__table__.insert(),
            [
                {"id": 1, "book": 1, "value": 20},
                {"id": 2, "book": 2, "value": 20},
                {"id": 3, "book": 3, "value": None},
                {"id": 4, "book": 4, "value": 10},
                # Book 5 has no custom row: an outer-join empty value.
                {"id": 6, "book": 6, "value": 20},
            ],
        )
    monkeypatch.setattr(db, "cc_classes", {2086: Difficulty})
    try:
        yield engine, Difficulty
    finally:
        engine.dispose()


def _paged_ids(session, query, order_by, page_size=2):
    pages = []
    for offset in range(0, 6, page_size):
        pages.append([
            row[0]
            for row in query.order_by(*order_by).offset(offset).limit(page_size).all()
        ])
    return pages


def test_configured_integer_sort_is_total_and_keeps_empties_last_across_pages(
        sortable_library):
    """Both directions produce stable pages, including ties and both empty shapes."""
    from cps import db
    from cps.custom_column_sort import resolve_magic_shelf_sort

    engine, difficulty = sortable_library
    config = SimpleNamespace(config_sortable_custom_columns="2086")
    columns = [ColumnDefinition(2086, name="Difficulty")]
    session = sessionmaker(bind=engine)()
    try:
        ascending = resolve_magic_shelf_sort("cc-2086-asc", config, columns)
        descending = resolve_magic_shelf_sort("cc-2086-desc", config, columns)

        assert ascending.key == "cc-2086-asc"
        assert descending.key == "cc-2086-desc"
        assert len(ascending.join) == 2 and ascending.join[0] is difficulty
        assert len(descending.join) == 2 and descending.join[0] is difficulty

        base = session.query(db.Books.id).outerjoin(*ascending.join)
        assert _paged_ids(session, base, ascending.order_by) == [[4, 1], [2, 6], [3, 5]]

        base = session.query(db.Books.id).outerjoin(*descending.join)
        assert _paged_ids(session, base, descending.order_by) == [[6, 2], [1, 4], [5, 3]]
    finally:
        session.close()


def test_hostile_unknown_and_deleted_keys_execute_only_the_default_order(sortable_library):
    """Untrusted or stale keys fall back before SQL construction sees request text."""
    from cps import db
    from cps.custom_column_sort import resolve_magic_shelf_sort

    engine, _difficulty = sortable_library
    config = SimpleNamespace(config_sortable_custom_columns="2086,999")
    rejected = (
        ("id; DROP TABLE books", [ColumnDefinition(2086)]),
        ("1) OR (1=1", [ColumnDefinition(2086)]),
        ("cc-٢٠٨٦-asc", [ColumnDefinition(2086)]),
        ("cc-999-asc", [ColumnDefinition(2086)]),
        ("cc-2086-desc", [ColumnDefinition(2086, deleted=True)]),
        ("cc-2086-asc", [ColumnDefinition(2086, "text")]),
        ("cc-2086-desc", [ColumnDefinition(2086, multiple=True)]),
    )
    statements = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    session = sessionmaker(bind=engine)()
    try:
        for raw_key, live_columns in rejected:
            resolved = resolve_magic_shelf_sort(raw_key, config, live_columns)
            assert resolved.key == "new"
            assert resolved.join == ()
            assert [row[0] for row in session.query(db.Books.id)
                    .order_by(*resolved.order_by).all()] == [6, 5, 4, 3, 2, 1]
    finally:
        session.close()
        event.remove(engine, "before_cursor_execute", capture_statement)

    emitted_sql = "\n".join(statements)
    assert all(raw_key not in emitted_sql for raw_key, _columns in rejected)
    assert "DROP TABLE" not in emitted_sql
    assert "OR (1=1" not in emitted_sql


def test_only_scalar_numeric_and_datetime_columns_can_be_persisted(tmp_path):
    """A legacy app.db migrates, then refuses text and multi-value selections."""
    from cps.config_sql import _Settings, _migrate_table
    from cps.custom_column_sort import eligible_columns, persist_configured_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-app.db'}")
    metadata = MetaData()
    Table(
        "settings",
        metadata,
        *(column.copy() for column in _Settings.__table__.columns
          if column.name != "config_sortable_custom_columns"),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO settings (id) VALUES (1)"))

    session = sessionmaker(bind=engine)()
    try:
        _migrate_table(session, _Settings)
        config_row = session.query(_Settings).one()
        columns = [
            ColumnDefinition(2, "int", name="Pages"),
            ColumnDefinition(3, "float", name="Score"),
            ColumnDefinition(4, "datetime", name="Started"),
            ColumnDefinition(5, "text", name="Mood"),
            ColumnDefinition(6, "int", multiple=True, name="Multiple numbers"),
        ]

        assert [column.id for column in eligible_columns(columns)] == [2, 3, 4]
        persist_configured_columns(config_row, ["6", "5", "2", "2", "hostile"], columns)
        session.commit()
        session.expire_all()

        assert session.execute(text(
            "SELECT config_sortable_custom_columns FROM settings WHERE id = 1"
        )).scalar_one() == "2"
    finally:
        session.close()
        engine.dispose()
