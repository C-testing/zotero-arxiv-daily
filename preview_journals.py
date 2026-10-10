"""Preview public journal candidates without keys, email, or Zotero writes."""

import argparse
import json
from pathlib import Path

from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf
from zotero_arxiv_daily.journal_feed import retrieve_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    with initialize_config_dir(config_dir=str(root / 'config'), version_base=None):
        config = compose(config_name='default')
        source = OmegaConf.to_container(config.source.journals, resolve=True)
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
