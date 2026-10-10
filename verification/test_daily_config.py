import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
except ImportError:
    raise unittest.SkipTest("Hydra is needed to verify composed daily topic settings")

from zotero_arxiv_daily.daily_topic import override_for_day


class DailyConfigTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        with initialize_config_dir(config_dir=str(root / 'config'), version_base=None):
            self.base = compose(config_name='default')

    def test_tomorrow_merges_journals_and_restricts_topic_without_api(self):
        override = override_for_day(self.base, datetime(2026, 10, 10, 22, tzinfo=timezone.utc))
        config = OmegaConf.merge(OmegaConf.to_container(self.base, resolve=False), override)
        self.assertEqual(len(config.source.journals.journal_issns), 18)
        self.assertEqual(len(config.source.journals.allowed_journals), 18)
        self.assertIn('Carbohydrate Polymers', config.source.journals.journal_issns)
        self.assertEqual(config.source.journals.topic_terms[0], 'gum arabic')
        self.assertEqual(config.source.journals.publication_years, 5)
        self.assertIsNone(config.source.journals.lookback_days)
        self.assertTrue(config.source.journals.sort_by_publication)
        self.assertEqual(config.executor.order_by, 'publication_date')
        self.assertEqual(config.email.subject_prefix, 'Gum Arabic Research Daily')
        self.assertFalse(config.llm.enabled)
        self.assertEqual(config.zotero.recommendation_collection, 'BQ33UNRV')
        self.assertEqual(config.executor.delivery_state, '.delivery/sent.json')
        self.assertEqual(OmegaConf.to_container(config.zotero, resolve=False)['api_key'], '${oc.env:ZOTERO_KEY}')

    def test_next_day_restores_base_without_mutating_it(self):
        self.assertFalse(override_for_day(self.base, datetime(2026, 10, 11, 22, tzinfo=timezone.utc)))
        self.assertEqual(len(self.base.source.journals.journal_issns), 15)
        self.assertIsNone(self.base.source.journals.get('topic_terms'))
        self.assertEqual(self.base.email.subject_prefix, 'Cellulose Journal Daily')
