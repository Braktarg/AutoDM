from __future__ import annotations

import re


def evaluate_dm_reply(reply: str) -> dict:
    txt = (reply or "").strip()
    issues: list[str] = []
    score = 100

    if len(txt) > 2600:
        issues.append("too_long")
        score -= 18
    if "¿" not in txt and "?" not in txt:
        issues.append("no_open_question")
        score -= 12
    if len(re.findall(r"\b(silencio|ecos|presencia|ancestral|ominoso)\b", txt.lower())) >= 3:
        issues.append("mystery_word_overuse")
        score -= 15
    if txt.count("\n\n") >= 8:
        issues.append("too_many_paragraphs")
        score -= 10
    if not re.search(r"\b(hace(s)?|decides|que haces|que har[aá]s|opci[oó]n)\b", txt.lower()):
        issues.append("weak_actionability")
        score -= 8

    return {"score": max(0, score), "issues": issues}

