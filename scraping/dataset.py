import os

import pandas as pd

import scielo
import universidades

# 1) corpus de cada fuente con lo que haya en disco (no usa la red; se puede correr mientras el scraping sigue)
universidades.consolidar()
scielo.consolidar()

# 2) un solo dataset: mismos 6 campos + 'source' (universidad o revista) + 'fuente'
uni = pd.read_json(universidades.CARPETA + '/corpus.jsonl', lines=True, dtype=False).rename(columns={'university': 'source'})
sci = pd.read_json(scielo.CARPETA + '/corpus.jsonl', lines=True, dtype=False).rename(columns={'journal': 'source'})
uni['fuente'], sci['fuente'] = 'universidad', 'scielo'
df = pd.concat([uni, sci], ignore_index=True).drop_duplicates('paper_id')
df['year'] = pd.to_numeric(df['year'], errors='coerce').astype('Int64')

# caracteres de control o separadores raros dentro del texto rompen algunos lectores de CSV: se cambian por espacio
textos = ['paper_id', 'title', 'abstract', 'authors', 'url', 'source', 'fuente']
for c in textos:
    df[c] = df[c].astype('string').str.replace(r'[\x00-\x1f\x7f-\x9f  ]+', ' ', regex=True).str.strip()

# 3) se guarda en UTF-8 simple (sin BOM) y con saltos de línea '\n'; el .gz es el mismo CSV comprimido (más fácil de subir)
os.makedirs('dataset', exist_ok=True)
df.to_csv('dataset/papers.csv', index=False, encoding='utf-8', lineterminator='\n')
df.to_csv('dataset/papers.csv.gz', index=False, encoding='utf-8', lineterminator='\n')
df.to_json('dataset/papers.jsonl', orient='records', lines=True, force_ascii=False)

# 4) comprobación: se vuelve a leer como lo haría Colab
assert pd.read_csv('dataset/papers.csv').shape == df.shape
assert pd.read_csv('dataset/papers.csv', engine='python').shape == df.shape  # el lector lento no acepta campos gigantes
assert pd.read_csv('dataset/papers.csv.gz').shape == df.shape
assert pd.read_json('dataset/papers.jsonl', lines=True).shape == df.shape
print(f"{len(df)} papers en dataset/papers.csv, papers.csv.gz y papers.jsonl")
print(df['fuente'].value_counts().to_string())
