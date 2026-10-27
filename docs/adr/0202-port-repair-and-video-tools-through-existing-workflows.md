# ADR-0202: Port repair and video tools through existing workflows

## Status

Accepted for the independent fork.

## Context

The requested port retains metadata repair and video downloading, omitting Tidal.
The old mutable cache and private transcoder functions would bypass 2.0's draft
and verified Sync boundaries.

## Decision

Preserve the standalone conservative repair matcher and its original test cases.
Translate semantic Tracks at the Application Layer boundary and publish selected
proposals through one revision-bound `LibraryWorkspace.apply_track_edits` batch.
Repair batches require explicit Library Review even when ordinary metadata edits
save automatically. This draft-local hold resets on save, revert or reload and
does not change the global save preference.
An unknown disc remains 0 until a provider supplies it, correcting the original
matcher's implicit disc-1 assumption. Explicit scans disclose and send album and
artist names to the selected provider. Requests use TLS, bounded responses and
timeouts; MusicBrainz is rate limited. Library changes invalidate pending results.

Add `yt-dlp[default]` for Host downloading, including its YouTube challenge scripts.
A supported JavaScript runtime remains a Host prerequisite for affected sites.
Capture a Device Profile and encoder settings
per queued job. Constrain that profile for screen or TV output, prepare with
`MediaTranscoder`, and verify category tags before publishing a unique cached Host
file. Failed work is cleaned up. Cached files enter normal Host Media Scan and
Sync Review; these tools never write directly to a device.

The GUI uses PySide6 and current theme primitives. A cancellable Host-job runner
delivers progress and results through queued Qt signals. Shutdown waits for jobs
and rejects late completion. Tidal code and dependencies are absent.

## Validation and limits

Tests cover original matching cases, semantic field mapping, selected edits,
stale revisions, locking, cancelled results, retry and sync handoff. Local FFmpeg
fixtures exercise conversion, category tags and cancellation cleanup. Automated
tests mock online services. Physical playback and frozen builds remain manual
acceptance checks.

On 27 October 2026, the final targeted run passed 270 tests covering the tools,
application shell, Host file handoff, Library save controller, translations,
transcoding and packaging. Lint, formatting, the lockfile, documentation and strict
typing of all changed Python files also pass.

The complete 8,259-test run was interrupted after a native librosa/Numba worker
crash. A broader GUI run also encountered a Qt style crash. Separate backup-page
checks exposed an empty-dialog-title failure and a locale-dependent number-format
failure; the title failure reproduces in isolation. Whole-repository Mypy reports
an unchanged optional-widget assertion in `test_genius_page.py:192`. These results
are not a clean full-suite acceptance claim for the upstream rewrite.
