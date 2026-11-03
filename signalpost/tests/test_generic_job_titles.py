import datetime as dt

from norway_company_agent.content_quality import classify_job

TODAY = dt.date(2026, 11, 3)


def test_generic_titles_without_dates_are_rejected():
    for title in ("Jobb i Torghatten", "Karriere hos Acme", "Work for us", "Jobs",
                  "Søk plass som lærling og kadett", "Ledige stillinger"):
        assert classify_job({"title": title}, TODAY) == "recruiting landing page, not a single job ad", title


def test_real_ads_are_kept():
    for title in ("Torghatten søker maskinsjef", "Matros Forøy - Ågskardet", "Jobbcoach 100 % stilling",
                  "Karrierekonsulent", "Lærling i elektrofaget"):
        assert classify_job({"title": title}, TODAY) is None, title


def test_generic_title_with_a_date_is_kept():
    assert classify_job({"title": "Jobb i Torghatten", "date_posted": "2026-10-30"}, TODAY) is None
