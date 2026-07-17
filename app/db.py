"""Database access - two connection factories, two trust levels.

Connections commit on clean exit and roll back if an exception escapes.

The whole isolation design hangs on the split below:
- owner_conn():   postgres role, table owner, BYPASSES RLS. Migrations only.
- student_conn(): tal_app role, RLS enforced, scoped to ONE student for the
                  duration of ONE transaction.
"""

from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app import config


@contextmanager
def owner_conn():
    """Connection as the table owner. RLS DOES NOT APPLY here.

    Use for: migration jobs, test seeding. Never for request handling -
    if the app served requests through this role, isolation would silently
    evaporate (the owner sees every row, no error, no warning).
    """
    with psycopg.connect(config.DSN_OWNER) as conn:
        register_vector(conn)  # register the pgvector column type
        yield conn


@contextmanager
def student_conn(student_id: str):
    """Connection scoped to one student. RLS is live.

    set_config(..., is_local=True) is SET LOCAL: the setting dies with the
    transaction. That, not plain SET, is what makes this safe behind a
    transaction-mode connection pooler - a plain SET would leak the student
    id to whoever gets this pooled connection next.
    """
    with psycopg.connect(config.DSN_APP) as conn:
        register_vector(conn)
        with conn.transaction():
            conn.execute(
                "SELECT set_config('app.student_id', %s, true)",
                (str(student_id),),
            )
            yield conn
        # transaction commits here; app.student_id evaporates with it
