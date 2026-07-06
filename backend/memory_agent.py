import os
import re
import math
from pathlib import Path

class MemoryAgent:
    def __init__(self, global_mem_dir: Path, agent_mem_dir: Path = None, team_mem_dir: Path = None, cwd_slug: str = ""):
        self.global_mem_dir = global_mem_dir
        self.agent_mem_dir = agent_mem_dir
        self.team_mem_dir = team_mem_dir
        self.cwd_slug = cwd_slug

    def _read_md(self, path: Path) -> str:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return ""

    def _chunk_text(self, text: str, max_chunk_len: int = 500) -> list[str]:
        """Split text into smaller chunks for semantic retrieval, e.g., by headers or lines."""
        paragraphs = re.split(r'\n(?:###? |##? |# )', text)
        chunks = []
        current_chunk = []
        current_len = 0
        
        for p in paragraphs:
            p_clean = p.strip()
            if not p_clean:
                continue
            if len(p_clean) + current_len > max_chunk_len and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [p_clean]
                current_len = len(p_clean)
            else:
                current_chunk.append(p_clean)
                current_len += len(p_clean)
                
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))
        return chunks

    def _term_frequencies(self, text: str) -> dict[str, int]:
        """Simple tokenizer and term frequency calculator with stop words filtering."""
        stop_words = {
            "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", 
            "arent", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", 
            "but", "by", "cant", "cannot", "could", "did", "do", "does", "doing", "dont", "down", "during", 
            "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", 
            "hers", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", 
            "more", "most", "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", 
            "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "should", "so", 
            "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then", 
            "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", 
            "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", 
            "why", "with", "would", "you", "your", "yours", "yourself", "yourselves",
            "的", "了", "在", "是", "我", "你", "他", "她", "它", "們", "這", "那", "有", "無", "與", 
            "和", "或", "個", "隻", "都", "就", "也", "去", "來", "為", "所", "以"
        }
        words = re.findall(r'\w+', text.lower())
        tf = {}
        for w in words:
            if w not in stop_words and len(w) > 1:
                tf[w] = tf.get(w, 0) + 1
        return tf

    def _cosine_similarity(self, tf1: dict[str, int], tf2: dict[str, int]) -> float:
        """Calculate cosine similarity between two term frequency mappings."""
        dot_product = sum(tf1[w] * tf2[w] for w in tf1 if w in tf2)
        mag1 = math.sqrt(sum(v*v for v in tf1.values()))
        mag2 = math.sqrt(sum(v*v for v in tf2.values()))
        if not mag1 or not mag2:
            return 0.0
        return dot_product / (mag1 * mag2)

    def get_core_memory(self, agent_id: str = "") -> dict:
        """Core working memory (User + System + Current Agent Identity)"""
        core = {}
        user = self._read_md(self.global_mem_dir / "user" / "profile.md")
        if user:
            core["user"] = user
        system = self._read_md(self.global_mem_dir / "system" / "state.md")
        if system:
            core["system"] = system
        if self.agent_mem_dir:
            identity = self._read_md(self.agent_mem_dir / "identity.md")
            if identity:
                core["identity"] = identity
        return core

    def get_archival_memory(self, agent_id: str = "") -> list[dict]:
        """Archival larger storage (Experience Projects + Project Internal logs)"""
        archival = []
        if self.agent_mem_dir and self.cwd_slug:
            f = self.agent_mem_dir / "projects" / f"{self.cwd_slug}.md"
            proj = self._read_md(f)
            if proj:
                archival.append({
                    "title": f"Agent Experience ({agent_id} / {self.cwd_slug})",
                    "content": proj,
                    "mtime": f.stat().st_mtime
                })
        
        if self.cwd_slug:
            claude_home = self.global_mem_dir.parent
            proj_mem_dir = claude_home / "projects" / self.cwd_slug / "memory"
            if proj_mem_dir.exists():
                for f in sorted(proj_mem_dir.glob("*.md")):
                    content = self._read_md(f)
                    if content:
                        archival.append({
                            "title": f"Project Internal ({self.cwd_slug} / {f.stem})",
                            "content": content,
                            "mtime": f.stat().st_mtime
                        })
        return archival

    def build_smart_context(self, agent_id: str = "", max_chars: int = 16000, query: str = "") -> str:
        """
        Build dynamic context with intelligent paging and RAG semantic similarity recall.
        Core memory has absolute priority. Archival chunks are dynamically ranked and selected based on query similarity.
        """
        core = self.get_core_memory(agent_id)
        sections = []
        
        if "user" in core:
            sections.append(f"[User Memory]\n{core['user']}")
        if "system" in core:
            sections.append(f"[System Memory]\n{core['system']}")
        if "identity" in core:
            sections.append(f"[Agent Identity — {agent_id}]\n{core['identity']}")
            
        current_len = sum(len(s) for s in sections)
        
        archival = self.get_archival_memory(agent_id)
        
        all_chunks = []
        for item in archival:
            chunks = self._chunk_text(item["content"])
            for idx, c in enumerate(chunks):
                all_chunks.append({
                    "title": f"{item['title']} (Part {idx + 1})",
                    "content": c,
                    "mtime": item["mtime"]
                })

        if query and all_chunks:
            q_tf = self._term_frequencies(query)
            for c in all_chunks:
                c_tf = self._term_frequencies(c["content"])
                c["score"] = self._cosine_similarity(q_tf, c_tf)
            all_chunks.sort(key=lambda x: (x.get("score", 0.0), x["mtime"]), reverse=True)
            all_chunks = [c for c in all_chunks if c.get("score", 0.0) >= 0.1][:5]
        else:
            all_chunks.sort(key=lambda x: x["mtime"], reverse=True)
        
        for item in all_chunks:
            remaining = max_chars - current_len
            if remaining <= 100:
                break
                
            title = item["title"]
            content = item["content"]
            score_info = f" (Relevance: {item['score']:.2f})" if "score" in item else ""
            
            if len(content) > remaining:
                truncated_content = content[:remaining] + "\n\n... [truncated to fit memory context]"
                sections.append(f"[{title}{score_info}]\n{truncated_content}")
                break
            else:
                sections.append(f"[{title}{score_info}]\n{content}")
                current_len += len(sections[-1])
                
        return "\n\n---\n\n".join(sections) if sections else ""
