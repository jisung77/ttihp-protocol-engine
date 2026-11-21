"""Compare a wall-clock WAIT observation window with a two-slot retirement proposal."""
from itertools import product


def reference(start, limit, events, cancel):
    """First synchronized event in the closed window wins, including the deadline."""
    end = start + limit
    event = next((t for t in range(start, end + 1) if t in events), None)
    decision = end if event is None else event
    slot = decision + (decision - start) % 2
    if cancel is not None and cancel <= slot:
        return None
    return ("TIMEOUT" if event is None else "OK", decision, slot)


def latch_then_retire(start, limit, events, cancel):
    pending = None
    for t in range(start, start + limit + 2):
        if t == cancel:
            return None
        if pending is None:
            if t in events:
                pending = ("OK", t)
            elif t == start + limit:
                pending = ("TIMEOUT", t)
        if pending is not None and (t - start) % 2 == 0:
            return (*pending, t)
    raise AssertionError("missing completion")


def sweep():
    count = 0
    for start, limit in product((8, 9), range(4)):
        end = start + limit
        for bits in product((False, True), repeat=limit + 3):
            events = {t for t, b in zip(range(start - 1, end + 2), bits) if b}
            for cancel in (None, *range(start, end + 2)):
                assert latch_then_retire(start, limit, events, cancel) == reference(start, limit, events, cancel)
                count += 1
    first = latch_then_retire(8, 1, {9}, None)
    other = latch_then_retire(9, 0, {9}, None)
    assert first == ("OK", 9, 10) and other == ("OK", 9, 9)
    return {"cases": count, "coincident_decisions": [first, other],
            "cancel_on_either_decision_or_retire": True}
