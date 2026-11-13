from norway_company_agent.site_health import dead_page_reason, registry_site_problem


def web(status="available", note="", title="", text="", final=""):
    return {"status": status, "note": note,
            "value": {"title": title, "main_text_excerpt": text, "final_url": final} if status == "available" else None}


def test_dead_domains_and_error_pages_are_not_published():
    for note in ("Hostname did not resolve", "HTTP 404", "HTTP 400", "HTTP 403",
                 "RemoteDisconnected: Remote end closed connection without response"):
        assert registry_site_problem("www.barentsgym.no", web("blocked", note), "BARENTS GYM AS"), note


def test_local_network_errors_keep_the_registry_site():
    for note in ("ConnectionResetError: [Errno 54] Connection reset by peer",
                 "ValueError: Private, loopback, link-local, multicast, and reserved addresses are blocked",
                 "robots.txt disallows this user agent"):
        assert registry_site_problem("www.wavin.no", web("source_error", note), "NORSK WAVIN AS") is None, note


def test_not_a_web_address():
    assert "not a web address" in registry_site_problem("wwwrekdaltransport", web("blocked", "x"), "REKDAL TRANSPORT AS")


def test_placeholder_pages():
    assert dead_page_reason("Welcome to nginx!", "If you see this page, the nginx web server is successfully installed")
    assert dead_page_reason("Account Suspended", "This Account has been suspended.")
    assert dead_page_reason("Squarespace - Website Expired", "")
    assert dead_page_reason("Fenomental", "Kommer snart") is None
    assert dead_page_reason("Bakkehaug Bil - Din bilforhandler", "Velkommen til oss") is None


def test_redirect_to_another_business():
    w = web(title="JD Power - Data, Analytics & Insights", final="https://www.jdpower.com/business")
    assert "redirects to jdpower.com" in registry_site_problem("www.rodboka.no", w, "RØDBOKA AS")
    ok = web(title="Sun² Skjoldskiftet", final="https://sun-skjoldskiftet.webnode.page/")
    assert registry_site_problem("sun2skjold.no", ok, "SOL SKJOLD AS") is None


def test_live_site_passes():
    w = web(title="Gundersons", final="https://www.gundersons.no/")
    assert registry_site_problem("www.gundersons.no", w, "GUNDERSONS AS") is None


def test_shared_link_to_other_sites_article_is_not_company_news():
    import datetime as dt
    from norway_company_agent.content_quality import classify_news
    art = {"headline": "Skal bygge «tanks» i Norge", "date_published": "2024-06-14",
           "url": "https://www.dagbladet.no/nyheter/skal-bygge-tanks/123"}
    assert classify_news(art, "ARENA NORD UTVIKLING AS", dt.date(2026, 11, 5),
                         site_url="https://arenanord.no/hjem") == "link to another site's article that does not name the company"
    art2 = {**art, "headline": "Arena Nord får ny kontrakt"}
    assert classify_news(art2, "ARENA NORD UTVIKLING AS", dt.date(2026, 11, 5), site_url="https://arenanord.no/hjem") is None


def test_parked_and_unnamed_registry_sites_are_not_published():
    w = web(title="www.Hjelmen-as.no is parked at Miss Hosting Web Hosting", final="https://www.hjelmen-as.no/")
    assert registry_site_problem("www.Hjelmen-as.no", w, "HJELMEN AS")
    sgf = web(title="SGF | Vårt mål er å gjøre SGF til et foretrukket hjem", text="Vi forvalter boliger",
              final="https://www.sgf.no/")
    assert "never names the company" in registry_site_problem("www.sgf.no", sgf, "AS ÅRVOLLVEIEN 15")
    own = web(title="Advokatfirmaet Høivang - Advokatfirma i Sandefjord", final="https://www.hoivang.no/")
    assert registry_site_problem("www.hoivang.no", own, "ADVOKAT AMALIE HØIVANG") is None
