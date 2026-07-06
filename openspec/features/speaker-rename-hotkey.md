# Feature: In-Session Speaker Rename Hotkey

**Status:** In progress (branch `feat/speaker-rename-hotkey`)
**Owner:** idanshimon
**Created:** 2026-05-06

## Problem

When using the Azure backend with `--azure-speaker-labels`, RTT diarizes speakers as `Guest-1`, `Guest-2`, etc. The user (the meeting attendee) knows in real time who each guest *is* — but has no way to label them while the transcript is running. Today's options:

1. Manually rewrite the file post-meeting
2. Use a `--speaker-map "Guest-2=Hawk"` flag set BEFORE the meeting starts (impractical — diarization order is non-deterministic; you can't know which guest will be labeled Guest-1)

This means transcripts arrive without speaker names, requiring a manual cleanup pass for every recording.

## Goal

While the transcript is running, the user can press a single hotkey (`Ctrl+R`) to map a generic speaker label (`Guest-1`, `Guest-2`, …) to a real name (`Hawk Ticehurst`). The mapping applies retroactively to all past lines AND prospectively to all future lines.

## Non-goals

- Voice fingerprinting / automatic identification (that's an Azure feature, not RTT's job)
- Persisting maps across meetings (each meeting has different speakers)
- GUI / non-TTY rename flow

## User experience

```
[13:53:14] Speaker Guest-2: This is the output 1 and this is the problems view…
[13:53:18] Speaker Guest-2: Down here at the bottom if you like those types of tools.

# User presses Ctrl+R
✏️  Rename speaker. Known speakers: Guest-1, Guest-2
src=label dst=real name (e.g. "Guest-2=Hawk Ticehurst") or blank to cancel: Guest-2=Hawk Ticehurst
✅ Renamed Guest-2 → Hawk Ticehurst (12 past lines updated, future lines auto-mapped)

[13:53:25] Speaker Hawk Ticehurst: We have.
[13:53:41] Speaker Hawk Ticehurst: Run task type style UI…
```

The transcript file on disk is **rewritten atomically** (write to `.tmp`, rename) so partial writes can't corrupt it.

## Design

### TranscriptBuffer changes

- New field: `_speaker_map: dict[str, str]`
- New method: `_apply_speaker_map(line) -> line` — substitutes `"Speaker {src}:"` → `"Speaker {dst}:"` and `"Speaker {src} "` → `"Speaker {dst} "`. Called at every read path (file write, snapshot, delta snapshot).
- New method: `known_speakers() -> List[str]` — parses `_lines` for distinct `Speaker XXX:` patterns, returns deduped list in order seen
- New method: `rename_speaker(src, dst) -> int` — adds to `_speaker_map`, atomically rewrites the on-disk file with substitutions applied to every existing line, returns count of past lines updated

### ClipboardHotkeyListener changes

- Add `Ctrl+R` (`\x12`) handler in both `_run_posix` and `_run_windows`
- On press: temporarily restore canonical mode + echo, prompt with `input()`, then re-enter raw mode. POSIX: `termios.tcsetattr` flips. Windows: `msvcrt.getwch()` already echos.
- Parse input like `Guest-2=Hawk Ticehurst`. Whitespace tolerated. Empty input cancels. Invalid format prints an error and aborts.
- Validate `src` against `known_speakers()`; warn (don't abort) if not found — user might be renaming pre-emptively.

### CLI flag for batch rename (optional, separate)

`--speaker-map "Guest-1=Hawk,Guest-2=JC"` — pre-populates the map before the session starts. Useful when re-running on a recording where you know the mapping ahead of time. Not critical for the hotkey flow.

## Edge cases

| Case | Behavior |
|---|---|
| Rename to empty string | Reject ("dst cannot be empty") |
| Same src renamed twice | Second wins; file rewritten with latest mapping |
| Rename src→dst, then dst→other | Chain: original src lines → other (apply-map iterates) |
| File deleted mid-session | `rename_speaker` checks `_file_path.exists()` before rewrite; in-memory map still updates |
| Concurrent rename + new line append | `_lock` serializes; new line acquires lock after rewrite finishes |
| Hotkey pressed before any transcription | `known_speakers()` returns []; user gets "no speakers seen yet, type src=dst anyway" |

## Testing strategy

### Unit (pytest)
- `TranscriptBuffer.add()` writes raw line to file when no map set
- `TranscriptBuffer.add()` writes substituted line when map set
- `TranscriptBuffer.rename_speaker(src, dst)` updates on-disk content for past lines
- `TranscriptBuffer.rename_speaker()` returns correct count
- `TranscriptBuffer.known_speakers()` returns distinct labels in seen order
- Substitution doesn't touch non-`Speaker X:` text (e.g. content that mentions "Guest-1" verbatim)
- Empty/whitespace inputs to `rename_speaker` rejected

### Manual (live)
1. Start `rtt` with Azure backend on a 2-speaker meeting
2. Wait for both Guest-1 and Guest-2 to appear
3. Press `Ctrl+R` → enter `Guest-1=<name>` → verify file updated, future lines correct
4. Press `Ctrl+R` again → enter `Guest-2=<name>` → verify
5. Press `Ctrl+S` → verify clipboard has renamed names
6. Quit (`Ctrl+C`) → verify final file has both names throughout

## Implementation checkpoints

- [x] `TranscriptBuffer._speaker_map` + `_apply_speaker_map` (already in working tree)
- [x] `TranscriptBuffer.known_speakers()` (already in working tree)
- [x] `TranscriptBuffer.rename_speaker()` with atomic rewrite (already in working tree)
- [x] Apply map at `add()` write path (already in working tree)
- [x] Apply map at `snapshot()` and `_delta_snapshot_locked()` (already in working tree)
- [ ] `ClipboardHotkeyListener` Ctrl+R handler — POSIX
- [ ] `ClipboardHotkeyListener` Ctrl+R handler — Windows
- [ ] Prompt UX (clear, with current known speakers shown)
- [ ] Unit tests (`tests/test_speaker_rename.py`)
- [ ] Update README with new hotkey
- [ ] Update CHANGELOG
- [ ] Manual live test on a real meeting

## Risks & mitigations

- **Risk:** Prompt blocks the rendering thread — looks frozen.
  **Mitigation:** Hotkey listener runs in its own thread; transcription thread keeps appending to `_lines`. New lines arriving during the prompt are still captured (they just go in unrenamed until the user finishes; then `rename_speaker` rewrites everything including those).

- **Risk:** Atomic rewrite races with concurrent `add()`.
  **Mitigation:** Both paths acquire `self._lock`. `add()` does append+write under lock; `rename_speaker` does map-update + full rewrite under lock. Worst case: rename waits for one append to finish.

- **Risk:** User types junk into prompt.
  **Mitigation:** Clear error message, abort, return to listening. No partial state changes.

- **Risk:** Substring match collision (e.g. renaming `Guest-1` accidentally hits `Guest-10`).
  **Mitigation:** Anchor on `"Speaker {src}:"` and `"Speaker {src} "` (with trailing colon or space). `Guest-1:` won't match `Guest-10:`.

## Open questions

1. Should the hotkey be `Ctrl+R` or something else? `Ctrl+R` is the bash reverse-search shortcut, which could feel familiar but might surprise terminal users.
2. Should the prompt also accept `--list` to enumerate known speakers without renaming?
3. Should we auto-suggest the next unmapped speaker (e.g. "Press Ctrl+R: Rename Guest-2?") in a status line?
