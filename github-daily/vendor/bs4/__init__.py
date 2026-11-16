# -*- coding: utf-8 -*-
"""Minimal bs4 stand-in: only what github-trending-daily scripts actually use.

Supported: BeautifulSoup(html, "html.parser"), Tag.get(), Tag.get_text(),
Tag.select(sel), Tag.select_one(sel) with tag / .class / [attr] / [attr="v"] /
[attr*="v"] and descendant combinators. NOT a general bs4 replacement.
"""

import re
from html.parser import HTMLParser

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class Tag:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent

    # -- bs4-ish API -------------------------------------------------
    def get(self, name, default=None):
        return self.attrs.get(name, default)

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def get_text(self):
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.get_text())
        return "".join(out)

    def iter_descendants(self):
        for c in self.children:
            if not isinstance(c, str):
                yield c
                yield from c.iter_descendants()

    def select(self, selector):
        return [n for n in _select(self, selector)]

    def select_one(self, selector):
        found = self.select(selector)
        return found[0] if found else None

    def __repr__(self):
        return f"<Tag {self.tag} {self.attrs}>"


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Tag("[document]")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Tag(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Tag(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


_COMPOUND = re.compile(r"^([A-Za-z0-9_-]*)((?:\.[\w-]+)*)((?:\[[^\]]*\])*)$")
_ATTR_SPEC = re.compile(r"^([\w:-]+)\s*(?:([*^$~|]?=)\s*(.*))?$")


def _parse_compound(token):
    m = _COMPOUND.match(token)
    if not m:
        raise ValueError(f"unsupported selector token: {token!r}")
    tag = m.group(1) or None
    classes = re.findall(r"\.([\w-]+)", m.group(2))
    specs = []
    for raw in re.findall(r"\[([^\]]*)\]", m.group(3)):
        am = _ATTR_SPEC.match(raw.strip())
        if not am:
            raise ValueError(f"unsupported attr selector: [{raw}]")
        name, op, val = am.group(1), am.group(2), am.group(3)
        if val is not None and len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        specs.append((name, op, val))
    return tag, classes, specs


def _match(node, compound):
    tag, classes, specs = compound
    if tag and node.tag != tag:
        return False
    have = node.classes
    for c in classes:
        if c not in have:
            return False
    for name, op, val in specs:
        actual = node.attrs.get(name)
        if actual is None:
            return False
        if op is None:
            continue
        if op == "=":
            ok = actual == val
        elif op == "*=":
            ok = val in actual
        elif op == "^=":
            ok = actual.startswith(val)
        elif op == "$=":
            ok = actual.endswith(val)
        elif op == "~=":
            ok = val in actual.split()
        elif op == "|=":
            ok = actual == val or actual.startswith(val + "-")
        else:
            raise ValueError(f"unsupported operator {op!r}")
        if not ok:
            return False
    return True


def _select(root, selector):
    tokens = selector.split()
    if not tokens:
        return []
    comps = [_parse_compound(t) for t in tokens]
    current = [n for n in root.iter_descendants() if _match(n, comps[-1])]
    for comp in reversed(comps[:-1]):
        kept = []
        for node in current:
            p = node.parent
            while p is not None:
                if _match(p, comp):
                    kept.append(node)
                    break
                p = p.parent
        current = kept
    return current


class BeautifulSoup(Tag):
    def __init__(self, markup, features=None, **kwargs):
        super().__init__("[document]")
        builder = _TreeBuilder()
        builder.feed(markup or "")
        builder.close()
        self.children = builder.root.children
        for c in self.children:
            if not isinstance(c, str):
                c.parent = self
