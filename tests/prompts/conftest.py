"""Shared fixtures for the prompt tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iblai_infra.prompts.setup import S3_KEY_PROMPT


def answer_s3_picker(select: MagicMock, choice: str) -> MagicMock:
    """Answer the S3 key picker with `choice`; every other picker keeps answering
    with `select.return_value`, so existing LLM-picker answers still apply."""
    s3_picker = MagicMock()
    s3_picker.ask.return_value = choice
    select.side_effect = lambda message, *a, **kw: (
        s3_picker if message == S3_KEY_PROMPT else select.return_value
    )
    return s3_picker


@pytest.fixture(autouse=True)
def llm_key_picker():
    """Answer the credentials step's LLM key picker with OpenAI, and skip the S3 key.

    The keys themselves are still read with `questionary.password`, so each
    test's password answers line up as they always have, and an empty one
    means no key. Skipping the S3 picker asks for nothing more. Tests that
    drive `questionary.select` themselves patch over this and must answer
    both pickers too (see `answer_s3_picker`).
    """
    with patch("questionary.select") as select:
        select.return_value.ask.return_value = "openai"
        answer_s3_picker(select, "")
        yield select
