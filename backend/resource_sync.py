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
(surfaced as a ``conflict`` instead). When a registry source is deleted, its
managed copies are pruned (see ``sync()``/``sync_agent()``/``sync_skill()``);
unmanaged content at the same name is left alone and reported as ``*_only``
(possibly importable via ``import_native()``) rather than removed.
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


def _skill_entry_file(directory: Path) -> Path | None:
    """The single file used for identity/preview of a skill directory."""
    if not directory.is_dir():
        return None
    for candidate in ("SKILL.md", "README.md"):
        entry = directory / candidate
        if entry.is_file():
            return entry
    return None


def _read_text_or_none(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


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

    def _agent_source(self, name: str) -> Path | None:
        p = self.claude_agents / f"{name}.md"
        return p if p.is_file() else None

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

    def _skill_source(self, name: str) -> Path | None:
        f = self.claude_skills / f"{name}.md"
        if f.is_file():
            return f
        d = self.claude_skills / name
        if d.is_dir() and ((d / "SKILL.md").is_file() or (d / "README.md").is_file()):
            return d
        return None

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

    # ── per-item render helpers ──────────────────────────────────────────────
    # Shared by the full sync() sweep and the single-item sync_agent()/
    # sync_skill() used by CRUD auto-sync, so both stay behaviourally
    # identical without duplicating the create/update/conflict decision.

    def _render_agent_to_codex(self, source: Path, target: Path, dry_run: bool) -> str | None:
        if target.is_symlink():
            return "conflicts"
        expected = _agent_toml(source)
        if target.exists() and (
            target.read_text(encoding="utf-8") == expected or _agent_equivalent(source, target)
        ):
            return None
        if target.exists() and not self._is_managed_agent(target):
            return "conflicts"
        action = "updated" if target.exists() else "created"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(expected, encoding="utf-8")
        return action

    def _render_agent_to_claude_mirror(self, source: Path, target: Path, dry_run: bool) -> str | None:
        if target.is_symlink():
            return "conflicts"
        if target.exists() and _claude_mirror_equivalent(source, target):
            return None
        if target.exists() and not _is_managed_claude_mirror(target):
            return "conflicts"
        action = "updated" if target.exists() else "created"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_claude_mirror_copy(source), encoding="utf-8")
        return action

    def _render_skill(self, source: Path, target: Path, dry_run: bool) -> str | None:
        """Used for both Codex and Claude-mirror skill targets — identical
        verbatim-payload-copy + marker-file semantics on both sides."""
        source_hash = _skill_entry_hash(source)
        target_valid = not target.is_symlink() and target.is_dir() and (target / "SKILL.md").is_file()
        if target.exists() and target_valid and _skill_entry_hash(target) == source_hash:
            return None
        if target.exists() and not self._is_managed_skill(target):
            return "conflicts"
        action = "updated" if target.exists() else "created"
        if not dry_run:
            payload = _skill_payload(source)
            if target.exists():
                shutil.rmtree(target)
            _write_skill_payload(target, payload)
        return action

    @staticmethod
    def _prune_agent_target(target: Path, is_managed_fn, dry_run: bool) -> bool:
        """Remove a render target whose registry source no longer exists —
        but only if it's actually one of our own generated copies. Unmanaged
        content (including symlinks, which we never touch) is left alone; it
        surfaces as a plain ``*_only`` status entry instead, same as any other
        engine-native resource with no registry counterpart."""
        if not target.exists() or target.is_symlink() or not is_managed_fn(target):
            return False
        if not dry_run:
            target.unlink()
        return True

    @staticmethod
    def _prune_skill_target(target: Path, is_managed_fn, dry_run: bool) -> bool:
        if not target.exists() or target.is_symlink() or not is_managed_fn(target):
            return False
        if not dry_run:
            shutil.rmtree(target)
        return True

    @staticmethod
    def _split_extra(names: set[str], targets: dict[str, Path], is_managed_fn) -> tuple[list[str], list[str]]:
        """Split target-only names (no registry source) into genuinely
        engine-native content (``*_only`` — a real import candidate) vs. a
        stale copy Agent Desktop generated itself whose source has since been
        deleted (``orphaned`` — pending cleanup on the next sync, never an
        import candidate). Mirrors the eligibility check in
        ``_prune_agent_target``/``_prune_skill_target`` exactly, so status()
        never disagrees with what the next sync() would actually prune."""
        foreign, orphaned = [], []
        for name in sorted(names):
            target = targets[name]
            eligible = not target.is_symlink() and is_managed_fn(target)
            (orphaned if eligible else foreign).append(name)
        return foreign, orphaned

    def status(self) -> dict:
        agent_sources = self._agent_sources()
        agent_targets = self._agent_targets()
        skill_sources = self._skill_sources()
        skill_targets = self._skill_targets()

        agent_extra = set(agent_targets) - set(agent_sources)
        agent_codex_only, agent_orphaned = self._split_extra(
            agent_extra, agent_targets, self._is_managed_agent
        )
        agents = {
            "synced": [], "missing_in_codex": [], "outdated": [],
            "conflicts": [], "codex_only": agent_codex_only, "orphaned": agent_orphaned,
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

        skill_extra = set(skill_targets) - set(skill_sources)
        skill_codex_only, skill_orphaned = self._split_extra(
            skill_extra, skill_targets, self._is_managed_skill
        )
        skills = {
            "synced": [], "missing_in_codex": [], "outdated": [],
            "conflicts": [], "codex_only": skill_codex_only, "orphaned": skill_orphaned,
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

        agent_extra = set(agent_targets) - set(agent_sources)
        agent_claude_only, agent_orphaned = self._split_extra(
            agent_extra, agent_targets, _is_managed_claude_mirror
        )
        agents = {
            "synced": [], "missing_in_claude": [], "outdated": [],
            "conflicts": [], "claude_only": agent_claude_only, "orphaned": agent_orphaned,
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

        skill_extra = set(skill_targets) - set(skill_sources)
        skill_claude_only, skill_orphaned = self._split_extra(
            skill_extra, skill_targets, self._is_managed_skill
        )
        skills = {
            "synced": [], "missing_in_claude": [], "outdated": [],
            "conflicts": [], "claude_only": skill_claude_only, "orphaned": skill_orphaned,
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
            "agents": {"created": [], "updated": [], "conflicts": [], "pruned": []},
            "skills": {"created": [], "updated": [], "conflicts": [], "pruned": []},
        }
        agent_sources = self._agent_sources()
        agent_targets = self._agent_targets()
        for name, source in sorted(agent_sources.items()):
            target = agent_targets.get(name) or (self.codex_home / "agents" / f"{name}.toml")
            action = self._render_agent_to_codex(source, target, dry_run)
            if action:
                result["agents"][action].append(name)
        for name, target in sorted(agent_targets.items()):
            if name in agent_sources:
                continue
            if self._prune_agent_target(target, self._is_managed_agent, dry_run):
                result["agents"]["pruned"].append(name)

        skill_sources = self._skill_sources()
        skill_targets = self._skill_targets()
        for name, source in sorted(skill_sources.items()):
            target = skill_targets.get(name) or (self.codex_skills / name)
            action = self._render_skill(source, target, dry_run)
            if action:
                result["skills"][action].append(name)
        for name, target in sorted(skill_targets.items()):
            if name in skill_sources:
                continue
            if self._prune_skill_target(target, self._is_managed_skill, dry_run):
                result["skills"]["pruned"].append(name)

        if self.claude_native_home is not None:
            result["claude_mirror"] = self._sync_claude_mirror(dry_run)
        return result

    def _sync_claude_mirror(self, dry_run: bool) -> dict:
        result = {
            "agents": {"created": [], "updated": [], "conflicts": [], "pruned": []},
            "skills": {"created": [], "updated": [], "conflicts": [], "pruned": []},
        }
        agent_sources = self._agent_sources()
        agent_targets = self._claude_mirror_agent_targets()
        for name, source in sorted(agent_sources.items()):
            target = agent_targets.get(name) or (self.claude_native_home / "agents" / f"{name}.md")
            action = self._render_agent_to_claude_mirror(source, target, dry_run)
            if action:
                result["agents"][action].append(name)
        for name, target in sorted(agent_targets.items()):
            if name in agent_sources:
                continue
            if self._prune_agent_target(target, _is_managed_claude_mirror, dry_run):
                result["agents"]["pruned"].append(name)

        skill_sources = self._skill_sources()
        skill_targets = self._claude_mirror_skill_targets()
        for name, source in sorted(skill_sources.items()):
            target = skill_targets.get(name) or (self.claude_native_home / "skills" / name)
            action = self._render_skill(source, target, dry_run)
            if action:
                result["skills"][action].append(name)
        for name, target in sorted(skill_targets.items()):
            if name in skill_sources:
                continue
            if self._prune_skill_target(target, self._is_managed_skill, dry_run):
                result["skills"]["pruned"].append(name)
        return result

    # ── single-item sync (CRUD auto-sync) ────────────────────────────────────
    # Renders (or, if the source was just deleted, prunes) exactly one named
    # Agent/Skill across every render target — no full-directory listing of
    # the registry or of any engine home, so a single save/delete stays O(1)
    # in the number of *other* Agents/Skills instead of paying for a full
    # sync() sweep every time. Behaviourally equivalent to sync() restricted
    # to this one name (same render/conflict/prune helpers).

    def sync_agent(self, name: str, dry_run: bool = False) -> dict:
        result: dict[str, str | None] = {"codex": None, "claude_mirror": None}
        source = self._agent_source(name)
        codex_target = self.codex_home / "agents" / f"{name}.toml"
        if source is not None:
            result["codex"] = self._render_agent_to_codex(source, codex_target, dry_run)
        elif self._prune_agent_target(codex_target, self._is_managed_agent, dry_run):
            result["codex"] = "pruned"

        if self.claude_native_home is not None:
            mirror_target = self.claude_native_home / "agents" / f"{name}.md"
            if source is not None:
                result["claude_mirror"] = self._render_agent_to_claude_mirror(source, mirror_target, dry_run)
            elif self._prune_agent_target(mirror_target, _is_managed_claude_mirror, dry_run):
                result["claude_mirror"] = "pruned"
        return result

    def sync_skill(self, name: str, dry_run: bool = False) -> dict:
        result: dict[str, str | None] = {"codex": None, "claude_mirror": None}
        source = self._skill_source(name)
        codex_target = self.codex_skills / name
        if source is not None:
            result["codex"] = self._render_skill(source, codex_target, dry_run)
        elif self._prune_skill_target(codex_target, self._is_managed_skill, dry_run):
            result["codex"] = "pruned"

        if self.claude_native_home is not None:
            mirror_target = self.claude_native_home / "skills" / name
            if source is not None:
                result["claude_mirror"] = self._render_skill(source, mirror_target, dry_run)
            elif self._prune_skill_target(mirror_target, self._is_managed_skill, dry_run):
                result["claude_mirror"] = "pruned"
        return result

    # ── conflict inspection + explicit single-target resolution ─────────────

    def conflict_preview(self, kind: str, name: str) -> dict:
        """Raw content of a name's registry source and its Codex / Claude-
        mirror render targets (whichever exist), so the UI can show the user
        *why* something is flagged as a conflict before they decide whether
        to force-overwrite it. For skills, only the entry file (SKILL.md /
        README.md) is read — a full-tree diff isn't a bounded-size operation
        worth doing for a UI preview."""
        if kind == "agent":
            source = self._agent_source(name)
            codex_target = self.codex_home / "agents" / f"{name}.toml"
            mirror_target = (
                self.claude_native_home / "agents" / f"{name}.md" if self.claude_native_home else None
            )
            return {
                "registry": _read_text_or_none(source),
                "codex": _read_text_or_none(codex_target),
                "claude_mirror": _read_text_or_none(mirror_target),
            }
        if kind == "skill":
            source = self._skill_source(name)
            if source is None:
                source_entry = None
            elif source.is_file():
                source_entry = source
            else:
                source_entry = _skill_entry_file(source)
            codex_entry = _skill_entry_file(self.codex_skills / name)
            mirror_entry = (
                _skill_entry_file(self.claude_native_home / "skills" / name) if self.claude_native_home else None
            )
            return {
                "registry": _read_text_or_none(source_entry),
                "codex": _read_text_or_none(codex_entry),
                "claude_mirror": _read_text_or_none(mirror_entry),
            }
        raise ValueError(f"unknown kind: {kind!r}")

    def resolve_conflict(self, kind: str, name: str, target_engine: str, dry_run: bool = False) -> dict:
        """Explicit, single-target force-overwrite for an item currently
        flagged as a conflict. Only ever invoked from a direct user action —
        never from the automatic CRUD-triggered sync — and only touches the
        one target the caller names, bypassing the "never overwrite unmanaged
        content" guard for that target alone so the user can consciously
        replace their own hand-edited native copy with the registry's render
        instead of being stuck in conflict forever."""
        if target_engine not in ("codex", "claude_mirror"):
            raise ValueError(f"unknown target_engine: {target_engine!r}")
        if target_engine == "claude_mirror" and self.claude_native_home is None:
            raise ValueError("claude_mirror target is not enabled for this registry")

        if kind == "agent":
            source = self._agent_source(name)
            if source is None:
                raise ValueError(f"no registry source for agent {name!r}")
            if target_engine == "codex":
                target = self.codex_home / "agents" / f"{name}.toml"
                content = _agent_toml(source)
            else:
                target = self.claude_native_home / "agents" / f"{name}.md"
                content = _claude_mirror_copy(source)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            return {"ok": True, "kind": kind, "name": name, "target": target_engine}

        if kind == "skill":
            source = self._skill_source(name)
            if source is None:
                raise ValueError(f"no registry source for skill {name!r}")
            root = self.codex_skills if target_engine == "codex" else self.claude_native_home / "skills"
            target = root / name
            if not dry_run:
                payload = _skill_payload(source)
                if target.exists() and not target.is_symlink():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                _write_skill_payload(target, payload)
            return {"ok": True, "kind": kind, "name": name, "target": target_engine}

        raise ValueError(f"unknown kind: {kind!r}")

    # ── one-time adoption of engine-native resources ─────────────────────────

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
