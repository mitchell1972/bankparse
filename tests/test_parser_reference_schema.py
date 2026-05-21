"""
The statement-parser prompts must instruct the AI to extract reference
as a SEPARATE field. These tests pin the prompt copy so accidental
edits don't regress the extraction behaviour. Cheap to maintain — the
prompts only change deliberately.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_statement_prompt_documents_reference_field():
    from parsers.ai_parser import STATEMENT_PROMPT
    assert '"reference"' in STATEMENT_PROMPT
    # And the body explains what reference is so the model uses it well
    assert "reference" in STATEMENT_PROMPT.lower()
    # The split between merchant and reference is called out
    assert "MERCHANT" in STATEMENT_PROMPT
    assert "REFERENCE" in STATEMENT_PROMPT.upper()


def test_statement_prompt_strict_mentions_reference():
    from parsers.ai_parser import STATEMENT_PROMPT_STRICT
    assert '"reference"' in STATEMENT_PROMPT_STRICT


def test_statement_text_prompt_documents_reference():
    from parsers.ai_parser import STATEMENT_TEXT_PROMPT
    assert '"reference"' in STATEMENT_TEXT_PROMPT


def test_statement_text_prompt_strict_documents_reference():
    from parsers.ai_parser import STATEMENT_TEXT_PROMPT_STRICT
    assert '"reference"' in STATEMENT_TEXT_PROMPT_STRICT


def test_statement_prompts_give_examples_of_reference_usage():
    """Prompts should include at least one example mapping (merchant +
    reference) — without them the AI tends to merge everything into
    description."""
    from parsers.ai_parser import STATEMENT_TEXT_PROMPT
    # Either the BACS example or the standing-order example, or an
    # invoice example, should be present.
    likely_examples = ["INV-", "RENT FEB", "REF:", "Plumbing", "Invoice"]
    assert any(e in STATEMENT_TEXT_PROMPT for e in likely_examples), (
        "STATEMENT_TEXT_PROMPT should show at least one merchant-vs-"
        "reference worked example so the model knows what to extract"
    )
