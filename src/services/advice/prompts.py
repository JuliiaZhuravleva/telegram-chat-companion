"""The two prompts the advice classifier asks, verbatim from the evaluated run.

Both ask only for FACTS about a message ("does it suggest something", "whose
feelings does it answer") and never for a verdict: the verdict is code
(`verdict.py`), because the owner's rules -- what counts as unsolicited, which
topics are hers to decide -- change by editing a function, not by re-tuning a
prompt and re-measuring it. Changing a word here invalidates the measured
agreement with her labels (5 of 7 on advice, 5 of 7 on support, 2026-10-28);
re-run a small labelled sample before shipping an edit.

Substitution is one regex pass, not `str.format` (the templates are full of
JSON braces) and not chained `str.replace` either: a chain rescans what it
already inserted, so a member who types `{comment}` into the thread would have
the comment pasted there too. `re.sub` never rescans its own output.
"""

from __future__ import annotations

import re

POST_PROMPT = """Перед тобой пост из личного блога. Ответь на вопросы о нём. Сначала поле what, потом остальные. Верни JSON:
{"what": "1 предложение: о чём пост",
 "about_her_feelings": true | false,   // автор делится СВОИМИ чувствами или переживаниями — тяжёлыми или радостными
 "hard_state": true | false,           // её трудное состояние: усталость, депрессия, боль, тревога, вина, потеря, болезнь близких или питомца, обида
 "real_request": true | false,         // автор ПО-НАСТОЯЩЕМУ просит читателей о совете, рекомендации или помощи. Риторический вопрос, заголовок или «вот как я делаю» — false
 "joke": true | false}                 // пост — шутка, мем, прикол

ПОСТ:
{post}"""

COMMENT_PROMPT = """Перед тобой сообщение из чата обсуждений личного блога (или серия сообщений одного человека подряд — читай её как одну реплику). Ответь на вопросы-факты о нём. Ничего не оценивай, только отвечай на вопросы. Сначала поле what, потом остальные. Верни JSON:
{"what": "1–2 предложения: что человек делает этим сообщением",
 "joke": true | false,               // сообщение — шутка, ирония, абсурд, прикол, а не всерьёз
 "to": "author" | "member" | "nobody", // к кому обращено: к автору блога, к другому участнику, ни к кому конкретно
 "answers_member_question": true | false, // отвечает на вопрос, который задал другой участник (не автор блога)
 "answers_author_request": true | false,  // отвечает на просьбу автора блога о совете или рекомендации
 "suggests": true | false,           // говорит кому-то, что сделать, попробовать, как поступить, думать или чувствовать. Сюда же «а ты пробовала…», «попробуй», «тебе надо», «лучше бы», «мне помогло…» с намёком, что поможет и ей
 "topic": null | "life" | "psyche" | "thinking" | "blog" | "everyday" | "tech" | "creative" | "shopping" | "entertainment" | "other",
                                     // только если suggests: о чём предложение. life — её жизнь, отношения, работа, учёба, решения; psyche — её психика, самочувствие, практики, терапия, врачи, диагнозы; thinking — как правильно думать, чувствовать, страдать, рассуждать, что считать реальным; blog — как ей вести блог, формат постов; everyday — еда, сон, чай, прогулка, отдых; tech — программы, техника; creative — идеи для творчества; shopping — покупки; entertainment — что посмотреть, почитать, поиграть
 "asks_permission": true | false,    // спрашивает, можно ли дать совет
 "backs_her": true | false,          // соглашается с ней или поддерживает решение, которое она уже приняла сама
 "dismisses_suffering": true | false, // всерьёз говорит, что её страдания выдуманы, «только в голове», «нормальная реакция организма», что их легко убрать
 "feelings": "none" | "short_sympathy" | "names_her_feelings" | "angry_for_her" | "similar_experience" | "glad_with_her" | "asks_about_feelings",
                                     // как человек откликается на ЕЁ чувства (не на тему поста): none — никак; short_sympathy — короткое «жесть», «обнимаю»; names_her_feelings — проговаривает, что она чувствует и почему; angry_for_her — злится за неё; similar_experience — делится похожим опытом, чтобы она поняла, что с ней всё в порядке; glad_with_her — радуется вместе с ней; asks_about_feelings — бережно спрашивает о её чувствах
 "substantive": true | false}        // развёрнутая мысль по теме: несколько предложений с аргументом, наблюдением или своим взглядом

ПОСТ:
{post}

ЧТО БЫЛО В ЧАТЕ ДО ЭТОГО (старые сверху):
{thread}

СООБЩЕНИЕ ({who}):
{comment}"""

NO_POST = "(сообщение не привязано к посту)"
EMPTY_THREAD = "(нет)"

POST_CHARS_FOR_POST_STEP = 3000
POST_CHARS_FOR_COMMENT_STEP = 1500

_SLOT_RE = re.compile(r"\{(post|thread|who|comment)\}")


def _fill(template: str, values: dict[str, str]) -> str:
    return _SLOT_RE.sub(lambda m: values[m.group(1)], template)


def render_post_prompt(post_text: str) -> str:
    return _fill(POST_PROMPT, {"post": post_text[:POST_CHARS_FOR_POST_STEP]})


def render_comment_prompt(*, post_text: str | None, thread: str, who: str, comment: str) -> str:
    post = post_text[:POST_CHARS_FOR_COMMENT_STEP] if post_text else NO_POST
    return _fill(
        COMMENT_PROMPT,
        {"post": post, "thread": thread or EMPTY_THREAD, "who": who, "comment": comment},
    )
