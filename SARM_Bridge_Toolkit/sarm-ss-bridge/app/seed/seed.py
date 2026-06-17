"""Seed script — builds the sample SQLite database.

Creates a small, legible dataset for demo purposes (CLAUDE.md §6.4):
    - 3 certifiers
    - ~12 scope items across 2 resource groups
    - decision options: keep_membership / remove_membership

Run with:
    python -m app.seed.seed
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger("sarm.bridge.seed")

# Make the parent package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


DB_PATH = "data/sample.sqlite"

CREATORS = [
    {"id": "user:dhutchins", "name": "David Hutchins"},
    {"id": "user:kmurphy", "name": "Kellen Murphy"},
    {"id": "user:cgriffin", "name": "Carter R. Griffin"},
]

# Scope items: (id, subject_id, subject_label, certifier_id,
#               resource_id, resource_label, decision_prompt, context_data)
SCOPE_ITEMS = [
    # Group: Finance Readers (Grouper)
    (
        "item-001",
        "user:jdoe",
        "Jane Doe",
        "user:dhutchins",
        "group:finance-readers",
        "Finance Readers (Grouper)",
        "Please review this group membership.",
        json.dumps({"memberSince": "2023-04-12", "lastAccessAt": "2026-04-30T14:22:00Z", "addedBy": "user:hradmin"}),
    ),
    (
        "item-002",
        "user:asmith",
        "Alice Smith",
        "user:dhutchins",
        "group:finance-readers",
        "Finance Readers (Grouper)",
        "Please review this group membership.",
        json.dumps({"memberSince": "2024-01-15", "lastAccessAt": "2026-05-10T09:00:00Z", "addedBy": "user:dhutchins"}),
    ),
    (
        "item-003",
        "user:bwilson",
        "Bob Wilson",
        "user:kmurphy",
        "group:finance-readers",
        "Finance Readers (Grouper)",
        "Please review this group membership.",
        json.dumps({"memberSince": "2025-06-01", "lastAccessAt": "2026-03-15T11:30:00Z", "addedBy": "user:hradmin"}),
    ),
    (
        "item-004",
        "user:cjones",
        "Carol Jones",
        "user:kmurphy",
        "group:finance-readers",
        "Finance Readers (Grouper)",
        "Please review this group membership.",
        json.dumps({"memberSince": "2022-11-20", "lastAccessAt": "2026-05-01T08:00:00Z", "addedBy": "user:kmurphy"}),
    ),

    # Group: App Admins
    (
        "item-005",
        "user:jdoe",
        "Jane Doe",
        "user:kmurphy",
        "group:app-admins",
        "App Admins (Grouper)",
        "Please review this admin role.",
        json.dumps({"memberSince": "2024-03-01", "lastAccessAt": "2026-05-12T16:45:00Z", "addedBy": "user:sysadmin"}),
    ),
    (
        "item-006",
        "user:msmith",
        "Mike Smith",
        "user:kmurphy",
        "group:app-admins",
        "App Admins (Grouper)",
        "Please review this admin role.",
        json.dumps({"memberSince": "2023-08-15", "lastAccessAt": "2026-05-14T10:00:00Z", "addedBy": "user:kmurphy"}),
    ),
    (
        "item-007",
        "user:alee",
        "Amy Lee",
        "user:cgriffin",
        "group:app-admins",
        "App Admins (Grouper)",
        "Please review this admin role.",
        json.dumps({"memberSince": "2025-01-10", "lastAccessAt": "2026-02-28T14:00:00Z", "addedBy": "user:sysadmin"}),
    ),

    # Group: Service Accounts
    (
        "item-008",
        "user:svc-deploy",
        "Deployment Service Account",
        "user:dhutchins",
        "group:svc-deploy",
        "Deployment Service Account",
        "Please review this service account access.",
        json.dumps({"created": "2024-06-01", "lastUsed": "2026-05-15T03:00:00Z", "addedBy": "user:sysadmin"}),
    ),
    (
        "item-009",
        "user:svc-report",
        "Reporting Service Account",
        "user:dhutchins",
        "group:svc-report",
        "Reporting Service Account",
        "Please review this service account access.",
        json.dumps({"created": "2023-01-15", "lastUsed": "2026-05-14T22:00:00Z", "addedBy": "user:dhutchins"}),
    ),

    # Individual items (no group)
    (
        "item-010",
        "user:tgarcia",
        "Tom Garcia",
        "user:cgriffin",
        None,
        None,
        "Please review this user's access.",
        json.dumps({"lastLogin": "2025-12-01T09:00:00Z", "addedBy": "user:cgriffin"}),
    ),
    (
        "item-011",
        "user:lpark",
        "Lisa Park",
        "user:cgriffin",
        None,
        None,
        "Please review this user's access.",
        json.dumps({"lastLogin": "2026-05-10T14:30:00Z", "addedBy": "user:cgriffin"}),
    ),
    (
        "item-012",
        "user:rchen",
        "Robert Chen",
        "user:dhutchins",
        None,
        None,
        "Please review this user's access.",
        json.dumps({"lastLogin": "2024-06-15T11:00:00Z", "addedBy": "user:dhutchins"}),
    ),
]

# SQL to create the scope_items table
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scope_items (
    id              TEXT PRIMARY KEY,
    subject_id      TEXT NOT NULL,
    subject_label   TEXT NOT NULL,
    certifier_hint  TEXT NOT NULL,
    resource_id     TEXT,
    resource_label  TEXT,
    decision_prompt TEXT,
    context_data    TEXT,
    decision_options TEXT NOT NULL DEFAULT '[]'
);
"""

# SQL to create the decisions table — persisted decisions per §5.4
CREATE_TABLE_DECISIONS = """
CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    scope_item_id   TEXT NOT NULL,
    decision        TEXT NOT NULL,
    certifier_id    TEXT NOT NULL,
    decided_at      TEXT NOT NULL,
    channel         TEXT,
    comment         TEXT,
    delegated_from  TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(scope_item_id) REFERENCES scope_items(id)
);
"""

CREATE_INDEX_DECISIONS = """
CREATE INDEX IF NOT EXISTS idx_decisions_scope_item
    ON decisions(scope_item_id);
"""

# SQL to create the return_channels table — stores AE returnChannel
# keyed by bearer token per capability exchange (§4.7).
CREATE_TABLE_RETURN_CHANNELS = """
CREATE TABLE IF NOT EXISTS return_channels (
    bearer_token    TEXT PRIMARY KEY,
    return_channel  TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""

# Decision options stored as JSON
DECISION_OPTIONS_JSON = json.dumps([
    {"value": "keep_membership", "label": "Approve membership"},
    {"value": "remove_membership", "label": "Revoke membership"},
])

# SQL action for remove_membership (dry-run by default)
REMOVE_MEMBER_SQL = "DELETE FROM scope_items WHERE id = :scopeItemId"


def build_database(db_path: str) -> None:
    """Create the sample database and seed it."""
    logger.info("Seed — opening database at %s", db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create tables
    logger.info("Seed — CREATE TABLE scope_items")
    cur.execute(CREATE_TABLE)
    logger.info("Seed — CREATE TABLE decisions")
    cur.execute(CREATE_TABLE_DECISIONS)
    logger.info("Seed — CREATE INDEX idx_decisions_scope_item")
    cur.execute(CREATE_INDEX_DECISIONS)
    logger.info("Seed — CREATE TABLE return_channels")
    cur.execute(CREATE_TABLE_RETURN_CHANNELS)

    # Insert scope items
    logger.info("Seed — inserting %d scope items", len(SCOPE_ITEMS))
    for i, item in enumerate(SCOPE_ITEMS):
        (id_, subject_id, subject_label, certifier_hint,
         resource_id, resource_label, decision_prompt, context_data) = item

        cur.execute(
            """INSERT OR IGNORE INTO scope_items
               (id, subject_id, subject_label, certifier_hint,
                resource_id, resource_label, decision_prompt,
                context_data, decision_options)
               VALUES (:id, :subject_id, :subject_label, :certifier_hint,
                       :resource_id, :resource_label, :decision_prompt,
                       :context_data, :decision_options)""",
            {
                "id": id_,
                "subject_id": subject_id,
                "subject_label": subject_label,
                "certifier_hint": certifier_hint,
                "resource_id": resource_id,
                "resource_label": resource_label,
                "decision_prompt": decision_prompt,
                "context_data": context_data,
                "decision_options": DECISION_OPTIONS_JSON,
            },
        )
        if (i + 1) % 5 == 0 or i == len(SCOPE_ITEMS) - 1:
            logger.info("Seed — inserted %d/%d scope items", i + 1, len(SCOPE_ITEMS))

    conn.commit()
    count = cur.execute("SELECT COUNT(*) FROM scope_items").fetchone()[0]
    logger.info("Seed — SELECT COUNT scope_items → %d rows", count)
    logger.info("Seed — done: %d scope items in %s", count, db_path)
    print(f"Seeded {count} scope items into {db_path}")
    conn.close()


if __name__ == "__main__":
    # Use the path relative to this file's parent (app/seed/)
    package_root = Path(__file__).resolve().parent.parent.parent
    db_path = package_root / DB_PATH
    build_database(str(db_path))
