from norway_company_agent import identity
from norway_company_agent.claim_checks import (EXCERPT_MISSING, recheck_excerpts, rescue_group_site_items,
                                               withheld_counts)
from norway_company_agent.content_quality import apply_content_quality
from norway_company_agent.identity import apply_website_identity_gate
from norway_company_agent.output_contract import to_contract
from norway_company_agent.website import source_text

ORG = "923609016"  # valid mod-11
PARENT = "914778271"


def _profile(parent=None):
    reg = {"overordnetEnhet": parent} if parent else {}
    return {"organisation_number": ORG, "name": "Fjordbygg Vest AS",
            "evidence": {"registry": {"status": "available", "value": reg}}}


def _site(home_text, news_page_text, url="https://fjordgruppen.no/"):
    texts = {url: source_text(home_text), url + "nyheter/a": source_text(news_page_text)}
    return {"status": "available", "source_url": url, "value": {
        "requested_url": url, "final_url": url, "title": "Fjordgruppen", "description": "",
        "main_text_excerpt": home_text, "identity_text_excerpt": home_text,
        "news_articles": [{"headline": "Fjordbygg Vest AS vinner kontrakt", "date_published": "2026-10-01",
                           "url": url + "nyheter/a", "source_page": url + "nyheter/a"},
                          {"headline": "Gruppen kjøper ny maskin", "date_published": "2026-10-02",
                           "url": url + "nyheter/b", "source_page": url + "nyheter/b"}],
        "job_postings": [], "careers_pages": [], "_source_texts": texts}}


def test_parent_domain_is_group_site_and_only_named_items_survive(monkeypatch):
    monkeypatch.setattr(identity, "DOMAIN_OWNERS", {"fjordgruppen.no": [PARENT]})
    profile = _profile(PARENT)
    site = _site("Fjordgruppen bygger Fjordbygg Vest i hele regionen. " * 5,
                 "Nyhet: Fjordbygg Vest AS vinner kontrakt med kommunen")
    gated = apply_website_identity_gate(profile, site)["website"]
    assessment = gated["value"]["identity_assessment"]
    assert assessment["group_site"] and not assessment["publishable"]
    out = rescue_group_site_items(profile, gated)
    assert [a["headline"] for a in out["value"]["news_articles"]] == ["Fjordbygg Vest AS vinner kontrakt"]
    assert len(out["value"]["quarantined_news_articles"]) == 1
    profile["evidence"]["website"] = apply_content_quality(profile, out, today=__import__("datetime").date(2026, 11, 2))
    contract = to_contract(profile, run_id="t", started_at="", completed_at="", terminal_status="completed")
    news = [c for c in contract["claims"] if c["field"] == "news"]
    assert len(news) == 1 and news[0]["value"]["headline"].startswith("Fjordbygg Vest AS")
    counts = withheld_counts([profile])
    assert counts["news"]["group/parent/sister site: item's page does not name the company"] == 1


def test_sister_domain_with_full_name_on_homepage_is_not_group(monkeypatch):
    monkeypatch.setattr(identity, "DOMAIN_OWNERS", {"fjordgruppen.no": ["999999999"]})
    site = _site("Velkommen til Fjordbygg Vest AS, din byggmester. " * 4, "x")
    gated = apply_website_identity_gate(_profile(), site)["website"]
    assert not gated["value"]["identity_assessment"]["group_site"]


def test_other_orgnr_only_on_homepage_is_group_site(monkeypatch):
    monkeypatch.setattr(identity, "DOMAIN_OWNERS", {})
    site = _site("Fjordbygg Vest – en del av konsernet. Org.nr 914 778 271. " * 3, "x")
    gated = apply_website_identity_gate(_profile(), site)["website"]
    assert gated["value"]["identity_assessment"]["group_site"]


def test_excerpt_recheck_drops_unquoted_claims():
    url = "https://fjordbygg.no/"
    site = {"status": "available", "value": {
        "final_url": url, "description": "Vi bygger hus på Vestlandet",
        "structured_facts": {"telephone": "+47 55 00 00 00", "foundingDate": "1999"},
        "news_articles": [{"headline": "Ny   KONTRAKT  i Bergen", "source_page": url},
                          {"headline": "Overskrift som ikke finnes", "source_page": url}],
        "job_postings": [{"title": "Tømrer", "source_page": url}],
        "careers_pages": [],
        "_source_texts": {url: source_text("<p>Vi bygger hus p&aring; Vestlandet</p><a>Ny kontrakt i <b>Bergen</b></a>"
                                           "<script>{\"t\":\"T\\u00f8mrer\"}</script> 1999")}}}
    value = recheck_excerpts(site)["value"]
    assert [a["headline"] for a in value["news_articles"]] == ["Ny   KONTRAKT  i Bergen"]
    assert value["excluded_news_articles"][0]["excluded_reason"] == EXCERPT_MISSING
    assert [j["title"] for j in value["job_postings"]] == ["Tømrer"]
    assert value["description"] == "Vi bygger hus på Vestlandet"
    assert "telephone" in value["excluded_structured_facts"] and value["structured_facts"] == {"foundingDate": "1999"}
    counts = withheld_counts([{"evidence": {"website": site}}])
    assert counts["news"][EXCERPT_MISSING] == 1


def test_excerpt_recheck_noop_without_fetched_text():
    site = {"status": "available", "value": {"news_articles": [{"headline": "x"}]}}
    assert recheck_excerpts(site)["value"]["news_articles"] == [{"headline": "x"}]


def test_email_domain_tried_before_name_guesses():
    from norway_company_agent import domain_discovery
    seen = []
    def fetch(url):
        seen.append(url)
        return {"status": "not_found"}, {"requests": 1}
    domain_discovery.discover_website(
        {"organisation_number": ORG, "name": "Fjordbygg Vest AS",
         "evidence": {"registry": {"value": {"epostadresse": "post@fjordbygg-vest.no"}}}},
        fetch, max_candidates=3, email_candidates=["https://fjordbygg-vest.no"])
    assert seen[0] == "https://fjordbygg-vest.no"
