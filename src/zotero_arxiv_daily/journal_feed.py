"""Crossref metadata filtering for a configured journal and research topic list."""

import html
import json
import re
import warnings
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .daily_topic import SHANGHAI


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


def matches_topic(text, topic_terms=None):
    text = text.casefold()
    if topic_terms is not None:
        text = " ".join(re.sub(r"[-\u2010\u2011\u2013]", " ", text).split())
        return any(re.search(r"\b" + r"\s+".join(re.escape(word) for word in term.casefold().split()) + r"\b", text)
                   for term in topic_terms if term.strip())
    material = re.search(r"\b(?:cellulose|nanocellulose|cellulosic|lignocellulos\w*|pectin|gum arabic|polysaccharide\w*|biopolymer\w*)\b", text)
    mechanism = re.search(r"\b(?:material\w*|composite\w*|film\w*|foam\w*|hydrogel\w*|sensor\w*|interfac\w*|tough\w*|self.assembly|conduct\w*|nanofibr\w*|nanofiber\w*|nanocrystal\w*|modif\w*|functionaliz\w*|orient\w*|reinfor\w*|fracture\w*|fatigue\w*|electrod\w*|battery|photonic\w*|actuat\w*|dissol\w*|crystallin\w*)\b", text)
    return bool(material and mechanism)


def select_record(item, allowed_journals, cutoff, today, topic_terms=None):
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
    if re.match(r"^(?:correction|corrigendum|erratum|retraction|withdrawal|expression of concern)\b", title, re.I):
        return None
    if item.get("update-to") or not matches_topic(title + " " + abstract, topic_terms):
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
    today = today or datetime.now(SHANGHAI).date()
    years = config.get("publication_years")
    if years is not None:
        try:
            cutoff = today.replace(year=today.year - int(years))
        except ValueError:
            cutoff = today.replace(year=today.year - int(years), day=28)
    else:
        cutoff = today - timedelta(days=int(config.get("publication_days", 30)))
    lookback_days = config.get("lookback_days", 7)
    indexed_cutoff = today - timedelta(days=int(lookback_days)) if lookback_days is not None else None
    get_json = get_json or _get_json
    records = {}
    journal_issns = config.get("journal_issns")
    searches = [(journal, f"https://api.crossref.org/journals/{issn}/works")
                for journal, issn in journal_issns.items()] if journal_issns else [(query, None) for query in config["queries"]]
    for query, api_url in searches:
        cursor = "*"
        paged_by_date = bool(config.get("sort_by_publication"))
        for page in range(10):
            params = {
                "rows": 200,
                "filter": f"type:journal-article,from-pub-date:{cutoff},until-pub-date:{today}",
            }
            if indexed_cutoff is not None:
                params["filter"] += f",from-index-date:{indexed_cutoff}"
            if paged_by_date:
                params.update(sort="published", order="desc", offset=page * 200)
            else:
                params['cursor'] = cursor
            if api_url:
                if config.get("topic_query"):
                    params["query"] = config["topic_query"]
                result = get_json(params, api_url=api_url)["message"]
            else:
                params["query"] = query
                result = get_json(params)["message"]
            items = result.get("items", [])
            for item in items:
                record = select_record(item, config["allowed_journals"], cutoff, today,
                                       config.get("topic_terms"))
                if record:
                    records[record["doi"]] = record
            next_cursor = result.get("next-cursor")
            if len(items) < 200:
                break
            if not paged_by_date:
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
            if page == 9:
                warnings.warn(f"Crossref source '{query}' reached its 2000-record limit; coverage is incomplete.")
            sleep(1)
    return sorted(records.values(), key=lambda record: record["published_date"], reverse=True)
