"""Vectorización de abstracts tokenizados.

Las funciones reciben ``list[list[str]]``: una lista de documentos y, dentro
de cada documento, sus palabras ya limpias.
"""

import json
import os
import re
from pathlib import Path

import numpy as np
from gensim.models import Word2Vec
from sklearn.cluster import KMeans


def vectorize_corpora(corpus_paths, output_dir="data", *, min_count=1):
    """Vectoriza varios ``corpus.jsonl`` en una única salida global.

    Cada fila conserva su ``paper_id``. Los corpus se concatenan antes de
    entrenar Word2Vec, por lo que todos los documentos comparten el mismo
    espacio vectorial. Las filas repetidas se eliminan por ``paper_id`` o por
    abstract exactamente igual, conservando la primera aparición.
    """
    rows = []
    for corpus_path in corpus_paths:
        path = Path(corpus_path)
        if not path.is_file():
            raise FileNotFoundError(f"No existe el corpus: {path}")
        rows.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if not rows:
        raise ValueError("Los corpus no contienen documentos")

    unique_rows = {}
    abstract_ids = set()
    for row in rows:
        paper_id = row.get("paper_id")
        if not paper_id:
            raise ValueError("Cada documento debe tener paper_id")
        abstract = row.get("abstract")
        if not isinstance(abstract, str) or not abstract.strip():
            raise ValueError("Cada documento debe tener un abstract no vacío")
        abstract_id = abstract
        if paper_id in unique_rows or abstract_id in abstract_ids:
            continue
        unique_rows[paper_id] = row
        abstract_ids.add(abstract_id)
    duplicates = len(rows) - len(unique_rows)
    rows = list(unique_rows.values())
    paper_ids = list(unique_rows)
    documents = [
        re.findall(r"\w+", str(row.get("abstract") or "").lower(), flags=re.UNICODE)
        for row in rows
    ]
    output_dir = Path(output_dir)
    vocabulary, matrix = tfidf(documents, idf=True)
    save_vectorizations(
        output_dir / "vectorizations.tfidf.jsonl",
        paper_ids,
        matrix,
        "tfidf",
        vocabulary=vocabulary,
    )
    model = word2vec(documents, min_count=min_count)
    vectors = doc2vec(documents, método=1, modelo=model)
    save_vectorizations(
        output_dir / "vectorizations.doc2vec.jsonl",
        paper_ids,
        vectors,
        "doc2vec-mean",
    )
    return len(rows), duplicates


def save_vectorizations(path, paper_ids, vectors, method, *, vocabulary=None):
    """Guarda un JSONL con un vector asociado a cada ``paper_id``.

    ``vocabulary`` debe proporcionarse para TF-IDF y define el orden de sus
    columnas. Se guarda en un archivo compañero ``*.metadata.json``.
    """
    paper_ids = list(paper_ids)
    vectors = list(vectors)
    if len(paper_ids) != len(vectors):
        raise ValueError("paper_ids y vectors deben tener la misma longitud")
    if method == "tfidf" and vocabulary is None:
        raise ValueError("TF-IDF necesita vocabulary para conservar el orden de columnas")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            for paper_id, vector in zip(paper_ids, vectors):
                row = {
                    "paper_id": paper_id,
                    "method": method,
                    "vector": np.asarray(vector).tolist(),
                }
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    if vocabulary is not None:
        metadata = path.with_suffix(".metadata.json")
        metadata_temp = metadata.with_suffix(metadata.suffix + ".tmp")
        try:
            metadata_temp.write_text(
                json.dumps(
                    {"method": method, "vocabulary": list(vocabulary)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            metadata_temp.replace(metadata)
        finally:
            metadata_temp.unlink(missing_ok=True)
    return path


def tfidf(listadocs, idf=False, topN=14, modo=1):
    total = [palabra for lista in listadocs for palabra in lista]
    corpus = set(total)
    conteo = {word: total.count(word) for word in corpus}
    if modo == 1:
        corpus = sorted(conteo, key=lambda word: (-conteo[word], word))[:topN]
    elif modo == 2:
        corpus = sorted(word for word in corpus if conteo.get(word) >= topN)
    else:
        corpus = sorted(corpus)
    pertenencia = {word: 0 for word in corpus}
    matriz = [[] for doc in listadocs]
    for idxdoc in range(len(listadocs)):
        for word in corpus:
            cont = listadocs[idxdoc].count(word)
            matriz[idxdoc].append(cont)
            if cont != 0:
                pertenencia[word] = pertenencia.get(word, 0) + 1
    if idf:
        N = len(listadocs)
        idffactor = [np.log2(N / NP) for NP in pertenencia.values()]
        matriz = np.array(idffactor) * np.array(matriz)

    return corpus, matriz


def word2vec(listadocs, *, min_count=5):
    modelo = Word2Vec(
        sentences=listadocs,
        vector_size=50,
        window=2,
        min_count=min_count,
        epochs=5,
        workers=max(1, (os.cpu_count() or 1) - 1),
        seed=2026,
        sg=0,
    )
    return modelo


def doc2vec(datos, método, modelo, kmeans=None):
    FV_Documentos = []
    if método == 1:
        for documento in datos:
            palabras = [palabra for palabra in documento if palabra in modelo.wv.key_to_index]
            if palabras:
                vector = modelo.wv.get_mean_vector(palabras)
            else:
                vector = np.zeros(modelo.vector_size)
            FV_Documentos.append(vector)
    elif método == 2:
        if kmeans is None:
            kmeans = KMeans(n_clusters=10, random_state=2026, n_init=10).fit(modelo.wv.vectors)
        for documento in datos:
            vector2 = np.zeros(kmeans.n_clusters)
            palabras = [palabra for palabra in documento if palabra in modelo.wv.key_to_index]
            if palabras:
                labels = kmeans.predict(modelo.wv[palabras])
                for cluster in range(kmeans.n_clusters):
                    vector2[cluster] = np.sum(labels == cluster)
                norma = np.linalg.norm(vector2)
                if norma!=0: vector2 = vector2 / norma
            FV_Documentos.append(vector2)

    return FV_Documentos
