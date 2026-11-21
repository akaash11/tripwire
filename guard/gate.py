"""Decision 1 routing on top of Akaash's ask() + clause_questions() (docs/TANU.md)."""

from __future__ import annotations

import concurrent.futures

from .decisions import MODEL, ask, clause_questions
from .listing import input_blocks

MUST_YES = 0.9
MUST_CONF = 0.85
MUST_NO = 0.1
MUST_NOT_YES = 0.9
MUST_NOT_NO = 0.1
INJECTION_MAX = 0.1
ASK_TIMEOUT_S = 8.0


def _is_http_400(exc: BaseException) -> bool:
    code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if code == 400:
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return "status 400" in text or "status_code=400" in text or "http 400" in text or " 400 " in text


def _noul(ans: dict | None) -> float | None:
    if not ans:
        return None
    v = ans.get("noul")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _conf(ans: dict | None) -> float:
    if not ans:
        return 0.0
    try:
        return float(ans.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _choice(ans: dict | None) -> str | None:
    if not ans:
        return None
    v = ans.get("choice")
    return str(v) if v is not None else None


def _messages_text(messages) -> str:
    if not messages:
        return ""
    lines = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        lines.append(
            f"Checkout message: type={msg.get('type')} code={msg.get('code')} "
            f"content={msg.get('content')}"
        )
    return "\n".join(lines)


def _blocks(products: list[dict], merchant_messages=None) -> list[dict]:
    blocks = input_blocks(products)
    extra = _messages_text(merchant_messages)
    if extra and blocks and blocks[0].get("type") == "text":
        blocks[0] = {"type": "text", "text": blocks[0].get("text", "") + "\n" + extra}
    elif extra:
        blocks.insert(0, {"type": "text", "text": extra})
    return blocks


def _ask(blocks: list[dict], questions: dict, timeout_s: float) -> dict:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(ask, blocks, questions)
        try:
            return fut.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(f"{MODEL} timed out after {timeout_s}s") from exc


def route(contract: dict, answers: dict) -> dict:
    """Apply TANU.md thresholds to an ask() result. Does not call the API."""
    must_ids = [c["id"] for c in (contract.get("must") or []) if isinstance(c, dict) and c.get("id")]
    must_not_ids = [c["id"] for c in (contract.get("must_not") or []) if isinstance(c, dict) and c.get("id")]
    reasons: list[str] = []
    clear_fail = False
    approve_ok = True
    if not must_ids and not must_not_ids:
        approve_ok = False
        reasons.append("contract: no must or must_not clauses")

    for cid in must_ids:
        ans = answers.get(cid)
        noul, conf = _noul(ans), _conf(ans)
        if noul is None:
            approve_ok = False
            reasons.append(f"{cid}: missing Decision 1 answer")
            continue
        if noul <= MUST_NO and conf >= MUST_CONF:
            clear_fail = True
            reasons.append(f"{cid}: must clause failed (noul={noul:.2f}, conf={conf:.2f})")
        elif not (noul >= MUST_YES and conf >= MUST_CONF):
            approve_ok = False
            reasons.append(f"{cid}: must clause uncertain (noul={noul:.2f}, conf={conf:.2f})")

    for cid in must_not_ids:
        ans = answers.get(cid)
        noul, conf = _noul(ans), _conf(ans)
        if noul is None:
            approve_ok = False
            reasons.append(f"{cid}: missing Decision 1 answer")
            continue
        if noul >= MUST_NOT_YES and conf >= MUST_CONF:
            clear_fail = True
            reasons.append(f"{cid}: must_not clause present (noul={noul:.2f}, conf={conf:.2f})")
        elif noul > MUST_NOT_NO:
            approve_ok = False
            reasons.append(f"{cid}: must_not clause uncertain (noul={noul:.2f}, conf={conf:.2f})")

    inj = _noul(answers.get("injection"))
    inj_conf = _conf(answers.get("injection"))
    if inj is None:
        approve_ok = False
        reasons.append("injection: missing Decision 1 answer")
    elif inj > INJECTION_MAX:
        approve_ok = False
        reasons.append(f"injection: signal {inj:.2f} (conf {inj_conf:.2f}); escalate above {INJECTION_MAX}")

    returns_required = str(contract.get("returns") or "").strip().lower() == "required"
    choice = _choice(answers.get("returns"))
    if returns_required:
        if choice == "final_sale":
            clear_fail = True
            reasons.append("returns: required but Decision 1 chose final_sale")
        elif choice == "unknown" or not choice:
            approve_ok = False
            reasons.append(f"returns: required but Decision 1 chose {choice or 'nothing'}")
        elif choice != "returnable":
            approve_ok = False
            reasons.append(f"returns: unexpected choice {choice}")

    if clear_fail:
        verdict = "block"
    elif approve_ok and not reasons:
        verdict = "approve"
    else:
        verdict = "escalate"

    return {
        "verdict": verdict,
        "clauses": answers,
        "escalated": verdict == "escalate",
        "reasons": reasons,
        "error": None,
        "model": MODEL,
    }


def evaluate(contract: dict, products: list[dict] | None = None, merchant_messages=None) -> dict:
    """Call ask() + clause_questions() and route. HTTP 400 → ask_human; other errors → escalate."""
    questions = clause_questions(contract or {})
    blocks = _blocks(products or [], merchant_messages)
    try:
        answers = _ask(blocks, questions, ASK_TIMEOUT_S)
    except Exception as exc:
        if _is_http_400(exc):
            return {
                "verdict": "ask_human",
                "clauses": {},
                "escalated": False,
                "reasons": ["decisions: HTTP 400 safety block; asking a human"],
                "error": "safety_block",
                "model": MODEL,
            }
        reason = "decisions: timed out" if isinstance(exc, TimeoutError) else "decisions: api error"
        return {
            "verdict": "escalate",
            "clauses": {},
            "escalated": True,
            "reasons": [reason],
            "error": reason,
            "model": MODEL,
        }
    return route(contract or {}, answers)
