# lab-works

Скилл для агента: выполнение лабораторных работ по программированию и ИТ-дисциплинам
в вузе — от методички до сдачи. Ведёт контекст студента и ход работ, собирает
отчёты `.docx`/`.pdf` по ГОСТ 7.32, конспект для защиты, снимает доказательства
запуска и проверяет результат перед сдачей.

## Состав

```
SKILL.md                  — workflow и инварианты (что читает агент всегда)
references/               — справочники, которые агент читает по необходимости
  report-format.md        — content.json, ГОСТ, многозадачные отчёты, text.docx
  evidence.md             — скриншоты, CLI-доказательства, CDP, диалоги
  tools.md                — runner, ASCII-safe, PDF, восстановление окружения
  troubleshooting.md      — типовые блокирующие ошибки
scripts/                  — инструменты (stdlib-only, кроме PyMuPDF для PDF)
  make_docx.py            — сборщик DOCX (копируется в проект как _tools/make_docx.py)
  lab_runner.py           — сборка, конвертация и quality gate по _lab_state.json
  capture_cdp.js          — снимок страницы или CLI-вывода (Edge Headless)
  cdp.js                  — интерактив, диалоги, popup
  shot.ps1, docx2pdf.ps1  — нативные окна и DOCX → PDF
  install_skill.py        — установка/обновление скилла в каталоги агента
  tests/                  — unit-тесты Python и Node
evals/                    — сценарии оценки и корпус реальных методичек
.skill-eval/              — локальный контур оценки (pipeline.py)
```

## Требования

- Windows 10/11 (PowerShell, Win32 API для снимков окон).
- Python 3.10+ (скрипты используют только стандартную библиотеку).
- Node.js ≥ 22 для `cdp.js` и `capture_cdp.js`.
- Microsoft Edge или Google Chrome.
- Microsoft Word (COM) или LibreOffice для PDF.
- PyMuPDF (`pip install pymupdf`) — для проверки страниц PDF и чтения PDF-методичек;
  без него gate работает, но пишет предупреждение.

## Установка и обновление

Скилл устанавливается копированием каталога в место, откуда агент читает навыки:

```text
py scripts/install_skill.py --check    # сравнить установленные копии с репозиторием
py scripts/install_skill.py            # обновить с бэкапом и проверкой SHA-256
```

По умолчанию обновляются `~/.agents/skills/lab-works` и
`~/.gemini/config/skills/lab-works`. Явные цели задаются через `--target`.
Dev-каталоги (`.git`, `.skill-eval/workspace`, вендоренный tooling) не копируются,
предыдущая версия сохраняется рядом как `lab-works.backup-<время>`.

## Разработка

```text
python -m unittest discover -s scripts/tests      # Python-тесты
node --test scripts/tests/test_cdp.js             # CDP helpers
node --test scripts/tests/test_capture_cdp.js     # снимок через Edge
python .skill-eval/pipeline.py check              # структура скилла и каталог eval
python .skill-eval/pipeline.py test               # всё вышеперечисленное
```

Итеративная оценка скилла (A/B with/without skill) описана в
`.skill-eval/README.md`; журнал фактических прогонов — в `EVAL_NOTES.md`.

## Ограничения

- Снимки окон (`shot.ps1`) требуют интерактивного рабочего стола; в headless-сессиях
  используйте `capture_cdp.js`.
- Доказательство CLI — отрисованный кадр реального вывода, а не фотография консоли;
  в кадре печатаются код возврата и пометка об автоматической отрисовке.
- Репозиторий содержит файлы со смешанными переводами строк (`CRLF` и `\r\r\n`);
  нормализация вынесена в отдельную задачу.
