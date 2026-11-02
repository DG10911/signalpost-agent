from norway_company_agent.claim_checks import _found
from norway_company_agent.website import source_text


def test_meta_description_is_checkable():
    html = '<html><head><meta name="description" content="Vi tilbyr alt av renhold i Oslo og Viken"></head><body>Hei</body></html>'
    assert _found("Vi tilbyr alt av renhold i Oslo og Viken", source_text(html))


def test_entity_encoded_value_matches():
    html = '<script type="application/ld+json">{"telephone":"&#x2B;46 [0]40-671 23 00"}</script>'
    assert _found("&#x2B;46 [0]40-671 23 00", source_text(html))


def test_missing_quote_still_fails():
    assert not _found("Helt annen tekst", source_text("<p>Hei verden</p>"))


def test_truncated_excerpt_with_trailing_space_matches():
    text = source_text('<meta name="description" content="kombinert med medisinsk oppfolging">')
    assert _found("kombinert med me ", text)
