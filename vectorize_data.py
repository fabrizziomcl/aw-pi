"""Genera una única vectorización para todos los corpus disponibles."""

from vectorizers import vectorize_corpora


if __name__ == "__main__":
    count, duplicates = vectorize_corpora(
        [
            "data/scielo_simple/corpus.jsonl",
            "data/universidades_simple/corpus.jsonl",
        ]
    )
    print(
        f"Vectorizados {count} papers en data/; "
        f"duplicados por paper_id o abstract exacto descartados: {duplicates}"
    )
