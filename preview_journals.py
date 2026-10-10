"""Preview public journal candidates without keys, email, or Zotero writes."""

import argparse
import json
from datetime import date, datetime, time
from pathlib import Path

from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf
from zotero_arxiv_daily.journal_feed import retrieve_records
from zotero_arxiv_daily.daily_topic import SHANGHAI, override_for_day


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--topic-date', type=date.fromisoformat,
                        help='Preview a scheduled topic without pretending future papers have been published.')
    parser.add_argument('--publication-days', type=int, help='Override the preview window only.')
    args = parser.parse_args()
    if args.publication_days is not None and args.publication_days < 1:
        parser.error('--publication-days must be positive')
    root = Path(__file__).resolve().parent
    with initialize_config_dir(config_dir=str(root / 'config'), version_base=None):
        config = compose(config_name='default')
        now = datetime.combine(args.topic_date, time(12), SHANGHAI) if args.topic_date else None
        override = override_for_day(config, now)
        if override:
            config = OmegaConf.merge(OmegaConf.to_container(config, resolve=False), override)
        source = OmegaConf.to_container(config.source.journals, resolve=True)
        if args.publication_days is not None:
            source.pop('publication_years', None)
            source['publication_days'] = args.publication_days
            source['lookback_days'] = max(source.get('lookback_days') or 0, args.publication_days)
    records = retrieve_records(source)
    data = json.dumps(records, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding='utf-8')
    print(f'Public journal candidates: {len(records)}. No email sent; no Zotero items modified.')
    for record in records[:10]:
        print(f"{record['journal']} | {record['published_date']} | {record['doi']} | {record['title']}")


if __name__ == '__main__':
    main()
