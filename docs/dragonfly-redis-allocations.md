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
| 11 | n8n | Bull queue |
| 12 | Plane | cache/sessions (`REDIS_URL`) |

Free: 3, 4, 13, 14, 15.
