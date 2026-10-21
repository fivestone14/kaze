# The Kaze phone app

Three screens on the phone (Read, Sight, Steps), drawn by the Mac. The phone runs a thin Android shell: a
full-screen browser window with no address bar that opens the Kaze server and signs in with one token. Everything on
screen is a web page served from `app/web/`, so **changing a screen is a file edit on the Mac, not a new APK**.

    phone ──https──▶ your tunnel ──▶ 127.0.0.1:8791 (app/server) ──▶ app/web + live/latest.json
                                                                 └──▶ Mirai's bar file + live price file

## What runs where

| Piece | Where | What it does |
|---|---|---|
| `app/server/app_server.py` | the Mac (`com.kaze.app`) | the web server: screens, feed, bars, live price, one token |
| `app/web/` | the Mac, read by the server | the three screens: `index.html`, `app.css`, `app.js`, `fonts/` |
| `app/android/` | built on the Mac, on the phone | the shell: one Activity, a WebView, pull-to-refresh, back |

The server listens on `127.0.0.1` only (default port 8791, `KAZE_APP_PORT` in `.env`). Every request needs HTTP Basic
auth, user `kaze`, password `KAZE_APP_TOKEN` from `.env`, compared in constant time; anything else is a 401. launchd
keeps it alive (`KeepAlive`, a minute between restarts). The shell holds `KAZE_URL` and `KAZE_TOKEN` from
`local.properties`, compiled in, and answers the server's 401 with them on every request, page, files and fetches.

### Routes

| Route | What comes back | Cache |
|---|---|---|
| `/` and the files under `app/web/` | the page, stylesheet, script, font; nothing outside `app/web` | `no-cache` |
| `/latest.json` | `$KAZE_DATA/live/latest.json`, read fresh each request (`docs/data_map.md`) | `no-store` |
| `/bars.json` | `{trade_date, bars: [{at, close}]}`: the session's 1-minute SPX closes, Mirai's bar file | `no-store` |
| `/price.stream` | server-sent events: `data: {"price", "at"}` each time Mirai's live price changes | `no-cache` |

`/bars.json` reads Mirai's 1-minute bar file for the session directly (not the store), rebuilt each time that file
grows, so the price line moves every minute between reads; the session is the one `latest.json` describes (the date of
its `cut_at`), today's New York date when there is no feed, and a missing file is an empty list. `/price.stream`
(`text/event-stream`, no compression) polls `market/spx_live_price_latest.json` under Mirai's data folder every 250 ms
and sends an event only when the price or its trade time changes (the current one at once on connect), a comment line
every 15 s so the tunnel (which drops a connection quiet for 100 s) keeps it, and closes after 30 minutes for the page
to reopen; a fifth stream at once is a 503 with `Retry-After: 30`. A stream ends when the phone closes its end (the
server checks four times a second) or after the 30 minutes. The page is sent with `Content-Security-Policy:
default-src 'self'` (no inline styles or scripts anywhere), so nothing but the Mac's own files can run in it. Any method
but GET and HEAD is a 401 without the token and a 405 with it. A crash inside a request is a 503 and one log line; a
client that drops the connection mid-reply is one log line; nothing prints a traceback (the server's error hook is one
log line too).

The server logs one line per request to `$KAZE_DATA/logs/app_log.txt` (route cut to 120 characters, status,
milliseconds), never the token or a body. The one hop in front of it, the https address the phone reaches, is
configured outside this repo: point a tunnel or a reverse proxy at `127.0.0.1:8791` and give the phone that address
as `KAZE_URL`.

**`KAZE_URL` must be https.** The token travels in every request as HTTP Basic, so the only hop that may see it in
clear is the one inside the Mac. The shell refuses cleartext (`usesCleartextTraffic="false"`) and the server listens
on `127.0.0.1` only; there is deliberately no Wi-Fi or http mode.

## Setup on the Mac

Add the two lines to `.env` (see `.env.example`) and keep the file to yourself:

    KAZE_APP_PORT=8791
    KAZE_APP_TOKEN=<python3 -c "import secrets; print(secrets.token_urlsafe(32))">
    chmod 600 .env

Then `engine/jobs/install_jobs.sh` installs `com.kaze.app` beside the read and the nightly, or run it by hand:

    python3 -m app.server.app_server

After changing the token, restart the running server (`launchctl kickstart -k gui/$(id -u)/com.kaze.app`) and
rebuild the APK, which carries the token; after changing the tunnel address, rebuild the APK alone.

## Building the APK

The toolchain, once (about 1 GB, not Android Studio):

    brew install openjdk@17 gradle
    brew install --cask android-commandlinetools
    export JAVA_HOME=/opt/homebrew/opt/openjdk@17 ANDROID_HOME="$HOME/Library/Android/sdk"
    yes | sdkmanager --sdk_root="$ANDROID_HOME" --licenses
    sdkmanager --sdk_root="$ANDROID_HOME" "platform-tools" "platforms;android-34" "build-tools;34.0.0"

Then, in `app/android/`:

    cp local.properties.example local.properties    # fill in KAZE_URL and KAZE_TOKEN; never committed
    chmod 600 local.properties
    gradle assembleDebug                            # app/build/outputs/apk/debug/app-debug.apk (about 3 MB)

A missing value in `local.properties` fails the build by name. `gradle assembleRelease` wants a signing key
(`keystore.properties` beside `build.gradle.kts`, git-ignored; the build error prints the `keytool` command). Bump
`versionCode` and `versionName` in `app/build.gradle.kts` on every rebuild that goes to the phone: Android refuses an
APK whose `versionCode` is not higher than the installed one. There is no `gradlew` in the tree: the wrapper is a
binary jar, and a binary in a public repo is a thing nobody reads (the one binary here is the OFL font in
`app/web/fonts/`, with its licence beside it).

Every build prints one warning that is not a problem: `This version only understands SDK XML versions up to 3`. It
means the command-line tools are newer than the Android Gradle Plugin; the APK is correct.

## Installing on the phone

Over a cable: `adb install app/build/outputs/apk/debug/app-debug.apk`. Or copy the APK somewhere the phone can
download it, open it in Chrome and allow Chrome to install unknown apps once (Settings → Apps → Chrome → Install
unknown apps). The APK carries the token in plain text (`strings` on it shows it), so treat it like the token: do not
leave it on a public path.

## Changing a screen

Edit `app/web/index.html`, `app.css` or `app.js` and pull down on the phone. The page is served `no-cache`, so the
next load is the new file; the tab and the 5 / 30 / 60 choice survive the pull. The APK changes only when the shell
changes (the server address, the token, the colours compiled into the window), which is rare.

The data contract is `latest.json` alone, plus `bars.json` for the price line and `price.stream` for the live price;
`docs/data_map.md` ("The phone feed") lists every key, and the page reads nothing the contract does not name. Sight and
Steps explain the read the Read page's live window belongs to (its chain travels on `windows.current`); the Which Way
and Feeds cards are the newest read's. Where the feed has no value the page shows `—`; where a step failed the page says
so (`failed`, `range_failed`, `push_failed`, `grade_failed`) in the banner at the top and in the block the failure
empties; each block draws inside its own guard, so one bad field empties that block with its reason, never the page.

The Read chart's time axis ends at the live window's end and spans a fixed time per channel, 75 minutes for 5 Min,
4 hours for 30 Min and 4 hours 30 for 60 Min (`PAST_SPAN_MINUTES` in `app.js`), never starting before the session's
open: about fifteen 5-minute boxes at about 20 px each on a 360 px phone, eight 30-minute windows at about 38 px and
nine overlapping 60-minute ones at about 68 px, so no box shrinks to a sliver and no width goes to time before the
open. The first time label says `Open` while the axis starts at the open, else the start's time; the other labels are
the finished boxes' read times, left out where they would touch Now or each other. The price axis fits what is on
screen (the line, the boxes, the live cone) with a few pixels of room, plus the room the cone's lower price label
needs above the time labels; a cone label that would touch the live dot moves further out or left of it. Past reads
draw as boxes with a gauge of how much of the band the window used; windows still running (`windows.running`) draw as
dashed blue boxes with a hollow mark and a blue used-so-far gauge, and as blue Running rows in the list between the
live row and the finished ones; they carry no time label, since their middle is where Now sits. Where finished
windows overlap (60 Min), every other one from the day's first gets a thin outline so neighbours read apart.

The Average Since The Read strip keeps its two numbers at the top line's edges, `● Live −2.1` on the left and
`◌ Average −1.4` on the right (the dashed ring placed 8 px before the words from their drawn width), so they never
touch however close the needles are; every needle ends 4 px above the average band's bottom, and the numbers under the
strip (the read's price at the centre, each band's edge number at its edge) move out past their neighbour, and in from
the card's edge, where they would touch. Sight's Which Way card asks each reading a plain question and shows the
engine's short answer (`Since Yesterday · Up 37 Pts`, `Path · Choppy`; the words and their thresholds are the
engine's, `docs/data_map.md` section 8); each row is a button, and a tap shows the exact figure under the answer
(`+37 Since Close`). The engine's width dials are Band Wideners on every screen: Sight's How Far card lists all five
with their standing (`Fed Decision Day · Not Today`, `From 14:00` on a Fed day, `Due ×2.6 · Not Counted Yet`; `Data
Release In The Window · None Due` or `Due 10:00 ×1.4 · Not Counted Yet`; `Last 15 Minutes Of The Day · From 15:45` or
`Due ×1.36 · Not Counted Yet`; any of them `Counted ×n` once the ledger gives it weight; `Price Speeding
Up` and `Options Expect More Movement · Watching · Not Counted Yet` until the ledger gives them weight, then `Counted
×n`), and Steps row 7 names only the counted ones with the applied product beside them (`×1.00` and `None Counted
Yet` until one is). Rows
grow with their text instead of clipping it: nothing on the three screens is cut off with an ellipsis at 360 px.

The shell pins the WebView's text zoom at 100% (`textZoom` in `MainActivity.kt`): the page lays its boxes out in px,
and the phone's font-size setting would enlarge the letters alone and push them out of their boxes. The page reads at
the size `app.css` gives it whatever that setting is; the chart's label spacing still grows with the text if a
browser enlarges it.

The page holds `/price.stream` open while it is shown (a `fetch` with a streaming reader, signed in like every other
request), closes it when the page is hidden and reopens it when shown; each tick moves the header's price and percent
change, the end of the price line with its live dot, the running average and the Average Since The Read strip (the
window's minute closes plus the newest tick, worked out in one place, `liveNow`). With no tick for 10 s it falls back
to the 30 s poll without a word and shows the price's time instead.

The header's words come from the feed's `session` block and the phone's clock, rendered in the phone's own zone: `Read
12:35 · Price Live` within eight minutes of the read while ticks arrive, `Read 12:35 · Price 12:41:07` (the newest
price's time) once they stop, `Late · Last Read 12:35 · 9m Ago` (rust) when no read has landed for eight minutes while
the session is open, `Closed · Last Read 15:55` once the session is over, and `No Read Yet Today · Last Read Fri 10-16
15:55` (rust) once the next session has opened and thirteen minutes have passed with no newer read (the Mac asleep, the
jobs not installed, `.env` broken). The strip, the list and the pill say `Last` or `Ended` instead of `Live` whenever
the read is not live. A 401, a 404 (no feed yet), a 503 and an unreachable Mac each get their own words; an unreachable
Mac or an unreadable feed keeps the last good numbers on screen under `Last` wording. The Feeds card says `Live` for the
SPX price while ticks arrive (`Partial` and its age once they stop in session), an age for minute feeds, `Night · 09:27`
for the futures, `To 12-31` for the calendar and `Checked 08:00` for the Schwab login (grey dots while those three are
fresh for their kind), under an `n Of m OK` pill. It also carries the calendar's horizon note (`Calendar Ends
2026-12-31`, then `Calendar Ended ...` with the feed blind) and whether the nightly ran for the last session.
