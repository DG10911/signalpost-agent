import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.content_quality import apply_content_quality, careers_vacancy, classify_news  # noqa: E402

CO = "GASSNOVA SF"


def art(headline, url, date="2026-06-04"):
    return {"headline": headline, "url": url, "date_published": date}


def test_keeps_dated_news_in_news_section():
    assert classify_news(art("Thomas Skadal har sagt opp sin stilling i Gassnova",
                             "https://gassnova.no/nyheter/thomas-skadal"), CO) is None


def test_keeps_headline_naming_company_outside_news_section():
    assert classify_news(art("Gassnova lanserer nytt program", "https://gassnova.no/2026/06/program/"), CO) is None


def test_rejects_product_and_policy_pages():
    assert "site page" in classify_news(art("Elko termostat med wifi", "https://x.no/produkt/elko-termostat"), CO)
    assert "privacy" in classify_news(art("Personvernerklæring", "https://x.no/personvern"), CO)


def test_rejects_opinion_guide_esg_and_placeholders():
    assert "opinion" in classify_news(art("Kronikk: Hva vi lærte", "https://x.no/nyheter/kronikk-x"), CO)
    assert "guide" in classify_news(art("Hva koster MC-lappen i 2026?", "https://x.no/blogg/mc"), CO)
    assert "ESG" in classify_news(art("Vår bærekraftsrapport", "https://x.no/nyheter/barekraft"), CO)
    assert "placeholder" in classify_news(art("Hello world!", "https://x.no/2020/01/hello-world/"), CO)


def test_rejects_slug_headlines_generic_link_text_and_undated():
    assert "page address" in classify_news(art("produkter avrundet", "https://x.no/produkter-avrundet.html"), CO)
    assert "link text" in classify_news(art("Les mer", "https://x.no/nyheter/a"), CO)
    assert "link text" in classify_news(art("05/10/2026", "https://x.no/nyheter/b"), CO)
    assert classify_news(art("Gassnova nyhet", "https://gassnova.no/nyheter/c", date=""), CO) == "no publication date"


def test_unrelated_blog_article_is_left_blank_with_reason():
    reason = classify_news(art("Når barna får bestemme bilturen", "https://bilmagasinet.no/nar-barna/"), "BILMAGASINET AS")
    assert reason and "does not name the company" in reason


def test_careers_vacancy_check():
    assert careers_vacancy("Vi har dessverre ingen ledige stillinger nå.")[0] is False
    assert careers_vacancy("Karriere hos oss. Send oss gjerne en åpen søknad.")[0] is False
    assert careers_vacancy("Prosjektleder – søknadsfrist 1. desember")[0] is True
    assert careers_vacancy("", job_ads=2)[0] is True


def test_apply_keeps_reasons_and_dedupes():
    site = {"value": {
        "news_articles": [art("Gassnova vinner pris", "https://gassnova.no/nyheter/pris"),
                          art("Gassnova vinner pris", "https://gassnova.no/nyheter/pris/"),
                          art("elko termostat", "https://gassnova.no/produkt/elko-termostat")],
        "careers_pages": [{"url": "https://gassnova.no/karriere", "vacancy": False,
                           "vacancy_evidence": 'page says "ingen ledige stillinger"'}]}}
    out = apply_content_quality({"name": CO}, site)["value"]
    assert [a["url"] for a in out["news_articles"]] == ["https://gassnova.no/nyheter/pris"]
    assert len(out["excluded_news_articles"]) == 2
    assert out["careers_pages"] == []
    assert "ingen ledige" in out["excluded_careers_pages"][0]["excluded_reason"]


def test_company_events_and_how_titles_in_news_section_are_kept():
    assert classify_news(art("Møt oss på UMAMI Arena 2027",
                             "https://act-gruppen.com/messer-og-seminarer/mot-oss"), "ACT GRUPPEN AS") is None
    assert classify_news(art("Knowledge Sharing 2026: Fra hvorfor til hvordan",
                             "https://gassnova.no/nyheter/knowledge-sharing-2026"), CO) is None
    # outside a news section a "hvordan" article is still a guide
    assert "guide" in classify_news(art("Hvordan velge riktig rekkverk", "https://x.no/blogg/rekkverk"), CO)


def test_manager_path_on_shared_domain_is_not_the_companys_site():
    from norway_company_agent import identity
    identity.set_domain_owners({"norian.no": ["111", "222", "333", "444"]})
    try:
        assert identity.is_shared_manager_site("SAMEIET ULLENSAKERGATA 8-12", "999", "norian.no/eiendomsforvaltning")
        assert not identity.is_shared_manager_site("ACME BYGG AS", "999", "www.facebook.com/acmebygg")
    finally:
        identity.set_domain_owners({})
