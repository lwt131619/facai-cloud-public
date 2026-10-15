# Facai Mission Control UI target

Build a polished, responsive Chinese-language control room around a **live mission pipeline**, not a generic chat screen. The main visual is a left-to-right sequence of stage cards on desktop and a vertical sequence on phones. Each stage shows its name, actual state, current agent/model/runtime, elapsed time, and its own progress only when measurable. A thin connecting rail shows the completed path, active stage, and blocked/failed stage. Selecting a stage opens its real output, events, files, tests, and checkpoints without leaving the mission.

## Information hierarchy

1. Top bar: product identity, connection state, active mission count, pending human actions, user menu.
2. Mission overview: searchable mission cards with title, actual status, workflow progress, next action, last event time, and cost. Never mix data between missions.
3. Mission header: original request (verbatim), optional derived prompt clearly labeled, mission owner and creation time, current status, overall workflow progress, pause/resume/cancel where supported.
4. Pipeline: one stage card per workflow step. Waiting, queued, running, paused, completed, failed, and skipped must be visually distinct and have text labels as well as color. Show unknown duration as indeterminate, never fabricate a percentage. Derive overall percentage from completed steps divided by applicable steps; a failed step is not complete.
5. Stage inspector: live output/log stream, agent and model attribution, actual step events, artifacts, diff, test result, deployment result, and cost. Show the precise upstream failure cause and retry action when available.
6. Event timeline: chronological, filterable by stage/type/severity, with timestamps and original event payload available for inspection.
7. Registries/settings: Agent, Model, Skill, Runtime separate views with binding controls, credential reference names only, and connection checks.

## Visual direction

Use a calm dark operations palette with restrained electric blue for active work, green for verified completion, amber for waiting/needs input, and red for failure. Strong typography, generous spacing, crisp borders, and subtle motion make state changes clear. Keep contrast accessible; respect reduced-motion preferences. Do not rely on color alone. Desktop should show pipeline and inspector together; tablet/phone should stack them with sticky mission summary and easy stage navigation. Prioritize legibility over decorative effects.

## Data and interaction rules

- State and progress come from server data and events; no simulated timers or invented success.
- An in-progress stage can show an indeterminate activity animation and elapsed time unless the executor reports a trustworthy numeric fraction.
- Server-sent events update the active stage and timeline; reconnect from last sequence and present disconnected/reconnecting states.
- Preserve original input byte-for-byte; derived prompt is separate and inspectable.
- Never show raw API keys. A model refusal, timeout, rate limit, auth error, agent error, and tool error have separate labels and provider attribution.
- Loading, empty, error, permission, and long-running states are designed explicitly.
- Keyboard focus, semantic labels, mobile tap targets, and screen-reader status announcements are required.

## Implementation sequence

Start with the existing HTML/CSS/JS and API. Build the mission pipeline and stage inspector on real current endpoints first. Add backend fields/events only when the UI needs them. Verify with two simultaneous missions and tests that no events or artifacts cross boundaries. Do not add a heavy framework solely for visual polish; choose a modern component framework only if it materially simplifies later stateful views and the build/deploy workflow is included. Check syntax, run API tests, verify desktop and mobile layouts, and commit the increment.
