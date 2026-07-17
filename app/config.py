"""Configuration - one place that reads the environment.

python-dotenv loads the .env file into environment variables once, at import
time.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # reads .env from the working directory; no-op if absent

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# The reconcile judge gets a STRONGER model than the workhorse: the joint
# batch decision is the highest-stakes call in the write path (a miss puts a
# contradiction live), it runs offline, and it is one call per source.
# Measured 2026-07-11: mini missed a contradiction the prompt named verbatim
# ("pulls his stop-loss" vs "consistent stop-loss routine") - model strength,
# not more prompt rules, was the remaining lever.
RECONCILE_JUDGE_MODEL = os.getenv("RECONCILE_JUDGE_MODEL", "gpt-4o")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536  # must match vector(1536) in schema.sql - model and column are a locked pair

# Owner connection BYPASSES RLS (it owns the tables) -> migration jobs only.
# App connection (tal_app role) is subject to RLS -> all request-scoped work.
# Port 5434: another local Postgres already owns 5433 on this machine.
DSN_OWNER = os.getenv("DSN_OWNER", "postgresql://postgres:postgres@localhost:5434/tal")
DSN_APP = os.getenv("DSN_APP", "postgresql://tal_app:tal_app@localhost:5434/tal")
