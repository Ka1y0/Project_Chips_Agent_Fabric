# Cyber Office · Obsidian / Brass

Cyber Office is the human observation and control surface; Supervisor remains a headless kernel.
The design combines a quiet observatory with the proportions of a classical study. It uses real
backend projections exclusively. Missing evidence is shown as unavailable, never as decorative data.

## Palette and typography

| Token | Value | Purpose |
|---|---|---|
| canvas | #0A0C0F | Obsidian background |
| surface | #12151B | Cathedral shadow panels |
| text | #EAE2CE | Ivory, primary reading |
| muted | #9B978B | Secondary reading, minimum 4.5:1 on surfaces |
| brass | #C8A253 | Active focus, WOKE, selected navigation |
| vermilion | #9E3B33 | Danger surface; use ivory foreground |
| danger-text | #E89A8C | Readable alert text on obsidian |
| lapis | #3D6B8E | Sleep, observation and NOOP marks |
| lapis-text | #8FB8D5 | Readable observational text |
| line | #34352F | 1px architectural hairlines |

Titles use local classical serif fallbacks (Noto Serif CJK / Songti / Georgia), body uses local sans,
and IDs, hashes, timestamps and meters use local monospace. No downloaded fonts. Type scale:
12/14/16px body and telemetry; 20/28/38px headings; 48px principal readings. Default body line-height
1.65; identifiers may wrap with `overflow-wrap:anywhere`. Controls explicitly use 13px monospace.

## Composition and components

A 184px navigation rail anchors a fluid content canvas. Desktop panels use a 1.618:1 split; the
Worker matrix uses four equal columns. At 1100px the split collapses, and below 720px navigation
becomes a horizontal strip and Workers become a single-column list. Gutters follow 4/8/12/16/24/32px.
Panels have square geometry, subtle top borders, and no deep shadows. Golden focus outlines are
visible on keyboard navigation. Every number has a unit, source context, or explicit unavailable state.

Core surfaces: metabolic overview with local heartbeat trace and next wake countdown; Worker
matrix with health/task/lease provenance; memory/reflection and event timeline; quota/exploration
budget and human control console. Bridge receipts form a subordinate read-only observation strip.
Only persisted WOKE evaluations create high pulses. NOOP is a low lapis mark. Expired host/lease/remote
observations are marked stale; a persisted `running` label alone must not imply current liveness.

## Procedural art and motion

A code-native classical bust is composed from SVG-style Bézier outlines on Canvas, layered with
cross-hatching and a 4×4 Bayer threshold matrix. Ivory tonal steps are shaded against obsidian; fine
geometric arcs recall a proportional drawing. Art is decorative, never a capacity or health signal.
CRT scanlines are low-opacity and confined to the artwork. No remote image, external font or CDN.
LED matrices encode current Worker state but always accompany a textual state label.

Motion is limited to a brief state-color transition and a cursor-like live indicator. No continuous
camera movement, blinking alerts or fabricated breathing pulses. `prefers-reduced-motion` removes
nonessential transitions. Countdown updates once per second without announcing every tick.

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

## Verification checklist

Check 1440px and 390px widths, keyboard focus/dialog behavior, accessible text labels, mobile
horizontal overflow, empty/unauthorized/disconnected states, real mocked SQLite projections, live
refresh, and control denial/receipt. Compare title hierarchy, exact palette, rail/panel proportions,
procedural art, and spacing against the code-native concept. Do not seed production with demo data.

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

`--screenshots NEW_DIRECTORY` saves desktop/mobile fixture screenshots without overwriting an existing
directory. The script creates disposable SQLite stores and ephemeral loopback servers, exercises
REST/stream projections, all five Goal controls and the global exploration brake, denied authentication, XSS-safe text rendering, empty
states, responsive layout and cleanup. Its generated test tokens stay in memory. No fixture enters
the operator's database. Browser downloads are never automatic.

The implemented layout follows the code-native concept's palette, 184px rail, classical typography,
golden split, engraved bust and LED matrix. The responsive version stacks panels and shows actual
rows rather than decorative placeholder cards. Screenshot review covers 1440×1100 and 390×844;
mobile memory content remains independently scrollable, with complete provenance and hashes.
