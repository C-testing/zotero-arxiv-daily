"""Crossref metadata filtering for a configured journal and research topic list."""

import html
import json
import re
import warnings
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def clean_text(value):
    parser = _TextParser()
    parser.feed(value or "")
    return " ".join(html.unescape(" ".join(parser.parts)).split())


def normalize_doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).lower()


def publication_date(item):
    dates = []
    for field in ("published-online", "published-print", "published", "issued"):
        parts = item.get(field, {}).get("date-parts", [])
        if not parts or not parts[0]:
            continue
        values = parts[0]
        try:
            dates.append(date(values[0], values[1] if len(values) > 1 else 1,
                              values[2] if len(values) > 2 else 1))
        except (ValueError, TypeError):
            continue
    return min(dates) if dates else None


def matches_topic(text):
    text = text.casefold()
    material = re.search(r"\b(?:cellulose|nanocellulose|cellulosic|lignocellulos\w*|pectin|gum arabic|polysaccharide\w*|biopolymer\w*)\b", text)
    mechanism = re.search(r"\b(?:material\w*|composite\w*|film\w*|foam\w*|hydrogel\w*|sensor\w*|interfac\w*|tough\w*|self.assembly|conduct\w*|nanofibr\w*|nanofiber\w*|nanocrystal\w*|modif\w*|functionaliz\w*|orient\w*|reinfor\w*|fracture\w*|fatigue\w*|electrod\w*|battery|photonic\w*|actuat\w*|dissol\w*|crystallin\w*)\b", text)
    return bool(material and mechanism)


def select_record(item, allowed_journals, cutoff, today):
    if item.get("type") != "journal-article":
        return None
    journals = item.get("container-title") or []
    allowed = {name.strip().casefold() for name in allowed_journals}
    journal = next((name for name in journals if name.strip().casefold() in allowed), None)
    published = publication_date(item)
    doi = normalize_doi(item.get("DOI", ""))
    title = clean_text(next(iter(item.get("title") or []), ""))
    abstract = clean_text(item.get("abstract", ""))
    if not journal or not doi or not title or published is None or not cutoff <= published <= today:
        return None
    if re.match(r"^(?:correction|erratum|retraction|withdrawal|expression of concern)\b", title, re.I):
        return None
    if item.get("update-to") or not matches_topic(title + " " + abstract):
        return None
    authors = item.get("author") or []
    names = [author.get("name") or " ".join(filter(None, [author.get("given"), author.get("family")]))
             for author in authors]
    affiliations = sorted({affiliation["name"] for author in authors
                           for affiliation in author.get("affiliation", []) if affiliation.get("name")})
    return {
        "title": title, "abstract": abstract, "doi": doi, "journal": journal,
        "published_date": published.isoformat(), "authors": names,
        "creators": [{"creatorType": "author", **({"name": author["name"]} if author.get("name") else
                     {"firstName": author.get("given", ""), "lastName": author.get("family", "")})}
                     for author in authors],
        "affiliations": affiliations, "url": f"https://doi.org/{doi}",
    }


def _get_json(params, api_url="https://api.crossref.org/works"):
    request = Request(api_url + "?" + urlencode(params), headers={
        "User-Agent": "zotero-arxiv-daily/1.0 (https://github.com/TideDra/zotero-arxiv-daily)",
        "Accept": "application/json",
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            if attempt == 2 or isinstance(error, HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise
            sleep(2 ** (attempt + 1))


def retrieve_records(config, today=None, get_json=None):
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=int(config.get("publication_days", 30)))
    indexed_cutoff = today - timedelta(days=int(config.get("lookback_days", 7)))
    get_json = get_json or _get_json
    records = {}
    journal_issns = config.get("journal_issns")
    searches = [(journal, f"https://api.crossref.org/journals/{issn}/works")
                for journal, issn in journal_issns.items()] if journal_issns else [(query, None) for query in config["queries"]]
    for query, api_url in searches:
        cursor = "*"
        for page in range(10):
            params = {
                "rows": 200, "cursor": cursor,
                "filter": f"type:journal-article,from-index-date:{indexed_cutoff},from-pub-date:{cutoff},until-pub-date:{today}",
            }
            if api_url:
                result = get_json(params, api_url=api_url)["message"]
            else:
                params["query"] = query
                result = get_json(params)["message"]
            items = result.get("items", [])
            for item in items:
                record = select_record(item, config["allowed_journals"], cutoff, today)
                if record:
                    records[record["doi"]] = record
            next_cursor = result.get("next-cursor")
            if len(items) < 200 or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            if page == 9:
                warnings.warn(f"Crossref source '{query}' reached its 2000-record limit; coverage is incomplete.")
            sleep(1)
    return sorted(records.values(), key=lambda record: record["published_date"], reverse=True)
