"""Safe Agent/Skill deployment from Agent Desktop's registry to native engine homes.

Agent Desktop treats a single ``claude_home`` (a.k.a. the registry — historically
this was always ``~/.claude`` because that was also Claude Code's native home,
but it can now be configured independently via ``registryHome``) as the
canonical source of Agent/Skill truth, regardless of which CLI engines are
actually installed. Each engine gets its own generated, engine-native copy:

- Codex: Markdown -> TOML (different format entirely) at ``~/.codex/agents``
  and a plain directory copy at the configured Codex skills root.
- Claude Code: when the registry is the *same* directory as Claude Code's own
  ``~/.claude`` (the default, back-compatible case), Claude Code already reads
  the registry directly — no copy needed. When the registry has been pointed
  elsewhere (e.g. a Codex-only user who doesn't want their data nested inside
  a Claude-branded folder), pass ``claude_native_home`` so a Markdown mirror is
  also materialised at Claude Code's real native location.

Every generated copy carries a managed marker; anything already present at a
target that lacks the marker is treated as user-owned and is never overwritten
(surfaced as a ``conflict`` instead).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from pathlib import Path

import yaml


MANAGED_MARKER = "# Managed by Agent Desktop resource sync."
SKILL_MARKER = ".agent-desktop-sync.json"
CLAUDE_MIRROR_MARKER = "<!-- Managed by Agent Desktop resource sync."


def _frontmatter_and_body(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text.strip()
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, "\n".join(lines[end + 1 :]).strip()


def _agent_toml(source: Path) -> str:
    metadata, body = _frontmatter_and_body(source)
    name = str(metadata.get("name") or source.stem)
    description = str(metadata.get("description") or "")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    # JSON strings are valid TOML basic strings and safely handle arbitrary
    # Markdown backslashes/quotes without hand-written TOML escaping.
    return (
        f"{MANAGED_MARKER}\n"
        f"# source-sha256: {digest}\n"
        f"name = {json.dumps(name, ensure_ascii=False)}\n"
        f"description = {json.dumps(description, ensure_ascii=False)}\n"
        f"developer_instructions = {json.dumps(body, ensure_ascii=False)}\n"
    )


def _toml_agent_to_markdown(source: Path) -> str:
    """Reverse of ``_agent_toml``, used when importing a Codex-native agent
    into the registry. Codex TOML only carries name/description/instructions,
    so the imported Markdown necessarily starts out with a smaller frontmatter
    than a hand-authored registry agent (no tools/skills/mcp/etc.) — that's an
    inherent format gap, not a bug in the conversion."""
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        data = {}
    name = str(data.get("name") or source.stem)
    description = str(data.get("description") or "")
    body = str(data.get("developer_instructions") or "").strip()
    return (
        "---\n"
        f"name: {json.dumps(name, ensure_ascii=False)}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "---\n\n"
        f"{body}\n"
    )


def _agent_equivalent(source: Path, target: Path) -> bool:
    metadata, body = _frontmatter_and_body(source)
    expected = {
        "name": str(metadata.get("name") or source.stem),
        "description": str(metadata.get("description") or ""),
        "developer_instructions": body,
    }
    try:
        actual = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    return all(actual.get(key) == value for key, value in expected.items())


def _claude_mirror_copy(source: Path) -> str:
    """Registry -> Claude-native Markdown mirror: verbatim body, no format
    conversion needed (both sides already speak Markdown+frontmatter) — just
    a marker. Unlike the Codex marker (a whole separate TOML file, free to
    start with a comment line), this marker must be inserted *inside* the
    frontmatter block rather than before it: Claude Code's own parser (and
    this app's) requires the file to start with ``---``, so a leading
    comment line would silently break the copy for real use, not just for
    our own sync bookkeeping."""
    text = source.read_text(encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    marker = f"{MANAGED_MARKER}\n# source-sha256: {digest}\n"
    if text.startswith("---"):
        head, _, rest = text.partition("\n")
        return f"{head}\n{marker}{rest}"
    # No frontmatter to anchor to — safe to prepend as a leading comment.
    return f"{CLAUDE_MIRROR_MARKER} source-sha256: {digest} -->\n{text}"


def _is_managed_claude_mirror(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0] == "---" and lines[1] == MANAGED_MARKER:
        return True
    return text.startswith(CLAUDE_MIRROR_MARKER)


def _claude_mirror_equivalent(source: Path, target: Path) -> bool:
    try:
        target_text = target.read_text(encoding="utf-8")
    except OSError:
        return False
    return target_text == _claude_mirror_copy(source)


def _skill_payload(source: Path) -> dict[str, bytes]:
    if source.is_file():
        return {"SKILL.md": source.read_bytes()}

    payload: dict[str, bytes] = {}
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name != SKILL_MARKER:
            payload[path.relative_to(source).as_posix()] = path.read_bytes()
    if "SKILL.md" not in payload and "README.md" in payload:
        payload["SKILL.md"] = payload.pop("README.md")
    return payload


def _payload_hash(payload: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(payload.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _skill_entry_hash(source: Path) -> str:
    """Fast identity used by status/dry-run; assets are read only when syncing."""
    if source.is_file():
        content = source.read_bytes()
    else:
        entry = source / "SKILL.md"
        if not entry.is_file():
            entry = source / "README.md"
        content = entry.read_bytes()
    return hashlib.sha256(content).hexdigest()


def _write_skill_payload(target: Path, payload: dict[str, bytes]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for relative, content in payload.items():
        path = target / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    marker = {
        "managed_by": "agent-desktop",
        "entry_sha256": hashlib.sha256(payload.get("SKILL.md", b"")).hexdigest(),
    }
    (target / SKILL_MARKER).write_text(
        json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_plain_skill_payload(target: Path, payload: dict[str, bytes]) -> None:
    """Like ``_write_skill_payload`` but without the managed marker — used by
    ``import_native()`` where the result becomes real, user-owned registry
    content rather than a generated copy."""
    target.mkdir(parents=True, exist_ok=True)
    for relative, content in payload.items():
        path = target / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class ResourceSyncService:
    """Inspect and deploy registry Agents/Skills to native engine paths.

    ``claude_home`` is the registry (single source of truth). ``claude_native_home``
    is optional and only meaningful when it points somewhere *other than*
    ``claude_home`` — that's the "registry has been decoupled from Claude
    Code's real home" case, where Claude also needs a generated mirror just
    like Codex does. Pass ``None`` (the default) when they're the same
    directory; Claude Code already reads the registry directly and no mirror
    step is needed.
    """

    def __init__(
        self,
        claude_home: Path,
        codex_home: Path,
        codex_skills: Path,
        claude_native_home: Path | None = None,
    ):
        self.claude_home = Path(claude_home)
        self.codex_home = Path(codex_home)
        self.codex_skills = Path(codex_skills)
        native = Path(claude_native_home) if claude_native_home is not None else None
        self.claude_native_home = (
            native if native is not None and native.resolve() != self.claude_home.resolve() else None
        )

    @property
    def claude_agents(self) -> Path:
        return self.claude_home / "agents"

    @property
    def claude_skills(self) -> Path:
        return self.claude_home / "skills"

    def _agent_sources(self) -> dict[str, Path]:
        if not self.claude_agents.exists():
            return {}
        return {p.stem: p for p in self.claude_agents.glob("*.md") if p.is_file()}

    def _agent_targets(self) -> dict[str, Path]:
        root = self.codex_home / "agents"
        if not root.exists():
            return {}
        return {p.stem: p for p in root.glob("*.toml") if p.is_file()}

    def _skill_sources(self) -> dict[str, Path]:
        if not self.claude_skills.exists():
            return {}
        result: dict[str, Path] = {}
        for entry in self.claude_skills.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".md":
                result[entry.stem] = entry
            elif entry.is_dir() and (
                (entry / "SKILL.md").is_file() or (entry / "README.md").is_file()
            ):
                result[entry.name] = entry
        return result

    def _skill_targets(self) -> dict[str, Path]:
        if not self.codex_skills.exists():
            return {}
        return {p.name: p for p in self.codex_skills.iterdir()}

    def _claude_mirror_agent_targets(self) -> dict[str, Path]:
        if self.claude_native_home is None:
            return {}
        root = self.claude_native_home / "agents"
        if not root.exists():
            return {}
        return {p.stem: p for p in root.glob("*.md") if p.is_file()}

    def _claude_mirror_skill_targets(self) -> dict[str, Path]:
        if self.claude_native_home is None:
            return {}
        root = self.claude_native_home / "skills"
        if not root.exists():
            return {}
        result: dict[str, Path] = {}
        for entry in root.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".md":
                result[entry.stem] = entry
            elif entry.is_dir() and (entry / "SKILL.md").is_file():
                result[entry.name] = entry
        return result

    @staticmethod
    def _is_managed_agent(path: Path) -> bool:
        try:
            return path.read_text(encoding="utf-8").startswith(MANAGED_MARKER)
        except OSError:
            return False

    @staticmethod
    def _is_managed_skill(path: Path) -> bool:
        return (path / SKILL_MARKER).is_file()

    def status(self) -> dict:
        agent_sources = self._agent_sources()
        agent_targets = self._agent_targets()
        skill_sources = self._skill_sources()
        skill_targets = self._skill_targets()

        agents = {
            "synced": [], "missing_in_codex": [], "outdated": [],
            "conflicts": [], "codex_only": sorted(set(agent_targets) - set(agent_sources)),
        }
        for name, source in sorted(agent_sources.items()):
            target = agent_targets.get(name)
            if target is None:
                agents["missing_in_codex"].append(name)
            elif target.is_symlink():
                agents["conflicts"].append(name)
            elif target.read_text(encoding="utf-8") == _agent_toml(source) or _agent_equivalent(source, target):
                agents["synced"].append(name)
            elif self._is_managed_agent(target):
                agents["outdated"].append(name)
            else:
                agents["conflicts"].append(name)

        skills = {
            "synced": [], "missing_in_codex": [], "outdated": [],
            "conflicts": [], "codex_only": sorted(set(skill_targets) - set(skill_sources)),
        }
        for name, source in sorted(skill_sources.items()):
            target = skill_targets.get(name)
            source_hash = _skill_entry_hash(source)
            if target is None:
                skills["missing_in_codex"].append(name)
                continue
            if target.is_symlink() or not target.is_dir() or not (target / "SKILL.md").is_file():
                bucket = "outdated" if self._is_managed_skill(target) else "conflicts"
                skills[bucket].append(name)
                continue
            target_hash = _skill_entry_hash(target)
            if source_hash == target_hash:
                skills["synced"].append(name)
            elif self._is_managed_skill(target):
                skills["outdated"].append(name)
            else:
                skills["conflicts"].append(name)

        result = {"agents": agents, "skills": skills}
        if self.claude_native_home is not None:
            result["claude_mirror"] = self._claude_mirror_status()
        return result

    def _claude_mirror_status(self) -> dict:
        agent_sources = self._agent_sources()
        agent_targets = self._claude_mirror_agent_targets()
        skill_sources = self._skill_sources()
        skill_targets = self._claude_mirror_skill_targets()

        agents = {
            "synced": [], "missing_in_claude": [], "outdated": [],
            "conflicts": [], "claude_only": sorted(set(agent_targets) - set(agent_sources)),
        }
        for name, source in sorted(agent_sources.items()):
            target = agent_targets.get(name)
            if target is None:
                agents["missing_in_claude"].append(name)
            elif target.is_symlink():
                agents["conflicts"].append(name)
            elif _claude_mirror_equivalent(source, target):
                agents["synced"].append(name)
            elif _is_managed_claude_mirror(target):
                agents["outdated"].append(name)
            else:
                agents["conflicts"].append(name)

        skills = {
            "synced": [], "missing_in_claude": [], "outdated": [],
            "conflicts": [], "claude_only": sorted(set(skill_targets) - set(skill_sources)),
        }
        for name, source in sorted(skill_sources.items()):
            target = skill_targets.get(name)
            source_hash = _skill_entry_hash(source)
            if target is None:
                skills["missing_in_claude"].append(name)
                continue
            if target.is_symlink() or not target.is_dir() or not (target / "SKILL.md").is_file():
                bucket = "outdated" if self._is_managed_skill(target) else "conflicts"
                skills[bucket].append(name)
                continue
            target_hash = _skill_entry_hash(target)
            if source_hash == target_hash:
                skills["synced"].append(name)
            elif self._is_managed_skill(target):
                skills["outdated"].append(name)
            else:
                skills["conflicts"].append(name)

        return {"agents": agents, "skills": skills}

    def sync(self, dry_run: bool = False) -> dict:
        result = {
            "agents": {"created": [], "updated": [], "conflicts": []},
            "skills": {"created": [], "updated": [], "conflicts": []},
        }
        agent_targets = self._agent_targets()
        for name, source in sorted(self._agent_sources().items()):
            target = agent_targets.get(name) or (self.codex_home / "agents" / f"{name}.toml")
            expected = _agent_toml(source)
            if target.is_symlink():
                result["agents"]["conflicts"].append(name)
                continue
            if target.exists() and (
                target.read_text(encoding="utf-8") == expected or _agent_equivalent(source, target)
            ):
                continue
            if target.exists() and not self._is_managed_agent(target):
                result["agents"]["conflicts"].append(name)
                continue
            action = "updated" if target.exists() else "created"
            result["agents"][action].append(name)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(expected, encoding="utf-8")

        skill_targets = self._skill_targets()
        for name, source in sorted(self._skill_sources().items()):
            target = skill_targets.get(name) or (self.codex_skills / name)
            source_hash = _skill_entry_hash(source)
            target_valid = not target.is_symlink() and target.is_dir() and (target / "SKILL.md").is_file()
            if target.exists() and target_valid and _skill_entry_hash(target) == source_hash:
                continue
            if target.exists() and not self._is_managed_skill(target):
                result["skills"]["conflicts"].append(name)
                continue
            action = "updated" if target.exists() else "created"
            result["skills"][action].append(name)
            if not dry_run:
                payload = _skill_payload(source)
                if target.exists():
                    shutil.rmtree(target)
                _write_skill_payload(target, payload)

        if self.claude_native_home is not None:
            result["claude_mirror"] = self._sync_claude_mirror(dry_run)
        return result

    def _sync_claude_mirror(self, dry_run: bool) -> dict:
        result = {
            "agents": {"created": [], "updated": [], "conflicts": []},
            "skills": {"created": [], "updated": [], "conflicts": []},
        }
        agent_targets = self._claude_mirror_agent_targets()
        for name, source in sorted(self._agent_sources().items()):
            target = agent_targets.get(name) or (self.claude_native_home / "agents" / f"{name}.md")
            if target.is_symlink():
                result["agents"]["conflicts"].append(name)
                continue
            if target.exists() and _claude_mirror_equivalent(source, target):
                continue
            if target.exists() and not _is_managed_claude_mirror(target):
                result["agents"]["conflicts"].append(name)
                continue
            action = "updated" if target.exists() else "created"
            result["agents"][action].append(name)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(_claude_mirror_copy(source), encoding="utf-8")

        skill_targets = self._claude_mirror_skill_targets()
        for name, source in sorted(self._skill_sources().items()):
            target = skill_targets.get(name) or (self.claude_native_home / "skills" / name)
            source_hash = _skill_entry_hash(source)
            target_valid = not target.is_symlink() and target.is_dir() and (target / "SKILL.md").is_file()
            if target.exists() and target_valid and _skill_entry_hash(target) == source_hash:
                continue
            if target.exists() and not self._is_managed_skill(target):
                result["skills"]["conflicts"].append(name)
                continue
            action = "updated" if target.exists() else "created"
            result["skills"][action].append(name)
            if not dry_run:
                payload = _skill_payload(source)
                if target.exists():
                    shutil.rmtree(target)
                _write_skill_payload(target, payload)
        return result

    def import_native(self, dry_run: bool = False) -> dict:
        """Adopt engine-native Agents/Skills that have no counterpart in the
        registry yet (``codex_only`` / ``claude_only`` in status()) into the
        registry, so a Codex-only or existing user's hand-made resources stop
        being permanent second-class citizens. Anything carrying our own
        managed marker is skipped — that's an orphaned copy we generated
        ourselves, not independent user intent worth resurrecting."""
        result = {
            "agents": {"imported": [], "skipped": []},
            "skills": {"imported": [], "skipped": []},
        }
        agent_names = set(self._agent_sources())
        skill_names = set(self._skill_sources())

        for name, path in sorted(self._agent_targets().items()):
            if name in agent_names or self._is_managed_agent(path):
                continue
            self._import_agent(name, _toml_agent_to_markdown(path), result, dry_run)
            agent_names.add(name)

        for name, path in sorted(self._claude_mirror_agent_targets().items()):
            if name in agent_names or _is_managed_claude_mirror(path):
                continue
            self._import_agent(name, path.read_text(encoding="utf-8"), result, dry_run)
            agent_names.add(name)

        for name, path in sorted(self._skill_targets().items()):
            if name in skill_names or self._is_managed_skill(path):
                continue
            self._import_skill(name, path, result, dry_run)
            skill_names.add(name)

        for name, path in sorted(self._claude_mirror_skill_targets().items()):
            if name in skill_names or self._is_managed_skill(path):
                continue
            self._import_skill(name, path, result, dry_run)
            skill_names.add(name)

        return result

    def _import_agent(self, name: str, markdown: str, result: dict, dry_run: bool) -> None:
        dest = self.claude_agents / f"{name}.md"
        if dest.exists():
            result["agents"]["skipped"].append(name)
            return
        result["agents"]["imported"].append(name)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(markdown, encoding="utf-8")

    def _import_skill(self, name: str, source: Path, result: dict, dry_run: bool) -> None:
        dest = self.claude_skills / name
        if dest.exists():
            result["skills"]["skipped"].append(name)
            return
        result["skills"]["imported"].append(name)
        if not dry_run:
            _write_plain_skill_payload(dest, _skill_payload(source))
