import re

import pytest

from services import pii
from tests.conftest import SAMPLE_PII_TEXT

RAW_VALUES = ["Jane Doe", "536-90-4399", "(555) 123-4567", "123 Main Street", "4111 1111 1111 1111"]


@pytest.fixture(scope="module")
def tokenized():
    tok = pii.DocumentTokenizer(rewrite_text=True)
    page = tok.process_page(SAMPLE_PII_TEXT, page=1)
    return tok, page


def test_detects_expected_entity_types(tokenized):
    _, page = tokenized
    types = {s.entity_type for s in page.spans}
    assert {"PERSON", "US_SSN", "PHONE_NUMBER", "ADDRESS", "CREDIT_CARD"} <= types


def test_tokenized_values_are_replaced_with_tokens(tokenized):
    _, page = tokenized
    assert "<PERSON_1>" in page.text
    assert "<PHONE_1>" in page.text
    assert "<ADDRESS_1>" in page.text
    assert "Jane Doe" not in page.text
    assert "(555) 123-4567" not in page.text
    assert "123 Main Street" not in page.text


def test_repeated_value_reuses_same_token(tokenized):
    tok, page = tokenized
    # "Jane Doe" and the phone number each appear twice in the sample.
    assert page.text.count("<PERSON_1>") == 2
    assert page.text.count("<PHONE_1>") == 2
    assert tok.token_stats["<PERSON_1>"]["count"] == 2
    assert tok.token_stats["<PHONE_1>"]["count"] == 2


def test_second_person_gets_next_token(tokenized):
    tok, _ = tokenized
    persons = {t: s for t, s in tok.token_stats.items() if s["type"] == "PERSON"}
    assert set(persons) == {"<PERSON_1>", "<PERSON_2>"}
    assert tok.vault["<PERSON_2>"]["value"] == "Robert Miles"


def test_redacted_values_become_bare_tags_and_never_enter_vault(tokenized):
    tok, page = tokenized
    assert "<US_SSN>" in page.text
    assert "<CREDIT_CARD>" in page.text
    assert "536-90-4399" not in page.text
    assert "4111 1111 1111 1111" not in page.text
    vault_values = " ".join(v["value"] for v in tok.vault.values())
    assert "536-90-4399" not in vault_values
    assert "4111" not in vault_values
    assert not any(v["type"] in ("US_SSN", "CREDIT_CARD") for v in tok.vault.values())


def test_no_raw_pii_remains_in_processed_text(tokenized):
    _, page = tokenized
    for value in RAW_VALUES:
        assert value not in page.text, value


def test_inventory_and_token_stats_contain_no_values(tokenized):
    tok, _ = tokenized
    blob = repr(tok.inventory) + repr(tok.token_stats)
    for value in RAW_VALUES:
        assert value not in blob, value
    assert tok.inventory["PERSON"]["count"] == 3  # Jane x2 + Robert
    assert tok.inventory["PERSON"]["pages"] == [1]
    assert tok.inventory["US_SSN"]["count"] == 1
    assert tok.token_stats["<PERSON_1>"]["first_page"] == 1


def test_vault_maps_tokens_to_original_values(tokenized):
    tok, _ = tokenized
    assert tok.vault["<PERSON_1>"] == {"type": "PERSON", "value": "Jane Doe"}
    assert tok.vault["<PHONE_1>"]["type"] == "PHONE_NUMBER"
    assert tok.vault["<ADDRESS_1>"]["value"].startswith("123 Main Street")


def test_render_restores_tokenized_but_not_redacted(tokenized):
    tok, page = tokenized
    rendered, count = pii.render(page.text, tok.vault)
    assert "Jane Doe" in rendered
    assert "(555) 123-4567" in rendered
    assert "123 Main Street" in rendered
    assert "<US_SSN>" in rendered  # irreversible
    assert "<CREDIT_CARD>" in rendered
    assert count == page.text.count("<PERSON_1>") + page.text.count("<PERSON_2>") + page.text.count(
        "<PHONE_1>"
    ) + page.text.count("<ADDRESS_1>")


def test_render_with_empty_vault_is_noop():
    text = "Hello <PERSON_1> and <US_SSN>"
    assert pii.render(text, {}) == (text, 0)


def test_render_value():
    vault = {"<PERSON_1>": {"type": "PERSON", "value": "Jane Doe"}}
    assert pii.render_value("<PERSON_1>", vault) == "Jane Doe"
    assert pii.render_value("<PERSON_9>", vault) is None
    assert pii.render_value(None, vault) is None


def test_tokens_restart_per_document():
    a = pii.DocumentTokenizer(rewrite_text=True)
    a.process_page("Insured: Alice Johnson.", 1)
    b = pii.DocumentTokenizer(rewrite_text=True)
    b.process_page("Insured: Bob Stone.", 1)
    assert "<PERSON_1>" in a.vault and "<PERSON_1>" in b.vault
    assert a.vault["<PERSON_1>"]["value"] != b.vault["<PERSON_1>"]["value"]


def test_pages_are_tracked_across_document():
    tok = pii.DocumentTokenizer(rewrite_text=True)
    tok.process_page("Nothing sensitive here.", 1)
    tok.process_page("Insured: Alice Johnson, phone (555) 987-6543.", 2)
    tok.process_page("Alice Johnson again.", 3)
    assert tok.token_stats["<PERSON_1>"]["first_page"] == 2
    assert tok.inventory["PERSON"]["pages"] == [2, 3]


def test_rewrite_text_false_keeps_text_but_still_builds_vault():
    tok = pii.DocumentTokenizer(rewrite_text=False)
    page = tok.process_page(SAMPLE_PII_TEXT, 1)
    assert page.text == SAMPLE_PII_TEXT
    assert "<PERSON_1>" in tok.vault
    assert tok.inventory["US_SSN"]["count"] == 1


def test_token_regex_matches_only_numbered_tokens():
    assert pii.TOKEN_RE.fullmatch("<PERSON_12>")
    assert pii.TOKEN_RE.fullmatch("<PHONE_1>")
    assert not pii.TOKEN_RE.fullmatch("<US_SSN>")
    assert not re.fullmatch(pii.TOKEN_RE, "<person_1>")


def test_overlap_resolution_prefers_address_over_fragments():
    spans = [
        pii.Span(10, 20, "DATE_TIME", "123 Main", 0.6),
        pii.Span(10, 45, "ADDRESS", "123 Main Street, Springfield, IL 62704", 1.0),
        pii.Span(27, 38, "LOCATION", "Springfield", 0.85),
    ]
    chosen = pii._resolve_overlaps(spans)
    assert [s.entity_type for s in chosen] == ["ADDRESS"]


def test_blank_text_yields_nothing():
    tok = pii.DocumentTokenizer(rewrite_text=True)
    page = tok.process_page("   \n ", 1)
    assert page.text == "   \n "
    assert tok.vault == {} and tok.inventory == {}
