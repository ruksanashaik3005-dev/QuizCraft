from __future__ import annotations

import json
from typing import Any


class QuizValidationError(ValueError):
    """A generated quiz does not satisfy the application contract."""


def parse_correct_answer(value: str | list[str]) -> str | list[str]:
    prefix = "quizcraft-multi-v1:"
    if not isinstance(value, str) or not value.startswith(prefix):
        return value
    try:
        decoded = json.loads(value[len(prefix):])
    except json.JSONDecodeError:
        return value
    if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
        return decoded
    return value


def answer_is_correct(question: dict[str, Any], selected: Any) -> bool:
    correct = parse_correct_answer(question["correct_answer"])
    if question.get("question_type") == "multiple_select":
        return (
            isinstance(selected, list)
            and all(isinstance(item, str) for item in selected)
            and len(selected) == len(set(selected))
            and set(selected) == set(correct if isinstance(correct, list) else [correct])
        )
    return isinstance(selected, str) and selected == correct


def validate_quiz(
    questions: Any,
    expected_count: int | None = None,
    *,
    allowed_categories: list[str] | None = None,
    source_count: int = 0,
) -> list[dict[str, Any]]:
    if not isinstance(questions, list) or not questions:
        raise QuizValidationError("The generated quiz did not contain any questions.")
    if expected_count is not None and len(questions) != expected_count:
        raise QuizValidationError(
            f"Expected {expected_count} questions but received {len(questions)}. Generate again."
        )
    normalized = []
    seen = set()
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise QuizValidationError(f"Question {index} is not a JSON object.")
        text = question.get("question")
        options = question.get("options")
        answer = question.get("correct_answer")
        question_type = question.get("question_type", "single_select")
        explanation = question.get("explanation")
        category = question.get("category", "Conceptual")
        source_references = question.get("source_references", [])
        if not isinstance(text, str) or not text.strip():
            raise QuizValidationError(f"Question {index} has no question text.")
        key = text.casefold().strip()
        if key in seen:
            raise QuizValidationError("The generated quiz contains duplicate questions.")
        seen.add(key)
        if not isinstance(options, list) or len(options) != 4:
            raise QuizValidationError(f"Question {index} must have exactly four options.")
        if any(not isinstance(option, str) or not option.strip() for option in options):
            raise QuizValidationError(f"Question {index} has an empty or invalid option.")
        if len({option.casefold().strip() for option in options}) != 4:
            raise QuizValidationError(f"Question {index} must have four distinct options.")
        if question_type == "MCQ":
            question_type = "single_select"
        if not isinstance(question_type, str) or question_type not in {
            "single_select",
            "multiple_select",
        }:
            raise QuizValidationError(
                f"Question {index} must use single_select or multiple_select."
            )
        if question_type == "single_select":
            if not isinstance(answer, str) or answer not in options:
                raise QuizValidationError(
                    f"Question {index} must identify one correct option by its exact text."
                )
            normalized_answer: str | list[str] = answer.strip()
        else:
            if (
                not isinstance(answer, list)
                or not answer
                or any(not isinstance(item, str) or item not in options for item in answer)
                or len(answer) != len(set(answer))
            ):
                raise QuizValidationError(
                    f"Question {index} must identify distinct correct options by their exact text."
                )
            none_options = [
                item for item in answer
                if "none of the above" in item.casefold() or "none above" in item.casefold()
            ]
            if none_options and len(answer) != 1:
                raise QuizValidationError(
                    f"Question {index} cannot combine 'None of the above' with another correct option."
                )
            normalized_answer = [item.strip() for item in answer]
        if not isinstance(explanation, str) or not explanation.strip():
            raise QuizValidationError(f"Question {index} must include an explanation.")
        if not isinstance(category, str) or not category.strip():
            raise QuizValidationError(f"Question {index} must include a category.")
        if allowed_categories and category not in allowed_categories:
            raise QuizValidationError(
                f"Question {index} uses a category that was not selected."
            )
        if not isinstance(source_references, list) or any(
            type(reference) is not int or reference < 1 or reference > source_count
            for reference in source_references
        ):
            raise QuizValidationError(
                f"Question {index} contains an invalid source reference."
            )
        if len(set(source_references)) != len(source_references):
            raise QuizValidationError(
                f"Question {index} contains duplicate source references."
            )
        if source_count and not source_references:
            raise QuizValidationError(
                f"Question {index} must reference at least one retrieved passage."
            )
        normalized.append(
            {
                "question": text.strip(),
                "options": [option.strip() for option in options],
                "correct_answer": normalized_answer,
                "explanation": explanation.strip(),
                "question_type": question_type,
                "category": category.strip(),
                "source_references": source_references,
            }
        )
    return normalized


def calculate_score(
    questions: list[dict[str, Any]], answers: dict[str, Any]
) -> dict[str, Any]:
    review = []
    correct = 0
    for index, question in enumerate(questions):
        answer = answers.get(str(index))
        is_correct = answer_is_correct(question, answer)
        correct += int(is_correct)
        review.append({"your_answer": answer, "is_correct": is_correct})
    total = len(questions)
    percentage = (100.0 * correct / total) if total else 0.0
    if percentage == 100:
        summary = "Excellent work — you answered every question correctly."
    elif percentage >= 70:
        summary = "Good progress. Review the explanations for the questions you missed."
    elif percentage >= 40:
        summary = "Keep practicing. Revisit the key concepts and try another quiz."
    else:
        summary = "A fresh review of the material will help. Use the explanations as a guide."
    return {
        "total": total,
        "correct": correct,
        "incorrect": total - correct,
        "percentage": percentage,
        "summary": summary,
        "review": review,
    }


_DEMO_QUESTIONS = {
    "Easy": [
        {
            "question": "Which data structure follows the First In, First Out (FIFO) principle?",
            "options": ["Queue", "Stack", "Tree", "Graph"],
            "correct_answer": "Queue",
            "explanation": "A queue removes items in the same order they were added, which is FIFO.",
        },
        {
            "question": "Which HTML element is commonly used for the largest page heading?",
            "options": ["<h1>", "<head>", "<title>", "<header>"],
            "correct_answer": "<h1>",
            "explanation": "The h1 element represents the highest-level heading in an HTML document.",
        },
        {
            "question": "What does CPU stand for?",
            "options": ["Central Processing Unit", "Computer Program Utility", "Core Power Unit", "Central Program User"],
            "correct_answer": "Central Processing Unit",
            "explanation": "The CPU is the processor that executes program instructions.",
        },
        {
            "question": "Which Python type stores an ordered, changeable collection?",
            "options": ["list", "tuple", "set", "frozenset"],
            "correct_answer": "list",
            "explanation": "A Python list is ordered and mutable, so its contents can be changed.",
        },
        {
            "question": "Which number system uses only the digits 0 and 1?",
            "options": ["Binary", "Decimal", "Octal", "Hexadecimal"],
            "correct_answer": "Binary",
            "explanation": "Binary is base two and represents values using only 0 and 1.",
        },
    ],
    "Medium": [
        {
            "question": "What is the average-case time complexity of searching for a key in a well-distributed hash table?",
            "options": ["O(1)", "O(log n)", "O(n)", "O(n log n)"],
            "correct_answer": "O(1)",
            "explanation": "With a suitable hash function and controlled collisions, lookup is constant time on average.",
        },
        {
            "question": "Which SQL clause filters groups after aggregate functions are computed?",
            "options": ["HAVING", "WHERE", "ORDER BY", "DISTINCT"],
            "correct_answer": "HAVING",
            "explanation": "HAVING filters grouped rows, often using aggregate expressions.",
        },
        {
            "question": "In object-oriented programming, what is method overriding?",
            "options": ["A subclass providing a new implementation of an inherited method", "Giving two methods the same name in one class", "Calling a method without an object", "Making a method private"],
            "correct_answer": "A subclass providing a new implementation of an inherited method",
            "explanation": "Overriding lets a subclass specialize behavior inherited from its parent class.",
        },
        {
            "question": "Which OSI layer is primarily responsible for routing packets between networks?",
            "options": ["Network layer", "Transport layer", "Data link layer", "Session layer"],
            "correct_answer": "Network layer",
            "explanation": "The network layer handles logical addressing and routing across networks.",
        },
        {
            "question": "What is the main purpose of a database index?",
            "options": ["Speed up data lookup", "Guarantee every query is unique", "Encrypt table contents", "Replace primary keys"],
            "correct_answer": "Speed up data lookup",
            "explanation": "An index provides a data structure that can reduce the work required to find rows.",
        },
    ],
    "Hard": [
        {
            "question": "Which property of a transaction requires its effects to survive a system crash after commit?",
            "options": ["Durability", "Atomicity", "Isolation", "Consistency"],
            "correct_answer": "Durability",
            "explanation": "Durability means committed changes persist despite subsequent failures.",
        },
        {
            "question": "What is the amortized time complexity of appending to a dynamic array that doubles its capacity?",
            "options": ["O(1)", "O(log n)", "O(n)", "O(n log n)"],
            "correct_answer": "O(1)",
            "explanation": "Occasional O(n) resizing is spread over many constant-time appends, yielding O(1) amortized cost.",
        },
        {
            "question": "In a process scheduler, what is a key risk of strict priority scheduling without aging?",
            "options": ["Starvation of low-priority processes", "Deadlock of every process", "Loss of virtual memory", "Excessive context-free switching"],
            "correct_answer": "Starvation of low-priority processes",
            "explanation": "A continuous stream of high-priority work can prevent lower-priority processes from running.",
        },
        {
            "question": "Which technique is commonly used to avoid deadlock by ensuring the system never enters an unsafe state?",
            "options": ["Banker's algorithm", "Round-robin scheduling", "LRU page replacement", "Binary search"],
            "correct_answer": "Banker's algorithm",
            "explanation": "Banker's algorithm grants resource requests only when the resulting state remains safe.",
        },
        {
            "question": "For a comparison-based sorting algorithm, what is the best possible worst-case asymptotic lower bound?",
            "options": ["Ω(n log n)", "Ω(log n)", "Ω(n)", "Ω(n²)"],
            "correct_answer": "Ω(n log n)",
            "explanation": "The comparison decision tree requires at least log₂(n!) comparisons, which is Ω(n log n).",
        },
    ],
}


def demo_quiz(difficulty: str) -> list[dict[str, Any]]:
    if difficulty not in _DEMO_QUESTIONS:
        raise QuizValidationError("Choose Easy, Medium, or Hard difficulty.")
    return [dict(question, options=list(question["options"])) for question in _DEMO_QUESTIONS[difficulty]]
