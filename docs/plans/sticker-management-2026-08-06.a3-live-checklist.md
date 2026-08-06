# Дедупликация стикеров — ручной чек-лист живой проверки (A-3)

> Дополняет автоматические тесты: `tests/unit/test_sticker_dedup.py`,
> `tests/unit/test_sticker_learning.py::TestDuplicateDetection`,
> `tests/integration/test_sticker_dedup_integration.py`.
> Источник: `docs/plans/sticker-management-2026-08-06.md` §1, ADR-0007
> (`docs/decisions/ADR-0007-sticker-duplicate-hash-dedup.md`), особенно Decision 8.

Предусловие: доступ к живому боту (`bot.get_file`/`bot.download_file`) и Python-окружению
проекта (`.venv`) для офлайн-запуска `compute_image_hash`.

## 0. Важно — что этот чек-лист НЕ проверяет (Decision 8)

Три стикера ниже уже лежат в базе **до** миграции `023_sticker_dedup_hash.py` — их
`image_hash` в реальной БД останется `NULL`, пока кто-то не запустит на них повторный анализ
вручную. **Повторная отправка этих стикеров боту сегодня НЕ склеит их автоматически** — это
ожидаемо, не баг (см. ADR-0007 Decision 8). Здесь мы проверяем только **корректность самого
алгоритма хеширования** на реальных файлах — offline, в обход бота.

## 1. Hash-correctness: скачать и сравнить попарно

Стикеры: `AgAD6xIAAv3NMUs`, `AgAD_xEAAsJVUEo`, `AgADzioAAuSTIEs`.

- [ ] Через бота (например, временный debug-хендлер, `bot.get_file(file_id)` в консоли, или
      `getFile`/скачивание через Bot API `https://api.telegram.org/bot<TOKEN>/getFile?file_id=...`
      → `https://api.telegram.org/file/bot<TOKEN>/<file_path>`) скачать все три стикера как
      сырые байты. **Внимание:** `file_unique_id` ≠ `file_id` — `bot.get_file` требует
      `file_id`; если под рукой только `file_unique_id`, сначала найти соответствующий
      `file_id` (`SELECT file_id FROM sticker_knowledge WHERE file_unique_id = '...'`).
- [ ] Прогнать `compute_image_hash()` (`src/services/modules/sticker/dedup.py`) на каждом из
      трёх файлов офлайн (без сети, без бота):
      ```python
      from src.services.modules.sticker.dedup import compute_image_hash, hamming_distance
      h1 = compute_image_hash(open("sticker1.webp", "rb").read())
      h2 = compute_image_hash(open("sticker2.webp", "rb").read())
      h3 = compute_image_hash(open("sticker3.webp", "rb").read())
      ```
- [ ] Посчитать все три попарные дистанции Хэмминга (`hamming_distance(h1, h2)`,
      `hamming_distance(h1, h3)`, `hamming_distance(h2, h3)`). Ожидается: **все три ≤ 4**
      (`DEDUP_HAMMING_THRESHOLD`) — это и есть доказательство, что алгоритм поймал бы их как
      дубликаты, существуй хеш на момент их загрузки.
- [ ] Если хотя бы одна пара превышает порог — это не обязательно баг алгоритма: возможно,
      названные стикеры не идентичная картинка, а просто визуально похожи (кроп/сжатие
      сильнее ожидаемого). Записать реальные дистанции в этот файл (или в тикет) для будущего
      наведения на резерв порога (ADR-0007 Decision 3's revisit trigger), а не считать это
      автоматически провалом чек-листа.
- [ ] Если формат хотя бы одного стикера — НЕ статичный webp (animated `.tgs` или video
      `.webm`), хеш нужно снимать не с сырых байт, а с кадра по правилам ADR-0007 Decision 2
      (`.tgs` → `sampled_frames[0]`; `.webm` → `ffmpeg -ss 0` анкор-кадр, не motion-keyframe).
      Проверить тип каждого стикера перед прогоном (`sticker.is_animated`/`is_video`).

## 2. Живой end-to-end прогон нового дубликата (не входит в 3 примера, но проверяет реальный путь)

Три названных стикера непригодны для end-to-end проверки (Decision 8), поэтому здесь —
отдельная искусственная пара:

- [ ] Найти любой уже проанализированный стикер в каталоге (есть `image_hash`,
      `visual_description` — свежий, загруженный уже после миграции 023).
- [ ] Переслать боту **этот же стикер из другого паблик-пака** (то есть с другим `file_id`/
      `file_unique_id`, но той же картинкой — самый частый источник "реальных" дублей,
      Telegram переиспользует одну и ту же картинку между паками). Ожидается:
  - бот не тратит новый Vision-вызов (ориентир: время ответа заметно быстрее полного анализа,
    и/или проверить логи на `"Sticker duplicate detected via image hash"`);
  - новый стикер в базе (`SELECT visual_description, duplicate_of_file_unique_id FROM
    sticker_knowledge WHERE file_unique_id = '<новый>'`) имеет то же `visual_description`,
    что и канонический, и `duplicate_of_file_unique_id` указывает на канонический
    `file_unique_id`.
- [ ] Убедиться, что admin-уведомление о новом стикере всё равно пришло как обычно (Decision 7:
      "duplicates get the existing new-sticker admin notification for free").

## 3. Гигиена / regressions

- [ ] `git grep` по `tests/` и по этому файлу на реальные `file_id`/токены, использованные при
      скачивании (шаг 1) — токен бота и полные `file_path` не должны попасть ни в коммит, ни в
      этот файл.
- [ ] `pytest tests/unit tests/integration -q`, `ruff check src/ tests/`, `mypy src/` — зелёные.

## Готово, когда

- Секция 1 выполнена и три попарные дистанции записаны здесь (или в тикете) — независимо от
  того, уложились они в порог или нет (это доказательство хеш-корректности, не гарантия
  результата).
- Секция 2 выполнена хотя бы один раз на живом боте.
- Секция 3 — гигиена подтверждена.
