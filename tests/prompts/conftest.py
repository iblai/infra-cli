"""Shared fixtures for the prompt tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def llm_key_picker():
    """Answer the credentials step's LLM key picker with OpenAI.

    The key itself is still read with `questionary.password`, so each test's
    password answers line up as they always have, and an empty one means no
    key. Tests that drive `questionary.select` themselves patch over this and
    must answer the picker too.
    """
    with patch("questionary.select") as select:
        select.return_value.ask.return_value = "openai"
        yield select
