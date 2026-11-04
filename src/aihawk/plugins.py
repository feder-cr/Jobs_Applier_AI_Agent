"""What the model needs to know about one site's application forms: a Markdown file, one per site.

A plugin is a prompt, not code. Its front matter says which sites it is for and how a posting's link becomes the form's
link; its body is added to the model's instructions when it fills a form on those sites. To support a new site, add a
file to src/aihawk/plugins/, or to a plugins/ folder beside your aihawk.yaml, which wins over a bundled one of the same
name. AIHawk applies only where a plugin exists; other postings are listed for applying by hand.

A plugin only explains a site. What protects the person stays in apply.py, out of any plugin's reach: the model cannot
send the form, writes nothing that the CV and the answers do not say, and does not get around a captcha or a login.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from urllib.parse import urlsplit

import yaml


class PluginError(Exception):
    """A plugin file cannot be used."""


@dataclass(frozen=True)
class Plugin:
    name: str
    hosts: tuple[str, ...]
    instructions: str
    apply_path: str = ""

    def matches(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return any(host == known or host.endswith("." + known) for known in self.hosts)

    def form_url(self, url: str) -> str:
        """The link of the posting's form: the posting's own, or with the path that leads to the form."""
        if not self.apply_path:
            return url
        parts = urlsplit(url)
        path = parts.path.rstrip("/")
        if not path.endswith(self.apply_path):
            path += self.apply_path
        return parts._replace(path=path).geturl()


def parse_plugin(name: str, text: str) -> Plugin:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise PluginError(f"plugin {name}: it must start with a front matter between two --- lines")
    head, body = text[4:].split("\n---\n", 1)
    try:
        meta = yaml.safe_load(head) or {}
    except yaml.YAMLError as error:
        raise PluginError(f"plugin {name}: its front matter is not valid YAML: {error}") from error
    hosts = meta.get("hosts")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(host, str) for host in hosts):
        raise PluginError(f"plugin {name}: `hosts` must list the sites it is for")
    if not body.strip():
        raise PluginError(f"plugin {name}: it has no instructions")
    return Plugin(str(meta.get("name") or name), tuple(host.lower() for host in hosts), body.strip(), str(meta.get("apply_path") or ""))


def _files(folder: Traversable | Path) -> dict[str, str]:
    return {item.name: item.read_text(encoding="utf-8") for item in folder.iterdir() if item.name.endswith(".md")}


def load_plugins(workspace: Path | None = None) -> tuple[Plugin, ...]:
    """The bundled plugins and the person's own, whose files win over bundled ones of the same name."""
    files = _files(resources.files("aihawk").joinpath("plugins"))
    own = workspace / "plugins" if workspace else None
    if own and own.is_dir():
        files |= _files(own)
    return tuple(parse_plugin(name.removesuffix(".md"), text) for name, text in sorted(files.items()))


def plugin_for(url: str, plugins: tuple[Plugin, ...]) -> Plugin | None:
    return next((plugin for plugin in plugins if plugin.matches(url)), None)
