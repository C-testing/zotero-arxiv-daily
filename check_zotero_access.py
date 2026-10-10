"""Check cloud permissions without modifying the Zotero library."""

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, default=12724319)
    parser.add_argument("--require-write", action="store_true")
    parser.add_argument("--collection-key", default="BQ33UNRV")
    parser.add_argument("--report-destination", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("ZOTERO_KEY") or getpass.getpass("Zotero API key: ")
    if not key.strip():
        print("Missing Zotero API key.", file=sys.stderr)
        return 1

    def fetch(path):
        request = urllib.request.Request(
            "https://api.zotero.org" + path,
            headers={"Zotero-API-Key": key, "Zotero-API-Version": "3"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    try:
        account = fetch("/keys/current")
        if str(account.get("userID")) != str(args.user_id):
            print("The key belongs to a different Zotero account.", file=sys.stderr)
            return 1
        if args.require_write and not account.get("access", {}).get("user", {}).get("write"):
            print("The key needs library write permission to save recommendations.", file=sys.stderr)
            return 1
        collection = fetch(f"/users/{args.user_id}/collections/{args.collection_key}")
        if args.report_destination:
            destination = fetch(
                f"/users/{args.user_id}/collections/{args.collection_key}/items/top"
                "?format=json&limit=100&sort=dateAdded&direction=desc"
            )
            recommendations = [item["data"] for item in destination
                               if any(tag.get("tag") == "cellulose-daily"
                                      for tag in item["data"].get("tags", []))]
            journals = sorted({item.get("publicationTitle", "") for item in recommendations})
            print(f"Destination: {collection['data']['name']}; tagged recommendations: {len(recommendations)}.")
            print("Journals: " + "; ".join(journals))
        items = fetch(
            f"/users/{args.user_id}/items?format=json&limit=100"
            "&itemType=conferencePaper%20%7C%7C%20journalArticle%20%7C%7C%20preprint"
        )
        usable = sum(bool(item["data"].get("abstractNote", "").strip()) for item in items)
        print(f"Cloud access OK. Papers checked: {len(items)}; with abstracts: {usable}.")
        if usable == 0:
            print("No usable abstracts in this sample. Sync Zotero and check paper abstracts.")
        return 0
    except urllib.error.HTTPError as error:
        print(f"Zotero cloud request failed: HTTP {error.code}.", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError):
        print("Cannot reach the Zotero cloud API.", file=sys.stderr)
    except (KeyError, ValueError, TypeError):
        print("Unexpected Zotero API response.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
