from .models import CandidateAnalysis, MetricSample, Scene


def _mean(samples: list[MetricSample], start: float, end: float, field: str) -> float:
    values = [getattr(s, field) for s in samples if start <= s.time <= end]
    return sum(values) / len(values) if values else 0


def generate_candidates(
    scenes: list[Scene],
    visual: list[MetricSample],
    audio: list[MetricSample],
    min_duration: float,
    max_duration: float,
) -> list[CandidateAnalysis]:
    result, seen = [], set()
    for left in range(len(scenes)):
        for right in range(left, len(scenes)):
            start, end = scenes[left].start, scenes[right].end
            duration = end - start
            if duration > max_duration + 1e-6:
                break
            if duration + 1e-6 < min_duration:
                continue
            key = (round(start, 3), round(end, 3))
            if key in seen:
                continue
            seen.add(key)
            metrics = {
                name: _mean(visual, start, end, name)
                for name in (
                    "motion",
                    "visual_change",
                    "black_probability",
                    "static_probability",
                    "detail",
                    "colorfulness",
                )
            }
            metrics["audio_activity"] = _mean(audio, start, end, "audio_energy")
            result.append(
                CandidateAnalysis(
                    id=len(result) + 1,
                    start=start,
                    end=end,
                    scene_numbers=[s.number for s in scenes[left : right + 1]],
                    metrics=metrics,
                )
            )
    if not result and scenes:
        end = min(scenes[-1].end, max_duration)
        if end > 0:
            result.append(
                CandidateAnalysis(
                    id=1,
                    start=max(0, end - max_duration),
                    end=end,
                    scene_numbers=[s.number for s in scenes],
                )
            )
    return result
