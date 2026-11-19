#!/usr/bin/env python
# coding:utf-8
# Purpose: audit ID/href/FuncIRI references of a Drawing
# Copyright (c) 2022, Manfred Moitzi
# License: MIT License
"""
The **audit** module checks the internal reference graph of a
:class:`~svgwrite.drawing.Drawing`.

SVG documents connect elements by references:

* ``id`` attributes name elements,
* ``xlink:href`` / ``href`` attributes point at elements (``use``,
  ``tref``, ``textPath``, ``feImage``, gradients, patterns, filters, ...),
* ``url(#id)`` *FuncIRI* values point at paint servers, clip paths, masks,
  markers and filters (``fill``, ``stroke``, ``clip-path``, ``filter``,
  ``marker-start`` ...), including references embedded in ``style``
  attributes and in the CSS text of ``style`` elements.

The audit detects:

* **missing** references -- a ``#id`` without a matching element,
* **duplicate** and **empty** ids,
* **type mismatches** -- e.g. ``fill="url(#rect1)"`` pointing at a ``rect``
  instead of a paint server,
* ``use`` reference **cycles** (without flagging legitimate sharing, where
  several ``use`` elements point at the same target),
* percent-**escaped** fragment ids such as ``url(#my%20grad)``,
* **deeply nested** trees beyond a configurable depth.

Forward references inside ``defs`` are legal and resolve fine, because the
whole document tree is indexed before references are checked.  External
URLs (``http://...``, ``file.svg#id``, ``data:...`` ...) are legal and
never reported as missing.

Main entry points:

* :meth:`Drawing.audit_references` -- returns an :class:`AuditReport`,
* :meth:`Drawing.save` / :meth:`Drawing.write` with ``audit=True`` --
  raise :class:`ReferenceAuditError` in strict mode before writing a broken
  document.
"""
from __future__ import unicode_literals

import re
from urllib.parse import unquote

from svgwrite.base import BaseElement

__all__ = [
    'audit_references',
    'ReferenceAuditor',
    'AuditReport',
    'Finding',
    'ReferenceAuditError',
    'ERROR',
    'WARNING',
]

ERROR = 'error'
WARNING = 'warning'

#: element names that may be referenced through an href attribute,
#: keyed by the referencing element name.  ``None`` means "any element".
HREF_EXPECTED_TYPES = {
    'use': None,
    'tref': frozenset(('text', 'tspan', 'tref', 'textArea')),
    'textPath': frozenset(('path', 'shape')),
    'linearGradient': frozenset(('linearGradient', 'radialGradient')),
    'radialGradient': frozenset(('linearGradient', 'radialGradient')),
    'pattern': frozenset(('pattern',)),
    'filter': frozenset(('filter',)),
    # image / feImage / a / script / animation elements may reference any
    # resource, including external URLs -- no local type constraints.
}

#: presentation / FuncIRI attributes and the element types they require.
#: ``None`` means "any element type is acceptable".
FUNCIRI_EXPECTED_TYPES = {
    'fill': frozenset(
        ('linearGradient', 'radialGradient', 'pattern', 'solidColor')),
    'stroke': frozenset(
        ('linearGradient', 'radialGradient', 'pattern', 'solidColor')),
    'clip-path': frozenset(('clipPath',)),
    'mask': frozenset(('mask',)),
    'filter': frozenset(('filter',)),
    'marker': frozenset(('marker',)),
    'marker-start': frozenset(('marker',)),
    'marker-mid': frozenset(('marker',)),
    'marker-end': frozenset(('marker',)),
    'color-profile': frozenset(('color-profile',)),
    'cursor': frozenset(('cursor',)),
    # the legacy CSS 'clip' property uses rect(...), not url() -- if a
    # url() shows up, any referenced element is accepted without type check.
    'clip': None,
    'alignment-baseline': None,
    'baseline-shift': None,
    'dominant-baseline': None,
}

# shape element names, used for the textPath type check
_SHAPE_NAMES = frozenset((
    'line', 'rect', 'circle', 'ellipse', 'polyline', 'polygon', 'path',
))

_URL_RE = re.compile(r'url\(\s*([^)]*?)\s*\)')
# property name immediately preceding a url() inside a CSS declaration
_PROPERTY_RE = re.compile(r'([-\w]+)\s*:\s*[^;:]*$')
_DEFAULT_MAX_DEPTH = 256


class Finding(object):
    """A single audit finding.

    :ivar code: short machine readable code, e.g. ``'missing-reference'``
    :ivar severity: :data:`ERROR` or :data:`WARNING`
    :ivar message: human readable explanation
    :ivar path: location of the referencing/defining element, e.g.
      ``'svg[0]/defs[0]/linearGradient[0]/@fill'``
    :ivar ref: the raw reference value (``None`` for id findings)
    """
    __slots__ = ('code', 'severity', 'message', 'path', 'ref')

    def __init__(self, code, severity, message, path, ref=None):
        self.code = code
        self.severity = severity
        self.message = message
        self.path = path
        self.ref = ref

    def __repr__(self):
        return 'Finding(%r, %r, %r, %r)' % (
            self.code, self.severity, self.message, self.path)

    def __eq__(self, other):
        if not isinstance(other, Finding):
            return NotImplemented
        return (self.code == other.code and self.severity == other.severity
                and self.message == other.message and self.path == other.path
                and self.ref == other.ref)

    def __hash__(self):
        return hash((self.code, self.severity, self.message,
                     self.path, self.ref))


class AuditReport(object):
    """Result of a reference audit.

    Findings are stored in a deterministic order:

    1. id findings (empty/duplicate ids) in document pre-order,
    2. deep-nesting findings in document pre-order,
    3. missing/type/escaped reference findings in document pre-order,
    4. use-cycle findings, in cycle-detection order.
    """
    def __init__(self, strict=False):
        self.strict = strict
        self.findings = []

    def add(self, code, severity, message, path, ref=None):
        self.findings.append(Finding(code, severity, message, path, ref))

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def ok(self):
        """``True`` if no error-level findings exist.

        In strict mode every defect is an error; in non-strict mode only
        unresolved references and structural problems count as errors while
        escaped ids, duplicate ids, type mismatches and deep nesting stay
        warnings.
        """
        return not self.errors

    def raise_if_errors(self):
        if self.errors:
            raise ReferenceAuditError(self)

    def __len__(self):
        return len(self.findings)

    def __iter__(self):
        return iter(self.findings)

    def __bool__(self):
        return bool(self.findings)

    def __str__(self):
        lines = []
        for finding in self.findings:
            lines.append('%s: %s (%s): %s' % (
                finding.severity.upper(), finding.code,
                finding.path, finding.message))
        return '\n'.join(lines)


class ReferenceAuditError(Exception):
    """Raised when a strict audit finds error-level problems."""
    def __init__(self, report):
        self.report = report
        lines = ['reference audit failed with %d error(s):' %
                 len(report.errors)]
        for finding in report.errors:
            lines.append('  %s (%s): %s' % (
                finding.code, finding.path, finding.message))
        super(ReferenceAuditError, self).__init__('\n'.join(lines))


def audit_references(drawing, strict=True, max_depth=_DEFAULT_MAX_DEPTH):
    """Audit all id/href/``url(#...)`` references of *drawing*.

    :param drawing: a :class:`~svgwrite.drawing.Drawing` (or any
      :class:`~svgwrite.base.BaseElement` root)
    :param bool strict: in strict mode every defect is reported as error;
      in non-strict mode empty ids, duplicate ids, type mismatches, escaped
      fragment ids and excessive nesting are warnings
    :param int max_depth: nesting depth at which a ``max-depth-exceeded``
      finding is emitted; traversal itself stays iterative and cannot hit
      Python's recursion limit
    :returns: :class:`AuditReport`
    """
    auditor = ReferenceAuditor(strict=strict, max_depth=max_depth)
    return auditor.audit(drawing)


class ReferenceAuditor(object):
    """Walks a document tree and builds an :class:`AuditReport`."""

    def __init__(self, strict=True, max_depth=_DEFAULT_MAX_DEPTH):
        self.strict = strict
        self.max_depth = max_depth
        # id -> list of (element, path) in pre-order
        self._id_defs = {}
        # id -> element name of the first definition (for type checks)
        self._id_types = {}
        # element id() -> True, guards against shared subtrees
        self._seen_elements = set()
        # element id() -> True, depth warning already emitted
        self._depth_reported = set()
        self._id_findings = []
        self._depth_findings = []
        self._ref_findings = []
        self._cycle_findings = []
        self._root = None
        self.report = None

    # -- public API -------------------------------------------------------

    def audit(self, root):
        # reset all per-run state so one auditor instance can audit
        # several drawings independently
        self._id_defs = {}
        self._id_types = {}
        self._seen_elements = set()
        self._depth_reported = set()
        self._id_findings = []
        self._depth_findings = []
        self._ref_findings = []
        self._cycle_findings = []
        self._root = root
        self.report = AuditReport(strict=self.strict)
        self._collect_ids(root)
        self._scan_references(root)
        self._detect_use_cycles(root)
        self.report.findings = (
            self._id_findings
            + self._depth_findings
            + self._ref_findings
            + self._cycle_findings)
        return self.report

    # -- severity helper --------------------------------------------------

    def _severity(self, code):
        """Strict mode turns every defect into an error."""
        if self.strict:
            return ERROR
        return {
            'empty-id': WARNING,
            'duplicate-id': WARNING,
            'type-mismatch': WARNING,
            'escaped-url': WARNING,
            'max-depth-exceeded': WARNING,
        }.get(code, ERROR)

    def _record(self, bucket, code, message, path, ref=None):
        bucket.append(Finding(code, self._severity(code), message, path, ref))

    # -- tree traversal ---------------------------------------------------

    def _walk(self, root):
        """Iterative pre-order walk; yields (element, path, depth).

        Elements shared between several parents are only visited once.
        ``Title``/``Desc``/``Metadata`` objects are skipped (they carry no
        references nor ids).
        """
        self._seen_elements = set()
        stack = [(root, self._segment(root, None, 0), 0)]
        while stack:
            element, path, depth = stack.pop()
            key = id(element)
            if key in self._seen_elements:
                continue
            self._seen_elements.add(key)
            yield element, path, depth
            children = getattr(element, 'elements', None)
            if not children:
                continue
            if depth >= self.max_depth:
                # one finding per truncated element, even though the tree
                # is walked once per audit phase
                if key not in self._depth_reported:
                    self._depth_reported.add(key)
                    self._record(
                        self._depth_findings, 'max-depth-exceeded',
                        'nesting depth exceeds %d; subtree skipped' %
                        self.max_depth,
                        path)
                continue
            # push in reverse so document order is preserved on pop
            for index in range(len(children) - 1, -1, -1):
                child = children[index]
                if isinstance(child, BaseElement):
                    stack.append(
                        (child, self._segment(child, path, index),
                         depth + 1))

    @staticmethod
    def _segment(element, parent_path, index):
        name = getattr(element, 'elementname', 'baseElement')
        segment = '%s[%d]' % (name, index)
        if parent_path is None:
            return segment
        return parent_path + '/' + segment

    # -- phase 1: id index ------------------------------------------------

    def _collect_ids(self, root):
        for element, path, depth in self._walk(root):
            if 'id' not in element.attribs:
                continue
            raw_id = element.attribs['id']
            idstr = '' if raw_id is None else str(raw_id)
            if idstr == '':
                self._record(
                    self._id_findings, 'empty-id',
                    "element has an empty 'id' attribute", path, '')
                continue
            defs = self._id_defs.setdefault(idstr, [])
            if defs:
                first_path = defs[0][1]
                self._record(
                    self._id_findings, 'duplicate-id',
                    "duplicate id %r already defined at %s" %
                    (idstr, first_path),
                    path, idstr)
            else:
                self._id_types[idstr] = element.elementname
            defs.append((element, path))

    # -- phase 2: references ----------------------------------------------

    def _scan_references(self, root):
        for element, path, depth in self._walk(root):
            self._scan_href(element, path)
            self._scan_attributes(element, path)
            self._scan_style_attribute(element, path)
            self._scan_style_element(element, path)

    def _scan_href(self, element, path):
        href = self._get_href(element)
        if href is None:
            return
        self._check_reference(
            path + '/@xlink:href', href,
            HREF_EXPECTED_TYPES.get(element.elementname),
            allow_external=True)

    @staticmethod
    def _get_href(element):
        attribs = getattr(element, 'attribs', None)
        if not attribs:
            return None
        # SVG 2 'href' and SVG 1.1 'xlink:href' are both supported
        if 'xlink:href' in attribs:
            return attribs['xlink:href']
        if 'href' in attribs:
            return attribs['href']
        return None

    def _scan_attributes(self, element, path):
        for attr in sorted(element.attribs):
            if attr in ('xlink:href', 'href', 'id', 'style'):
                continue
            if attr not in FUNCIRI_EXPECTED_TYPES:
                continue
            value = element.attribs[attr]
            if not isinstance(value, str):
                continue
            expected = FUNCIRI_EXPECTED_TYPES[attr]
            for raw_iri in _URL_RE.findall(value):
                self._check_iri(raw_iri, path + '/@' + attr, expected)

    def _scan_style_attribute(self, element, path):
        value = element.attribs.get('style')
        if not isinstance(value, str):
            return
        for match in _URL_RE.finditer(value):
            raw_iri = match.group(1)
            property_name = self._property_before(value, match.start())
            expected = FUNCIRI_EXPECTED_TYPES.get(property_name)
            self._check_iri(
                raw_iri,
                '%s/@style/%s' % (path, property_name or '?'),
                expected)

    def _scan_style_element(self, element, path):
        if element.elementname != 'style':
            return
        content = getattr(element, '_content', '')
        if not isinstance(content, str):
            return
        for match in _URL_RE.finditer(content):
            raw_iri = match.group(1)
            property_name = self._property_before(content, match.start())
            expected = FUNCIRI_EXPECTED_TYPES.get(property_name)
            self._check_iri(
                raw_iri,
                '%s/::style/%s' % (path, property_name or '?'),
                expected)

    @staticmethod
    def _property_before(text, position):
        match = _PROPERTY_RE.search(text[:position])
        return match.group(1) if match else None

    # -- reference resolution ---------------------------------------------

    def _check_reference(self, path, raw_ref, expected_types,
                         allow_external):
        if not isinstance(raw_ref, str):
            self._record(
                self._ref_findings, 'missing-reference',
                'non-string reference value %r' % (raw_ref,), path,
                str(raw_ref))
            return
        if raw_ref == '':
            self._record(
                self._ref_findings, 'empty-reference',
                'empty reference', path, raw_ref)
            return
        if raw_ref[0] != '#':
            if allow_external:
                # external URL (http://, file.svg#id, data:, ...): legal
                return
            self._record(
                self._ref_findings, 'missing-reference',
                'reference %r is not a local fragment (#id)' % raw_ref,
                path, raw_ref)
            return
        self._check_fragment(raw_ref[1:], path, expected_types,
                             raw_ref=raw_ref)

    def _check_iri(self, raw_iri, path, expected_types):
        iri = raw_iri.strip()
        if iri == '':
            self._record(
                self._ref_findings, 'empty-reference',
                'empty url() reference', path, raw_iri)
            return
        # strip optional quotes: url("#id") / url('#id')
        if len(iri) >= 2 and iri[0] in ('"', "'") and iri[-1] == iri[0]:
            iri = iri[1:-1].strip()
        self._check_reference(
            path, iri, expected_types, allow_external=True)

    def _check_fragment(self, fragment, path, expected_types, raw_ref):
        escaped = '%' in fragment
        idstr = unquote(fragment) if escaped else fragment
        if idstr == '':
            self._record(
                self._ref_findings, 'empty-reference',
                'reference points at an empty fragment', path, raw_ref)
            return
        if escaped:
            self._record(
                self._ref_findings, 'escaped-url',
                'percent-escaped fragment %r; some SVG renderers do not '
                'match escaped ids' % raw_ref,
                path, raw_ref)
        target_name = self._id_types.get(idstr)
        if target_name is None:
            self._record(
                self._ref_findings, 'missing-reference',
                'no element with id %r found in document' % idstr,
                path, raw_ref)
            return
        if expected_types is not None and not self._type_matches(
                target_name, expected_types):
            self._record(
                self._ref_findings, 'type-mismatch',
                'id %r references a %s element, expected one of %s' % (
                    idstr, target_name,
                    ', '.join(sorted(expected_types))),
                path, raw_ref)

    @staticmethod
    def _type_matches(target_name, expected_types):
        if target_name in expected_types:
            return True
        # textPath accepts any basic shape ('shape' is a placeholder)
        if 'shape' in expected_types and target_name in _SHAPE_NAMES:
            return True
        return False

    # -- phase 3: use cycle detection -------------------------------------

    def _detect_use_cycles(self, root):
        # Build a directed graph of the document: edges are
        #   * containment edges (parent -> child)
        #   * <use> reference edges (use -> referenced element)
        # A valid document tree cannot cycle by containment, so every
        # detected cycle necessarily contains a <use> edge.  Several uses
        # sharing one target create diamond shapes but no back edges, hence
        # legitimate sharing is not flagged.
        id_to_element = {}
        for idstr, defs in self._id_defs.items():
            id_to_element[idstr] = defs[0][0]

        adjacency = {}          # node key -> list of child node keys
        node_path = {}          # node key -> element path
        order = []              # nodes in document pre-order
        for element, path, depth in self._walk(root):
            key = id(element)
            node_path[key] = path
            order.append(key)
            adjacency.setdefault(key, [])
            for child in getattr(element, 'elements', ()):
                if not isinstance(child, BaseElement):
                    continue
                adjacency[key].append(id(child))
            if element.elementname != 'use':
                continue
            href = self._get_href(element)
            if not (isinstance(href, str) and href.startswith('#')):
                continue
            fragment = href[1:]
            target_id = unquote(fragment) if '%' in fragment else fragment
            target = id_to_element.get(target_id)
            if target is not None:
                adjacency[key].append(id(target))

        # Iterative white/gray/black DFS; a back edge to a gray node closes
        # a cycle.  Explicit stack keeps this safe on deeply nested trees.
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {node_key: WHITE for node_key in adjacency}

        for root_key in order:
            if color[root_key] != WHITE:
                continue
            # stack entries: (node key, index of next edge, trail of paths)
            stack = [(root_key, 0, [])]
            color[root_key] = GRAY
            while stack:
                node_key, edge_index, trail = stack[-1]
                edges = adjacency[node_key]
                if edge_index >= len(edges):
                    color[node_key] = BLACK
                    stack.pop()
                    continue
                child_key = edges[edge_index]
                stack[-1] = (node_key, edge_index + 1, trail)
                child_color = color.get(child_key)
                if child_color == GRAY:
                    # report only the cyclic suffix of the DFS trail
                    child_path = node_path[child_key]
                    if child_path in trail:
                        cycle_trail = trail[trail.index(child_path):]
                    elif child_key == node_key:
                        # self reference: use -> itself
                        cycle_trail = []
                    else:
                        cycle_trail = trail
                    cycle = cycle_trail + [node_path[node_key],
                                           child_path]
                    self._record(
                        self._cycle_findings, 'use-cycle',
                        'circular <use> reference chain: '
                        + ' -> '.join(cycle),
                        node_path[node_key])
                elif child_color == WHITE:
                    color[child_key] = GRAY
                    stack.append(
                        (child_key, 0, trail + [node_path[node_key]]))
