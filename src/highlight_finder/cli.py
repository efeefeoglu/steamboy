import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .audio import analyze_audio
from .candidates import generate_candidates
from .config import AnalysisConfig
from .exporter import create_contact_sheet, export_clip
from .media import MediaError, inspect_media
from .models import AnalysisResult
from .ranking import deduplicate, rank_candidate
from .report import write_outputs
from .scenes import detect_scenes
from .semantic.heuristic import HeuristicAnalyzer
from .semantic.openai_compatible import OpenAICompatibleAnalyzer
from .visual import analyze_visual


def run(input_path: Path, output: Path, config: AnalysisConfig) -> Path:
    errors: list[str] = []
    print("Inspecting media…")
    media = inspect_media(input_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job = output / f"{input_path.stem}-{stamp}"
    (job / "clips").mkdir(parents=True)
    (job / "contact_sheets").mkdir()
    print("Detecting scenes…")
    scenes = detect_scenes(input_path, media.duration)
    print("Calculating visual metrics…")
    visual = analyze_visual(input_path, config.sample_fps)
    print("Analyzing audio…")
    try:
        audio = analyze_audio(input_path, media.has_audio, config.sample_fps)
    except Exception as exc:
        audio = []
        errors.append(f"Audio analysis fallback: {exc}")
    print("Creating candidate sequences…")
    candidates = generate_candidates(
        scenes, visual, audio, config.min_duration, config.max_duration
    )
    candidates.sort(
        key=lambda c: c.metrics.get("motion", 0) + c.metrics.get("audio_activity", 0), reverse=True
    )
    candidates = candidates[: config.semantic_limit]
    print("Performing semantic analysis…")
    use_api = bool(os.getenv("HIGHLIGHT_FINDER_API_KEY"))
    analyzer = OpenAICompatibleAnalyzer() if use_api else HeuristicAnalyzer()
    if not use_api:
        errors.append("No HIGHLIGHT_FINDER_API_KEY; semantic fields are heuristic estimates.")
    for candidate in candidates:
        sheet = job / "contact_sheets" / f"candidate_{candidate.id:03d}.jpg"
        try:
            create_contact_sheet(
                input_path, candidate, sheet, media.duration, config.contact_sheet_frames
            )
            candidate.contact_sheet = sheet.relative_to(job).as_posix()
        except Exception as exc:
            errors.append(f"Candidate {candidate.id} contact sheet: {exc}")
        try:
            candidate.semantic = analyzer.analyze(candidate)
        except Exception as exc:
            errors.append(f"Candidate {candidate.id} semantic fallback: {exc}")
            candidate.semantic = HeuristicAnalyzer().analyze(candidate)
    print("Ranking candidates…")
    for candidate in candidates:
        rank_candidate(candidate, config.weights)
    selected = deduplicate(candidates, config.overlap_threshold, config.max_results)
    print("Exporting clips…")
    exported = []
    for index, candidate in enumerate(selected, 1):
        clip = job / "clips" / f"highlight_{index:02d}.mp4"
        try:
            export_clip(input_path, candidate, clip, media)
            candidate.clip = clip.relative_to(job).as_posix()
            exported.append(candidate)
        except Exception as exc:
            errors.append(f"Candidate {candidate.id} export: {exc}")
    result = AnalysisResult(
        input=media,
        configuration=config.model_dump(),
        scenes=scenes,
        metrics=visual + audio,
        candidates=candidates,
        selected_candidate_ids=[c.id for c in exported],
        errors=errors,
    )
    print("Generating the report…")
    write_outputs(result, exported, job)
    print(f"Done: {job}")
    return job


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="highlight-finder")
    sub = root.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--output", type=Path, default=Path("output"))
    analyze.add_argument("--max-results", type=int, default=5)
    analyze.add_argument("--min-duration", type=float, default=8)
    analyze.add_argument("--max-duration", type=float, default=30)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        run(
            args.input,
            args.output,
            AnalysisConfig(
                min_duration=args.min_duration,
                max_duration=args.max_duration,
                max_results=args.max_results,
            ),
        )
    except (MediaError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0
