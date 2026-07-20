# Highlight Finder MVP

Highlight Finder is a local, CLI-first assisted highlight finder for gameplay recordings and
game trailers. It detects shots, samples visual movement and quality signals, measures audio
activity, combines neighboring scenes into several candidate lengths, ranks them, exports H.264
MP4 clips, and writes a self-contained review report.

It **cannot reliably determine that an action is meaningful or complete**. Motion and loudness
are supporting numerical signals—not proof of quality. The optional vision model can improve
semantic judgment, but it can also make mistakes. Always review exported clips.

## Requirements and setup

* Python 3.11 or newer
* `ffmpeg` and `ffprobe` on `PATH` (Ubuntu: `sudo apt install ffmpeg`; macOS:
  `brew install ffmpeg`; Windows: install an FFmpeg build and add its `bin` directory to `PATH`)

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## Usage

```bash
highlight-finder analyze input.mp4 --output output --max-results 5 \
  --min-duration 8 --max-duration 30
# Equivalent:
python -m highlight_finder analyze input.mp4
```

Progress is printed for every pipeline stage. The default local analyzer needs no account, API,
database, or cloud storage. Each run creates `output/<video>-<UTC timestamp>/` containing
`analysis.json`, `report.html`, `contact_sheets/`, and `clips/highlight_XX.mp4`. Open
`report.html` directly in a browser; its relative media links work locally. Partial audio,
semantic, contact-sheet, and export failures are recorded in `analysis.json`.

## Optional vision semantics

Set an OpenAI-compatible endpoint and model. Only the key is required; unset it to guarantee a
fully local heuristic run.

```bash
export HIGHLIGHT_FINDER_API_KEY='...'
export HIGHLIGHT_FINDER_API_URL='https://api.openai.com/v1/chat/completions' # optional
export HIGHLIGHT_FINDER_MODEL='gpt-4o-mini'                                 # optional
```

The contact sheet and aggregate metrics are sent to that configured provider. Responses are
schema-validated and constrained to scores from zero to one. An API error falls back per
candidate to local estimates and is recorded.

## Testing

```bash
ruff check .
pytest
python scripts/smoke_test.py
```

The smoke utility creates a synthetic video in a temporary directory and verifies the complete
CLI output. It skips with a clear message if FFmpeg is absent.

## Troubleshooting

* **Missing media tools:** verify both `ffmpeg -version` and `ffprobe -version` work.
* **No candidates:** use a minimum duration appropriate to very short input; short videos receive
  a best-effort whole-video candidate.
* **No sound:** silent/no-audio files are supported and audio contributes zero rather than failing.
* **Poor rankings:** gameplay camera motion and loud music can mislead heuristics. Enable a vision
  provider and, in all cases, use the report to review the evidence.
* **Codec errors:** use an FFmpeg build that includes the `libx264` and AAC encoders.

## Scope and future work

Publishing, authentication, cloud jobs, vertical reframing, captions, music, downloads,
licensing decisions, editing, and custom-model training are intentionally excluded. Future work
can add genre-aware models, better fade/silence boundary snapping, transcript signals, manual
boundary adjustment, and alternate exporters through the existing semantic and export seams.
