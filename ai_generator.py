from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from prompts import build_document_prompt, build_topic_prompt, SYSTEM_PROMPT
from quiz_utils import validate_quiz


class LLMError(RuntimeError):
    """An API request failed or returned content that could not be used."""


def _sanitize_provider_text(value: str, api_secret: str) -> str:
    if api_secret:
        value = value.replace(api_secret, "[redacted]")
    value = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", value)
    value = re.sub(r"\bgsk_[A-Za-z0-9_-]+\b", "[redacted-key]", value)
    return " ".join(value.split())[:400]


def _provider_error_detail(body: str, api_secret: str, cloudflare: bool) -> str:
    try:
        response = json.loads(body)
    except json.JSONDecodeError:
        response = None

    if isinstance(response, dict):
        error = response.get("error", response)
        if isinstance(error, dict):
            fields = []
            for name in ("code", "type", "message"):
                value = error.get(name)
                if isinstance(value, (str, int)) and str(value).strip():
                    fields.append(f"{name}={_sanitize_provider_text(str(value), api_secret)}")
            failed_generation = error.get("failed_generation") or response.get("failed_generation")
            if isinstance(failed_generation, str) and failed_generation.strip():
                try:
                    generated = json.loads(failed_generation)
                except json.JSONDecodeError:
                    generated_shape = "non-JSON"
                else:
                    generated_shape = (
                        "JSON object" if isinstance(generated, dict)
                        else "JSON array" if isinstance(generated, list)
                        else "JSON scalar"
                    )
                fields.append(
                    f"failed_generation=[content withheld; {len(failed_generation)} characters; {generated_shape}]"
                )
            if fields:
                return "; ".join(fields)
        elif isinstance(error, str) and error.strip():
            return f"message={_sanitize_provider_text(error, api_secret)}"

    if cloudflare:
        match = re.search(r"error code:\s*(\d+)", body, re.IGNORECASE)
        if match:
            return f"Cloudflare error code {match.group(1)}"
    if body.strip():
        return f"non-JSON response: {_sanitize_provider_text(body, api_secret)}"
    return "no structured error details returned"


def _post_completion(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    retry_json_validation: bool = True,
) -> dict[str, Any]:
    request_headers = dict(headers)
    request_headers.setdefault("Accept", "application/json")
    request_headers.setdefault("User-Agent", "QuizCraft/0.1.0")
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        configured_timeout = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    except ValueError:
        configured_timeout = 60
    timeout = min(120, max(5, configured_timeout))
    api_secret = headers.get("Authorization", "").removeprefix("Bearer ")
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            break
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if (
                exc.code == 400
                and "json_validate_failed" in body
                and "response_format" in payload
                and retry_json_validation
            ):
                return _post_completion(
                    url,
                    headers,
                    payload,
                    retry_json_validation=False,
                )
            if exc.code == 400 and "response_format" in body and "response_format" in payload:
                compatible_payload = dict(payload)
                compatible_payload.pop("response_format")
                return _post_completion(url, headers, compatible_payload)
            if exc.code in (408, 425, 429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(0.25 * (2**attempt))
                continue
            if exc.code == 429:
                raise LLMError("The provider rate limit was reached. Wait briefly or check your provider quota.") from exc
            if exc.code in (401, 403):
                cloudflare = "cloudflare" in (exc.headers.get("Server", "") if exc.headers else "").lower()
                detail = _provider_error_detail(body, api_secret, cloudflare)
                if exc.code == 401:
                    raise LLMError(
                        f"LLM provider authentication failed (HTTP 401; {detail}). Verify that the configured "
                        "backend key is active for this provider."
                    ) from exc
                if cloudflare and detail.startswith("Cloudflare error code"):
                    raise LLMError(
                        f"The request was blocked at the Cloudflare edge (HTTP 403; {detail}) before Groq "
                        "returned a structured API error. This does not establish a key or model permission "
                        "problem; check the backend network or egress policy."
                    ) from exc
                raise LLMError(
                    f"LLM provider access denied (HTTP 403; {detail}). A 403 can indicate model, project, "
                    "organization, or account permissions; it does not by itself mean the API key is invalid. "
                    "Check model access and the key's project or organization in the provider console."
                ) from exc
            detail = _provider_error_detail(
                body,
                api_secret,
                "cloudflare" in (exc.headers.get("Server", "") if exc.headers else "").lower(),
            )
            raise LLMError(f"LLM provider returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(0.25 * (2**attempt))
                continue
            raise LLMError("Could not reach the LLM provider after retrying. Check the network and try again.") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMError("The LLM provider returned an invalid response envelope.") from exc
    if not isinstance(result, dict):
        raise LLMError("The LLM provider returned an unexpected response.")
    return result


def _response_text(result: dict[str, Any]) -> str:
    try:
        text = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("The provider response did not contain a chat completion.") from exc
    if isinstance(text, list):
        text = "".join(
            item.get("text", "") for item in text if isinstance(item, dict)
        )
    if not isinstance(text, str) or not text.strip():
        raise LLMError("The model returned an empty answer.")
    return text.strip()


def _parse_questions(content: str) -> list[dict[str, Any]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError("The model response was not valid JSON. Try generating again.") from exc
    if isinstance(data, dict):
        data = data.get("questions")
    if not isinstance(data, list):
        raise LLMError("The model response must contain a questions array.")
    return data


def generate_quiz(
    *,
    topic: str,
    difficulty: str,
    question_count: int,
    academic_level: str,
    context: list[dict[str, Any]] | None = None,
    custom_instructions: str = "",
    categories: list[str] | None = None,
    api_key: str = "",
    base_url: str = "https://api.groq.com/openai/v1",
    model: str = "openai/gpt-oss-20b",
) -> list[dict[str, Any]]:
    if not api_key.strip():
        raise LLMError("No API key found. Configure LLM_API_KEY/GROQ_API_KEY, or select Demo mode.")
    if not topic.strip():
        raise LLMError("A topic is required.")
    if question_count not in (5, 10, 15, 20):
        raise LLMError("Question count must be 5, 10, 15, or 20.")
    if difficulty not in ("Easy", "Medium", "Hard"):
        raise LLMError("Choose Easy, Medium, or Hard difficulty.")
    if context:
        user_prompt = build_document_prompt(
            topic=topic,
            difficulty=difficulty,
            question_count=question_count,
            academic_level=academic_level,
            passages=context,
            custom_instructions=custom_instructions,
            categories=categories,
        )
    else:
        user_prompt = build_topic_prompt(
            topic=topic,
            difficulty=difficulty,
            question_count=question_count,
            academic_level=academic_level,
            custom_instructions=custom_instructions,
            categories=categories,
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": min(7000, 700 * question_count),
        "response_format": {"type": "json_object"},
    }
    result = _post_completion(
        f"{base_url.rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        payload,
    )
    questions = _parse_questions(_response_text(result))
    return validate_quiz(
        questions,
        expected_count=question_count,
        allowed_categories=categories,
        source_count=len(context or []),
    )
