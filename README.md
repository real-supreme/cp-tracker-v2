# Clash Panda Tracker

An asynchronous backend for tracking **Clash of Clans' clans, players, and wars** against the official Clash of Clans API.

Built to handle large-scale entity tracking, change detection, and API rate limits while keeping memory usage predictable and the database as the source of truth.

**Copyright (C) 2022–2025 Emmanuel Smiju (real-supreme).**  
Licensed under [AGPL-3.0](LICENSE).

## Developer's Note

Clash Panda Tracker originally powered the backend of the Clash Panda Discord Bot.

Some parts of the original implementation and dataset have been intentionally modified or omitted before making this repository public. In particular, information related to Farm Clans, Farm Wars, and war-weight tracking has been censored to preserve the privacy of the original tracking operations.

Initial Designs included using [coc.py Dependency](https://github.com/mathsman5133/coc.py), but with the In memory comparision of all tracked clans, I wanted to move away to a controlled setup which dealt with a more memory-friendly approach of tracking clans. Hence the rise of our custom **Throttler** and **priority handlers**.

### Why make it public?

Building a tracker for thousands of clans and tens of thousands of players was an interesting engineering challenge, particularly before AI-assisted development became commonplace.

This repository is a cleaned-up, public version of the backend that powered those operations. It retains much of the original data flow and architectural approach, with modifications to naming conventions, formatting, documentation and redacted APIs.

Some documentation was AI-assisted during the process of preparing the repository for public release.

The goal is to share the engineering behind the project: handling large datasets, scheduling API requests, detecting changes efficiently, and building a reliable asynchronous tracking pipeline with strict memory limits.

## How It Works

The database is the source of truth for all tracked entities.

1. **Entity Discovery:** Clans and players are loaded from the database. Newly searched entities can be added to the tracking pool dynamically.
2. **Scheduled Polling:** Workers fetch entity data from the Clash of Clans API using a shared, throttled HTTP client.
3. **Change Detection:** Each entity's data is hashed. When its hash changes, a comparison determines exactly what changed.
4. **Event Generation:** Detected changes are recorded in an audit database, while the entity's current state is updated in the main database.
5. **Historical Tracking:** Changes are preserved as progressive events, allowing the system to track transitions such as a player joining a clan or being promoted to Elder.
6. **API Access:** FastAPI endpoints expose the tracked data to authorized users and services.
7. **Discord Bot Integration:** Clash Panda's Discord Bot reads directly from the database rather than repeatedly querying the API, reducing network overhead and unnecessary API calls.

## Features

- **Clan, Player & War Tracking:** Tracks entities against the official Clash of Clans API.
- **Database-First Architecture:** The database remains the source of truth. Memory usage is bounded by the worker count and batch size, with approximately 5 KB of entity data per in-flight item.
- **Shared HTTP Client:** Polling workers and FastAPI on-demand requests share a throttled HTTP client.
- **Priority-Driven Scheduling:** API access interest and entity volatility (rate of detected changes) influence refresh priority.
- **Adaptive Polling:** Refresh intervals adjust dynamically based on entity activity and scheduling priorities.
- **Cache-Control Floors:** Minimum refresh intervals are enforced per entity to avoid unnecessary API requests.
- **War Lifecycle Awareness:** Wars are polled only during battle day.
- **Dead-Entity Handling:** Permanently irrelevant entities are excluded from future polling. This includes 404 responses, `ENDED` wars, and `NOT_IN_WAR` war records.
- **Rate-Limit Recovery:** On HTTP 429 responses, polling capacity is automatically halved. The system sends a single notification when throttling begins and another when normal operation recovers.

## Configuration

Configuration is intentionally split into two categories.

| Configuration | Location | Purpose |
|---|---|---|
| Secrets | `.env` | API keys, database credentials, client keys, SMTP credentials |
| Tunables | `config/config.yaml` | Application and tracking parameters |
| Database backend | `DATABASE_URL` in `.env` | Selects the database connection |

Supported database backends:

- CockroachDB Cloud
- CockroachDB OSS
- PostgreSQL

Example configuration files are provided:

- `.env.example`
- `config/config.example.yaml`

## Quick Start

### 1. Clone the repository

```bash
git clone <repository-url>
cd clash-panda-tracker
```

### 2. Prepare configuration

```bash
cp .env.example .env
cp config/config.example.yaml config/config.yaml
```

### 3. Configure environment variables

Update `.env` with the required credentials and configuration:

- `COC_API_KEYS`
- `DATABASE_URL`
- `API_CLIENT_KEYS`
- `SMTP_*`

### 4. Start the application

```bash
make up
```

Refer to the example configuration files for available options and their expected values.

---

**A project built around a simple idea:** tracking thousands of entities reliably is not just about making API requests. It's about scheduling, prioritization, rate-limit management, efficient change detection, and keeping the database consistent.