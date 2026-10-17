from ii_graph import CodebaseKnowledgeGraph
kg = CodebaseKnowledgeGraph(".")
n = kg.build_graph()
print(f"Graph built: {n} files parsed")
idx = kg.get_holographic_index()
print(f"Index length: {len(idx)} characters")
print("First 300 chars:")
print(idx[:300])
