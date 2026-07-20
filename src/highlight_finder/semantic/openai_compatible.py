import base64
import json
import os
import urllib.request

from ..models import CandidateAnalysis, SemanticScore


class OpenAICompatibleAnalyzer:
    def __init__(self) -> None:
        self.key = os.environ["HIGHLIGHT_FINDER_API_KEY"]
        self.url = os.getenv(
            "HIGHLIGHT_FINDER_API_URL", "https://api.openai.com/v1/chat/completions"
        )
        self.model = os.getenv("HIGHLIGHT_FINDER_MODEL", "gpt-4o-mini")

    def analyze(self, candidate: CandidateAnalysis) -> SemanticScore:
        if not candidate.contact_sheet:
            raise ValueError("Semantic analysis requires a contact sheet")
        image = base64.b64encode(open(candidate.contact_sheet, "rb").read()).decode()
        prompt = (
            "Assess this candidate gameplay/trailer sequence. Return only JSON with gameplay_probability, menu_or_title_card_probability, visual_interest, self_contained, action_completion, short_form_suitability (0..1), summary, selection_reason, rejection_reason. Metrics: "
            + json.dumps(candidate.metrics)
        )
        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + image},
                        },
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            content = json.loads(response.read())["choices"][0]["message"]["content"]
        return SemanticScore.model_validate({**json.loads(content), "estimated": False})
