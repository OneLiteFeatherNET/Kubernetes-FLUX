# Dragonfly shared Redis — DB allocations

All apps below share one Dragonfly instance (`dragonfly.dragonfly.svc.cluster.local:6379`,
password in secret `dragonfly-auth` / namespace `dragonfly`), separated by Redis DB number
(`SELECT n`). Check this table before assigning a new DB to an app.

| DB | App | Purpose |
|---|---|---|
| 0 | Harbor | core |
| 1 | Harbor | jobservice |
| 2 | Harbor | registry |
| 5 | Harbor | trivy |
| 6 | Harbor | cache |
| 7 | Harbor | cache-layer |
| 8 | shlink | cache |
| 9 | Outline | cache/queues |
| 10 | Outline | collaboration |
| 3 | Sentry | Celery broker, buffers, quotas, rate limits |
| 11 | n8n | Bull queue |
| 13 | Penpot | cache/sessions + realtime collaboration (`redis-uri`) |

Free: 4, 12, 14, 15.

⚠️ Sentry on DB 3 is the heaviest user here — it drives this instance as a
Celery broker, not just as a cache. If other apps start seeing Redis latency,
suspect Sentry first and consider moving it to a dedicated Dragonfly.

DB 12 was Plane's; it became free when Plane was removed in favour of Penpot.
