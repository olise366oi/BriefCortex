"""Collect facts about our company that sit inside articles about others."""
import asyncio
from typing import List, Sequence, Tuple

from app.models.fact_base import IncidentalFact
from app.services.freshness import is_stale_period, parse_publish_date
from app.services.incidental import extract_incidental_sentences, opening_sentence
from app.services.logger_service import get_logger
from app.services.sentence_classifier import classify_sentences

logger = get_logger("incidental")

# How many articles to ask the model about at once. Subject classification and
# sentence labelling are one call each; batching keeps a run inside its budget.
CLASSIFY_CONCURRENCY = 4

# Report the sentence as backed by reporting, or say plainly that nothing did.
PROVENANCE_CORROBORATED = "corroborated"
PROVENANCE_UNVERIFIED = "unverified"


def _article_subject(result: dict, clean_text: str) -> str:
    """Return the company the article is about, as its headline states it.

    The headline is the publisher's own claim about the subject, so it is the
    most reliable short answer to "whose story is this?". When the headline is
    unusable the opening sentence stands in. An empty string is returned rather
    than a guess.
    """
    title = " ".join(str(result.get("title") or "").split())
    if title:
        # Publishers append the outlet name; keep the headline part only.
        head = re.split(r"\s(?:-|—|\|)\s", title)[0].strip()
        if head:
            return head[:120]

    opening = opening_sentence(clean_text)
    return " ".join(opening.split())[:120]


async def harvest_incidental_facts(
    articles: Sequence[dict],
    company: str,
    window_start: str,
    *,
    already_collected: Sequence[str] = (),
    max_articles: int = 12,
) -> Tuple[List[IncidentalFact], dict]:
    """Return incidental facts about our company found in other companies' articles.

    Receives articles already routed as being about someone else, each as
    ``{"url", "clean_text", "result"}``. Sentences the model labels as facts are
    kept; opinions and unclear cases are dropped, since this block exists to give
    a reader something to check, not something to rely on.
    """
    name = company.strip()
    stats = {"articles_read": 0, "sentences_found": 0, "kept": 0, "dropped": 0}
    if not name:
        return [], stats

    candidates = []
    known = {text.strip().lower() for text in already_collected}

    for article in articles:
        if len(candidates) >= max_articles:
            break
        url = str(article.get("url") or "")
        clean_text = article.get("clean_text") or ""
        if not url or not clean_text:
            continue

        # The headline names who the article is about, so a reader can see at a
        # glance that this sentence came out of someone else's story. Storing a
        # routing label ("primary") here told the reader nothing: every lead read
        # "from an article about primary".
        subject = _article_subject(article.get("result") or {}, clean_text)

        sentences = extract_incidental_sentences(
            clean_text,
            name,
            exclude=[text for text in known if text],
        )
        if not sentences:
            continue

        candidates.append(
            {
                "url": url,
                "clean_text": clean_text,
                "opening": opening_sentence(clean_text),
                "subject": subject,
                "sentences": sentences,
                "publish_date": parse_publish_date(
                    (article.get("result") or {}).get("published_date")
                ),
            }
        )

    if not candidates:
        return [], stats

    stats["articles_read"] = len(candidates)
    semaphore = asyncio.Semaphore(CLASSIFY_CONCURRENCY)

    async def evaluate(candidate: dict):
        async with semaphore:
            texts = [item.content for item in candidate["sentences"]]
            labels = await classify_sentences(texts, candidate["opening"])
        return candidate, labels

    handled = await asyncio.gather(*[evaluate(item) for item in candidates])

    facts: List[IncidentalFact] = []
    seen: set[str] = set()

    for candidate, labels in handled:
        for sentence, kind in zip(candidate["sentences"], labels):
            stats["sentences_found"] += 1
            if kind != "fact":
                stats["dropped"] += 1
                continue
            if is_stale_period(sentence.content, window_start):
                stats["dropped"] += 1
                continue

            key = sentence.content.strip().lower()
            if key in seen:
                continue
            seen.add(key)

            try:
                facts.append(
                    IncidentalFact(
                        content=sentence.content,
                        source_url=candidate["url"],
                        publish_date=candidate["publish_date"],
                        article_subject=candidate["subject"],
                        provenance=PROVENANCE_UNVERIFIED,
                    )
                )
                stats["kept"] += 1
            except Exception:
                stats["dropped"] += 1

    logger.info(
        "Incidental harvest | articles=%s | sentences=%s | kept=%s | dropped=%s",
        stats["articles_read"],
        stats["sentences_found"],
        stats["kept"],
        stats["dropped"],
    )
    return facts, stats
