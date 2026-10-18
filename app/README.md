# The Kaze phone app

Three screens on the phone (Read, Sight, Steps), drawn by the Mac. The phone runs a thin Android shell: a
full-screen browser window with no address bar that opens the Kaze server and signs in with one token. Everything on
screen is a web page served from `app/web/`, so **changing a screen is a file edit on the Mac, not a new APK**.

    phone ──https──▶ your tunnel or reverse proxy ──▶ 127.0.0.1:8791 (app/server) ──▶ app/web + live/latest.json

## What runs where

| Piece | Where | What it does |
|---|---|---|
| `app/server/app_server.py` | the Mac, as launchd job `com.kaze.app` (KeepAlive) | listens on `127.0.0.1:KAZE_APP_PORT` (default 8791); serves `app/web/`, `/latest.json` and `/bars.json`; HTTP Basic auth, user `kaze`, password `KAZE_APP_TOKEN` from `.env`, compared in constant time; 401 otherwise |
| `app/web/` | the Mac, read by the server | `index.html` (the shell and the three views), `app.css`, `app.js` (one fetch every 30 s and on return to the app, one render per block), `fonts/` (Plus Jakarta Sans, OFL) |
| `app/android/` | built on the Mac, installed on the phone | the shell: one Activity, a WebView, pull-to-refresh, back walks the page history; `KAZE_URL` and `KAZE_TOKEN` come from `local.properties` and are compiled in |

### Routes

| Route | What comes back | Cache |
|---|---|---|
| `/` and the files under `app/web/` | the page, its stylesheet, script and font; no directory listings, nothing outside `app/web` | `no-cache` |
| `/latest.json` | `$KAZE_DATA/live/latest.json`, read fresh on every request (the engine's contract, `docs/data_map.md`) | `no-store` |
| `/bars.json` | `{trade_date, bars: [{at, close}]}`: the session's 1-minute SPX closes, read from the store over a read-only connection and rebuilt at most every 20 s; the session is the one `latest.json` describes (the date of its `cut_at`), today's New York date when there is no feed | `no-store` |

The server logs one line per request to `$KAZE_DATA/logs/app_log.txt` (route, status, milliseconds), never the
token or a body. The one hop in front of it, the https address the phone reaches, is configured outside this repo:
point a tunnel or a reverse proxy at `127.0.0.1:8791` and give the phone that address as `KAZE_URL`.

## Setup on the Mac

Add the two lines to `.env` (see `.env.example`):

    KAZE_APP_PORT=8791
    KAZE_APP_TOKEN=<python3 -c "import secrets; print(secrets.token_urlsafe(32))">

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
    gradle assembleDebug                            # app/build/outputs/apk/debug/app-debug.apk (about 3 MB)

A missing value in `local.properties` fails the build by name. `gradle assembleRelease` wants a signing key
(`keystore.properties` beside `build.gradle.kts`, git-ignored; the build error prints the `keytool` command). There is
no `gradlew` in the tree: the wrapper is a binary jar, and a binary in a public repo is a thing nobody reads.

Every build prints one warning that is not a problem: `This version only understands SDK XML versions up to 3`. It
means the command-line tools are newer than the Android Gradle Plugin; the APK is correct.

## Installing on the phone

Over a cable: `adb install app/build/outputs/apk/debug/app-debug.apk`. Or copy the APK somewhere the phone can
download it, open it in Chrome and allow Chrome to install unknown apps once (Settings → Apps → Chrome → Install
unknown apps). The APK carries the token, so treat it like the token: do not leave it on a public path.

## Changing a screen

Edit `app/web/index.html`, `app.css` or `app.js` and pull down on the phone. The page is served `no-cache`, so the
next load is the new file. The APK changes only when the shell changes (the server address, the token, the colours
compiled into the window), which is rare.

The data contract is `latest.json` alone, plus `bars.json` for the price line. Where the feed has no value the page
shows `—`; where a step failed the page says so (`failed`, `range_failed`, `push_failed`, `grade_failed`) in the
banner at the top and in the block the failure empties; a feed older than six minutes is marked `Stale` with its age.
Times are rendered in the phone's own zone.
