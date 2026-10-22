# Phone Voice / Jarvis Plan

Status: specification only. No Android or iOS client is shipped in this repository.

## Product rule

The instant voice path is local. A random multi-host internet pipeline must never be required to recognize the wake word, stop speech, set a timer, or begin a response.

## Target request classes

| Class | Default execution | Example |
|---|---|---|
| Reflex | Phone deterministic/local | stop, continue, timer |
| Private local | Phone model | summarize a local note |
| Interactive hard | Best stable complete worker or optional cloud | coding question |
| Background | Worker swarm | research, long document |
| Research distributed | Explicit experimental pool | LAN pipeline benchmark |

The user must be able to disable all remote execution.

## Phone components

- wake-word detector,
- voice activity detector,
- offline STT,
- benchmark-selected small local model,
- local tool/action dispatcher,
- offline TTS,
- encrypted local memory with visible consent,
- network policy router,
- download/update verifier using the signed manifest protocol.

A Qwen-family checkpoint is a candidate, not a committed dependency. Exact checkpoint, quantization, license, and model-card terms require verification. Weight size alone is not RAM use; benchmark runtime buffers, KV cache, audio models, OS pressure, and thermal behavior.

## Android operating constraints

The implementation must account for:

- foreground-service and microphone disclosures,
- background execution limits,
- process death and state restoration,
- screen-off tests,
- thermal throttling,
- charging-only/Wi-Fi-only host preferences,
- metered-data consent,
- low-storage handling,
- interrupted signed updates,
- headset/Bluetooth audio routes,
- permission revocation.

The initial phone should route and run its local reflex model. It should not host WAN transformer stages.

## Latency service-level objectives

Measure on named low/mid/high target Android devices:

```text
Wake detection p50/p95
End-of-speech detection p50/p95
First local action p50/p95
First spoken audio p50/p95
Peak RSS
Battery per 10 minutes of active dialogue
Thermal throttling after 20 minutes
Offline success rate
```

Initial targets:

- local action under 300 ms p50,
- first spoken audio under 1 second p50 for reflex tasks,
- no network dependency for the local suite.

These are targets, not current claims.

## Local-versus-remote policy

Before remote submission, show or enforce:

- sensitivity classification,
- requested job class,
- expected delay/cost,
- selected model/worker policy,
- cloud permission,
- user cancellation.

Secrets, payment credentials, private-vault content, and identity documents should default to local-only.

## Voice fallback

If no worker is available:

1. answer locally if within capability,
2. offer a queued background retry,
3. use cloud only if the user enabled it,
4. otherwise state that deep work is unavailable.

Never fabricate a swarm answer.

## Delivery phases

1. Android shell and signed artifact updater.
2. Wake word + offline STT/TTS without an LLM.
3. Small local model benchmark bake-off.
4. Deterministic local actions.
5. Background `/jobs` submission and notification.
6. Privacy settings, battery policies, and device matrix.
7. Closed pilot in India before app-store launch.
