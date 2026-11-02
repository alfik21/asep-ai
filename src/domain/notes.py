"""Quick Notes & Tag Search domain logic.

Provides creation, lookup, tag-based & text search, updating, archiving,
and deletion of notes. Note contents are treated as untrusted user data.
Tags are stored as JSON arrays in SQLite and searchable via JSON functions.
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Optional

from src.database.connection import get_db_connection

UPDATABLE_FIELDS = {"title", "content", "category", "tags", "archived", "archived_at"}


def _get_or_create_category_id(
    category_name: Optional[str],
    cursor: sqlite3.Cursor,
) -> Optional[int]:
    """Finds or creates a category with domain='notes'.

    Args:
        category_name: Name of the category.
        cursor: Active database cursor within a transaction.

    Returns:
        Category ID if found or created, or None if category_name is empty/None.
    """
    if category_name is None:
        return None
    cleaned = category_name.strip()
    if not cleaned:
        return None

    cursor.execute(
        "SELECT id FROM categories WHERE domain = 'notes' AND LOWER(name) = LOWER(?);",
        (cleaned,),
    )
    row = cursor.fetchone()
    if row is not None:
        return row[0] if isinstance(row, tuple) else row["id"]

    cursor.execute(
        "INSERT INTO categories (domain, name, is_active) VALUES ('notes', ?, 1);",
        (cleaned,),
    )
    return cursor.lastrowid


def _escape_like(keyword: str) -> str:
    """Escapes SQLite LIKE wildcards (% and _) and backslash escape characters."""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_note_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converts a database row into a note dictionary with parsed tags list."""
    d = dict(row)
    tags_raw = d.pop("tags_json", None)
    if tags_raw:
        try:
            tags = json.loads(tags_raw)
            if not isinstance(tags, list):
                tags = [str(tags)]
        except (json.JSONDecodeError, TypeError):
            tags = []
    else:
        tags = []
    d["tags"] = tags
    return d


def save_note(
    content: str,
    title: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Saves a new note in the database.

    Security Note:
        Note content and title are treated as untrusted user data. They are stored
        using parameterized queries to prevent SQL injection. Downstream renderers
        (e.g., LLM context, UI templates) must sanitize or isolate these fields.

    Args:
        content: Required note body, must be a non-empty string.
        title: Optional note title.
        category: Optional category name (auto-created in categories with domain='notes').
        tags: Optional list of tags. Serialized as a lowercase stripped JSON list.
        db_path: Optional SQLite database file path.

    Returns:
        int: The auto-generated note ID.

    Raises:
        ValueError: If content is empty or not a string.
    """
    if not content or not isinstance(content, str) or not content.strip():
        raise ValueError("Note content must be a non-empty string.")

    cleaned_title: Optional[str] = None
    if title is not None:
        cleaned_t = title.strip() if isinstance(title, str) else str(title).strip()
        cleaned_title = cleaned_t if cleaned_t else None

    # Serialize tags as JSON list with lowercased, stripped tags
    if tags is not None:
        if not isinstance(tags, (list, tuple, set)):
            raise ValueError("Tags must be a list, tuple, or set of strings.")
        parsed_tags = [str(t).strip().lower() for t in tags if t is not None and str(t).strip()]
        tags_json = json.dumps(parsed_tags)
    else:
        tags_json = json.dumps([])

    now_utc_iso = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cat_id = _get_or_create_category_id(category, cursor)
            cursor.execute(
                """
                INSERT INTO notes (title, content, category_id, tags_json, created_at, updated_at, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL);
                """,
                (cleaned_title, content.strip(), cat_id, tags_json, now_utc_iso, now_utc_iso),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_note(note_id: int, db_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Retrieves a note by its ID.

    Args:
        note_id: ID of the note.
        db_path: Optional SQLite database file path.

    Returns:
        dict representation of the note with parsed tags, or None if not found.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                n.id,
                n.title,
                n.content,
                n.category_id,
                c.name AS category,
                n.tags_json,
                n.created_at,
                n.updated_at,
                n.archived_at
            FROM notes n
            LEFT JOIN categories c ON n.category_id = c.id
            WHERE n.id = ?;
            """,
            (note_id,),
        )
        row = cursor.fetchone()
        return _row_to_note_dict(row) if row is not None else None
    finally:
        conn.close()


def list_notes(
    category: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
    include_archived: bool = False,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Lists notes filtered by category, tag, or archival status.

    Args:
        category: Optional category name filter (case-insensitive).
        tag: Optional tag filter (case-insensitive exact tag match).
        limit: Maximum number of notes to return (defaults to 50).
        include_archived: If True, includes archived notes. Defaults to False.
        db_path: Optional SQLite database file path.

    Returns:
        List of note dictionaries ordered by creation time descending.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        query = [
            """
            SELECT 
                n.id,
                n.title,
                n.content,
                n.category_id,
                c.name AS category,
                n.tags_json,
                n.created_at,
                n.updated_at,
                n.archived_at
            FROM notes n
            LEFT JOIN categories c ON n.category_id = c.id
            WHERE 1=1
            """
        ]
        params: list[Any] = []

        if not include_archived:
            query.append("AND n.archived_at IS NULL")

        if category is not None and str(category).strip():
            query.append("AND LOWER(c.name) = LOWER(?)")
            params.append(str(category).strip())

        if tag is not None and str(tag).strip():
            query.append(
                "AND n.tags_json IS NOT NULL AND json_valid(n.tags_json) "
                "AND EXISTS (SELECT 1 FROM json_each(n.tags_json) WHERE LOWER(value) = LOWER(?))"
            )
            params.append(str(tag).strip())

        query.append("ORDER BY n.created_at DESC, n.id DESC")
        query.append("LIMIT ?;")
        params.append(max(1, limit))

        cursor.execute(" ".join(query), params)
        return [_row_to_note_dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def search_notes(
    keyword: Optional[str] = None,
    tag: Optional[str] = None,
    category: Optional[str] = None,
    include_archived: bool = False,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Searches notes by keyword (across title & content), tag, and category.

    All parameters are safely parameterized to prevent SQL injection and properly
    handle special characters like '%', '_', and quotes.

    Args:
        keyword: Substring search across title and content.
        tag: Exact tag match in the note's JSON tags array.
        category: Category name filter (case-insensitive).
        include_archived: If True, includes archived notes. Defaults to False.
        db_path: Optional SQLite database file path.

    Returns:
        List of matching note dictionaries ordered by creation time descending.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        query = [
            """
            SELECT 
                n.id,
                n.title,
                n.content,
                n.category_id,
                c.name AS category,
                n.tags_json,
                n.created_at,
                n.updated_at,
                n.archived_at
            FROM notes n
            LEFT JOIN categories c ON n.category_id = c.id
            WHERE 1=1
            """
        ]
        params: list[Any] = []

        if not include_archived:
            query.append("AND n.archived_at IS NULL")

        if keyword is not None and str(keyword).strip():
            escaped_kw = _escape_like(str(keyword).strip())
            like_pattern = f"%{escaped_kw}%"
            query.append("(n.title LIKE ? ESCAPE '\\' OR n.content LIKE ? ESCAPE '\\')")
            # Replace the last WHERE 1=1 clause or append AND
            query[-1] = "AND " + query[-1]
            params.extend([like_pattern, like_pattern])

        if tag is not None and str(tag).strip():
            query.append(
                "AND n.tags_json IS NOT NULL AND json_valid(n.tags_json) "
                "AND EXISTS (SELECT 1 FROM json_each(n.tags_json) WHERE LOWER(value) = LOWER(?))"
            )
            params.append(str(tag).strip())

        if category is not None and str(category).strip():
            query.append("AND LOWER(c.name) = LOWER(?)")
            params.append(str(category).strip())

        query.append("ORDER BY n.created_at DESC, n.id DESC;")

        cursor.execute(" ".join(query), params)
        return [_row_to_note_dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_note(note_id: int, db_path: Optional[str] = None, **kwargs: Any) -> bool:
    """Updates fields of an existing note.

    Args:
        note_id: ID of the note to update.
        db_path: Optional SQLite database file path.
        **kwargs: Fields to update ('title', 'content', 'category', 'tags', 'archived', 'archived_at').

    Returns:
        bool: True if note was updated, False if note does not exist.

    Raises:
        ValueError: If invalid fields or values are passed.
    """
    if not kwargs:
        return False

    invalid_fields = set(kwargs.keys()) - UPDATABLE_FIELDS
    if invalid_fields:
        raise ValueError(
            f"Invalid fields for update: {sorted(invalid_fields)}. Allowed: {sorted(UPDATABLE_FIELDS)}"
        )

    assignments: list[str] = []
    params: list[Any] = []
    now_utc_iso = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()

            if "content" in kwargs:
                content = kwargs["content"]
                if not content or not isinstance(content, str) or not content.strip():
                    raise ValueError("Note content must be a non-empty string.")
                assignments.append("content = ?")
                params.append(content.strip())

            if "title" in kwargs:
                title = kwargs["title"]
                cleaned_title = None
                if title is not None:
                    cleaned_t = title.strip() if isinstance(title, str) else str(title).strip()
                    cleaned_title = cleaned_t if cleaned_t else None
                assignments.append("title = ?")
                params.append(cleaned_title)

            if "category" in kwargs:
                cat_val = kwargs["category"]
                cat_id = _get_or_create_category_id(cat_val, cursor)
                assignments.append("category_id = ?")
                params.append(cat_id)

            if "tags" in kwargs:
                tags = kwargs["tags"]
                if tags is not None:
                    if not isinstance(tags, (list, tuple, set)):
                        raise ValueError("Tags must be a list, tuple, or set of strings.")
                    parsed_tags = [str(t).strip().lower() for t in tags if t is not None and str(t).strip()]
                    tags_json = json.dumps(parsed_tags)
                else:
                    tags_json = json.dumps([])
                assignments.append("tags_json = ?")
                params.append(tags_json)

            if "archived" in kwargs:
                archived = bool(kwargs["archived"])
                assignments.append("archived_at = ?")
                params.append(now_utc_iso if archived else None)
            elif "archived_at" in kwargs:
                assignments.append("archived_at = ?")
                params.append(kwargs["archived_at"])

            if not assignments:
                return False

            assignments.append("updated_at = ?")
            params.append(now_utc_iso)

            params.append(note_id)
            cursor.execute(
                f"UPDATE notes SET {', '.join(assignments)} WHERE id = ?;",
                params,
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def archive_note(note_id: int, db_path: Optional[str] = None) -> bool:
    """Archives a note by setting its archived_at timestamp.

    Args:
        note_id: ID of the note to archive.
        db_path: Optional SQLite database file path.

    Returns:
        bool: True if note was found and archived, False otherwise.
    """
    now_utc_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE notes SET archived_at = ?, updated_at = ? WHERE id = ?;",
                (now_utc_iso, now_utc_iso, note_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def delete_note(note_id: int, db_path: Optional[str] = None) -> bool:
    """Deletes a note permanently from the database.

    Args:
        note_id: ID of the note to delete.
        db_path: Optional SQLite database file path.

    Returns:
        bool: True if note was deleted, False if note did not exist.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM notes WHERE id = ?;", (note_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()
