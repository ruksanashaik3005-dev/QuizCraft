from __future__ import annotations

import json
from typing import Any


OUTPUT_SCHEMA = {
    "questions": [
        {
            "question": "Question text",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": "Exact text of one option",
            "explanation": "Short explanation of why the answer is correct",
            "category": "One selected category",
            "source_references": [1],
        }
    ]
}

SYSTEM_PROMPT = f"""You are QuizCraft, a careful educational assessment writer.
Return only valid JSON matching this schema exactly:
{json.dumps(OUTPUT_SCHEMA)}
Create original, unambiguous educational multiple-choice questions. Each question
must be an MCQ with exactly four distinct options, exactly one correct answer,
one requested category, and valid source_references. The
correct_answer must exactly match one option. Explanations must be accurate and
concise. Do not add Markdown fences or fields. Do not invent source-specific
facts when the supplied source material is insufficient."""


def build_topic_prompt(
    *,
    topic: str,
    difficulty: str,
    question_count: int,
    academic_level: str,
    custom_instructions: str = "",
    categories: list[str] | None = None,
) -> str:
    instructions = (
        f"\nAdditional learning objective or instructions: {custom_instructions.strip()}"
        if custom_instructions.strip()
        else ""
    )
    return f"""Create exactly {question_count} multiple-choice questions.
Topic: {topic}
Academic level: {academic_level}
Difficulty: {difficulty}{instructions}
Question categories: {", ".join(categories or ["Conceptual"])}

Vary the concepts tested, avoid duplicate questions and ambiguous wording, and
keep every question appropriate to the selected level. Use the required JSON
schema. Use exactly one selected category for each question and set
source_references to an empty list. Do not claim that these questions are based
on study notes."""


def build_document_prompt(
    *,
    topic: str,
    difficulty: str,
    question_count: int,
    academic_level: str,
    passages: list[dict[str, Any]],
    custom_instructions: str = "",
    categories: list[str] | None = None,
) -> str:
    context = "\n\n".join(
        f"[Passage {index} | source: {passage.get('source', 'study material')} | "
        f"chunk: {passage.get('chunk_index', 'unknown')}]\n"
        f"{passage['text']}"
        for index, passage in enumerate(passages, start=1)
    )
    instructions = (
        f"\nAdditional learning objective or instructions: {custom_instructions.strip()}"
        if custom_instructions.strip()
        else ""
    )
    return f"""Create exactly {question_count} multiple-choice questions grounded
only in the retrieved study passages below.
Requested topic: {topic}
Academic level: {academic_level}
Difficulty: {difficulty}{instructions}
Question categories: {", ".join(categories or ["Conceptual"])}

If the passages do not contain enough information, avoid unsupported claims;
ask only what the passages establish and state the limitation in the explanation.
Do not use outside facts to fill gaps. Avoid duplicate or ambiguous questions.
Use the required JSON schema. Use exactly one selected category for each
question. Each question must reference one or more retrieved passage numbers in
source_references; never invent passage numbers.

Retrieved passages:
{context}"""
