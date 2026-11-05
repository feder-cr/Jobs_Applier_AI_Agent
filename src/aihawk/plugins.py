"""What the model needs to know about one site's application forms: a Markdown file, one per site.

A plugin is a prompt, not code. Its front matter says which sites it is for and how a posting's link becomes the form's
link; its body is added to the model's instructions when it fills a form on those sites. To support a new site, add a
file to src/aihawk/plugins/, or to a plugins/ folder beside your aihawk.yaml, which wins over a bundled one of the same
name. AIHawk applies only where a plugin exists; other postings are listed for applying by hand.

A plugin with `when: always` in its front matter has no hosts: its body is added to every form's instructions, beside the
site's own plugin, and it never makes a site count as one AIHawk can apply to. The mail plugins are of that kind.

A plugin only explains a site. What protects the person stays in apply.py, out of any plugin's reach: the model is told
not to send the form and clicks on what would send it are refused (a last-resort script could still get around that),
it writes nothing that the CV and the answers do not say, and no captcha is solved: a box to tick is clicked once, as a person would, and a puzzle or a login is blocked.
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
    always: bool = False
    reaches: tuple[str, ...] = ()  # the mailboxes an always-plugin takes the model to; the only other sites it may visit

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
    always = meta.get("when") == "always"
    hosts = meta.get("hosts")
    if always and hosts is not None:
        raise PluginError(f"plugin {name}: a plugin for every form lists no hosts")
    if not always and (not isinstance(hosts, list) or not hosts or not all(isinstance(host, str) for host in hosts)):
        raise PluginError(f"plugin {name}: `hosts` must list the sites it is for")
    if meta.get("when") not in (None, "always"):
        raise PluginError(f"plugin {name}: `when` can only be always")
    if not body.strip():
        raise PluginError(f"plugin {name}: it has no instructions")
    names = tuple(host.lower() for host in hosts or ())
    reaches = meta.get("reaches") or []
    if not isinstance(reaches, list) or not all(isinstance(host, str) for host in reaches):
        raise PluginError(f"plugin {name}: `reaches` lists the sites its mailbox is on")
    return Plugin(str(meta.get("name") or name), names, body.strip(), str(meta.get("apply_path") or ""), always, tuple(reaches))


def _files(folder: Traversable | Path) -> dict[str, str]:
    files = {}
    for item in folder.iterdir():
        if item.name.endswith(".md"):
            try:
                files[item.name] = item.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as error:
                raise PluginError(f"plugin {item.name}: it is not UTF-8 text, so it cannot be read: save it again as UTF-8") from error
    return files


def load_plugins(workspace: Path | None = None) -> tuple[Plugin, ...]:
    """The bundled plugins and the person's own, whose files win over bundled ones of the same name."""
    files = _files(resources.files("aihawk").joinpath("plugins"))
    own = workspace / "plugins" if workspace else None
    if own and own.is_dir():
        files |= _files(own)
    return tuple(parse_plugin(name.removesuffix(".md"), text) for name, text in sorted(files.items()))


def plugin_for(url: str, plugins: tuple[Plugin, ...]) -> Plugin | None:
    return next((plugin for plugin in plugins if not plugin.always and plugin.matches(url)), None)


def always_plugins(plugins: tuple[Plugin, ...]) -> tuple[Plugin, ...]:
    """The plugins that apply to every form, such as the mail ones; they explain no site."""
    return tuple(plugin for plugin in plugins if plugin.always)
