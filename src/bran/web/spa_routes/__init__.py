"""The SPA's JSON API, one module per area.

`bran.web.spa_api` assembles these under `/spa` behind the same-origin guard.
Handlers that only read or write SQLite are plain `def`s, so FastAPI runs
them on its threadpool and a slow query cannot stall the event loop (chat
streams, live run streams and the scheduler all live on it). Handlers that
touch the loop — starting a background run, cancelling one, talking to the
live scheduler, streaming — stay `async`.
"""
