"""Property-based / fuzz coverage for the per-turn review engine.

These aim to demonstrate correctness of the reconstruction without manual QA by
checking, over many randomized version chains and decision sequences:

Independent oracles (ground truth that does not reuse the reconstruction):
  - revert-all  -> the pre-agent original (v0)
  - approve-all -> the current on-disk content (unchanged)
  - no decision -> the current content (identity)
  - disjoint edits -> a plain right-to-left line splice, for any kept subset

Meta-invariants any correct implementation must satisfy:
  - incremental decide+persist  ==  batch decide (persist once) -- the
    decide/persist ordering must not change the outcome
  - projecting the persisted content again is a fixed point
  - once every hunk is decided, review_content == accepted_baseline
  - reverting/keeping respects the dependency cascade

The same properties are exercised for text, opaque (binary), created and deleted
files, and awkward content (empty, no trailing newline, CRLF, unicode, dupes).

Randomness is seeded, so any failure reprints the seed for a deterministic repro.
"""

from __future__ import annotations

import random

import pytest

from vibe.core.checkpoints import AgentTurn, Checkpointer, Decision, FileState, RegionId

P = "f"


# -- helpers -----------------------------------------------------------------


def to_text(lines: list[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


def st(text: str | None) -> FileState:
    return FileState.absent() if text is None else FileState.from_text(text)


Chain = list[tuple[int, FileState, FileState]]


def build(chain: Chain) -> Checkpointer:
    cp = Checkpointer()
    for turn_id, pre, post in chain:
        cp.begin_turn(turn_id)
        cp.record_pre_edit(P, pre)
        cp.record_post_edit(P, post)
        cp.seal_turn()
    return cp


def region_ids(cp: Checkpointer, current: FileState) -> list[RegionId]:
    return [tr.region_id for tr in cp.view({P: current}).regions(P)]


def pending(cp: Checkpointer, current: FileState) -> list[RegionId]:
    return [
        tr.region_id
        for tr in cp.view({P: current}).regions(P)
        if tr.decision is Decision.PENDING
    ]


def perturb(lines: list[str], rng: random.Random, tag: str) -> list[str]:
    """A random line-level edit that changes the content (insert / delete /
    replace / block replace / edge insert), tagged so edits stay identifiable.
    """
    for _ in range(8):
        out = list(lines)
        if not out:
            out = [f"{tag}"]
        else:
            op = rng.choice(["rep", "ins", "del", "block", "edge"])
            i = rng.randrange(len(out))
            if op == "rep":
                out[i] = tag
            elif op == "ins":
                out[i:i] = [f"{tag}_{j}" for j in range(rng.randint(1, 3))]
            elif op == "del":
                del out[i : i + rng.randint(1, 2)]
            elif op == "block":
                out[i : i + rng.randint(1, 2)] = [f"{tag}a", f"{tag}b"]
            elif rng.random() < 0.5:
                out.insert(0, f"{tag}top")
            else:
                out.append(f"{tag}end")
        if out != lines:
            return out
    return [*lines, f"{tag}x"]


def gen_chain(rng: random.Random, n_turns: int) -> tuple[str, str, Chain]:
    """A random sealed chain over one file, with occasional between-turn user
    edits (captured as local layers). Returns (v0_text, current_text, chain).
    """
    v0 = [f"L{i}" for i in range(rng.randint(1, 7))]
    cur = list(v0)
    chain: Chain = []
    tag = 0
    for turn in range(n_turns):
        if turn > 0 and rng.random() < 0.4:  # between-turn user edit -> local
            cur = perturb(cur, rng, f"U{tag}")
            tag += 1
        pre = list(cur)
        cur = perturb(cur, rng, f"T{tag}")
        tag += 1
        chain.append((turn + 1, st(to_text(pre)), st(to_text(cur))))
    return to_text(v0), to_text(cur), chain


# -- core invariants over random text chains ---------------------------------

SEEDS = range(400)


@pytest.mark.parametrize("seed", SEEDS)
def test_anchors_and_fixed_point(seed: int) -> None:
    rng = random.Random(seed)
    v0, current, chain = gen_chain(rng, rng.randint(1, 6))
    cur = st(current)

    # Identity: with no decisions, disk projects to itself.
    assert build(chain).view({P: cur}).content(P) == cur, f"seed={seed}: identity"

    ids = region_ids(build(chain), cur)
    if not ids:
        return

    # Approve-all -> unchanged current; accepted baseline is also the full file.
    cp = build(chain)
    cp.decide_file(P, Decision.KEEP)
    assert cp.view({P: cur}).content(P) == cur, f"seed={seed}: approve-all != current"
    assert cp.view({P: cur}).accepted_baseline(P) == cur, (
        f"seed={seed}: baseline != current"
    )
    assert cp.view({P: cur}).is_fully_reviewed(P)

    # Revert-all -> the pristine original.
    cp = build(chain)
    cp.decide_file(P, Decision.REVERT)
    assert cp.view({P: cur}).content(P) == st(v0), f"seed={seed}: revert-all != v0"
    assert cp.view({P: cur}).accepted_baseline(P) == st(v0)
    assert cp.view({P: cur}).is_fully_reviewed(P)

    # Fixed point: re-projecting the reverted content is stable.
    reverted = cp.view({P: cur}).content(P)
    assert cp.view({P: reverted}).content(P) == reverted, (
        f"seed={seed}: not a fixed point"
    )


def _run_actions(
    chain: Chain,
    current: str,
    actions: list[tuple[RegionId, Decision]],
    *,
    persist: bool,
) -> FileState:
    """Apply a fixed decision script, either persisting between each decision
    (feeding the projection back as the new disk, like the manager) or only once
    at the end. Non-pending targets are skipped (destructive review).
    """
    cp = build(chain)
    cur = st(current)
    for region_id, decision in actions:
        view = {tr.region_id: tr for tr in cp.view({P: cur}).regions(P)}
        tr = view.get(region_id)
        if tr is None or tr.decision is not Decision.PENDING:
            continue
        cp.decide_region(P, region_id, decision)
        if persist:
            cur = cp.view({P: cur}).content(P)
    return cp.view({P: cur}).content(P)


@pytest.mark.parametrize("seed", SEEDS)
def test_incremental_persist_equals_batch(seed: int) -> None:
    rng = random.Random(seed)
    _v0, current, chain = gen_chain(rng, rng.randint(2, 6))
    ids = region_ids(build(chain), st(current))
    if not ids:
        return

    order = rng.sample(ids, len(ids))
    actions = [(rid, rng.choice([Decision.KEEP, Decision.REVERT])) for rid in order]

    incremental = _run_actions(chain, current, actions, persist=True)
    batch = _run_actions(chain, current, actions, persist=False)
    assert incremental == batch, f"seed={seed}: incremental persist diverged from batch"


@pytest.mark.parametrize("seed", SEEDS)
def test_fully_decided_content_equals_baseline(seed: int) -> None:
    rng = random.Random(seed)
    _v0, current, chain = gen_chain(rng, rng.randint(1, 6))
    cur = st(current)
    cp = build(chain)
    if not pending(cp, cur):
        return

    # Decide every hunk (random keep/revert), persisting between each.
    disk = cur
    guard = 0
    while pending(cp, disk):
        rid = pending(cp, disk)[0]
        cp.decide_region(P, rid, rng.choice([Decision.KEEP, Decision.REVERT]))
        disk = cp.view({P: disk}).content(P)
        guard += 1
        assert guard < 100, f"seed={seed}: review never settled"

    # No pending hunks: the on-disk projection and the accepted baseline agree.
    assert cp.view({P: disk}).content(P) == cp.view({P: disk}).accepted_baseline(P), (
        f"seed={seed}: content != baseline once fully decided"
    )


def _disk_lines(state: FileState) -> list[str]:
    return (state.data or b"").decode("utf-8").splitlines()


def test_manual_edits_during_review_stay_projectable() -> None:
    """A review session that interleaves decisions (persisted, like the manager's
    decide-then-write flow) with genuine manual disk edits.

    ``reconcile`` is called at each acting boundary with the true disk (as the
    manager does at render and before a decision), capturing any manual edit as a
    log event. After every step the on-disk content must project to itself: a
    manual edit survives projection, never silently dropped nor conflated with the
    decisions already baked into disk. Reverting every hunk then bottoms out at v0.
    """
    for seed in range(400):
        rng = random.Random(seed)
        v0, current, chain = gen_chain(rng, rng.randint(2, 6))
        cp = build(chain)
        disk = st(current)

        for step in range(rng.randint(4, 12)):
            cp.reconcile(P, disk)  # render boundary: seal any manual edit
            pend = pending(cp, disk)
            if pend and rng.random() < 0.6:
                rid = rng.choice(pend)
                decision = rng.choice([Decision.KEEP, Decision.REVERT])
                cp.decide_region(P, rid, decision)
                disk = cp.view({P: disk}).content(P)  # persist the projection
            else:
                disk = st(to_text(perturb(_disk_lines(disk), rng, f"M{step}")))
            cp.reconcile(P, disk)
            assert cp.view({P: disk}).content(P) == disk, (
                f"seed={seed}: on-disk content is not a fixed point after step {step}"
            )

        # Reverting every hunk (turns + captured manual edits) empties the applied
        # set, so the file bottoms out at the pristine original.
        for region_id in region_ids(cp, disk):
            cp.decide_region(P, region_id, Decision.REVERT)
        assert cp.view({P: disk}).content(P) == st(v0), f"seed={seed}: revert-all != v0"


@pytest.mark.parametrize("seed", SEEDS)
def test_revert_cascades_to_dependents(seed: int) -> None:
    rng = random.Random(seed)
    _v0, current, chain = gen_chain(rng, rng.randint(2, 6))
    cur = st(current)
    view = build(chain).view({P: cur}).regions(P)
    if not view:
        return

    cp = build(chain)
    target = rng.choice(view)
    cp.decide_region(P, target.region_id, Decision.REVERT)
    decided = {tr.region_id: tr.decision for tr in cp.view({P: cur}).regions(P)}

    # Every hunk built on the reverted one is dragged down with it.
    for tr in view:
        if target.region_id in tr.depends_on:
            assert decided[tr.region_id] is Decision.REVERT, (
                f"seed={seed}: dependent {tr.region_id} survived its dependency's revert"
            )


# -- independent oracle: disjoint edits, arbitrary partial decisions ----------


def gen_disjoint(
    rng: random.Random,
) -> tuple[list[str], list[tuple[int, int, list[str]]]]:
    size = rng.randint(6, 14)
    v0 = [f"L{i}" for i in range(size)]
    positions: list[int] = []
    p = rng.randint(0, 1)
    while p < size:
        positions.append(p)
        p += rng.randint(2, 3)  # gap >= 2 keeps hunks independent (no deps, no merge)
    edits: list[tuple[int, int, list[str]]] = []
    for k, pos in enumerate(positions):
        kind = rng.choice(["rep", "ins", "del"])
        if kind == "rep":
            edits.append((pos, 1, [f"E{k}"]))
        elif kind == "ins":
            edits.append((pos, 1, [f"E{k}", f"E{k}b", f"L{pos}"]))
        else:
            edits.append((pos, 1, []))
    return v0, edits


def build_disjoint(v0: list[str], edits: list[tuple[int, int, list[str]]]) -> Chain:
    cur = list(v0)
    offset = 0
    chain: Chain = []
    for k, (pos, length, new) in enumerate(edits):
        pre = list(cur)
        at = pos + offset
        cur[at : at + length] = new
        chain.append((k + 1, st(to_text(pre)), st(to_text(cur))))
        offset += len(new) - length
    return chain


def splice_oracle(
    v0: list[str], edits: list[tuple[int, int, list[str]]], kept: set[int]
) -> str:
    result = list(v0)
    for k in sorted(kept, reverse=True):  # right-to-left: disjoint splices don't shift
        pos, length, new = edits[k]
        result[pos : pos + length] = new
    return to_text(result)


@pytest.mark.parametrize("seed", SEEDS)
def test_disjoint_partial_decisions_match_splice_oracle(seed: int) -> None:
    rng = random.Random(seed)
    v0, edits = gen_disjoint(rng)
    if not edits:
        return
    chain = build_disjoint(v0, edits)
    current = chain[-1][2]

    ordered = sorted(region_ids(build(chain), current), key=lambda r: r.version_index)
    assert len(ordered) == len(edits), (
        f"seed={seed}: expected one hunk per disjoint edit"
    )

    kept = {k for k in range(len(edits)) if rng.random() < 0.5}
    cp = build(chain)
    for k, region_id in enumerate(ordered):
        cp.decide_region(P, region_id, Decision.KEEP if k in kept else Decision.REVERT)

    assert cp.view({P: current}).content(P) == st(splice_oracle(v0, edits, kept)), (
        f"seed={seed}: partial decision != independent splice oracle (kept={sorted(kept)})"
    )


# -- opaque (binary) files ----------------------------------------------------


def gen_binary_chain(
    rng: random.Random, n_turns: int
) -> tuple[FileState, FileState, Chain]:
    def blob() -> FileState:
        head = bytes(rng.randrange(256) for _ in range(rng.randint(1, 6)))
        tail = bytes(rng.randrange(256) for _ in range(rng.randint(0, 4)))
        return FileState(head + b"\x00" + tail)

    v0 = blob()
    cur = v0
    chain: Chain = []
    for turn in range(n_turns):
        nxt = blob()
        while nxt == cur:
            nxt = blob()
        chain.append((turn + 1, cur, nxt))
        cur = nxt
    return v0, cur, chain


@pytest.mark.parametrize("seed", range(150))
def test_binary_anchors_and_incremental(seed: int) -> None:
    rng = random.Random(seed)
    v0, current, chain = gen_binary_chain(rng, rng.randint(1, 5))

    assert build(chain).view({P: current}).content(P) == current, (
        f"seed={seed}: identity"
    )

    cp = build(chain)
    cp.decide_file(P, Decision.KEEP)
    assert cp.view({P: current}).content(P) == current, f"seed={seed}: approve-all"

    cp = build(chain)
    cp.decide_file(P, Decision.REVERT)
    assert cp.view({P: current}).content(P) == v0, f"seed={seed}: revert-all != v0"

    ids = region_ids(build(chain), current)
    if ids:
        order = rng.sample(ids, len(ids))
        actions = [(rid, rng.choice([Decision.KEEP, Decision.REVERT])) for rid in order]
        inc = _run_actions_state(chain, current, actions, persist=True)
        bat = _run_actions_state(chain, current, actions, persist=False)
        assert inc == bat, f"seed={seed}: opaque incremental != batch"


def _run_actions_state(
    chain: Chain,
    current: FileState,
    actions: list[tuple[RegionId, Decision]],
    *,
    persist: bool,
) -> FileState:
    cp = build(chain)
    cur = current
    for region_id, decision in actions:
        view = {tr.region_id: tr for tr in cp.view({P: cur}).regions(P)}
        tr = view.get(region_id)
        if tr is None or tr.decision is not Decision.PENDING:
            continue
        cp.decide_region(P, region_id, decision)
        if persist:
            cur = cp.view({P: cur}).content(P)
    return cp.view({P: cur}).content(P)


# -- created / deleted / absent transitions ----------------------------------


def test_created_file_reverts_to_absent() -> None:
    chain: Chain = [(1, st(None), st("hello\nworld\n"))]
    current = st("hello\nworld\n")
    cp = build(chain)
    cp.decide_file(P, Decision.REVERT)
    assert cp.view({P: current}).content(P) == st(None)


def test_created_file_approve_keeps_content() -> None:
    chain: Chain = [(1, st(None), st("hello\n"))]
    current = st("hello\n")
    cp = build(chain)
    cp.decide_file(P, Decision.KEEP)
    assert cp.view({P: current}).content(P) == current


def test_deleted_file_reverts_to_original() -> None:
    chain: Chain = [(1, st("a\nb\nc\n"), st(None))]
    current = st(None)
    cp = build(chain)
    cp.decide_file(P, Decision.REVERT)
    assert cp.view({P: current}).content(P) == st("a\nb\nc\n")


def test_create_then_edit_incremental_revert_restores_absent() -> None:
    # Create in turn 1, edit in turn 2; revert turn 1 first (persist), then the
    # rest — must bottom out at absent, mirroring the deletion-revert regression.
    chain: Chain = [(1, st(None), st("x\ny\n")), (2, st("x\ny\n"), st("x\nY\n"))]
    disk = st("x\nY\n")
    cp = build(chain)
    cp.decide_scope(P, AgentTurn(2), Decision.REVERT)
    disk = cp.view({P: disk}).content(P)
    assert disk == st("x\ny\n")
    cp.decide_scope(P, AgentTurn(1), Decision.REVERT)
    disk = cp.view({P: disk}).content(P)
    assert disk == st(None)


def test_delete_then_recreate_revert_deletion_drags_recreation() -> None:
    # Reverting the deletion must not resurrect the old content alongside the
    # recreated content ("old\nnew\n"). The recreation depends on the deletion
    # barrier, so reverting the deletion drags it down too.
    chain: Chain = [(1, st("old\n"), st(None)), (2, st(None), st("new\n"))]
    disk = st("new\n")
    cp = build(chain)
    cp.decide_scope(P, AgentTurn(1), Decision.REVERT)
    assert cp.view({P: disk}).content(P) == st("old\n")


def test_delete_then_recreate_revert_recreation_restores_absent() -> None:
    chain: Chain = [(1, st("old\n"), st(None)), (2, st(None), st("new\n"))]
    disk = st("new\n")
    cp = build(chain)
    cp.decide_scope(P, AgentTurn(2), Decision.REVERT)
    assert cp.view({P: disk}).content(P) == st(None)


def test_empty_file_creation_is_reviewable_and_revertible() -> None:
    # Creating an empty file (absent -> b"") is a whole-file change, not an
    # invisible no-op that cannot be reviewed or reverted.
    chain: Chain = [(1, st(None), FileState.from_text(""))]
    disk = FileState.from_text("")
    cp = build(chain)

    assert len(cp.view({P: disk}).regions(P)) == 1

    cp.decide_file(P, Decision.REVERT)
    assert cp.view({P: disk}).content(P) == st(None)


def test_empty_file_creation_approve_keeps_empty() -> None:
    chain: Chain = [(1, st(None), FileState.from_text(""))]
    disk = FileState.from_text("")
    cp = build(chain)
    cp.decide_file(P, Decision.KEEP)
    assert cp.view({P: disk}).content(P) == FileState.from_text("")


def test_partial_revert_preserves_crlf_bytes() -> None:
    # Partially reverting a CRLF file must not rewrite the whole file to LF/UTF-8.
    # Two disjoint edits; revert the first, keep the second.
    chain: Chain = [(1, st("a\r\nb\r\nc\r\n"), st("A\r\nb\r\nC\r\n"))]
    disk = st("A\r\nb\r\nC\r\n")
    cp = build(chain)
    first = region_ids(cp, disk)[0]
    cp.decide_region(P, first, Decision.REVERT)
    assert cp.view({P: disk}).content(P).data == b"a\r\nb\r\nC\r\n"


# -- awkward content ----------------------------------------------------------


AWKWARD = [
    "",  # empty file
    "solo",  # single line, no trailing newline
    "a\nb\nc",  # no trailing newline, multi-line
    "café\nnaïve\n",  # unicode
    "\n\n\n",  # only newlines
    "dup\ndup\ndup\n",  # duplicate lines (SequenceMatcher ambiguity)
    "  \tindented\n",  # whitespace
]


@pytest.mark.parametrize("original", AWKWARD)
@pytest.mark.parametrize("edited", AWKWARD)
def test_awkward_content_round_trips(original: str, edited: str) -> None:
    if original == edited:
        return
    chain: Chain = [(1, st(original), st(edited))]
    current = st(edited)

    assert build(chain).view({P: current}).content(P) == current

    cp = build(chain)
    cp.decide_file(P, Decision.REVERT)
    assert cp.view({P: current}).content(P) == st(original)

    cp = build(chain)
    cp.decide_file(P, Decision.KEEP)
    assert cp.view({P: current}).content(P) == current


# A full revert restores the original file's exact bytes: reverting everything
# empties the applied set, and reconstruction returns the stored original
# FileState verbatim rather than a re-encoded (newline-normalized) rebuild. So
# CRLF survives a round-trip through review as long as it is fully reverted.
def test_crlf_revert_restores_exact_bytes() -> None:
    original = "a\r\nb\r\nc\r\n"
    chain: Chain = [(1, st(original), st("a\r\nZ\r\nc\r\n"))]
    current = st("a\r\nZ\r\nc\r\n")
    cp = build(chain)
    cp.decide_file(P, Decision.REVERT)
    assert cp.view({P: current}).content(P) == st(original)
