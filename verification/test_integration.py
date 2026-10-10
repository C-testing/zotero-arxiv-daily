import importlib.util
import json
import sys
import tempfile
import types
import unittest
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from zotero_arxiv_daily.journal_feed import select_record, retrieve_records
from zotero_arxiv_daily.daily_topic import override_for_day
from zotero_arxiv_daily.recommendation_delivery import (
    validate_destination, sync_recommendations, load_delivered, mark_delivered,
)


SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'zotero_arxiv_daily'
TODAY = date(2026, 10, 10)
CUTOFF = date(2026, 9, 10)


def article(**changes):
    item = {'type': 'journal-article', 'DOI': '10.1234/TEST',
            'title': ['Cellulose composite films'], 'container-title': ['Advanced Materials'],
            'published-online': {'date-parts': [[2026, 10, 1]]},
            'abstract': '<jats:p>Nanocellulose &amp; pectin tough films.</jats:p>',
            'author': [{'given': 'A', 'family': 'B', 'affiliation': [{'name': 'University'}]}]}
    item.update(changes)
    return item


def paper(**changes):
    data = dict(doi='10.1234/test', title='Cellulose film', authors=['A B'], creators=[],
                journal='Advanced Materials', published_date='2026-10-01',
                url='https://doi.org/10.1234/test', pdf_url=None, tldr='Original abstract',
                abstract='Original abstract', abstract_available=True, affiliations=[], score=8.0)
    data.update(changes)
    return SimpleNamespace(**data)


class JournalTests(unittest.TestCase):
    def choose(self, item):
        return select_record(item, ['Advanced Materials'], CUTOFF, TODAY)

    def test_published_whitelisted_topic_is_selected(self):
        record = self.choose(article())
        self.assertEqual(record['doi'], '10.1234/test')
        self.assertEqual(record['abstract'], 'Nanocellulose & pectin tough films.')

    def test_preprint_and_lookalike_journal_rejected(self):
        self.assertIsNone(self.choose(article(type='posted-content')))
        self.assertIsNone(self.choose(article(**{'container-title': ['Advanced Materials Interfaces']})))

    def test_future_and_old_online_publications_rejected(self):
        self.assertIsNone(self.choose(article(**{'published-online': {'date-parts': [[2027, 1, 1]]}})))
        self.assertIsNone(self.choose(article(**{
            'published-online': {'date-parts': [[2020, 1, 1]]},
            'published-print': {'date-parts': [[2026, 10, 1]]}})))

    def test_unknown_date_and_retraction_rejected(self):
        self.assertIsNone(self.choose(article(**{'published-online': {}})))
        self.assertIsNone(self.choose(article(title=['Retraction: Cellulose composite films'])))
        self.assertIsNone(self.choose(article(title=['Corrigendum: Cellulose composite films'])))
        self.assertIsNone(self.choose(article(**{'update-to': [{'type': 'correction'}]})))

    def test_irrelevant_article_rejected(self):
        self.assertIsNone(self.choose(article(title=['Quantum gates'], abstract='Quantum computing')))

    def test_incidental_cellulose_biology_is_not_materials_research(self):
        self.assertIsNone(self.choose(article(title=['Plant immune receptor recognition'],
                                             abstract='Cellulose fragments activate plant immunity.')))

    def test_related_polysaccharide_sensor_kept(self):
        self.assertIsNotNone(self.choose(article(title=['Pectin hydrogel pressure sensors'], abstract='')))

    def test_deduplication_across_queries(self):
        config = {'queries': ['cellulose', 'nanocellulose'], 'allowed_journals': ['Advanced Materials']}
        fetch = Mock(return_value={'message': {'items': [article()]}})
        records = retrieve_records(config, TODAY, fetch)
        self.assertEqual(len(records), 1)
        self.assertIn('until-pub-date:2026-10-10', fetch.call_args.args[0]['filter'])

    def test_cursor_pagination(self):
        items = [article() for _ in range(200)]
        fetch = Mock(side_effect=[{'message': {'items': items, 'next-cursor': 'next'}},
                                 {'message': {'items': [article(DOI='10.1234/second')]}}])
        with patch('zotero_arxiv_daily.journal_feed.sleep'):
            records = retrieve_records({'queries': ['cellulose'], 'allowed_journals': ['Advanced Materials']}, TODAY, fetch)
        self.assertEqual(len(records), 2)
        self.assertEqual(fetch.call_args.args[0]['cursor'], 'next')

    def test_journal_endpoint_avoids_global_search_truncation(self):
        fetch = Mock(return_value={'message': {'items': [article()]}})
        config = {'journal_issns': {'Advanced Materials': '0935-9648'},
                  'allowed_journals': ['Advanced Materials']}
        self.assertEqual(len(retrieve_records(config, TODAY, fetch)), 1)
        self.assertEqual(fetch.call_args.kwargs['api_url'], 'https://api.crossref.org/journals/0935-9648/works')
        self.assertNotIn('query', fetch.call_args.args[0])

    def test_gum_topic_requires_gum_not_generic_cellulose(self):
        config = {'journal_issns': {'Advanced Materials': '0935-9648'},
                  'allowed_journals': ['Advanced Materials'],
                  'topic_terms': ['gum arabic', 'acacia gum', 'gum acacia', 'arabic gum']}
        items = [article(), article(DOI='10.1234/gum', title=['Acacia-gum emulsification'], abstract='')]
        fetch = Mock(return_value={'message': {'items': items}})
        self.assertEqual([item['doi'] for item in retrieve_records(config, TODAY, fetch)], ['10.1234/gum'])

    def test_gum_topic_still_rejects_preprints_old_and_future_papers(self):
        for item in [article(type='posted-content'),
                     article(**{'published-online': {'date-parts': [[2020, 1, 1]]}}),
                     article(**{'published-online': {'date-parts': [[2027, 1, 1]]}})]:
            item['title'] = ['Gum arabic emulsions']
            self.assertIsNone(select_record(item, ['Advanced Materials'], CUTOFF, TODAY, ['gum arabic']))

    def test_five_year_topic_queries_journals_and_does_not_require_recent_indexing(self):
        config = {'journal_issns': {'Advanced Materials': '0935-9648'},
                  'allowed_journals': ['Advanced Materials'], 'topic_terms': ['gum arabic'],
                  'topic_query': 'gum arabic acacia gum', 'publication_years': 5,
                  'lookback_days': None, 'sort_by_publication': True}
        fetch = Mock(return_value={'message': {'items': [article(title=['Gum arabic films'])]}})
        self.assertEqual(len(retrieve_records(config, TODAY, fetch)), 1)
        params = fetch.call_args.args[0]
        self.assertIn('from-pub-date:2021-10-10', params['filter'])
        self.assertNotIn('from-index-date', params['filter'])
        self.assertEqual(params['query'], 'gum arabic acacia gum')
        self.assertEqual((params['sort'], params['order']), ('published', 'desc'))
        self.assertNotIn('cursor', params)
        self.assertEqual(params['offset'], 0)
        retrieve_records(config, date(2024, 2, 29), fetch)
        self.assertIn('from-pub-date:2019-02-28', fetch.call_args.args[0]['filter'])

    def test_publication_sort_uses_offset_pagination_not_cursor(self):
        config = {'queries': ['gum arabic'], 'allowed_journals': ['Advanced Materials'],
                  'topic_terms': ['gum arabic'], 'sort_by_publication': True}
        first = [article(title=['Gum arabic films']) for _ in range(200)]
        fetch = Mock(side_effect=[{'message': {'items': first}},
                                 {'message': {'items': [article(DOI='10.1234/new', title=['Gum arabic films'])]}}])
        with patch('zotero_arxiv_daily.journal_feed.sleep'):
            self.assertEqual(len(retrieve_records(config, TODAY, fetch)), 2)
        self.assertEqual(fetch.call_args.args[0]['offset'], 200)
        self.assertNotIn('cursor', fetch.call_args.args[0])


class DailyTopicTests(unittest.TestCase):
    def test_shanghai_day_starts_before_utc_day_and_expires(self):
        override = {'email': {'subject_prefix': 'Gum Arabic Research Daily'}}
        config = {'daily_overrides': {'2026-10-11': override}}
        self.assertEqual(override_for_day(config, datetime(2026, 10, 10, 15, 59, tzinfo=timezone.utc)), {})
        self.assertEqual(override_for_day(config, datetime(2026, 10, 10, 16, tzinfo=timezone.utc)), override)
        self.assertEqual(override_for_day(config, datetime(2026, 10, 10, 22, tzinfo=timezone.utc)), override)
        self.assertEqual(override_for_day(config, datetime(2026, 10, 11, 15, 59, tzinfo=timezone.utc)), override)
        self.assertEqual(override_for_day(config, datetime(2026, 10, 11, 16, tzinfo=timezone.utc)), {})
        self.assertEqual(config['daily_overrides']['2026-10-11'], override)

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            override_for_day({}, datetime(2026, 10, 11))


class DeliveryTests(unittest.TestCase):
    def client(self):
        client = Mock()
        client.key_info.return_value = {'userID': 12724319, 'access': {'user': {'library': True, 'write': True}}}
        client.collection.return_value = {'data': {'name': 'Destination'}}
        client.items.return_value = []
        client.everything.side_effect = lambda value: value
        client.item_template.return_value = {'itemType': 'journalArticle'}
        client.create_items.return_value = {'success': {'0': 'NEWITEM'}, 'failed': {}}
        return client

    def test_wrong_account_or_destination_prevents_writes(self):
        client = self.client()
        with self.assertRaises(RuntimeError):
            validate_destination(client, 'BQ33UNRV', 'Different', 12724319)
        client.key_info.return_value['access']['user']['write'] = False
        with self.assertRaises(RuntimeError):
            validate_destination(client, 'BQ33UNRV', 'Destination', 12724319)
        client.create_items.assert_not_called()

    def test_new_item_preserves_publisher_metadata(self):
        client = self.client()
        sync_recommendations(client, [paper()], 'BQ33UNRV')
        item = client.create_items.call_args.args[0][0]
        self.assertEqual(item['collections'], ['BQ33UNRV'])
        self.assertEqual(item['publicationTitle'], 'Advanced Materials')
        self.assertEqual(item['abstractNote'], 'Original abstract')

    def test_duplicate_doi_adds_membership_without_copy(self):
        client = self.client()
        existing = {'key': 'EXISTING', 'version': 4,
                    'data': {'DOI': 'https://doi.org/10.1234/TEST', 'collections': ['OLD']}}
        client.items.return_value = [existing]
        sync_recommendations(client, [paper()], 'BQ33UNRV')
        client.create_items.assert_not_called()
        self.assertEqual(client.update_item.call_args.args[0]['data']['collections'], ['OLD', 'BQ33UNRV'])
        self.assertEqual(existing['data']['collections'], ['OLD'])

    def test_missing_abstract_does_not_create_an_invented_abstract(self):
        client = self.client()
        sync_recommendations(client, [paper(abstract_available=False, abstract='Title only')], 'BQ33UNRV')
        self.assertEqual(client.create_items.call_args.args[0][0]['abstractNote'], '')

    def test_already_filed_item_is_unchanged(self):
        client = self.client()
        client.items.return_value = [{'data': {'DOI': '10.1234/test', 'collections': ['BQ33UNRV']}}]
        sync_recommendations(client, [paper()], 'BQ33UNRV')
        client.create_items.assert_not_called()
        client.update_item.assert_not_called()

    def test_api_creation_failure_is_not_success(self):
        client = self.client()
        client.create_items.return_value = {'failed': {'0': {'code': 403}}}
        with self.assertRaises(RuntimeError):
            sync_recommendations(client, [paper()], 'BQ33UNRV')

    def test_delivered_history_survives_next_run(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'sent.json'
            self.assertEqual(load_delivered(path), set())
            mark_delivered(path, {'10.1234/old'}, [paper()])
            self.assertEqual(load_delivered(path), {'10.1234/old', '10.1234/test'})


class RuntimeTests(unittest.TestCase):
    def module(self, name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        return module

    def load(self, name):
        spec = importlib.util.spec_from_file_location('zotero_arxiv_daily.' + name, SOURCE / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_disabled_llm_needs_no_api_and_never_calls_model(self):
        openai = Mock(side_effect=AssertionError('No remote LLM may be created'))
        generated = paper()
        generated.generate_tldr = Mock(side_effect=AssertionError('No AI summary'))
        generated.generate_affiliations = Mock(side_effect=AssertionError('No AI extraction'))
        newer = paper(doi='10.1234/new', published_date='2026-10-09')
        retriever = Mock()
        retriever.retrieve_papers.return_value = [generated, newer]
        reranker = Mock()
        reranker.rerank.side_effect = lambda papers, corpus: papers
        modules = {
            'omegaconf': self.module('omegaconf', DictConfig=dict, ListConfig=list),
            'loguru': self.module('loguru', logger=Mock()),
            'pyzotero': self.module('pyzotero', zotero=Mock()),
            'openai': self.module('openai', OpenAI=openai),
            'tqdm': self.module('tqdm', tqdm=lambda values: values),
            'zotero_arxiv_daily.utils': self.module('utils', glob_match=Mock(), send_email=Mock()),
            'zotero_arxiv_daily.protocol': self.module('protocol', Paper=SimpleNamespace, CorpusPaper=SimpleNamespace),
            'zotero_arxiv_daily.retriever': self.module('retriever', get_retriever_cls=lambda name: lambda config: retriever),
            'zotero_arxiv_daily.reranker': self.module('reranker', get_reranker_cls=lambda name: lambda config: reranker),
            'zotero_arxiv_daily.construct_email': self.module('email', render_email=Mock(return_value='html')),
        }
        config = SimpleNamespace(
            llm={'enabled': False}, zotero=SimpleNamespace(include_path=None, ignore_path=None),
            executor=SimpleNamespace(source=['journals'], reranker='local', max_paper_num=10, send_empty=False))
        config.zotero.get = lambda key, default=None: default
        config.executor.get = lambda key, default=None: 'publication_date' if key == 'order_by' else default
        with patch.dict(sys.modules, modules):
            executor = self.load('executor').Executor(config)
            executor.fetch_zotero_corpus = lambda: [SimpleNamespace(abstract='cellulose', paths=[])]
            executor.run()
        self.assertEqual(modules['zotero_arxiv_daily.construct_email'].render_email.call_args.args[0],
                         [newer, generated])
        openai.assert_not_called()
        generated.generate_tldr.assert_not_called()

    def test_journal_email_has_venue_and_doi_without_fake_pdf(self):
        with patch.dict(sys.modules, {'zotero_arxiv_daily.protocol': self.module('protocol', Paper=SimpleNamespace)}):
            output = self.load('construct_email').render_email([paper(title='<b>Cellulose</b>')])
        self.assertIn('Advanced Materials | 2026-10-01 | 10.1234/test', output)
        self.assertIn('https://doi.org/10.1234/test', output)
        self.assertIn('&lt;b&gt;Cellulose&lt;/b&gt;', output)
        self.assertNotIn('>PDF</a>', output)


if __name__ == '__main__':
    unittest.main()
