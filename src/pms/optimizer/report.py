"""M5 — iteration report renderer (博湛负责实现).

Turns one iteration's deterministic ``FeedbackPayload`` list into a
human-readable Markdown report.  The attribution numbers are already
computed by the evaluation core (蓝友成); this module only presents them.

Requirements:

* ``use_llm=False`` renders a pure template report — always works.
* ``use_llm=True`` may ask DeepSeek to polish the analysis into natural
  language, but must fail-closed to the template version on any error.
* Each iteration's report should also be persisted (suggested:
  ``reports/iteration-{n}.md``) so the deck/report material can quote them
  directly.
"""

from __future__ import annotations

from collections.abc import Sequence

from pms.optimizer.schemas import FeedbackPayload

__all__ = ("render_report",)

# TODO(博湛): template renderer (promotions, score table, factor usage stats).
# TODO(博湛): optional LLM polish with fail-closed fallback.
# TODO(博湛): persist one Markdown file per iteration.


def render_report(
    iteration: int,
    feedbacks: Sequence[FeedbackPayload],
    use_llm: bool = False,
) -> str:
    """Render the iteration report as Markdown.

    Args:
        iteration: 1-based iteration number.
        feedbacks: this iteration's per-candidate feedback payloads.
        use_llm: optionally polish with an LLM (must fail-closed).

    Returns:
        Markdown report text.
    """
    raise NotImplementedError("M5 report renderer — to be implemented by 博湛")
