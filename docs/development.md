# Development: online mode

In online mode TraceLite Insights works against a running TraceLite: the simulator sends its events to TraceLite, and `insights sync` reads the resulting issues and events back into the DuckDB database. Offline mode (`insights load`, see the README) needs none of this.

## 1. Run TraceLite locally

Follow [Getting started in the TraceLite README](https://github.com/aref9571/tracelite#getting-started). It needs JDK 21 and Docker with Compose v2, and serves the API on `http://localhost:8080`.

The simulator sends events for three applications, so TraceLite needs one ingestion key for each of them in its `.env`, next to the management key. Create every key with `openssl rand -hex 32`:

```bash
TRACELITE_SECURITY_MANAGEMENTKEY=<management key>
TRACELITE_SECURITY_PROJECTS_0_APPLICATION=checkout-service
TRACELITE_SECURITY_PROJECTS_0_INGESTIONKEY=<checkout key>
TRACELITE_SECURITY_PROJECTS_1_APPLICATION=catalog-service
TRACELITE_SECURITY_PROJECTS_1_INGESTIONKEY=<catalog key>
TRACELITE_SECURITY_PROJECTS_2_APPLICATION=auth-service
TRACELITE_SECURITY_PROJECTS_2_INGESTIONKEY=<auth key>
```

Check that TraceLite is ready:

```bash
curl http://localhost:8080/actuator/health/readiness
```

## 2. Configure TraceLite Insights

Copy `.env.example` to `.env` in this repository and put the same keys in it:

```bash
INSIGHTS_TRACELITE_BASE_URL=http://localhost:8080
INSIGHTS_TRACELITE_MANAGEMENT_KEY=<management key>
INSIGHTS_TRACELITE_INGESTION_KEYS={"checkout-service": "<checkout key>", "catalog-service": "<catalog key>", "auth-service": "<auth key>"}
```

## 3. Send simulated events

```bash
insights simulate --seed 1 --days 28 --out data/sim-1/ --send
```

The command prints how many events were sent, rejected, failed or skipped, and writes the result of every event to `data/sim-1/sent.jsonl`. TraceLite only accepts events from the last 30 days, so the simulated period is shifted to end just before the current time.

## 4. Sync into DuckDB

```bash
insights sync
```

| Option | Default | Meaning |
|---|---|---|
| `--database` | `INSIGHTS_DATABASE_PATH` | DuckDB database file; created with its folder if it does not exist |
| `--tracelite-url` | `INSIGHTS_TRACELITE_BASE_URL` | Base URL of the TraceLite server |

It prints how many issues it saw, how many of them it read events for, and how many new events it stored. A refused key, an error answer from TraceLite or an unreachable server stops the sync with exit code 1; the key is never printed.

The sync is incremental, so it can be run again at any time:

1. It reads every issue, oldest first by `firstSeenAt`, and stores its newest state.
2. It reads events only for issues that are new or whose `lastSeenAt` is later than the stored value.
3. It reads those events newest first and stops at the first event that is not newer than the newest stored event of that issue and is already stored. Events that appear twice because new events pushed them onto the next page are stored only once.
4. It stores the issue after its events, so a sync that stops half-way reads that issue again next time.

Running `insights sync` a second time without new events in TraceLite reads no events and stores nothing new.
