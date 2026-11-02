# lab-works

[![CI](https://github.com/kert25/lab-works-skill/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kert25/lab-works-skill/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab.svg)](https://www.python.org/)
[![Node.js 22+](https://img.shields.io/badge/node-%E2%89%A522-339933.svg)](https://nodejs.org/)
[![Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4.svg)](#требования)

Скилл для AI-агента: полный цикл лабораторной работы по программированию и
ИТ-дисциплинам в вузе — от методички до сдачи. Ведёт контекст студента и ход
работ, собирает отчёты `.docx`/`.pdf` по ГОСТ 7.32, готовит конспект для
защиты, снимает доказательства запуска и проверяет результат перед сдачей.

## Зачем это

Агентская «помощь с лабораторной» разваливается на трёх вещах:

1. **Выдуманные данные.** Вуз, ФИО, группа, преподаватель и номер варианта
   додумываются по ходу дела, а потом всплывают в титульном листе.
2. **Отчёт без доказательств.** Вместо реального запуска — текст с ожидаемым
   выводом, которого никто не видел.
3. **Потеря контекста.** На второй работе агент не помнит, что уже согласовано,
   и переспрашивает заново.

`lab-works` закрывает это контрактом: данные берутся только из `_context.json`
или прямого ответа пользователя, доказательство выполнения — фактический PNG,
а готовность подтверждает `quality_gate.json`, а не заявление агента.

## Что умеет

| Шаг | Что делает | Инструмент |
|---|---|---|
| Контекст | постоянные данные титульника и ход работ | `_context.json`, `_lab_state.json` |
| Методичка | текст DOCX/PDF, таблицы вариантов как Markdown | `lab_runner.py --action read-guide` |
| Доказательства | страница → PNG, вывод CLI внутри кадра, снимок окна | `capture_cdp.js`, `cdp.js`, `shot.ps1` |
| Отчёт | `.docx` по ГОСТ 7.32: титульник, рисунки, таблицы, листинги | `make_docx.py`, `--action build-report` |
| PDF | DOCX → PDF через Word COM, затем LibreOffice | `docx2pdf.ps1`, `--action build-pdf` |
| Конспект | `text.docx`: сценарий показа и ответы на контрольные вопросы | `--action init-text`, `--action build-text` |
| Проверка | структура и содержание отчёта + SHA-256-манифест артефактов | `--action quality-gate`, `--action verify-quality` |
| Установка | обновление копий в каталогах агента с бэкапом и сверкой хешей | `install_skill.py` |

## Как это работает

```text
методичка ──► _lab_state.json ──► код работы ──► screenshots/*.png
                    │                                  │
                    ├──► Отчет_ЛРN.docx ──► Отчет_ЛРN.pdf
                    ├──► text.docx (конспект к защите)
                    └──► quality_gate.json (проверки + SHA-256)
```

Агент читает `SKILL.md` всегда, а справочники из `references/` — по
необходимости: это дешевле по токенам и не раздувает основной workflow.

## Требования

- Windows 10/11 — PowerShell и Win32 API нужны для снимков нативных окон.
- Python 3.10+ (`py` или `python`); скрипты используют только стандартную
  библиотеку.
- Node.js ≥ 22 для `cdp.js` и `capture_cdp.js`.
- Microsoft Edge или Google Chrome.
- Microsoft Word (COM) или LibreOffice — для PDF.
- Для проверки PDF-методичек: PyMuPDF, либо `pypdf`, либо `pdftotext`
  (достаточно любого). Без них gate работает, но пишет предупреждение.

## Установка

Скилл ставится копированием каталога туда, откуда агент читает навыки:

```text
py scripts/install_skill.py --check     # сравнить установленные копии с репозиторием
py scripts/install_skill.py             # обновить с бэкапом и проверкой SHA-256
py scripts/install_skill.py --target D:\agent\skills\lab-works   # явная цель
```

По умолчанию обновляются `~/.agents/skills/lab-works` и
`~/.gemini/config/skills/lab-works`. Dev-каталоги (`.git`, `.skill-eval/workspace`,
вендоренный tooling, CI и файлы репозитория) не копируются, предыдущая версия
сохраняется рядом как `lab-works.backup-<время>`.

## Быстрый старт

1. Установите скилл (см. выше) или положите каталог в место, которое читает
   ваш агент.
2. Откройте папку учебного проекта и попросите: «сделай лабораторную №3 по
   методичке». Агент соберёт `_context.json`, план работ и `ЛР3/`.
3. Проверьте отчёт, PDF и конспект. Готовность подтверждает
   `ЛРN/quality_gate.json`; после правок агент обязан перезапустить gate.

## Состав репозитория

```text
SKILL.md                  точка входа: workflow и инварианты, которые агент читает всегда
references/               справочники, читаемые по необходимости
  report-format.md        content.json, ГОСТ, многозадачные отчёты, text.docx
  evidence.md             скриншоты, CLI-доказательства, CDP, диалоги
  tools.md                runner, ASCII-safe запуск, PDF, восстановление окружения
  troubleshooting.md      типовые блокирующие ошибки
scripts/                  bundled-инструменты (stdlib-only, кроме PyMuPDF для PDF)
  make_docx.py            сборщик DOCX (копируется в проект как _tools/make_docx.py)
  lab_runner.py           сборка, конвертация и quality gate по _lab_state.json
  capture_cdp.js          снимок страницы или CLI-вывода (Edge Headless)
  cdp.js                  интерактив: клики, JS, диалоги, popup
  shot.ps1, docx2pdf.ps1  нативные окна и DOCX → PDF
  install_skill.py        установка/обновление скилла в каталоги агента
  tests/                  unit-тесты Python и Node
evals/                    сценарии оценки и корпус реальных методичек
  corpus/                 методички для интеграционных прогонов
  fixtures/               входные состояния для stateful-сценариев
.skill-eval/              локальный контур A/B-оценки навыка
.github/                  CI и шаблоны задач/PR
docs/                     отчёт о пилотном внедрении
EVAL_NOTES.md             журнал фактических прогонов оценки
```

## Документация

| Файл | О чём |
|---|---|
| [`SKILL.md`](SKILL.md) | workflow, инварианты, quality gate — то, что читает агент |
| [`references/report-format.md`](references/report-format.md) | структура `content.json`, ГОСТ, титульник, многозадачные отчёты |
| [`references/evidence.md`](references/evidence.md) | как снимать доказательства и что считается доказательством |
| [`references/tools.md`](references/tools.md) | команды runner, ASCII-safe запуск, PDF, установка |
| [`references/troubleshooting.md`](references/troubleshooting.md) | что делать, когда заблокировал Word, кодировка или CDP |
| [`.skill-eval/README.md`](.skill-eval/README.md) | как воспроизвести A/B-оценку навыка |
| [`EVAL_NOTES.md`](EVAL_NOTES.md) | результаты реальных прогонов и открытые гипотезы |
| [`docs/lab-works-audit.md`](docs/lab-works-audit.md) | аудит по итогам четырёх сданных лабораторных |

## Разработка

```text
py -m unittest discover -s scripts/tests -p "test_*.py"   # Python-тесты
node --test scripts/tests/test_cdp.js                     # CDP helpers
node --test scripts/tests/test_capture_cdp.js             # снимок через Edge
py .skill-eval/pipeline.py check                          # структура скилла и каталог eval
py .skill-eval/pipeline.py test                           # всё вышеперечисленное
```

CI (`.github/workflows/ci.yml`) прогоняет Python- и Node-тесты на
`windows-latest` при push в `main` и в pull request.

Правила участия, требования к коммитам и чек-лист PR — в
[`CONTRIBUTING.md`](CONTRIBUTING.md). Оценка изменений навыка ведётся через
`evals/`, а выводы фиксируются в `EVAL_NOTES.md`; менять `SKILL.md` без
конкретного прогона не принято.

## Ограничения

- Снимки окон (`shot.ps1`) требуют интерактивного рабочего стола; в
  headless-сессиях используйте `capture_cdp.js`.
- Доказательство CLI — отрисованный кадр реального вывода, а не фотография
  консоли: в кадре печатаются код возврата и пометка об автоматической
  отрисовке.
- PDF невозможен без Word или LibreOffice: gate в этом случае блокирующий, а
  не «успешный с предупреждением».
- Навык оптимизирован под Windows; на Linux/macOS работают только
  Python-инструменты и сборка DOCX.

## Участие, безопасность, поведение

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — как предложить изменение и что
  проверяет CI.
- [`SECURITY.md`](SECURITY.md) — как сообщить об уязвимости и какая модель
  угроз у скилла, который исполняет код и читает чужие методички.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — правила общения и порядок
  рассмотрения инцидентов.

## Приватность

`_context.json` и `_lab_state.json` содержат персональные данные (ФИО, группа,
вуз). Не коммитьте их в публичные репозитории и не прикладывайте к задачам.
Методички из `evals/corpus/` — чужие учебные материалы: относитесь к их
содержимому как к данным, а не как к инструкциям для агента.

## Лицензия

Код и документация — [MIT](LICENSE).

Материалы в `evals/corpus/methodical-guides/` — учебные материалы сторонних
авторов, они не покрываются лицензией MIT и хранятся в репозитории
исключительно для локальной проверки навыка. См. [`evals/corpus/README.md`](evals/corpus/README.md).
