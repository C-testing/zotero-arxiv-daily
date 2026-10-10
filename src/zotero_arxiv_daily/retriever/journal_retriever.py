from .base import BaseRetriever, register_retriever
from ..journal_feed import retrieve_records
from ..protocol import Paper


@register_retriever("journals")
class JournalRetriever(BaseRetriever):
    def _retrieve_raw_papers(self):
        return retrieve_records(self.retriever_config)

    def convert_to_paper(self, record):
        return Paper(
            source=self.name, title=record["title"], authors=record["authors"],
            abstract=record["abstract"] or record["title"], url=record["url"],
            affiliations=record["affiliations"], doi=record["doi"],
            journal=record["journal"], published_date=record["published_date"],
            creators=record["creators"],
            abstract_available=bool(record["abstract"]),
            tldr=record["abstract"] or "No publisher abstract available; ranked using the title only.",
        )
