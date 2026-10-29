"""Filing timeline with 8-K items tagged for restructuring relevance."""

from __future__ import annotations

from .edgar import filing_url

# 8-K item codes -> (description, relevance). Relevance drives highlighting.
ITEMS_8K = {
    "1.01": ("Entry into a material definitive agreement", "financing"),
    "1.02": ("Termination of a material definitive agreement", "financing"),
    "1.03": ("Bankruptcy or receivership", "critical"),
    "2.02": ("Results of operations", "info"),
    "2.03": ("Creation of a direct financial obligation", "financing"),
    "2.04": ("Triggering event accelerating an obligation (default)", "critical"),
    "2.05": ("Exit / disposal costs (closures, layoffs)", "restructuring"),
    "2.06": ("Material impairment", "restructuring"),
    "3.01": ("Delisting / failure to meet listing standards", "critical"),
    "3.02": ("Unregistered sale of equity securities", "financing"),
    "3.03": ("Material modification of security holders' rights", "restructuring"),
    "4.02": ("Non-reliance on prior financial statements", "critical"),
    "5.02": ("Director / officer departure or appointment", "governance"),
    "5.03": ("Amendments to articles or bylaws", "governance"),
    "5.07": ("Shareholder vote results", "info"),
    "7.01": ("Regulation FD disclosure", "info"),
    "8.01": ("Other events", "info"),
    "9.01": ("Financial statements and exhibits", "info"),
}

SPECIAL_FORMS = {
    "NT 10-K": ("Late filing notice for annual report", "critical"),
    "NT 10-Q": ("Late filing notice for quarterly report", "critical"),
    "15-12G": ("Deregistration of securities", "critical"),
    "15-12B": ("Deregistration of securities", "critical"),
    "25-NSE": ("Delisting from exchange", "critical"),
    "S-3": ("Shelf registration (capital raise capacity)", "financing"),
    "424B5": ("Prospectus supplement (securities offering)", "financing"),
    "SC TO-I": ("Issuer tender offer (debt/equity exchange)", "restructuring"),
    "10-K": ("Annual report", "report"),
    "10-Q": ("Quarterly report", "report"),
}

RANK = {"critical": 0, "restructuring": 1, "financing": 2, "governance": 3,
        "report": 4, "info": 5}


def filing_timeline(submissions: dict, since: str = "2019-01-01") -> list[dict]:
    cik = int(submissions["cik"])
    recent = submissions["filings"]["recent"]
    out = []
    for i, form in enumerate(recent["form"]):
        filed = recent["filingDate"][i]
        if filed < since:
            continue
        items = [s.strip() for s in (recent.get("items", [""] * (i + 1))[i] or "").split(",") if s.strip()]
        tagged = []
        if form.startswith("8-K"):
            tagged = [{"code": c, "label": ITEMS_8K.get(c, ("Item " + c, "info"))[0],
                       "relevance": ITEMS_8K.get(c, ("", "info"))[1]} for c in items]
        elif form in SPECIAL_FORMS:
            label, rel = SPECIAL_FORMS[form]
            tagged = [{"code": form, "label": label, "relevance": rel}]
        else:
            continue
        top = min((t["relevance"] for t in tagged), key=RANK.get, default="info")
        out.append({
            "filed": filed, "form": form, "items": tagged, "relevance": top,
            "description": recent["primaryDocDescription"][i],
            "url": filing_url(cik, recent["accessionNumber"][i], recent["primaryDocument"][i]),
        })
    return out
