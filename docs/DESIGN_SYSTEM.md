# Cyber Office · Arasaka × Retro-Futurism

Arasaka × Retro-Futurism is the sole visual standard for Cyber Office. It covers views 01–04, the Bridge feed, both dialogs, and every loading, empty, error and receipt state.
The portrait, scanning reticle and engineering marks are presentation assets. SQLite owns Goal, Worker,
lease, budget and heartbeat state. Human controls display the backend's transaction receipt.

## Operator terminology

Use these Cyberpunk 2077 terms and project-specific subtitle pairings in navigation and headings.

| Section or instrument | English subtitle |
|---|---|
| 01 生物监测 | BIOMONITOR · RELIC |
| 02 魔偶阵列 | DAEMONS · CYBERDECK |
| 03 分离芯片 | SHARDS · DATABASE |
| 04 入侵协议 | BREACH PROTOCOL · ICE |
| Relic 脉搏 | LOCAL · ZERO-TOKEN PING |
| 内存与黑墙 | RAM · BLACKWALL QUOTA |

Caption the portrait **ENGRAM · 01 // 愚者**. Label NOOP **静默 (PING)** and WOKE **唤醒 (UPLOAD)**.
An empty quota snapshot list reads **内存配额未上报 · 保守锁定**. A failed projection retains its
unavailable message. Reserve **神经链路 · 已接驳** for a live stream and **接入仓已解锁** for
operator-enabled controls. The backend checks the token on each request.

| Button | Backend operation |
|---|---|
| 挂起队列 | SOFT PAUSE |
| 系统重置 | HARD PAUSE |
| 重新接驳 | RESUME |
| 注入魔偶 | STEER |
| 强制断连 | STOP |
| 黑墙封锁（禁止自主漫游） | Pause global exploration |
| 开启黑墙通道 | Resume global exploration |

Goal confirmations name the operation, its effect and the selected Goal. The global exploration
confirmation names the database-wide scope. Display state, version and cursor from the receipt.

## Colour, type and material

| CSS token | Value | Use |
|---|---|---|
| `--canvas` | `#050507` | Deep black page and shadow |
| `--surface` | `#0B0B0E` | Instrument plates and dialogs |
| `--ink` / `--highlight` | `#ECEAE4` / `#FFFFFF` | Cold silver type and silver numerical highlights |
| `--silver` / `--muted` | `#C6C5CA` / `#A4A2A8` | NOOP marks, inscriptions, secondary text |
| `--crimson` / `--crimson-shadow` | `#D92323` / `#6E1114` | Active rail, UPLOAD spikes, live indicators and selected gradients |
| `--danger` / `--danger-text` | `var(--crimson)` / `var(--ink)` | Emergency outlines and legible silver warning text |
| `--line` / `--edge` | `#353439` / `#67666C` | Graticules, frame incisions and metal edges |

The local serif stack pairs Chinese Song-style headings with monospace English instrument codes. Tabular
monospace serves counts, times, pool readings, Goal IDs and hashes. The page title scales from
32 to 46 px. Section titles use 22–27 px and the principal numerical readings use 40 px on desktop.
Secondary details wrap inside their plate. Font loading uses system faces.

A repeating 128 px grain tile sits in the page and plate **backgrounds**, behind type and focus
rings. Panels and Worker cards have 12 px, 45° cuts at opposing corners with 1 px silver
edge markings. Goal and global controls use 6 px cuts and inset keyboard focus. Instrument
corner codes identify Relic and RAM. Running Worker cards have an
additional bright lip. Each Worker family has its own etched circuit trace on a shared stippled chip package. A crosshair on an empty signal plate accompanies empty-state text.

## Component states and motion

All interactive faces use the same opposing 45° cuts. Plates and dialogs use `--cut` (12 px),
buttons and fields use `--control-cut` (6 px), and status capsules use `--badge-cut` (3 px).
Keep a 1 px hairline along the cut. Selects retain keyboard and native selection semantics
with a calibrated chevron. Supporting browsers also skin the open picker. Both modal forms
carry an engineering identifier and open with focus inside the native dialog.

| Button state | Visible treatment | Behaviour |
|---|---|---|
| Rest | Cold edge, silver label | Available actions reflect the current projection |
| Hover / keyboard focus | Crimson edge and inward corner marks, plus an inset keyboard focus outline | No request |
| Pressed | Dark crimson fill and tighter marks | A Goal or global action opens confirmation |
| Confirming high risk | Crimson dialog edge and submit control | Shows the operation, effect, and scope |
| Pending / unavailable | Muted disabled controls and explicit policy or pending text | Duplicate submission stays gated |
| Receipt / error | Returned state and revision, or a marked error | The backend adjudicates the change |

| Token | Duration | Use |
|---|---|---|
| `--motion` | 160 ms | Button edges and registration marks |
| `--motion-entry` | 200 ms | A line draws beneath the destination heading or dialog title |
| `--motion-upload` | 420 ms | One red ripple and a grain brightness lift per fresh, visible WOKE sequence |
| `--motion-crt` | 4.8 s | Low-amplitude phosphor light and waveform scanline breathing |
| `--motion-orbit` | 120 s | One precision reticle revolution while in view |

Motion layers ignore pointer events. Navigation, focus, confirmation and receipt rendering
take effect immediately. NOOP leaves the grains still. The ornamental ring is independent
of ledger activity. Reduced motion removes rotation, pulse, phosphor breathing, transitions
and entry animations, while retaining the static red spike, labels and focus marks.

## Composition

Above 1050 px a 206 px left rail carries the four numbered anchors, connection status and
settings. A crimson stripe marks the current anchor. Its footer bears the static engineering mark
`ARASAKA-SPEC // LOOPBACK`. The mark identifies the design specification. Runtime connection
and access states have their own indicators. Smaller screens use a compact horizontal masthead. A ruled
title row leads to the paired metabolic and budget instruments in a 1.65 to 1 split. The portrait
occupies the right portion of the metabolic plate. The plot below it spans the full plate width.
The host and workflow strip remains directly beneath the instruments.

The Worker matrix flows from four columns to two at 1050 px. Below 800 px the main plates stack
and navigation occupies a second line. At 560 px and below the budget instruments and Worker cards
use a single column. Memory, reflections and events share a keyboard-operable tab strip. The
operator console and read-only Bridge receipts follow. Dialogs support keyboard focus and Escape.

## Portrait and motion

`portrait.webp` is a locally bundled grayscale light map generated for this project, a photographic
study of a carved philosopher's head. The shipped Canvas renderer samples its luminance and uses
a seeded random exposure threshold with serpentine Floyd–Steinberg error diffusion. Midtones expose cold silver-gray grains, with more white grains toward highlights.
Black shows between grains. A slowly rotating pair of 1 px concentric crimson arcs and silver ticks
frames the portrait. Both rings are decorative. The raster is computed at the displayed size and device density, capped at 640 px per
side. Rotation or resizing reprints the plate when its working resolution changes.

The print has two cached grain layers. A fresh WOKE from a live host gently displaces the smaller
layer over 4.8 seconds. A host reporting active Goals can sustain that motion within its heartbeat
TTL. The newest NOOP takes priority, including on a busy host. Unavailable projections, expired
host evidence, hidden or offscreen art, and reduced-motion preferences leave a still portrait.
The idle print schedules no animation frames.

## Reading the instruments

- **Heartbeat** uses up to the latest 48 ledger entries in sequence order. Each NOOP places a small
  silver point on the lower track. Each WOKE places a crimson peak with a narrow afterimage. Axis
  endpoints are actual ledger sequence numbers. Horizontal distance denotes successive checks.
  The larger wake count uses the complete fetched window. The next-wake readout uses the earliest
  dated, sleeping, nonterminal Goal eligible for display under its Goal pause state.
- **Exploration** displays the UTC-day reservation count and the common cap from enabled host
  policies. A segmented dot ruler fills proportionally to the reserved fraction, capped at 100%.
  The numeric reading preserves the actual count. Unknown or differing caps leave the ratio
  unavailable. Global and per-Goal brakes remain visible in the budget status.
- **QuotaGuard** groups the latest fetched snapshot per quota pool into available, warning,
  critical, exhausted or unknown. Stale samples and samples without valid freshness evidence count
  as unknown. Pool details retain the reported usage and observation time. An empty window
  displays dashes and an explicit empty state.
- **Exploration cooldown** combines the most recent proposal's UTC creation time with the common
  enabled-host cooldown interval. The graduated scale shows remaining time from zero to the policy
  limit. Missing proposals, conflicting intervals or unavailable policy suppress the needle.
  Expiration indicates the end of that backoff period. Budget, quota, leases and human controls
  still govern dispatch.
- **Workers** carry separate textual state and quota capsules with dot-matrix marks. Model, node,
  last-seen time, Task and owned lease generation remain available on each card. Unknown quota
  and an expired lease have explicit readings.

## Interaction, access and authority

All API content enters the DOM through `textContent`, not HTML interpolation. A scoped bearer may
be entered in a password field and held only in memory; never URL parameters, local/session storage,
logs or cookies. Refresh forgets credentials. Same-origin REST reads and authenticated WebSocket
frames drive projections. Disconnect, unauthorized, empty, partial/stale and unavailable states are
visible. REST refresh remains available if a stream fails.

Goal controls require a selected Goal, explicit confirmation and a nonempty reason/instruction. SOFT
PAUSE, HARD PAUSE, RESUME, STOP and STEER POST to the existing authorized backend. The UI does not
optimistically mutate Goal state. It shows request-in-flight, errors, and the actual receipt with
state/version/cursor. Disabled mutations remain disabled even with a supplied token. STOP and HARD
PAUSE have separate warning treatment; confirmation can be cancelled with Escape. Success is only a
backend receipt, never a frontend inference.

The database-wide exploration control has its own state, revision and pause/resume button.
It works without a selected Goal. Confirmation includes the database-wide scope and reason.
The API receipt supplies the committed revision. A stale confirmation requires a fresh projection.

Static files are read-only packaged assets mounted by FastAPI; importing/running the headless CLI
does not start a web server. The site loads without external dependencies and binds no additional
ports. API authorization and loopback defaults are unchanged.

## Visual acceptance

Use the isolated SQLite fixture to compare desktop and mobile renders. Verify the printed portrait,
NOOP stillness, WOKE signal, stale and empty quota windows, keyboard focus, responsive layout and
confirmed controls. Refreshing the page clears the in-memory bearer. Loss of authorization must
remove the protected readings. The application requests assets only from its own origin.

## Isolated read-only trial

From the repository root with the project installed in `.venv`, run:

```sh
.venv/bin/python scripts/observe_office.py
```

Open `http://127.0.0.1:7330/office/` after the first NOOP. This trial explicitly permits
token-free loopback observation in a newly created directory. An ordinary API requires an
`observe:read` bearer in **连接设置**. Human controls additionally require `goal:control` and
an operator-enabled writable API.

See [authenticated startup and token creation](AUTONOMOUS_HOST.md#authenticated-operator-access).
Read-only deployments expose the control state with the action disabled.

The WebSocket first-frame contract is `{ "type": "authenticate", "token": "…" }`, negotiated with
`chips-observe-v1`. It has strict types, no additional fields, a bounded size and a five-second
deadline. No snapshot precedes authentication. Revocation closes an active connection, and client
closure releases its server task promptly. Post-authentication incoming frames are rejected; all
human control uses the separately authorized REST endpoints. Header-authenticated V0 clients keep
their existing behavior. REST polling provides a fallback when a stream is unavailable.

Python API integration gates live in `tests/test_cyber_office.py`. Optional browser acceptance uses
the `ui-test` dependency extra and an explicitly installed Chromium, with no provider/model calls:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/run_cyber_office_acceptance.py \
  --browser-binary /path/to/chromium
```

`--screenshots NEW_DIRECTORY` saves desktop/mobile fixture screenshots including modal states without overwriting an existing
directory. The script creates disposable SQLite stores and ephemeral loopback servers, exercises
REST/stream projections, all five Goal controls and the global exploration brake, denied authentication, XSS-safe text rendering, empty
states, responsive layout and cleanup. Its generated test tokens stay in memory. No fixture enters
the operator's database. Browser downloads are never automatic.
