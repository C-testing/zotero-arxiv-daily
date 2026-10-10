"""Persist delivered DOIs and add recommendations without duplicating Zotero items."""

import json
from copy import deepcopy
from pathlib import Path

from .journal_feed import normalize_doi


def validate_destination(zot, collection_key, collection_name, user_id):
    info = zot.key_info()
    access = info.get("access", {}).get("user", {})
    if str(info.get("userID")) != str(user_id) or not access.get("library") or not access.get("write"):
        raise RuntimeError("Zotero key must allow library read/write access for the configured user.")
    collection = zot.collection(collection_key)
    if collection["data"]["name"] != collection_name:
        raise RuntimeError("Zotero destination name does not match the configured collection key.")


def sync_recommendations(zot, papers, collection_key):
    for paper in papers:
        if not paper.doi:
            raise ValueError("Journal recommendations require a DOI.")
        matches = zot.everything(zot.items(q=paper.doi, qmode="everything"))
        existing = next((item for item in matches if normalize_doi(item["data"].get("DOI", "")) ==
                         normalize_doi(paper.doi)), None)
        if existing:
            if collection_key not in existing["data"].get("collections", []):
                item = deepcopy(existing)
                item["data"].setdefault("collections", []).append(collection_key)
                if not zot.update_item(item):
                    raise RuntimeError("Zotero did not confirm collection membership update.")
            continue
        item = zot.item_template("journalArticle")
        item.update({
            "title": paper.title, "creators": paper.creators or [],
            "publicationTitle": paper.journal or "", "date": paper.published_date or "",
            "DOI": paper.doi, "url": paper.url,
            "abstractNote": paper.abstract if paper.abstract_available else "", "collections": [collection_key],
            "tags": [{"tag": "cellulose-daily"}],
            "extra": "Source: Crossref; journal whitelist. Relevance is not a paper-quality rating.",
        })
        result = zot.create_items([item])
        if result.get("failed") or not (result.get("success") or result.get("successful")):
            raise RuntimeError("Zotero did not confirm recommendation creation.")


def load_delivered(path):
    path = Path(path)
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data["delivered"])


def mark_delivered(path, existing, papers):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    delivered = existing | {normalize_doi(paper.doi) for paper in papers if paper.doi}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"delivered": sorted(delivered)}), encoding="utf-8")
    temp.replace(path)
