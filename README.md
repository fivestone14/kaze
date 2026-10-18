# Kaze

A range-first SPX forecast and the Android app that shows it.

Every 30 minutes Kaze measures how fast the index is moving right now and turns
that into the range it should end inside: today's tape sets the number, history
only shapes it, and direction stays 50/50 until a reading earns a say.

## Layout

| Folder | What it holds |
|---|---|
| `engine/` | Collect, store, range, grading, dials, jobs and the live feed |
| `app/` | The Android shell that shows the four screens: Read, Sight, Steps, Trade |
| `print/` | The printable sight board and worked-out sheet |
| `docs/` | Plan, data map and decisions |
| `checks/` | Git hooks that block secrets from ever reaching this repo |

Folders appear as each build phase lands.

## Setup

    cp .env.example .env            # then fill in the two paths
    git config core.hooksPath checks

Private data lives outside this repo, in the folder `KAZE_DATA` points to.
