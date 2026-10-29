"""Kosten-App: kleine Web-App fürs iPhone und iPad — Kosten der Sprachmodelle in Euro, einfache Verwaltung der
OpenRouter-Keys. Start: `python -m llm_core.kosten_app` (nur localhost; Server: server/, README „Kosten-App“).

Starlette, Jinja2 und Uvicorn kommen aus dem Extra `app` und werden nur in `web.py` und `__main__.py` importiert;
`kurs`, `daten` und `logik` laufen ohne Web-Framework (testbar, wiederverwendbar).
"""
