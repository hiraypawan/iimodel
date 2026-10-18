"""Shallow GitHub ingestion, conservative license screening, and graph caching.

Repositories are parsed as data: this module never imports or executes cloned
source. Only HTTPS GitHub owner/repository URLs are accepted. Graph caches use
JSON rather than pickle so untrusted cache files cannot execute code.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import networkx as nx

from graph.topology import GraphTopologyAnalyzer
from ii_graph import CodebaseKnowledgeGraph
from ingestion.license_checker import check_license

GRAPH_CACHE_NAME = ".graph_cache"


class RepoIngestor:
    def __init__(
        self,
        cache_dir: str | Path = ".cache/repos",
        *,
        max_source_files: int = 10_000,
        clone_timeout: int = 300,
    ):
        if max_source_files < 1 or clone_timeout < 1:
            raise ValueError("Resource limits must be positive.")
        self.cache_dir = Path(cache_dir).resolve()
        self.max_source_files = max_source_files
        self.clone_timeout = clone_timeout

    @staticmethod
    def parse_github_url(github_url: str) -> tuple[str, str, str]:
        """Validate a public HTTPS GitHub URL and return owner, repo, canonical URL."""
        parsed = urlparse(github_url.strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"github.com", "www.github.com"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Only public https://github.com/OWNER/REPO URLs are accepted.")
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) != 2:
            raise ValueError("GitHub URL must identify exactly one owner and repository.")
        owner, repo = parts
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not owner or not repo or any(char in owner + repo for char in "\\\x00"):
            raise ValueError("Invalid GitHub owner/repository path.")
        canonical = f"https://github.com/{owner}/{repo}.git"
        return owner, repo, canonical

    @staticmethod
    def _count_source_files(root: Path) -> int:
        count = 0
        ignored = {".git", "node_modules", "venv", ".venv", "dist", "build"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in ignored]
            count += sum(
                name.lower().endswith((".py", ".js", ".jsx", ".ts", ".tsx"))
                for name in filenames
            )
        return count

    @staticmethod
    def save_graph_cache(
        path: str | Path,
        graph: nx.DiGraph,
        *,
        source_url: str,
        license_type: str,
    ) -> Path:
        """Serialize graph metadata without Python object deserialization."""
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "source_url": source_url,
            "license_type": license_type,
            "nodes": [
                {"id": str(node), "attributes": dict(attributes)}
                for node, attributes in graph.nodes(data=True)
            ],
            "edges": [
                {"source": str(source), "target": str(target), "attributes": dict(attributes)}
                for source, target, attributes in graph.edges(data=True)
            ],
        }
        output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return output

    @staticmethod
    def load_graph_cache(path: str | Path) -> tuple[nx.DiGraph, dict[str, Any]]:
        """Load a versioned JSON graph cache; malformed entries are rejected."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("Unsupported graph-cache format.")
        nodes, edges = payload.get("nodes"), payload.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise ValueError("Graph cache must contain nodes and edges arrays.")
        graph = nx.DiGraph()
        for item in nodes:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("attributes"), dict):
                raise ValueError("Invalid graph-cache node record.")
            graph.add_node(item["id"], **item["attributes"])
        for item in edges:
            if not isinstance(item, dict) or not isinstance(item.get("source"), str) or not isinstance(item.get("target"), str) or not isinstance(item.get("attributes"), dict):
                raise ValueError("Invalid graph-cache edge record.")
            if item["source"] not in graph or item["target"] not in graph:
                raise ValueError("Graph-cache edge references a missing node.")
            graph.add_edge(item["source"], item["target"], **item["attributes"])
        metadata = {
            "source_url": payload.get("source_url", ""),
            "license_type": payload.get("license_type", ""),
        }
        return graph, metadata

    def ingest(self, github_url: str) -> str:
        """Clone, license-check, parse, and cache a supported GitHub repository."""
        owner, repo, canonical_url = self.parse_github_url(github_url)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = self.cache_dir / f"{owner}--{repo}"
        cache_path = repo_dir / GRAPH_CACHE_NAME
        if cache_path.is_file():
            graph, metadata = self.load_graph_cache(cache_path)
            summary = GraphTopologyAnalyzer(graph).generate_architecture_map()
            return (
                f"Loaded cached {metadata['license_type']} graph for {owner}/{repo}.\n"
                f"{summary}"
            )

        with tempfile.TemporaryDirectory(prefix="ii-ingest-", dir=self.cache_dir) as temp_name:
            temp_root = Path(temp_name)
            clone_path = temp_root / "repo"
            command = [
                "git", "clone", "--depth", "1", "--no-tags", "--",
                canonical_url, str(clone_path),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.clone_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Repository clone exceeded {self.clone_timeout} seconds.") from exc
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "git clone failed")[-2000:]
                raise RuntimeError(detail) from exc

            allowed, license_type, reason = check_license(clone_path)
            if not allowed:
                raise ValueError(f"Repository rejected ({license_type}): {reason}")
            file_count = self._count_source_files(clone_path)
            if file_count > self.max_source_files:
                raise ValueError(
                    f"Repository has {file_count} supported files; limit is {self.max_source_files}."
                )

            indexer = CodebaseKnowledgeGraph(str(clone_path))
            indexer.build_graph()
            self.save_graph_cache(
                clone_path / GRAPH_CACHE_NAME,
                indexer.graph,
                source_url=canonical_url,
                license_type=license_type,
            )
            summary = GraphTopologyAnalyzer(indexer.graph).generate_architecture_map()

            if repo_dir.exists():
                raise FileExistsError(
                    f"Cache directory exists without a valid graph cache: {repo_dir}"
                )
            os.replace(clone_path, repo_dir)

        return f"Ingested {owner}/{repo} ({license_type}; {file_count} supported files).\n{summary}"

    def query_graph(self, repo_name: str, target: str | None = None) -> str:
        """Query a cached graph by ``owner/repo`` or ``owner--repo`` name."""
        if "://" in repo_name:
            owner, repo, _ = self.parse_github_url(repo_name)
            cache_name = f"{owner}--{repo}"
        elif "/" in repo_name:
            parts = repo_name.strip("/").split("/")
            if len(parts) != 2:
                raise ValueError("repo_name must be owner/repo.")
            cache_name = "--".join(parts)
        else:
            cache_name = repo_name
        if cache_name in {"", ".", ".."} or "/" in cache_name or "\\" in cache_name:
            raise ValueError("Invalid cached repository name.")
        cache_path = self.cache_dir / cache_name / GRAPH_CACHE_NAME
        if not cache_path.is_file():
            raise FileNotFoundError(f"No graph cache found for {repo_name!r}.")
        graph, metadata = self.load_graph_cache(cache_path)
        analyzer = GraphTopologyAnalyzer(graph)
        if target:
            try:
                symbols = analyzer.rank_critical_symbols(target)
            except ValueError as exc:
                return str(exc)
            return json.dumps(
                {
                    "repo": repo_name,
                    "source_url": metadata["source_url"],
                    "target": target,
                    "ranked_symbols": symbols,
                    "cycles": analyzer.find_cycles(),
                },
                indent=2,
            )
        return analyzer.generate_architecture_map()
