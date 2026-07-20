import json
from pathlib import Path

from jinja2 import BaseLoader, Environment, select_autoescape

from .models import AnalysisResult, CandidateAnalysis

TEMPLATE = """<!doctype html><html><head><meta charset=utf-8><title>Highlight report</title><style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:2rem;background:#111;color:#eee}article{background:#222;padding:1rem;margin:1rem 0;border-radius:10px}img,video{max-width:100%}dt{font-weight:bold}.warning{color:#ffd166}</style></head><body><h1>Highlight Finder report</h1><p>{{ result.input.path.name }} · {{ '%.1f'|format(result.input.duration) }}s · {{ result.input.width }}×{{ result.input.height }}</p>{% if heuristic %}<p class=warning>Semantic judgments are heuristic estimates because no vision API was configured.</p>{% endif %}<h2>Selected highlights</h2>{% for c in selected %}<article><h3>Highlight {{ loop.index }} — {{ '%.3f'|format(c.total_score) }}</h3><video controls preload=metadata src="{{ c.clip }}"></video><img src="{{ c.contact_sheet }}"><p>{{ '%.2f'|format(c.start) }}–{{ '%.2f'|format(c.end) }}s ({{ '%.2f'|format(c.duration) }}s)</p><p>{{ c.semantic.selection_reason }}</p><details><summary>Score components</summary><pre>{{ c.ranking_components|tojson(indent=2) }}</pre></details></article>{% endfor %}<details><summary>Other evaluated candidates ({{ rejected|length }})</summary>{% for c in rejected %}<article>#{{ c.id }} · {{ '%.3f'|format(c.total_score) }} · {{ c.semantic.rejection_reason }}</article>{% endfor %}</details></body></html>"""


def write_outputs(result: AnalysisResult, selected: list[CandidateAnalysis], job: Path) -> None:
    (job / "analysis.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    rejected = [c for c in result.candidates if c.id not in result.selected_candidate_ids]
    env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))
    env.filters["tojson"] = lambda x, indent=None: json.dumps(x, indent=indent)
    html = env.from_string(TEMPLATE).render(
        result=result,
        selected=selected,
        rejected=rejected,
        heuristic=any(c.semantic and c.semantic.estimated for c in selected),
    )
    (job / "report.html").write_text(html, encoding="utf-8")
