"""Generate the plugin index and sha-pinned marketplace entries (goal.md §4.1, §4.2).

Boundary: reads the marketplace and plugin files of this repo; writes only the index (or prints).
The index lists each plugin's version and components (skills, commands, agents, MCP servers,
hooks) in the format of the official Grok plugin marketplace, so clients can show what a plugin
contains before installing it. Output is deterministic (sorted, no timestamps), so CI regenerates
it and fails on any diff. ``--entry`` prints the marketplace entry another catalog (for example
xai-org/plugin-marketplace) needs, pinned to a full commit sha for ``require_sha`` environments.

    uv run python scripts/plugin_index.py            # write the index
    uv run python scripts/plugin_index.py --check    # CI: fail when stale
    uv run python scripts/plugin_index.py --entry <40-hex sha> --url https://github.com/org/repo.git
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import cast

REPO = Path(__file__).resolve().parents[1]
CATALOG = Path(".grok-plugin/marketplace.json")
INDEX = Path(".grok-plugin/plugin-index.json")
INDEX_VERSION = 1
# Same display limits as the official marketplace index.
MAX_ITEMS = 50
MAX_TEXT = 120
SHA = re.compile(r"^[0-9a-f]{40}$")
Json = dict[str, object]


def clean(text: str) -> str:
    """One line, at most ``MAX_TEXT`` characters (ellipsis when cut)."""
    text = re.sub(r"\s+", " ", re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)).strip()
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1].rstrip() + "…"


def frontmatter(path: Path) -> dict[str, str]:
    """Scalar fields of a Markdown file's YAML front matter (block scalars joined)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    i = 1
    while i < len(lines) and lines[i].strip() not in ("---", "..."):
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        i += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if re.match(r"^[|>][+-]?$", value):
            block: list[str] = []
            while i < len(lines) and (lines[i][:1] in (" ", "\t") or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            value = " ".join(b for b in block if b)
        fields[key] = value.strip("\"'") if value[:1] in "\"'" else value
    return fields


def _item(name: str, description: str) -> Json:
    """One component entry (description omitted when empty)."""
    item: Json = {"name": clean(name)}
    if clean(description):
        item["description"] = clean(description)
    return item


def _markdown(root: Path, sub: str) -> list[Json]:
    """Agents or commands: one entry per Markdown file."""
    out: list[Json] = []
    for path in sorted((root / sub).rglob("*.md")) if (root / sub).is_dir() else []:
        meta = frontmatter(path)
        out.append(_item(meta.get("name") or path.stem, meta.get("description", "")))
    return out


def _json(path: Path) -> Json:
    """A JSON object file ({} when missing)."""
    if not path.is_file():
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8"))
    return cast(Json, data) if isinstance(data, dict) else {}


def components(root: Path) -> dict[str, list[Json]]:
    """A plugin's components, each category sorted by name and capped."""
    skills = [_item(frontmatter(d / "SKILL.md").get("name") or d.name,
                    frontmatter(d / "SKILL.md").get("description", ""))
              for d in sorted((root / "skills").iterdir()) if (d / "SKILL.md").is_file()] \
        if (root / "skills").is_dir() else []
    servers = cast(dict[str, Json], _json(root / ".mcp.json").get("mcpServers") or {})
    mcp = [_item(name, "stdio" if cfg.get("command") else "http" if cfg.get("url") else "")
           for name, cfg in servers.items()]
    hooks_obj = cast(dict[str, object], _json(root / "hooks" / "hooks.json").get("hooks") or {})
    hooks = [_item(event, ", ".join(str(cast(Json, e)["matcher"])
                                    for e in cast(list[object], entries)
                                    if isinstance(e, dict) and cast(Json, e).get("matcher")))
             for event, entries in hooks_obj.items() if isinstance(entries, list)]
    found = {"agents": _markdown(root, "agents"), "commands": _markdown(root, "commands"),
             "hooks": hooks, "mcpServers": mcp, "skills": skills}
    return {key: sorted(items, key=lambda i: str(i["name"]))[:MAX_ITEMS]
            for key, items in sorted(found.items()) if items}


def generate(repo: Path) -> Json:
    """The index for every plugin in the marketplace (local sources only in this repo)."""
    plugins: dict[str, Json] = {}
    for raw in cast(list[Json], _json(repo / CATALOG).get("plugins", [])):
        source = cast(Json, raw.get("source") or {})
        root = (repo / str(source.get("path", ""))).resolve()
        if source.get("type") != "local" or not root.is_relative_to(repo.resolve()):
            raise ValueError(f"{raw.get('name')}: only local sources inside the repo are indexed")
        manifest = _json(root / ".grok-plugin" / "plugin.json")
        record: Json = {}
        if isinstance(manifest.get("version"), str):
            record["version"] = manifest["version"]
        record["components"] = components(root)
        plugins[str(raw["name"])] = record
    return {"version": INDEX_VERSION, "plugins": dict(sorted(plugins.items()))}


def pinned_entry(repo: Path, name: str, url: str, sha: str) -> Json:
    """A url-source marketplace entry for ``name`` pinned to ``sha``.

    Raises:
        ValueError: A malformed sha or url, or an unknown plugin.
    """
    if not SHA.match(sha) or not url.startswith("https://"):
        raise ValueError("--entry needs a 40-hex lowercase sha and an https:// url")
    for raw in cast(list[Json], _json(repo / CATALOG).get("plugins", [])):
        if raw.get("name") == name:
            path = str(cast(Json, raw["source"])["path"]).removeprefix("./")
            entry = {k: v for k, v in raw.items() if k not in ("source", "version")}
            return {**entry, "source": {"source": "url", "url": url, "path": path, "sha": sha}}
    raise ValueError(f"no plugin named {name!r} in {CATALOG}")


def render(data: Json) -> str:
    """Stable JSON text."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None, repo: Path = REPO) -> int:
    """CLI entry point."""
    cli = argparse.ArgumentParser(description="Plugin index and pinned marketplace entries")
    cli.add_argument("--check", action="store_true")
    cli.add_argument("--entry", metavar="SHA")
    cli.add_argument("--url", default="")
    cli.add_argument("--plugin", default="computer-use")
    args = cli.parse_args(argv)
    try:
        if args.entry:
            print(render(pinned_entry(repo, args.plugin, args.url, args.entry)), end="")
            return 0
        text = render(generate(repo))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    index = repo / INDEX
    if args.check:
        if not index.is_file() or index.read_text(encoding="utf-8") != text:
            print(f"{INDEX} is stale: run scripts/plugin_index.py", file=sys.stderr)
            return 1
        return 0
    index.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
