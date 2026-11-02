"""Tests for Quick Notes & Tag Search Domain."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.database.connection import get_db_connection
from src.database.migrations import run_migrations
from src.domain.notes import (
    archive_note,
    delete_note,
    get_note,
    list_notes,
    save_note,
    search_notes,
    update_note,
)


class TestNotesDomain(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_notes.db")
        run_migrations(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_note_minimal(self):
        note_id = save_note(
            content="Simple quick note without title or tags",
            db_path=self.db_path,
        )
        self.assertIsInstance(note_id, int)
        self.assertGreater(note_id, 0)

        note = get_note(note_id, db_path=self.db_path)
        self.assertIsNotNone(note)
        self.assertEqual(note["id"], note_id)
        self.assertIsNone(note["title"])
        self.assertEqual(note["content"], "Simple quick note without title or tags")
        self.assertIsNone(note["category"])
        self.assertIsNone(note["category_id"])
        self.assertEqual(note["tags"], [])
        self.assertIsNone(note["archived_at"])
        self.assertIsNotNone(note["created_at"])
        self.assertIsNotNone(note["updated_at"])

    def test_save_note_full(self):
        note_id = save_note(
            title=" Architecture Decision ",
            content="Adopt SQLite with WAL mode for local storage.",
            category=" Architecture ",
            tags=[" SQLite ", "WAL", "DATABASE"],
            db_path=self.db_path,
        )
        self.assertGreater(note_id, 0)

        note = get_note(note_id, db_path=self.db_path)
        self.assertIsNotNone(note)
        self.assertEqual(note["title"], "Architecture Decision")
        self.assertEqual(note["content"], "Adopt SQLite with WAL mode for local storage.")
        self.assertEqual(note["category"], "Architecture")
        self.assertIsNotNone(note["category_id"])
        # Tags should be lowercased and stripped
        self.assertEqual(note["tags"], ["sqlite", "wal", "database"])

        # Verify category table entry
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT domain, name FROM categories WHERE id = ?;",
                (note["category_id"],),
            )
            cat_row = cursor.fetchone()
            self.assertIsNotNone(cat_row)
            self.assertEqual(cat_row["domain"], "notes")
            self.assertEqual(cat_row["name"], "Architecture")
        finally:
            conn.close()

    def test_save_note_validation(self):
        # Empty or whitespace-only content
        with self.assertRaises(ValueError):
            save_note(content="", db_path=self.db_path)

        with self.assertRaises(ValueError):
            save_note(content="   ", db_path=self.db_path)

        with self.assertRaises(ValueError):
            save_note(content=None, db_path=self.db_path)

        with self.assertRaises(ValueError):
            save_note(content=12345, db_path=self.db_path)

    def test_get_note_not_found(self):
        note = get_note(999999, db_path=self.db_path)
        self.assertIsNone(note)

    def test_category_reuse_case_insensitive(self):
        id1 = save_note(
            content="First note",
            category="Ideas",
            db_path=self.db_path,
        )
        id2 = save_note(
            content="Second note",
            category="ideas",
            db_path=self.db_path,
        )
        note1 = get_note(id1, db_path=self.db_path)
        note2 = get_note(id2, db_path=self.db_path)
        self.assertEqual(note1["category_id"], note2["category_id"])

        # Check only 1 category row was created
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM categories WHERE domain = 'notes';")
            count = cursor.fetchone()["cnt"]
            self.assertEqual(count, 1)
        finally:
            conn.close()

    def test_list_notes(self):
        save_note(content="Note 1", tags=["tag1"], db_path=self.db_path)
        save_note(content="Note 2", category="Work", tags=["tag2"], db_path=self.db_path)
        save_note(content="Note 3", category="Work", tags=["tag1", "tag2"], db_path=self.db_path)

        all_notes = list_notes(db_path=self.db_path)
        self.assertEqual(len(all_notes), 3)

        work_notes = list_notes(category="Work", db_path=self.db_path)
        self.assertEqual(len(work_notes), 2)
        for n in work_notes:
            self.assertEqual(n["category"], "Work")

        tag1_notes = list_notes(tag="tag1", db_path=self.db_path)
        self.assertEqual(len(tag1_notes), 2)

        limited = list_notes(limit=2, db_path=self.db_path)
        self.assertEqual(len(limited), 2)

    def test_archive_note(self):
        note_id = save_note(content="To be archived", db_path=self.db_path)
        self.assertTrue(archive_note(note_id, db_path=self.db_path))

        # Archiving again returns True (already archived or re-archived)
        self.assertTrue(archive_note(note_id, db_path=self.db_path))

        # Archiving non-existent note returns False
        self.assertFalse(archive_note(9999, db_path=self.db_path))

        # Verify excluded from normal list
        notes = list_notes(db_path=self.db_path)
        self.assertEqual(len(notes), 0)

        # Included when include_archived=True
        notes_with_archived = list_notes(include_archived=True, db_path=self.db_path)
        self.assertEqual(len(notes_with_archived), 1)
        self.assertIsNotNone(notes_with_archived[0]["archived_at"])

    def test_search_notes_by_keyword(self):
        save_note(
            title="Meeting with Client",
            content="Discussed Q4 roadmap and deliverables.",
            db_path=self.db_path,
        )
        save_note(
            title="Grocery List",
            content="Apples, milk, bread, client gifts.",
            db_path=self.db_path,
        )
        save_note(
            title="Random Thought",
            content="Thinking about quantum computing.",
            db_path=self.db_path,
        )

        # Keyword in title
        res = search_notes(keyword="Meeting", db_path=self.db_path)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "Meeting with Client")

        # Keyword in content
        res = search_notes(keyword="roadmap", db_path=self.db_path)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "Meeting with Client")

        # Keyword matching both
        res = search_notes(keyword="client", db_path=self.db_path)
        self.assertEqual(len(res), 2)

    def test_search_notes_by_tag_exact_boundary(self):
        save_note(title="Art note", content="About modern art", tags=["art"], db_path=self.db_path)
        save_note(title="Smart note", content="About smart homes", tags=["smart"], db_path=self.db_path)
        save_note(title="Party note", content="Party planning", tags=["party", "art"], db_path=self.db_path)

        art_notes = search_notes(tag="art", db_path=self.db_path)
        # Should match 'art' and 'party' notes, but NOT 'smart'
        self.assertEqual(len(art_notes), 2)
        titles = {n["title"] for n in art_notes}
        self.assertEqual(titles, {"Art note", "Party note"})

    def test_search_notes_special_characters_and_injection(self):
        save_note(
            title="100% Guaranteed Plan",
            content="Growth of 50% year_over_year with 100% commitment.",
            tags=["finance%"],
            db_path=self.db_path,
        )
        save_note(
            title="Another Plan",
            content="Normal growth with year-over-year progress.",
            tags=["growth"],
            db_path=self.db_path,
        )

        # Literal '%' search should only match the note with literal '%'
        res = search_notes(keyword="100%", db_path=self.db_path)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "100% Guaranteed Plan")

        # Literal '_' search should match 'year_over_year' not 'year-over-year'
        res = search_notes(keyword="year_over_year", db_path=self.db_path)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "100% Guaranteed Plan")

        # SQL injection attempt in keyword
        res = search_notes(keyword="' OR '1'='1' --", db_path=self.db_path)
        self.assertEqual(len(res), 0)

        # SQL injection attempt in tag
        res = search_notes(tag="finance%' OR '1'='1", db_path=self.db_path)
        self.assertEqual(len(res), 0)

    def test_search_notes_combined(self):
        save_note(
            title="Sprint Planning",
            content="Review sprint backlog and prioritize bugs.",
            category="Dev",
            tags=["sprint", "jira"],
            db_path=self.db_path,
        )
        save_note(
            title="Product Backlog",
            content="Groom backlog with stakeholders.",
            category="Product",
            tags=["sprint", "roadmap"],
            db_path=self.db_path,
        )

        # Match keyword + category
        res = search_notes(keyword="backlog", category="Dev", db_path=self.db_path)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "Sprint Planning")

        # Match keyword + tag
        res = search_notes(keyword="backlog", tag="jira", db_path=self.db_path)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "Sprint Planning")

        # Mismatch tag
        res = search_notes(keyword="backlog", category="Dev", tag="roadmap", db_path=self.db_path)
        self.assertEqual(len(res), 0)

    def test_update_note(self):
        note_id = save_note(
            title="Old Title",
            content="Old Content",
            category="Old Cat",
            tags=["old"],
            db_path=self.db_path,
        )
        old_note = get_note(note_id, db_path=self.db_path)

        success = update_note(
            note_id,
            title="New Title",
            content="New Content",
            category="New Cat",
            tags=["new", "updated"],
            db_path=self.db_path,
        )
        self.assertTrue(success)

        updated_note = get_note(note_id, db_path=self.db_path)
        self.assertEqual(updated_note["title"], "New Title")
        self.assertEqual(updated_note["content"], "New Content")
        self.assertEqual(updated_note["category"], "New Cat")
        self.assertEqual(updated_note["tags"], ["new", "updated"])
        self.assertGreaterEqual(updated_note["updated_at"], old_note["updated_at"])

        # Update non-existent
        self.assertFalse(update_note(99999, title="Whatever", db_path=self.db_path))

        # Update with empty content raises ValueError
        with self.assertRaises(ValueError):
            update_note(note_id, content="", db_path=self.db_path)

        # Update with invalid fields raises ValueError
        with self.assertRaises(ValueError):
            update_note(note_id, invalid_field="val", db_path=self.db_path)

    def test_delete_note(self):
        note_id = save_note(content="To be deleted", db_path=self.db_path)
        self.assertTrue(delete_note(note_id, db_path=self.db_path))
        self.assertIsNone(get_note(note_id, db_path=self.db_path))

        # Deleting again returns False
        self.assertFalse(delete_note(note_id, db_path=self.db_path))
        self.assertFalse(delete_note(99999, db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
