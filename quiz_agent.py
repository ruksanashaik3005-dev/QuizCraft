from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from quiz_utils import validate_quiz
from rag_pipeline import retrieve_study_context


@dataclass(frozen=True)
class QuizPlan:
    topic: str
    difficulty: str
    question_count: int
    use_material: bool


_STOP_WORDS = {
    "prepare", "create", "generate", "make", "build", "please", "a", "an", "the",
    "for", "from", "my", "uploaded", "upload", "notes", "note", "study", "material",
    "materials", "document", "documents", "based", "on", "about", "with", "using",
    "question", "questions", "quiz", "easy", "medium", "hard", "beginner",
    "intermediate", "advanced", "level", "levels", "me", "of", "in",
}


def plan_quiz_request(request: str, *, use_material: bool = False) -> QuizPlan:
    if not request.strip():
        raise ValueError("Enter a quiz request.")
    lower = request.casefold()
    count_match = re.search(r"\b(5|10|15)\b", lower)
    question_count = int(count_match.group(1)) if count_match else 5
    if re.search(r"\b(easy|beginner)\b", lower):
        difficulty = "Easy"
    elif re.search(r"\b(hard|advanced)\b", lower):
        difficulty = "Hard"
    else:
        difficulty = "Medium"
    words = re.findall(r"[a-zA-Z0-9+#.]+", request)
    topic_words = [word for word in words if word.casefold().strip(".") not in _STOP_WORDS and not word.isdigit()]
    topic = " ".join(topic_words).strip(" .,-")
    if not topic:
        raise ValueError("The agent could not identify a topic. Include a subject such as DBMS or operating systems.")
    return QuizPlan(topic, difficulty, question_count, use_material)


def execute_quiz_plan(
    plan: QuizPlan,
    *,
    generate_fn: Callable[[str, str, int, list[dict[str, Any]] | None], list[dict[str, Any]]],
    collection_id: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    passages = None
    if plan.use_material:
        if not collection_id:
            raise ValueError("Index study material before asking the agent to retrieve it.")
        passages = retrieve_study_context(collection_id, plan.topic)
        if not passages:
            raise ValueError("No relevant study passages were retrieved for that request.")
    questions = generate_fn(
        plan.topic, plan.difficulty, plan.question_count, passages
    )
    return validate_quiz(questions, expected_count=plan.question_count), passages or []
