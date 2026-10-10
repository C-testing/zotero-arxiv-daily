from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig
from .utils import glob_match
from .retriever import get_retriever_cls
from .protocol import CorpusPaper
from datetime import datetime
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from openai import OpenAI
from tqdm import tqdm
from .journal_feed import normalize_doi
from .recommendation_delivery import validate_destination, sync_recommendations, load_delivered, mark_delivered


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.llm_enabled = bool(config.llm.get("enabled", True))
        self.openai_client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url) if self.llm_enabled else None
    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        self.zotero_client = zot
        collections = zot.everything(zot.collections())
        collections = {c['key']:c for c in collections}
        corpus = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        corpus = [c for c in corpus if c['data']['abstractNote'] != '']
        def get_collection_path(col_key:str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            else:
                return collections[col_key]['data']['name']
        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'],
            added_date=datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths']
        ) for c in corpus]
    
    def filter_corpus(self, corpus:list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            logger.info("Selecting Zotero papers from the configured interest collections")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info("Excluding Zotero papers from the configured output collections")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            logger.info(f"Selected {len(corpus)} zotero papers")
        return corpus

    
    def run(self):
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        profile = self.config.zotero.get("research_profile")
        if profile:
            corpus.append(CorpusPaper(title="Research interests", abstract=profile,
                                     added_date=datetime.now(), paths=[]))
        if len(corpus) == 0:
            logger.error("No Zotero papers with abstracts found in the selected collections.")
            return
        destination = self.config.zotero.get("recommendation_collection")
        if destination:
            validate_destination(self.zotero_client, destination,
                                 self.config.zotero.recommendation_collection_name,
                                 self.config.zotero.user_id)
        state_path = self.config.executor.get("delivery_state")
        delivered = load_delivered(state_path) if state_path else set()
        all_papers = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            papers = retriever.retrieve_papers()
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)
        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        if state_path:
            all_papers = [paper for paper in all_papers if not paper.doi or normalize_doi(paper.doi) not in delivered]
        reranked_papers = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            reranked_papers = self.reranker.rerank(all_papers, corpus)
            if self.config.executor.get("order_by") == "publication_date":
                reranked_papers.sort(key=lambda paper: (paper.published_date or "", paper.score or 0), reverse=True)
            reranked_papers = reranked_papers[:self.config.executor.max_paper_num]
            if self.llm_enabled:
                logger.info("Generating TLDR and affiliations...")
                for p in tqdm(reranked_papers):
                    p.generate_tldr(self.openai_client, self.config.llm)
                    p.generate_affiliations(self.openai_client, self.config.llm)
            else:
                for p in reranked_papers:
                    p.tldr = p.tldr or p.abstract
        elif not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            return
        if destination:
            sync_recommendations(self.zotero_client, reranked_papers, destination)
            logger.info(f"Filed {len(reranked_papers)} recommendations in the configured Zotero collection.")
        logger.info("Sending email...")
        email_content = render_email(reranked_papers)
        send_email(self.config, email_content)
        logger.info("Email sent successfully")
        if state_path:
            mark_delivered(state_path, delivered, reranked_papers)
