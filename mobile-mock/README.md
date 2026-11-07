# Mock APK: Lumen support chat (backup)

A minimal Android app for the demo, kept in case the showcase app is not ready. It has one screen and uses no libraries: plain `android.*` views, `HttpURLConnection` and `org.json`. It talks to Resolve only through `POST /chat`, `GET /api/chat/{session_id}` and `GET /api/users`.

- **Top row:** the base URL (default `http://10.0.2.2:8010`, the laptop as seen from the emulator) and the customer id. Both are saved on the device.
- **Users:** picks a seeded user from `GET /api/users` and starts a new session.
- **New session:** creates a new random `session_id` and clears the chat.
- **Actions:** shows the tool calls from the last reply and the server's events for the current session (`GET /api/chat/{session}`). Each one is marked allowed or blocked, with the reason.
- **Handoff:** when a reply has `handed_off: true`, a yellow banner says "A teammate will reply here".
- **Errors:** shown in the status line and as a toast. The app does not crash when the network fails.

## Build (offline, with what is already on the machine)

```bash
cd mobile-mock
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties
export JAVA_HOME=$HOME/Library/Java/JavaVirtualMachines/temurin-21.0.7/Contents/Home
./gradlew --offline assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

The build uses Gradle 8.9 (the wrapper picks up the cached distribution), AGP 8.5.2, Kotlin 1.9.24, compileSdk/targetSdk 34 and minSdk 24. `build.gradle` pins `kotlin-reflect:1.9.23` and `kotlin-stdlib:1.9.24` on the build classpath, because AGP asks for 1.9.20, which is not in the local cache.

Install: `adb install -r app/build/outputs/apk/debug/app-debug.apk`

## Point it at the laptop

- **Emulator:** the default `http://10.0.2.2:8010` reaches the laptop's `localhost:8010`.
- **Phone:** put the phone and the laptop on the same Wi-Fi. Start the server on all interfaces with `python -m resolve --host 0.0.0.0 --port 8010`, find the laptop's IP with `ipconfig getifaddr en0`, and enter `http://<laptop-LAN-IP>:8010` in the app. If it cannot connect, check the macOS firewall and any guest-Wi-Fi client isolation.

Plain `http` works because the manifest sets `usesCleartextTraffic="true"`.

## Declared stubs and limits

- The session id lives in memory only. Restarting the app starts a new session, and earlier messages are not reloaded from the server.
- Cleartext HTTP is allowed in every build type, not only debug. This app is only meant for the demo.
- The app has no icon of its own (it uses the system default) and is signed only with the debug key.
- No polling: a reply from a human teammate after a handoff does not show up in the app.
