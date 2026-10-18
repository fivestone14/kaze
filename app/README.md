# The Kaze phone app

Three screens on the phone (Read, Sight, Steps), drawn by the Mac. The phone runs a thin Android shell: a
full-screen browser window with no address bar that opens the Kaze server and signs in with one token. Everything on
screen is a web page served from `app/web/`, so **changing a screen is a file edit on the Mac, not a new APK**.

    phone ──https──▶ your tunnel ──▶ 127.0.0.1:8791 (app/server) ──▶ app/web + live/latest.json

## What runs where

| Piece | Where | What it does |
|---|---|---|
| `app/server/app_server.py` | the Mac (`com.kaze.app`) | the web server: the screens, the feed, the bars, one token |
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
| `/bars.json` | `{trade_date, bars: [{at, close}]}`, the session's 1-minute SPX closes from the store | `no-store` |

`/bars.json` reads the store over a read-only connection (a plain, query-only open when the WAL sidecars are absent,
as after a restore from backup), rebuilt at most every 20 s; the session is the one `latest.json` describes (the
date of its `cut_at`), today's New York date when there is no feed; a missing store is a 503 that says so, and the
server never creates one. The page is sent with `Content-Security-Policy: default-src 'self'` (no inline styles or
scripts anywhere), so nothing but the Mac's own files can run in it. Any method but GET and HEAD is a 401 without
the token and a 405 with it. A crash inside a request is a 503 and one log line; a client that drops the connection
mid-reply is one log line; nothing prints a traceback (the server's error hook is one log line too).

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

The data contract is `latest.json` alone, plus `bars.json` for the price line. Sight and Steps explain the read the
Read page's live window belongs to (its chain travels on `windows.current`); the Which Way and Feeds cards are the
newest read's. Where the feed has no value the page shows `—`; where a step failed the page says so (`failed`,
`range_failed`, `push_failed`, `grade_failed`) in the banner at the top and in the block the failure empties; each
block draws inside its own guard, so one bad field empties that block with its reason, never the page. The header
says `Live` within six minutes of the read, `Late` after that while the session is open, `Closed · Last Read 15:55`
once it is over (from the feed's `session` block), and the strip, the list and the pill say `Last` or `Ended` instead
of `Live` whenever the read is not live. A 401, a 404 (no feed yet), a 503 and an unreachable Mac each get their own
words; an unreachable Mac or an unreadable feed keeps the last good numbers on screen under `Last` wording.
Times are rendered in the phone's own zone.
