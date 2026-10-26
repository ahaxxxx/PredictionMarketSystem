"""M1 — proposer (博湛负责实现).

Generates candidate factor compositions for one optimizer iteration.  Three
proposing levels, all sharing this one entry point:

* **L1 random** — first iteration (no feedback history): sample random
  compositions from the factor space.
* **L2 evolution** — mutate the best-scoring candidates from the previous
  iteration using their ``FeedbackPayload`` (perturb weights, add/drop a
  factor, nudge thresholds).
* **L3 LLM** — optional enhancement: ask DeepSeek for novel compositions
  given the feedback report.  Must fail-closed back to L2 when the LLM is
  unavailable so the loop never breaks.

Interface contract (frozen, see ``schemas.py``):

* Input:  the previous iteration's ``FeedbackPayload`` list.
* Output: ``CandidateSpec`` values with unique ``candidate_id``,
  ``proposer_level`` ("L1"/"L2"/"L3") and ``parent_candidate_id`` set.
* Use only factor_ids that exist in ``pms/factors/definitions/``.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from pms.optimizer.schemas import CandidateSpec, FeedbackPayload

__all__ = ("proposer",)

# TODO(博湛): implement L1 random sampling.
# TODO(博湛): implement L2 evolutionary mutation over FeedbackPayload.
# TODO(博湛): implement optional L3 LLM proposing via DeepSeek, fail-closed.


def proposer(
    feedbacks: Sequence[FeedbackPayload],
    n: int,
    rng: random.Random | None = None,
) -> list[CandidateSpec]:
    """Produce ``n`` new candidate compositions for one loop iteration.

    Args:
        feedbacks: feedback from the previous iteration (empty on round 1).
        n: how many candidates to propose.
        rng: optional seeded RNG so runs are reproducible.

    Returns:
        New ``CandidateSpec`` proposals in scoring-ready form.
    """
    raise NotImplementedError("M1 proposer — to be implemented by 博湛")
