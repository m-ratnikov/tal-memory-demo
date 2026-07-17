"""The leakage test: proof that isolation is enforced by the DATABASE.

No OpenAI, no HTTP - pure DB. That is the point: even if every line of app
code were buggy, these assertions would still hold, because RLS lives below
the app.

Requires the docker-compose Postgres to be up. Run: uv run pytest
"""

import pytest
from pgvector import Vector

from app.db import owner_conn, student_conn

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
MARKER = "isolation-test"
ZERO_VEC = Vector([0.0] * 1536)  # embeddings are irrelevant to isolation


@pytest.fixture(scope="module", autouse=True)
def seed_one_memory_per_student():
    """Seed before the module's tests, clean up after. Seeding uses the OWNER
    connection - the same bypass a migration uses. autouse=True applies it to
    every test in the module."""
    with owner_conn() as conn:
        conn.execute("DELETE FROM memories WHERE content LIKE %s", (f"{MARKER}%",))
        for student_id, name in ((ALICE, "alice"), (BOB, "bob")):
            conn.execute(
                "INSERT INTO memories (student_id, kind, content, embedding) "
                "VALUES (%s, 'trait', %s, %s)",
                (student_id, f"{MARKER}: {name} fact", ZERO_VEC),
            )
    yield  # tests run here
    with owner_conn() as conn:
        conn.execute("DELETE FROM memories WHERE content LIKE %s", (f"{MARKER}%",))


def test_a_student_sees_only_their_own_rows():
    """An unfiltered SELECT in Alice's session returns Alice's rows. Only."""
    with student_conn(ALICE) as conn:
        rows = conn.execute(
            "SELECT content FROM memories WHERE content LIKE %s", (f"{MARKER}%",)
        ).fetchall()
    assert [r[0] for r in rows] == [f"{MARKER}: alice fact"]


def test_asking_for_another_students_rows_returns_nothing():
    """The strong claim: EXPLICITLY querying for Bob's rows inside Alice's
    session yields zero rows. The WHERE clause asks for Bob; RLS answers no.
    App-layer bugs cannot leak what the database refuses to show."""
    with student_conn(ALICE) as conn:
        rows = conn.execute(
            "SELECT content FROM memories WHERE student_id = %s", (BOB,)
        ).fetchall()
    assert rows == []


def test_owner_bypasses_rls_which_is_why_the_app_must_not_connect_as_owner():
    """Documenting the trap as an executable fact: the owner sees BOTH rows.
    This is not a bug - it is why DSN_APP exists and why serving requests
    through the owner role would silently destroy the isolation story."""
    with owner_conn() as conn:
        rows = conn.execute(
            "SELECT content FROM memories WHERE content LIKE %s ORDER BY content",
            (f"{MARKER}%",),
        ).fetchall()
    assert len(rows) == 2
