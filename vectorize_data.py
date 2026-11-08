"""Genera una única vectorización para todos los corpus disponibles."""

from vectorizers import vectorize_corpora


if __name__ == "__main__":
    count, duplicates = vectorize_corpora(
        [
            "data/test-1000/peru-catalog/corpus.jsonl",
            "data/universidades/corpus.jsonl",
        ]
    )
    print(
        f"Vectorizados {count} papers en data/; "
        f"duplicados por paper_id o abstract exacto descartados: {duplicates}"
    )
