# logger-apk: notification logger

A tiny Android app with one `NotificationListenerService`. It watches only the three
tracked packages and posts each notification to the ingest service:

| Package | App |
|---|---|
| `com.scores365` | 365Scores |
| `com.mobilefootie.wc2010` | FotMob |
| `com.livescore` | LiveScore |

* **Persistent retry queue.** Every event goes to disk first (`filesDir/queue/`, one file per
  event, atomic rename) and is deleted only after ingest returns 2xx. A 200 duplicate
  counts as done. 408, 429, 5xx and network errors are retried every 30 s, oldest first.
  Any other 4xx moves the event to `queue/rejected/` so one bad payload can't block the queue.
* **On reconnect.** When the listener reconnects, it re-sends the notifications still on screen
  as `action: active_on_connect`. Ingest matches these against the earlier `posted` row.
* **Out of scope: data-only pushes.** They never create a notification, so a listener
  can't see them.
* **No dependencies.** The app uses only the Kotlin stdlib and the platform `org.json` and
  `HttpURLConnection`. There is no AndroidX or WorkManager.

## Build status

**Builds, and its unit tests pass.** It was first built on 2026-11-06 with AGP 8.7.3, JDK 21
and SDK platform 36. `./gradlew test assembleDebug` produces `notif-logger-<instance>-debug.apk`
(about 2.3 MB). The APK declares the listener service with `BIND_NOTIFICATION_LISTENER_SERVICE`
and has the instance ID and ingest URL built in. All 5 `RetryQueueTest` tests pass.

**Not yet run on a device.** Nothing has installed it on an emulator or a ReDroid instance, so
the listener, the uploader and the retry loop are untested on Android (checklist step 8).

In a cloud session, the `scores_research` environment's setup script installs the SDK at
`/opt/android-sdk` and writes `local.properties`. If Maven Central answers 429 (rate limited),
rerun the build; Gradle keeps what it already downloaded.

## Build (one APK per instance)

Requirements: JDK 17+ and an Android SDK with platform 36 (Android 16). Put
`sdk.dir=...` in `local.properties` or set `ANDROID_HOME`.

`INSTANCE_ID` and `INGEST_URL` are compiled into `BuildConfig` from Gradle properties:

```bash
cd logger-apk
./gradlew assembleDebug -PinstanceId=uk-c4-fotmob -PingestUrl=http://172.30.1.3:8000
# -> app/build/outputs/apk/debug/notif-logger-uk-c4-fotmob-debug.apk
./gradlew test            # JVM unit tests for the retry queue and HTTP classification
```

To build all 38, run `make fleet` from the repo root to generate `fleet/out/manifest.json`, then:

```bash
logger-apk/build_all.sh fleet/out/manifest.json out/apks
```

Every instance reaches ingest at `.3` on its market's docker subnet (see
`fleet/vpn/README.md`). That traffic stays local and never goes through the VPN.

## Install and grant the listener

```bash
adb connect 127.0.0.1:5610
adb -s 127.0.0.1:5610 install -r out/apks/notif-logger-uk-c4-fotmob-debug.apk
adb -s 127.0.0.1:5610 shell cmd notification allow_listener \
    com.dazn.study.notiflogger/com.dazn.study.notiflogger.NotifLoggerService
# verify
adb -s 127.0.0.1:5610 shell settings get secure enabled_notification_listeners
adb -s 127.0.0.1:5610 logcat -s NotifLogger
```

The ADB port for each instance is in the manifest (`adb_port`). `ops.health` checks
both that the process is running and that the listener is enabled.

## Payload contract (`POST /v1/notification`)

```json
{
  "instance": "uk-c4-fotmob",
  "package": "com.mobilefootie.wc2010",
  "posted_at": "2026-11-03T19:53:31.000Z",
  "notification_key": "0|com.mobilefootie.wc2010|77|null|10123",
  "channel_id": "goals",
  "title": "GOAL! Arsenal 1-0 Chelsea",
  "text": "Bukayo Saka (23')",
  "big_text": null,
  "sub_text": null,
  "category": "event",
  "ongoing": false,
  "group_key": "...",
  "action": "posted",
  "remove_reason": 2,
  "logged_at": "2026-11-03T19:53:31.120Z",
  "logger_version": "0.1.0",
  "extras": {"android.title": "...", "cid": "...", "...": "full extras bundle"}
}
```

* `action` is one of `posted`, `removed` or `active_on_connect`.
* `remove_reason` is present only on `removed`; it's the `NotificationListenerService.REASON_*` value.
* `ongoing: true` is stored as `channel = live_update`.
* Ingest also accepts `posted_at` as epoch milliseconds.
