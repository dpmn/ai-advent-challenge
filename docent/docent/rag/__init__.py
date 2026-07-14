"""Лёгкий RAG для docent: chunking, brute-force косинус, индекс по документации.

Без FAISS/reranker/Ollama — для документации одного репозитория достаточно
numpy-косинуса по эмбеддингам Cloud.ru.
"""
