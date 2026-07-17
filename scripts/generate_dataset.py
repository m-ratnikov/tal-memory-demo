"""Synthetic dataset generator: taxonomy -> world models -> rendered sources.

Run:  uv run python -m scripts.generate_dataset
Writes: data/truth/*.json     - world models (ground truth, kept for audit)
        data/students/*.json  - import bundles (reports + transcripts)
        data/golden/cases.json - eval cases derived MECHANICALLY from truth

Design principles this encodes:
- TAXONOMY, not free-form: students are generated from explicit dimensions
  (archetype x timeline shape x failure-mode flags). Diversity comes from
  structure; "LLM, give me 100 cases" collapses into paraphrases of three.
- GROUND TRUTH BY CONSTRUCTION: the world model (per-student topic chains
  with versioned statements) is generated FIRST; documents are rendered FROM
  it; golden cases derive from it mechanically. The pipeline under test never
  sees the truth - it must recover it from the rendered prose. No circularity,
  no labeling bottleneck.
- OVERSAMPLE KNOWN FAILURE MODES: every "improvement" student ships an
  out-of-order conversation (dated before the follow-up report, ingested
  after) - the exact case that leaked live on 2026-07-10. Numeric-score
  changes, sibling near-duplicates and compound phrasing are dimension flags.
- ARTIFACTS ARE CHECKED IN: generation runs once; evals stay reproducible
  and free. Regeneration is a deliberate act, not a side effect.

The generator LLM runs at temperature 0.9 (we WANT variety in prose) and is
separate from every model the pipeline uses - the system under test must not
author its own exam.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app import config as _config  # noqa: F401 - importing it loads .env
from app import obs

log = logging.getLogger(__name__)

gen_llm = ChatOpenAI(model="gpt-4o", temperature=0.9)

DATA = Path("data")

# ---------------------------------------------------------------------------
# 1. THE TAXONOMY - dimensions are explicit, assignment is deterministic.
# ---------------------------------------------------------------------------

Shape = Literal["steady", "improvement", "relapse", "mixed"]


@dataclass(frozen=True)
class StudentSpec:
    external_id: str
    name: str
    archetype: str
    shape: Shape
    # Anchor dates: stage 0 / 1 / 2 of this student's timeline.
    dates: tuple[date, date, date]
    numeric_scores: bool = False   # include an "N out of 10" score that changes
    near_dup: bool = False         # transcript restates one claim twice
    compound: bool = False         # archetype prose invites compound sentences


SPECS: list[StudentSpec] = [
    StudentSpec("stu-001", "Priya", "anxious profit-taker who cuts winners early", "improvement",
                (date(2026, 1, 12), date(2026, 3, 3), date(2026, 5, 20)), numeric_scores=True),
    StudentSpec("stu-002", "Marcus", "panic-seller who freezes on adverse moves", "improvement",
                (date(2026, 1, 19), date(2026, 3, 10), date(2026, 5, 27)), near_dup=True),
    StudentSpec("stu-003", "Yuki", "overconfident position-sizer ignoring risk limits", "improvement",
                (date(2026, 1, 26), date(2026, 3, 17), date(2026, 6, 2)), numeric_scores=True),
    StudentSpec("stu-004", "Elena", "revenge-trader chasing back losses same day", "improvement",
                (date(2026, 2, 2), date(2026, 3, 24), date(2026, 6, 9)), compound=True),
    StudentSpec("stu-005", "Tom", "methodical journaler with steady habits", "steady",
                (date(2026, 2, 9), date(2026, 4, 7), date(2026, 6, 16))),
    StudentSpec("stu-006", "Aisha", "news-driven trader who overreacts to headlines", "steady",
                (date(2026, 2, 16), date(2026, 4, 14), date(2026, 6, 23)), near_dup=True),
    StudentSpec("stu-007", "Viktor", "scalper flirting with burnout and overtrading", "steady",
                (date(2026, 2, 23), date(2026, 4, 21), date(2026, 6, 30)), numeric_scores=True),
    StudentSpec("stu-008", "Sofia", "FOMO chaser who buys late into runs", "relapse",
                (date(2026, 1, 5), date(2026, 3, 31), date(2026, 6, 6)), compound=True),
    StudentSpec("stu-009", "Liam", "over-leveraged swing trader ignoring stop rules", "relapse",
                (date(2026, 1, 30), date(2026, 4, 2), date(2026, 6, 18)), numeric_scores=True),
    StudentSpec("stu-010", "Chen", "analytical overthinker who misses entries", "mixed",
                (date(2026, 2, 5), date(2026, 4, 9), date(2026, 6, 25))),
]

# WAVE 2: extra students kept OUT of the main corpus, for demonstrating the
# migration wizard live (import -> distill -> review -> assess) against a store
# that is already populated. No golden cases derived - these students exist for
# the flow, not the eval suite. Coverage:
#   stu-011 Nadia - steady baseline (clean import)
#   stu-012 Omar  - improvement + numeric: out-of-order conversation archived,
#                   score change superseded (the happy-path edge cases)
#   stu-013 Grace - HAND-AUTHORED (data/students-wave2/stu-013.json, not
#                   generated here): a casual two-week self-report makes
#                   reconcile prematurely supersede a formally-assessed
#                   struggle - a defensible-but-wrong decision recall/
#                   groundedness pass but human review flags. The planted
#                   "detect and fix during review" case. Regenerating wave 2
#                   does not touch her file.
WAVE2_SPECS: list[StudentSpec] = [
    StudentSpec("stu-011", "Nadia", "meticulous planner who freezes at trade execution", "steady",
                (date(2026, 3, 2), date(2026, 5, 4), date(2026, 6, 29))),
    StudentSpec("stu-012", "Omar", "loss-averse hedger who cuts winners and lets losers ride", "improvement",
                (date(2026, 2, 12), date(2026, 4, 16), date(2026, 6, 22)), numeric_scores=True),
]

# How many topic chains must EVOLVE (get a second/third version), per shape.
MULTI_VERSION = {"steady": 0, "improvement": 2, "relapse": 1, "mixed": 1}

# Which artifacts exist and at which timeline stage. THE ADVERSARIAL CORE:
# for "improvement" the conversation is dated stage 1 - BEFORE the follow-up
# report at stage 2 - but conversations are always INGESTED AFTER reports are
# migrated. Its old-state claims must be archived, never go live.
ARTIFACT_PLAN: dict[Shape, list[tuple[str, int]]] = {
    "improvement": [("report", 0), ("conversation", 1), ("report", 2)],
    "steady":      [("report", 0), ("conversation", 1)],
    "relapse":     [("report", 0), ("report", 1), ("conversation", 2)],
    "mixed":       [("report", 0), ("conversation", 1), ("conversation", 2)],
}

# Off-domain topics for refusal cases - things no coaching source ever asserts.
REFUSAL_TOPICS = [
    "my favourite programming language", "my monthly rent budget",
    "my marathon training plan", "my diet plan", "which broker my wife uses",
    "my plans for buying a house", "my meditation app subscription",
    "the crypto wallet I lost in 2021", "my dog's name", "my star sign",
]

# ---------------------------------------------------------------------------
# 1b. REAL-WORLD FLAVOUR - the Academy's actual instructors, programs, modules
#     and strategies (from The Trading Academy's public feedback + backtest
#     sheets). Woven into CONVERSATION prose as COLOUR ONLY: the student may
#     mention it the way they already mention tickers, but it is never a report
#     claim and never a world-model fact - so golden cases (derived from truth)
#     stay clean. This is what makes the corpus read like THEIR world instead of
#     a generic sandbox, without polluting the eval set.
# ---------------------------------------------------------------------------

INSTRUCTORS = ["Simon Pullen", "Alex Morris", "Sid Naiman", "Denislav Dantev"]
STRATEGIES = ["the Reversal Method", "the SID Method"]
# program -> real module names lifted verbatim from the Academy's feedback sheet.
MODULES = {
    "Quick Win": ["Simon Pullen Quick Win: M&W", "Quick Win: Market Structure",
                  "Quick Win: Risk & Position Sizing"],
    "Legacy": ["Legacy Module 7: MACD", "Legacy Module 7: A.C.E. Trade Management",
               "Legacy Module 4: Support & Resistance"],
}


@dataclass(frozen=True)
class AcademyContext:
    instructor: str
    program: str   # "Quick Win" | "Legacy"
    module: str
    strategy: str


def _academy_for(index: int) -> AcademyContext:
    """Deterministic per-student assignment (index-based, no randomness so the
    corpus regenerates identically). Simon Pullen teaches Quick Win, the others
    anchor Legacy - matching how the real sheets read."""
    program = "Quick Win" if index % 2 == 0 else "Legacy"
    instructor = "Simon Pullen" if program == "Quick Win" \
        else INSTRUCTORS[1 + index % 3]  # Alex Morris / Sid Naiman / Denislav Dantev
    module = MODULES[program][index % len(MODULES[program])]
    strategy = STRATEGIES[index % len(STRATEGIES)]
    return AcademyContext(instructor, program, module, strategy)


def _setting_line(ctx: AcademyContext) -> str:
    """One colour-only line for the conversation prompt - explicitly fenced off
    from the facts the student must reveal, so the extractor never mistakes it
    for a durable claim."""
    return (f"Background colour (the student MAY reference this naturally in "
            f"passing, exactly like a ticker - but it is NOT one of the facts to "
            f"reveal and must never be phrased as a durable trait, goal, struggle "
            f"or preference): they train on the {ctx.program} program with "
            f"{ctx.instructor}, are currently on \"{ctx.module}\", and backtest "
            f"{ctx.strategy}.")

# ---------------------------------------------------------------------------
# 2. WORLD MODEL - versioned truth per student, generated by the LLM within
#    the spec's constraints. Stages (0/1/2) map to the spec's anchor dates.
# ---------------------------------------------------------------------------


class TruthVersion(BaseModel):
    stage: int = Field(ge=0, le=2, description="0, 1 or 2 - when this version "
                                               "of the claim became true.")
    statement: str = Field(description="One self-contained third-person "
                                       "sentence starting with the student's name.")


class TopicChain(BaseModel):
    topic: str = Field(description="Short axis label, e.g. 'reaction to "
                                   "losing trades' - distinct per chain.")
    kind: Literal["trait", "goal", "struggle", "preference"]
    versions: list[TruthVersion]


class StudentTruth(BaseModel):
    chains: list[TopicChain]


WORLD_PROMPT = """You author the GROUND-TRUTH world model of one trading-academy \
student, for generating synthetic coaching data. You define what is TRUE about \
the student and when it changed. Documents will be rendered from this later - \
be concrete and specific, never generic.

Requirements:
- Exactly {n_chains} topic chains, each a DISTINCT axis of the student
  (mix kinds: traits, struggles, goals, preferences).
- Exactly {n_multi} chain(s) must EVOLVE: {evolution_rule}
- Every statement: ONE sentence, third person, starts with "{name}",
  self-contained, specific to this student's archetype ({archetype}).
- Evolved versions must genuinely CHANGE the claim on the same axis
  (overcome, worsen, or measurably move) - not merely rephrase it.
{numeric_rule}
- All other chains have exactly one version at stage 0."""


def _world_model(spec: StudentSpec) -> StudentTruth:
    n_multi = MULTI_VERSION[spec.shape]
    if spec.shape == "relapse":
        evolution_rule = ("that chain has THREE versions (stages 0, 1, 2): a "
                          "problem at stage 0, clearly overcome at stage 1, "
                          "then RELAPSED at stage 2 (back to a form of the "
                          "old problem, worded as the current state).")
    elif spec.shape == "mixed":
        evolution_rule = "that chain has versions at stage 0 and stage 2."
    else:
        evolution_rule = ("each such chain has versions at stage 0 and stage 2 "
                          "(the stage-2 version shows clear improvement).")
    numeric_rule = (
        "- One evolving chain must be a numeric assessment score phrased as "
        "'<name>'s <something> score is N out of 10.' whose N CHANGES between "
        "versions." if spec.numeric_scores else ""
    )
    truth = gen_llm.with_structured_output(StudentTruth).invoke([
        ("system", WORLD_PROMPT.format(
            n_chains=6, n_multi=max(n_multi, 0),
            evolution_rule=evolution_rule if n_multi else
            "none - every chain has exactly one version at stage 0.",
            name=spec.name, archetype=spec.archetype,
            numeric_rule=numeric_rule)),
        ("user", f"Student: {spec.name}. Archetype: {spec.archetype}. "
                 f"Timeline shape: {spec.shape}."),
    ])
    assert isinstance(truth, StudentTruth)
    for chain in truth.chains:
        chain.versions.sort(key=lambda v: v.stage)
    return truth


def _statement_at(chain: TopicChain, stage: int) -> str | None:
    """The chain's live statement at a stage - None if not yet asserted."""
    live = None
    for v in chain.versions:
        if v.stage <= stage:
            live = v.statement
    return live


# ---------------------------------------------------------------------------
# 3. RENDERING - documents written FROM the truth live at their date.
# ---------------------------------------------------------------------------

REPORT_PROMPT = """You write concise trading-psychology assessment reports for a \
trading academy. Write ONE report of 90-150 words, plain prose, no headings, \
starting with "{opening}".

It must assert ALL of these facts about {name} (paraphrase naturally, but keep \
every numeric value VERBATIM):
{facts}

Rules: do not assert any OTHER durable claims about {name}'s traits, struggles, \
goals or preferences. Do not foreshadow or hint at future change. Present tense, \
as of the report date."""


class TranscriptLine(BaseModel):
    role: Literal["student", "coach"]
    content: str


class Transcript(BaseModel):
    lines: list[TranscriptLine]


CONVO_PROMPT = """You write a realistic coaching-session transcript between a \
trading coach and the student {name}. 8-12 alternating lines, starting with the \
student. The student speaks first person, casually and concretely (may mention \
specific tickers or trades as colour).

{setting}

Through the conversation the STUDENT must naturally reveal ALL of these facts \
(in their own words, first person; keep numeric values verbatim):
{facts}

{near_dup_rule}Rules: the student must not reveal other durable claims about \
their traits, struggles, goals or preferences beyond these. The coach asks \
short questions and reflects; the coach adds no new facts about the student."""


def _render_report(spec: StudentSpec, facts: list[str], first: bool) -> str:
    opening = "Personality assessment:" if first else "Follow-up assessment:"
    resp = gen_llm.invoke([
        ("system", REPORT_PROMPT.format(
            opening=opening, name=spec.name,
            facts="\n".join(f"- {f}" for f in facts))),
        ("user", f"Write the report for {spec.name}."),
    ])
    return str(resp.content).strip()


def _render_convo(spec: StudentSpec, facts: list[str], near_dup: bool,
                  setting: str = "") -> Transcript:
    near_dup_rule = (
        "The student must state the FIRST fact twice in the conversation, in "
        "clearly different wording each time (e.g. once early, once late).\n"
        if near_dup else "")
    t = gen_llm.with_structured_output(Transcript).invoke([
        ("system", CONVO_PROMPT.format(
            name=spec.name, near_dup_rule=near_dup_rule, setting=setting,
            facts="\n".join(f"- {f}" for f in facts))),
        ("user", f"Write the session transcript with {spec.name}."),
    ])
    assert isinstance(t, Transcript)
    return t


def _convo_facts(truth: StudentTruth, stage: int) -> list[str]:
    """What a conversation at this stage reveals: every chain whose statement
    CHANGED at this stage (that is the session's topic), plus two stable
    chains for texture. For an 'improvement' stage-1 session nothing has
    changed yet - it reveals the OLD state, which is exactly the adversarial
    payload: those claims are already superseded by the time it is ingested."""
    changed = [c for c in truth.chains
               if any(v.stage == stage for v in c.versions) and stage > 0]
    stable = [c for c in truth.chains if len(c.versions) == 1]
    picked = changed + stable[:2]
    if not changed:  # stage-1 improvement session: old state of evolving chains
        picked = [c for c in truth.chains if len(c.versions) > 1] + stable[:1]
    facts = [s for c in picked if (s := _statement_at(c, stage)) is not None]
    return facts


# ---------------------------------------------------------------------------
# 4. GOLDEN DERIVATION - mechanical, from truth alone. No LLM, no pipeline.
# ---------------------------------------------------------------------------

QUESTION_TEMPLATES = {
    "struggle": "What do I struggle with when it comes to {topic}?",
    "trait": "How would you describe me when it comes to {topic}?",
    "goal": "What is my goal around {topic}?",
    "preference": "How do I prefer to work when it comes to {topic}?",
}


def _derive_cases(spec: StudentSpec, truth: StudentTruth,
                  split: str, refusal_topic: str) -> list[dict]:
    cases: list[dict] = []
    base_tags = [spec.shape]

    def add(case_type: str, question: str, expected: str | None,
            supersede: bool, refusal: bool, extra_tags: list[str]) -> None:
        cases.append({
            "name": f"{spec.external_id}-{case_type}-{len(cases) + 1}",
            "student_external_id": spec.external_id,
            "question": question,
            "expected_fact": expected,
            "verify_supersede": supersede,
            "expect_refusal": refusal,
            "tags": sorted(set(base_tags + [case_type] + extra_tags)),
            "split": split,
        })

    single = [c for c in truth.chains if len(c.versions) == 1]
    multi = [c for c in truth.chains if len(c.versions) > 1]

    # RECALL: stable facts must come back. Up to 3 per student.
    for chain in single[:3]:
        numeric = any(ch.isdigit() for ch in chain.versions[-1].statement)
        add("recall",
            QUESTION_TEMPLATES[chain.kind].format(topic=chain.topic),
            chain.versions[-1].statement, False, False,
            [chain.kind] + (["numeric"] if numeric else []))

    # SUPERSEDE: evolving facts - current version retrieved, old one absent.
    for chain in multi:
        final = chain.versions[-1].statement
        old = chain.versions[0].statement
        numeric = any(ch.isdigit() for ch in final)
        extra = [chain.kind] + (["numeric"] if numeric else [])
        # Out-of-order tag: improvement students carry the stale claims in a
        # conversation ingested AFTER the newer report was migrated.
        if spec.shape == "improvement":
            extra.append("out-of-order")
        add("supersede",
            QUESTION_TEMPLATES[chain.kind].format(topic=chain.topic),
            final, True, False, extra)
        # Probe phrased AT the old claim - the tempting-wrong-answer angle.
        add("supersede-probe",
            f"Is this still true about me: {old}",
            final, True, False, extra)

    # REFUSAL: a topic no source ever asserted - must not be invented.
    add("refusal", f"What did we agree about {refusal_topic}?",
        None, False, True, ["refusal"])

    return cases


# ---------------------------------------------------------------------------
# 5. ASSEMBLY
# ---------------------------------------------------------------------------


def _at(d: date, minute: int = 0) -> str:
    return datetime.combine(d, time(9, minute, tzinfo=timezone.utc)).isoformat()


def generate_student(spec: StudentSpec, index: int) -> tuple[dict, list[dict]]:
    """World model -> rendered bundle + derived golden cases for one student."""
    truth = _world_model(spec)
    log.info("world model %s (%s): %d chains, %d evolving",
             spec.external_id, spec.name, len(truth.chains),
             sum(1 for c in truth.chains if len(c.versions) > 1))

    (DATA / "truth").mkdir(parents=True, exist_ok=True)
    (DATA / "truth" / f"{spec.external_id}.json").write_text(
        json.dumps({"spec": {"name": spec.name, "archetype": spec.archetype,
                             "shape": spec.shape,
                             "dates": [d.isoformat() for d in spec.dates]},
                    "chains": [c.model_dump() for c in truth.chains]},
                   indent=2), encoding="utf-8")

    ctx = _academy_for(index)          # real-world colour for this student's convos
    setting = _setting_line(ctx)
    bundle: dict = {"external_id": spec.external_id, "name": spec.name,
                    "reports": [], "conversations": []}
    report_n = convo_n = 0
    for kind, stage in ARTIFACT_PLAN[spec.shape]:
        stage_date = spec.dates[stage]
        if kind == "report":
            report_n += 1
            facts = [s for c in truth.chains
                     if (s := _statement_at(c, stage)) is not None]
            content = _render_report(spec, facts, first=(report_n == 1))
            _check_numbers(spec, facts, content)
            bundle["reports"].append({
                "external_id": f"{spec.external_id}-r{report_n}",
                "created_at": _at(stage_date), "content": content,
            })
        else:
            convo_n += 1
            facts = _convo_facts(truth, stage)
            transcript = _render_convo(spec, facts, spec.near_dup and convo_n == 1,
                                       setting=setting)
            _check_numbers(spec, facts, " ".join(m.content for m in transcript.lines))
            bundle["conversations"].append({
                "external_id": f"{spec.external_id}-c{convo_n}",
                "started_at": _at(stage_date),
                "messages": [{"role": m.role, "content": m.content,
                              "at": _at(stage_date, minute=i)}
                             for i, m in enumerate(transcript.lines)],
            })

    split = "dev" if index % 2 == 0 else "test"
    cases = _derive_cases(spec, truth, split,
                          REFUSAL_TOPICS[index % len(REFUSAL_TOPICS)])
    return bundle, cases


def _check_numbers(spec: StudentSpec, facts: list[str], rendered: str) -> None:
    """Cheap render validation: every 'N out of 10' score must survive
    verbatim (the render prompt demands it; verify, don't trust)."""
    for fact in facts:
        for i, token in enumerate(words := fact.split()):
            if token.isdigit() and i + 3 <= len(words) \
                    and words[i + 1] == "out" and token not in rendered:
                log.warning("render check %s: score %r from %r missing in output",
                            spec.external_id, token, fact)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate the synthetic corpus.")
    parser.add_argument("--wave2", action="store_true",
                        help="generate the 2 wizard-demo students into "
                             "data/students-wave2/ (no golden cases)")
    args = parser.parse_args()

    obs.configure_logging()

    if args.wave2:
        out = DATA / "students-wave2"
        out.mkdir(parents=True, exist_ok=True)
        for index, spec in enumerate(WAVE2_SPECS):
            bundle, _cases = generate_student(spec, index)  # cases discarded
            (out / f"{spec.external_id}.json").write_text(
                json.dumps(bundle, indent=2), encoding="utf-8")
            log.info("wrote %s (wave 2): %d report(s), %d conversation(s)",
                     spec.external_id, len(bundle["reports"]),
                     len(bundle["conversations"]))
        print(f"\n  {len(WAVE2_SPECS)} wave-2 students -> data/students-wave2/ "
              f"(no golden cases - wizard-demo material)")
        return

    (DATA / "students").mkdir(parents=True, exist_ok=True)
    (DATA / "golden").mkdir(parents=True, exist_ok=True)

    all_cases: list[dict] = []
    for index, spec in enumerate(SPECS):
        bundle, cases = generate_student(spec, index)
        (DATA / "students" / f"{spec.external_id}.json").write_text(
            json.dumps(bundle, indent=2), encoding="utf-8")
        all_cases.extend(cases)
        log.info("wrote %s: %d report(s), %d conversation(s), %d golden case(s)",
                 spec.external_id, len(bundle["reports"]),
                 len(bundle["conversations"]), len(cases))

    (DATA / "golden" / "cases.json").write_text(
        json.dumps(all_cases, indent=2), encoding="utf-8")
    by_split = {s: sum(1 for c in all_cases if c["split"] == s)
                for s in ("dev", "test")}
    print(f"\n  {len(SPECS)} students, {len(all_cases)} golden cases "
          f"(dev={by_split['dev']}, test={by_split['test']})")
    print("  -> data/students/, data/truth/, data/golden/cases.json")
    print("  Next: human-skim a sample, then import + migrate + ingest + evals.")


if __name__ == "__main__":
    main()
