#!/usr/bin/env python
#coding:utf-8
# Author: kundawang
# Purpose: merge multiple Drawing objects into one target drawing
# Created: 19.11.2026
# Copyright (C) 2026
# License: MIT License
"""
The **merge** module adds the ability to combine several independent
:class:`svgwrite.Drawing` objects into one target drawing without ID clashes.

The problem
-----------

Every :class:`~svgwrite.Drawing` lives in its own ID space. Two drawings may
contain a ``linearGradient`` or ``clipPath`` with the same ``id`` and internal
``url(#id)`` / ``xlink:href="#id"`` references. When their element trees are
naively concatenated into one SVG document the duplicate IDs overwrite each
other and the references resolve to the wrong objects.

The solution
------------

:meth:`Drawing.merge` wraps every source drawing in its own independent group
and puts the source ``defs`` inside that group. Every ``id`` of the source
drawing is moved into a private namespace by adding a prefix, and every
*internal* reference to those ids is rewritten accordingly:

* ``href`` / ``xlink:href`` fragment references (``use``, gradient/pattern/
  filter inheritance, ...)
* ``url(#id)`` functional references in presentation attributes
  (``fill``, ``stroke``, ``clip-path``, ``filter``, ``mask``, ``marker`` ...)
* ``url(#id)`` inside embedded ``<style>`` / ``<script>`` content
* element references in SMIL timing attributes (``begin``/``end``/
  ``repeatBegin``/``repeatEnd``)

External URLs (``http://...``, ``file.png``, ``data:...``) are never touched.

The source drawings are deep copied first, so they stay untouched, can be
merged again and the merged output is deterministic (no use of the global
:class:`~svgwrite.utils.AutoID` counter).

Validation
----------

In strict mode (the default) the first problem raises a :class:`MergeError`:

* :class:`DuplicateIdError` - an id exists more than once in a source drawing
* :class:`InvalidIdError` - an id or an id-prefix is not a valid XML Name
* :class:`NamespaceCollisionError` - a generated id already exists in the
  target drawing
* :class:`MissingReferenceError` - an internal fragment reference points to an
  id that does not exist in the source drawing
* :class:`CircularReferenceError` - the ``href`` graph of the source drawing
  contains a cycle

With ``strict=False`` no exception is raised; all problems are collected in a
:class:`MergeReport` and everything that can be repaired is repaired.
"""

import copy
import re

from svgwrite.base import BaseElement
from svgwrite.container import Group, SVG

# attributes whose value is a single IRI reference, possibly as fragment
HREF_ATTRIBUTES = ('href', 'xlink:href')

# presentation attributes that may contain one or more ``url(#id)`` paints
FUNCIRI_ATTRIBUTES = (
    'fill', 'stroke', 'clip-path', 'filter', 'mask', 'marker',
    'marker-start', 'marker-mid', 'marker-end', 'cursor', 'color-profile',
    'style',
)

# SMIL timing attributes that may contain an ``id.event`` / ``id.begin`` value
TIMING_ATTRIBUTES = ('begin', 'end', 'repeatBegin', 'repeatEnd')

# SMIL animation elements target another element via href for animation
# purposes only; they do not structurally include it and therefore must not
# participate in <use> cycle detection
SMIL_ELEMENTS = frozenset((
    'set', 'animate', 'animateMotion', 'animateColor', 'animateTransform',
))

# attributes of the source root <svg> that are document level declarations and
# must not be copied onto the nested wrapper element
ROOT_ONLY_ATTRIBUTES = (
    'xmlns', 'xmlns:xlink', 'xmlns:ev', 'baseProfile', 'version',
)

# an XML Name (used for the id attribute, see XML 1.0 § Name production)
_NAME_START_CHAR = (
    r'[A-Za-z_ -ɏḀ-↿⇀-⇿]'
)
_NAME_CHAR = _NAME_START_CHAR[:-1] + r'.0-9·-̀-ͯ‿-⁀]'
XML_NAME_RE = re.compile(r'^%s%s*$' % (_NAME_START_CHAR, _NAME_CHAR))

# url(#id) / url("#id") / url('#id') with optional whitespace
_FUNCIRI_RE = re.compile(
    r'url\(\s*[\'"]?#([^\'")\s]+)[\'"]?\s*\)'
)
# a pure fragment reference: #id
_FRAGMENT_RE = re.compile(r'^#(.*)$')
# a SMIL syncbase value like 'rect1.click+5s' or 'rect1.begin'
_SYNCBASE_RE = re.compile(r'^([A-Za-z_][\w.-]*)(?=\.[A-Za-z-]+)')


class MergeError(Exception):
    """Base class of all errors raised while merging drawings."""


class DuplicateIdError(MergeError):
    """An id occurs more than once in one source drawing."""


class InvalidIdError(MergeError):
    """An id (or an id-prefix) is not a valid XML Name."""


class NamespaceCollisionError(MergeError):
    """A generated id already exists in the target drawing."""


class MissingReferenceError(MergeError):
    """An internal fragment reference points to a non-existing id."""


class CircularReferenceError(MergeError):
    """The internal ``href`` reference graph contains a cycle."""


class Issue(object):
    """A single problem found while merging one source drawing.

    :ivar str kind: one of ``'duplicate_id'``, ``'invalid_id'``,
      ``'namespace_collision'``, ``'missing_reference'``,
      ``'circular_reference'``
    :ivar str message: human readable description
    :ivar source: the offending value(s), type depends on `kind`
    """
    __slots__ = ('kind', 'message', 'source')

    def __init__(self, kind, message, source=None):
        self.kind = kind
        self.message = message
        self.source = source

    def __repr__(self):
        return "Issue(%r, %r)" % (self.kind, self.message)

    def __eq__(self, other):
        if not isinstance(other, Issue):
            return NotImplemented
        return (self.kind == other.kind and self.message == other.message
                and self.source == other.source)


class MergeReport(object):
    """Result object returned by non-strict merges.

    Contains the added wrapper groups and every :class:`Issue` found, grouped
    by kind. An empty report means the merge was completely clean.
    """
    def __init__(self):
        #: list of wrapper groups (``g`` or nested ``svg``) added to the target
        self.groups = []
        self.issues = []

    def add_issue(self, kind, message, source=None):
        self.issues.append(Issue(kind, message, source))

    @property
    def ok(self):
        """:const:`True` if no issues were recorded."""
        return not self.issues

    def issues_by_kind(self, kind):
        return [issue for issue in self.issues if issue.kind == kind]

    @property
    def duplicate_ids(self):
        return self.issues_by_kind('duplicate_id')

    @property
    def invalid_ids(self):
        return self.issues_by_kind('invalid_id')

    @property
    def namespace_collisions(self):
        return self.issues_by_kind('namespace_collision')

    @property
    def missing_references(self):
        return self.issues_by_kind('missing_reference')

    @property
    def circular_references(self):
        return self.issues_by_kind('circular_reference')

    def __bool__(self):
        return self.ok

    __nonzero__ = __bool__  # Python 2

    def __repr__(self):
        return "MergeReport(groups=%d, issues=%d)" % (
            len(self.groups), len(self.issues))


def is_valid_id(name):
    """Return :const:`True` if `name` is a valid XML Name usable as ``id``."""
    return bool(name) and XML_NAME_RE.match(name) is not None


def _iter_elements(element):
    """Yield `element` and every svgwrite element in its subtree (pre-order).

    Plain XML helper objects (``Title``/``Desc``/``Metadata``) are yielded but
    not descended into, they cannot carry ids or references.
    """
    yield element
    children = getattr(element, 'elements', None)
    if children:
        for child in children:
            for item in _iter_elements(child):
                yield item


def _collect_ids(root):
    """Return an ordered mapping ``id -> [elements...]`` for the whole tree."""
    ids = {}
    for element in _iter_elements(root):
        attribs = getattr(element, 'attribs', None)
        if attribs and 'id' in attribs and attribs['id'] is not None:
            ids.setdefault(str(attribs['id']), []).append(element)
    return ids


def _collect_href_edges(root):
    """Return a list of ``(element, target_id)`` internal href edges."""
    edges = []
    for element in _iter_elements(root):
        attribs = getattr(element, 'attribs', None)
        if not attribs:
            continue
        for attr in HREF_ATTRIBUTES:
            value = attribs.get(attr)
            if isinstance(value, str):
                match = _FRAGMENT_RE.match(value.strip())
                if match:
                    edges.append((element, match.group(1)))
    return edges


class DrawingMerger(object):
    """Merge one or more source drawings into a target drawing.

    Usually the class is not used directly, see :meth:`Drawing.merge`.

    :param target: the receiving :class:`~svgwrite.drawing.Drawing`
    :param bool strict: raise :class:`MergeError` on the first problem
    """
    def __init__(self, target, strict=True):
        self.target = target
        self.strict = strict
        self.report = MergeReport()
        # all ids currently present in the target document
        self._target_ids = set(_collect_ids(target).keys())
        self._auto_counter = 0

    # -- public API ---------------------------------------------------------

    def merge(self, sources, prefix=None, insert=None, as_nested_svg=True):
        """Merge `sources` into the target drawing.

        :param sources: a :class:`~svgwrite.drawing.Drawing` or an iterable of
          drawings
        :param str prefix: id namespace prefix; generated ids look like
          ``prefix + '_' + original_id``. Without a prefix an automatic,
          collision-free prefix of the form ``mergeN`` is used.
        :param 2-tuple insert: ``(x, y)`` position of the merged group,
          defaults to ``(0, 0)``
        :param bool as_nested_svg: wrap the source in a nested ``<svg>``
          preserving width/height/viewBox (default); otherwise a plain
          ``<g>`` group is used
        :returns: :class:`MergeReport` (also when strict, an empty one)
        :raises MergeError: in strict mode on the first problem found
        """
        if isinstance(sources, BaseElement):
            sources = [sources]
        for index, source in enumerate(sources):
            source_prefix = self._next_prefix(prefix, index)
            self._merge_one(source, source_prefix, insert, as_nested_svg)
        return self.report

    # -- prefix handling ----------------------------------------------------

    def _next_prefix(self, user_prefix, index):
        if user_prefix is not None:
            # first source keeps the prefix, further ones get _2, _3, ...
            base = user_prefix if index == 0 else '%s_%d' % (
                user_prefix, index + 1)
        else:
            self._auto_counter += 1
            base = 'merge%d' % self._auto_counter
        if not is_valid_id(base):
            raise InvalidIdError(
                "Invalid id prefix %r: a prefix must be a valid XML Name."
                % user_prefix)
        # make sure the generated namespace cannot clash with target ids
        while self._prefix_taken(base):
            self._auto_counter += 1
            base = 'merge%d' % self._auto_counter
        return base

    def _prefix_taken(self, prefix):
        marker = prefix + '_'
        return any(existing == prefix or existing.startswith(marker)
                   for existing in self._target_ids)

    # -- per source processing ---------------------------------------------

    def _merge_one(self, source, prefix, insert, as_nested_svg):
        clone = copy.deepcopy(source)

        id_map = self._build_id_map(clone, prefix)
        # validate the reference graph before any attribute is rewritten, so
        # that a strict merge leaves the target untouched on failure
        self._check_references(clone, id_map)
        self._rewrite_tree(clone, id_map)
        wrapper = self._build_wrapper(clone, prefix, insert, as_nested_svg)

        self.target.add(wrapper)
        self.report.groups.append(wrapper)
        # the new ids are part of the target document from now on
        self._target_ids.update(id_map.values())

    def _build_id_map(self, root, prefix):
        """Validate the source ids and build ``old_id -> namespaced_id``.

        In non-strict mode duplicated ids are repaired: the first occurrence
        keeps the namespaced id, the following ones get deterministic
        ``-2``, ``-3`` ... variants which are recorded in the map as well.
        """
        collected = _collect_ids(root)
        id_map = {}
        used = set(self._target_ids)
        for old_id, elements in collected.items():
            if not is_valid_id(old_id):
                self._fail(InvalidIdError, 'invalid_id',
                           "Invalid id %r: not a valid XML Name." % old_id,
                           old_id)
            if len(elements) > 1:
                self._fail(DuplicateIdError, 'duplicate_id',
                           "Duplicate id %r occurs %d times in the source "
                           "drawing." % (old_id, len(elements)),
                           old_id)
            for occurrence, element in enumerate(elements):
                new_id = '%s_%s' % (prefix, old_id)
                if occurrence > 0:
                    new_id = self._unique_variant(new_id, used)
                if new_id in used:
                    self._fail(NamespaceCollisionError,
                               'namespace_collision',
                               "Generated id %r already exists in the target "
                               "drawing." % new_id, new_id)
                    if not self.strict:
                        new_id = self._unique_variant(new_id, used)
                used.add(new_id)
                if occurrence == 0:
                    id_map[old_id] = new_id
                else:
                    # point the duplicated element at its own repaired id
                    element.attribs['id'] = new_id
        return id_map

    @staticmethod
    def _unique_variant(base, used):
        counter = 2
        candidate = '%s-%d' % (base, counter)
        while candidate in used:
            counter += 1
            candidate = '%s-%d' % (base, counter)
        return candidate

    def _check_references(self, root, id_map):
        """Validate internal fragment references and the href cycle graph."""
        # missing internal references
        for element, target_id in _collect_href_edges(root):
            if target_id not in id_map:
                name = self._element_label(element)
                self._fail(MissingReferenceError, 'missing_reference',
                           "Element %s references missing internal id %r."
                           % (name, target_id), target_id)

        cycle = self._find_cycle(self._build_href_graph(root, id_map))
        if cycle is not None:
            self._fail(CircularReferenceError, 'circular_reference',
                       "Circular <use>/href reference chain: %s."
                       % ' -> '.join(cycle), cycle)

    def _build_href_graph(self, root, id_map):
        """Build a directed graph on id'd nodes.

        An href may live on an element without an own id (e.g. a ``<use>``
        inside a group); the nearest ancestor carrying an id then acts as the
        referencing node, so cycles through such elements are still detected.
        """
        graph = {}

        def walk(element, id_stack):
            own_id = element.attribs.get('id') if getattr(
                element, 'attribs', None) else None
            if own_id is not None:
                id_stack.append(str(own_id))
            attribs = getattr(element, 'attribs', None)
            if (attribs and id_stack
                    and element.elementname not in SMIL_ELEMENTS):
                for attr in HREF_ATTRIBUTES:
                    value = attribs.get(attr)
                    if isinstance(value, str):
                        match = _FRAGMENT_RE.match(value.strip())
                        if match and match.group(1) in id_map:
                            graph.setdefault(id_stack[-1], set()).add(
                                match.group(1))
            for child in getattr(element, 'elements', None) or ():
                walk(child, id_stack)
            if own_id is not None:
                id_stack.pop()

        walk(root, [])
        return graph

    @staticmethod
    def _find_cycle(graph):
        """Return a node list describing one cycle, or :const:`None`."""
        state = {}  # node -> 0 visiting, 1 done
        stack = []

        def visit(node):
            state[node] = 0
            stack.append(node)
            for nxt in graph.get(node, ()):
                mark = state.get(nxt)
                if mark == 0:
                    start = stack.index(nxt)
                    return stack[start:] + [nxt]
                if mark is None:
                    found = visit(nxt)
                    if found is not None:
                        return found
            stack.pop()
            state[node] = 1
            return None

        for node in graph:
            if node not in state:
                found = visit(node)
                if found is not None:
                    return found
        return None

    # -- rewriting ----------------------------------------------------------

    def _rewrite_tree(self, root, id_map):
        for element in _iter_elements(root):
            attribs = getattr(element, 'attribs', None)
            if attribs:
                self._rewrite_attributes(element, attribs, id_map)
            self._rewrite_text_content(element, id_map)

    def _rewrite_attributes(self, element, attribs, id_map):
        # 1. rename the element itself
        if 'id' in attribs and attribs['id'] is not None:
            old_id = str(attribs['id'])
            if old_id in id_map:
                attribs['id'] = id_map[old_id]

        for attr, value in list(attribs.items()):
            if not isinstance(value, str):
                continue
            if attr in HREF_ATTRIBUTES:
                attribs[attr] = self._rewrite_href(value, id_map)
            elif attr in FUNCIRI_ATTRIBUTES:
                attribs[attr] = self._rewrite_funciri(value, id_map)
            elif attr in TIMING_ATTRIBUTES:
                attribs[attr] = self._rewrite_timing(value, id_map)

        # keep the python level href attribute of XLink based objects in sync
        href = getattr(element, 'href', None)
        if isinstance(href, str):
            element.href = self._rewrite_href(href, id_map)

    @staticmethod
    def _rewrite_href(value, id_map):
        stripped = value.strip()
        match = _FRAGMENT_RE.match(stripped)
        if match and match.group(1) in id_map:
            return '#%s' % id_map[match.group(1)]
        # external URLs and unresolved fragments are left unchanged
        return value

    @staticmethod
    def _rewrite_funciri(value, id_map):
        def replace(match):
            old_id = match.group(1)
            if old_id in id_map:
                return 'url(#%s)' % id_map[old_id]
            return match.group(0)
        return _FUNCIRI_RE.sub(replace, value)

    @staticmethod
    def _rewrite_timing(value, id_map):
        def replace(match):
            old_id = match.group(1)
            if old_id in id_map:
                return id_map[old_id]
            return old_id
        return _SYNCBASE_RE.sub(replace, value)

    def _rewrite_text_content(self, element, id_map):
        # <style>/<script> hold their text in ``_content``; plain XML helper
        # objects (Title/Desc/Metadata) cannot contain url(#) references.
        content = getattr(element, '_content', None)
        if isinstance(content, str) and 'url(#' in content:
            element._content = _FUNCIRI_RE.sub(
                lambda m: 'url(#%s)' % id_map[m.group(1)]
                if m.group(1) in id_map else m.group(0),
                content)

    # -- wrapper construction ----------------------------------------------

    def _build_wrapper(self, source, prefix, insert, as_nested_svg):
        if as_nested_svg:
            wrapper = SVG(factory=self.target)
            # width/height/viewBox/... of the source root are preserved
            for attr, value in source.attribs.items():
                if attr not in ROOT_ONLY_ATTRIBUTES and value is not None:
                    wrapper[attr] = value
        else:
            wrapper = Group(factory=self.target)
            # groups have no size attributes, keep only transform/presentation
            for attr in ('transform', 'style', 'class'):
                value = source.attribs.get(attr)
                if value is not None:
                    wrapper[attr] = value

        wrapper['id'] = prefix
        if insert is not None and insert != (0, 0):
            if as_nested_svg:
                # nested <svg> establishes its own viewport at x/y
                wrapper['x'], wrapper['y'] = insert
            else:
                # a plain group is positioned by a transform; prepend it so
                # the source's own transform is evaluated first
                translation = 'translate(%s,%s)' % (insert[0], insert[1])
                existing = wrapper.attribs.get('transform')
                wrapper['transform'] = ('%s %s' % (
                    translation, existing)).strip() if existing else translation

        # the source defs become the first child of the independent group;
        # remove the empty auto-created defs of a fresh SVG wrapper.
        source_defs = source.defs
        source.elements.remove(source_defs)
        if as_nested_svg:
            wrapper.elements.remove(wrapper.defs)
        wrapper.add(source_defs)
        for child in source.elements:
            wrapper.add(child)
        return wrapper

    # -- error handling -----------------------------------------------------

    def _fail(self, exception_cls, kind, message, source=None):
        if self.strict:
            raise exception_cls(message)
        self.report.add_issue(kind, message, source)

    @staticmethod
    def _element_label(element):
        element_id = element.attribs.get('id')
        if element_id is not None:
            return '<%s id="%s">' % (element.elementname, element_id)
        return '<%s>' % element.elementname
