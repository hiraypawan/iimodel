# Browser Mobile, Voice, and Local Models

## Current status

v0.4 ships an installable standards-based PWA at `web/app.html` for Android Chrome, iOS Safari, and desktop browsers. It includes:

- a service-worker offline shell;
- PBKDF2/AES-256-GCM encrypted local conversations and job receipts;
- a non-persisted passphrase and session-only API token;
- push-to-talk while the page/PWA is open;
- background-job submission, status UI, and completion notifications;
- WebGPU, storage, microphone, memory, and notification capability reporting;
- a strict `registerIILocalModelAdapter(adapter)` foreground adapter boundary.

No local model adapter or phone checkpoint is bundled. If no adapter is registered, local inference is shown as unavailable and no answer is fabricated.

## Browser limits

A browser PWA is not a reliable replacement for a native always-on assistant:

- microphone capture can stop when the app is backgrounded or the screen locks;
- iOS/Android can suspend JavaScript, timers, service workers, and WebGPU work;
- the Web Speech recognition implementation may be absent or provider-backed rather than offline;
- background sync and push availability varies by OS/browser/install state;
- storage can be evicted unless persistence is granted;
- WebGPU availability does not prove enough RAM, model compatibility, thermal stability, or sustained performance.

Accordingly, II claims **push-to-talk while open**, not an always-on wake word, screen-off listening, or screen-off local inference.

## Product rule

The instant reflex path must be local. A random multi-host internet pipeline must never be required to recognize a wake word, stop speech, set a timer, or begin a response.

| Request class | Default execution | Current software |
|---|---|---|
| Reflex | deterministic/native or local | adapter boundary only |
| Private local | local phone/desktop model | foreground browser adapter boundary |
| Interactive hard | stable complete worker or optional cloud | background worker API |
| Background | worker replicas | implemented |
| Distributed research | explicit LAN pool | deterministic transport reference only |

The user must be able to avoid remote execution.

## PWA security design

The local vault:

- derives a non-extractable AES key using PBKDF2-SHA-256 with 310,000 iterations and a random salt;
- encrypts each IndexedDB record with AES-GCM, a random IV, and record-specific authenticated data;
- stores only an encrypted verifier—not the passphrase or key;
- keeps coordinator access tokens in `sessionStorage`, not IndexedDB or the service-worker cache;
- explicitly allowlists shell assets, so authenticated API responses are never cached.

The coordinator stores Web Push endpoints and keys encrypted. Actual server push requires a configured sender/VAPID deployment; otherwise the app displays notifications while it can check status and resumes polling when opened.

## Push-to-talk

While visible, the PWA uses the browser Speech Recognition interface when available. If only `MediaRecorder` exists, it requests the microphone but discards captured audio because there is no approved transcription adapter. It does not upload or persist fallback audio.

A production offline voice stack would still need:

- wake-word detector;
- voice activity detector;
- offline STT;
- benchmark-selected small Qwen-family or other local model;
- deterministic action dispatcher;
- offline TTS;
- signed artifact updater;
- explicit local/private/background policy;
- battery, thermal, headset, storage, and permission-revocation handling.

## Exact model gate

A Qwen-family checkpoint is a candidate strategy, not a selected dependency. Selection requires:

```text
exact checkpoint and tokenizer hashes
license and commercial redistribution review
quantization and conversion record
named Android/iOS/desktop targets
first-token and sustained decode results
peak memory including KV/audio/runtime buffers
thermal and battery results
quality and safety benchmark report
```

Weight-file size alone is not RAM use.

## Native Android decision

If product requirements include wake word, screen-off use, guaranteed offline STT/TTS, or robust long-running local inference, build a native Android foreground service after the browser device matrix. The current sandbox does not contain Java/Gradle/Xcode and no native package was fabricated.

Native validation must cover process death/restoration, microphone disclosure, screen-off operation, thermal throttling, charging/Wi-Fi policies, metered data, low storage, interrupted updates, Bluetooth/headset routes, and permission revocation.

## Measurement matrix

For named low/mid/high devices and browser/OS versions, publish:

```text
installation/update/offline-shell success
vault setup/unlock/data-loss behavior
microphone and speech-interface behavior
notification/push behavior when foreground/background/locked
WebGPU adapter and local-model compatibility
first local action and first audio p50/p95
peak memory and storage
battery per 10 minutes active dialogue
thermal throttling after 20 minutes
offline success rate
```

Targets such as “local action under 300 ms p50” remain targets until measured.

## Remote fallback policy

Before remote submission, expose sensitivity, expected delay/cost, model/worker policy, and cancellation. Secrets, payment credentials, identity documents, and private-vault content should default to local-only.

If no worker is available:

1. use a compatible registered local adapter if permitted;
2. offer a queued background retry;
3. use cloud only with explicit user policy;
4. otherwise state that the capability is unavailable.
