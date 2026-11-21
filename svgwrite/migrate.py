#!/usr/bin/env python
#coding:utf-8
# Purpose: controlled migration of drawings between SVG profiles
# License: MIT License
"""
Controlled migration of a *Full* profile :class:`~svgwrite.drawing.Drawing`
to an *SVG Tiny 1.2* drawing, e.g. for legacy mobile targets that only
accept SVG Tiny 1.2.

The migration

* migrates common shapes, groups, text, gradients and `use` references into
  a verifiable Tiny drawing, preserving hierarchy, size and every style
  attribute that Tiny can express,
* never silently drops content: every unsupported element or attribute is
  recorded in a :class:`MigrationReport` with the element path, and is either
  strictly rejected (:class:`MigrationError`) or explicitly degraded
  (dropped, but reported),
* keeps shared `defs`, cross-element references (`xlink:href`, `url(#id)`),
  namespaces and attribute/element name casing intact,
* does not modify the source document.

Usage::

    from svgwrite.migrate import migrate_to_tiny

    result = migrate_to_tiny(full_drawing, strict=False)
    tiny_drawing = result.drawing
    if not result.report.ok:
        print(result.report)

"""
from __future__ import unicode_literals

import copy
import re

from svgwrite.container import Defs
from svgwrite.drawing import Drawing
from svgwrite.validator2 import get_validator

TINY_PROFILE = 'tiny'

#: Attributes of the root <svg> element that are re-emitted by
#: Drawing.get_xml() and therefore must not be copied from the source.
_ROOT_REEMITTED = frozenset([
    'xmlns', 'xmlns:xlink', 'xmlns:ev', 'baseProfile', 'version',
])

#: Issue actions
REJECTED = 'rejected'   #: strict mode refused the document at this issue
DEGRADED = 'degraded'   #: content was dropped explicitly (and reported)

_FUNCIRI_RE = re.compile(r'url\(\s*#([^\)\s]+)\s*\)')


class MigrationIssue(object):
    """ A single migration problem.

    .. attribute:: path

        Element path in the source document, e.g. ``/svg/g#panel/rect[2]``.

    .. attribute:: element

        Name of the affected SVG element.

    .. attribute:: attribute

        Name of the affected SVG attribute or `None` for element level issues.

    .. attribute:: reason

        Human readable cause.

    .. attribute:: action

        One of ``'rejected'`` (strict mode) or ``'degraded'``.

    """
    def __init__(self, path, element, attribute, reason, action):
        self.path = path
        self.element = element
        self.attribute = attribute
        self.reason = reason
        self.action = action

    def __str__(self):
        if self.attribute is not None:
            what = "attribute '%s' of <%s>" % (self.attribute, self.element)
        else:
            what = "element <%s>" % self.element
        return "%s: %s %s [%s]" % (self.path, what, self.reason, self.action)

    def __repr__(self):
        return "MigrationIssue(%s)" % str(self)

    def __eq__(self, other):
        if not isinstance(other, MigrationIssue):
            return NotImplemented
        return (self.path, self.element, self.attribute, self.reason, self.action) == \
               (other.path, other.element, other.attribute, other.reason, other.action)


class MigrationReport(object):
    """ Collects all :class:`MigrationIssue` entries of one migration run. """
    def __init__(self):
        self.issues = []

    def add(self, issue):
        self.issues.append(issue)
        return issue

    @property
    def ok(self):
        """ `True` if nothing had to be rejected or degraded. """
        return not self.issues

    def by_action(self, action):
        """ All issues with the given action (``'rejected'``/``'degraded'``). """
        return [issue for issue in self.issues if issue.action == action]

    def __len__(self):
        return len(self.issues)

    def __iter__(self):
        return iter(self.issues)

    def __str__(self):
        if self.ok:
            return "migration report: ok, no issues"
        lines = ["migration report: %d issue(s)" % len(self.issues)]
        lines.extend("  %s" % issue for issue in self.issues)
        return "\n".join(lines)


class MigrationError(Exception):
    """ Raised in strict mode when content cannot be migrated to Tiny.

    The issues collected up to the failure are available as
    :attr:`report`, the triggering issue as :attr:`issue`.
    """
    def __init__(self, issue, report):
        super(MigrationError, self).__init__(str(issue))
        self.issue = issue
        self.report = report


class MigrationResult(object):
    """ Return value of :func:`migrate_to_tiny`.

    .. attribute:: drawing

        The new Tiny 1.2 :class:`~svgwrite.drawing.Drawing`.

    .. attribute:: report

        The :class:`MigrationReport`.

    """
    def __init__(self, drawing, report):
        self.drawing = drawing
        self.report = report

    def __iter__(self):
        # allow: drawing, report = migrate_to_tiny(dwg)
        return iter((self.drawing, self.report))


def migrate_to_tiny(drawing, strict=False, filename=None, debug=None):
    """ Migrate a *Full* profile drawing to a new *Tiny 1.2* drawing.

    :param drawing: source :class:`~svgwrite.drawing.Drawing` (not modified)
    :param bool strict: if `True` raise :class:`MigrationError` at the first
        element or attribute that cannot be expressed in Tiny 1.2, otherwise
        degrade explicitly (drop and report)
    :param string filename: filename for the new drawing, defaults to the
        source filename
    :param bool debug: validation mode of the new drawing, defaults to the
        source setting
    :return: :class:`MigrationResult` with the new drawing and the report

    """
    if debug is None:
        debug = drawing.debug
    if filename is None:
        filename = getattr(drawing, 'filename', 'noname.svg')
    migrator = _TinyMigrator(strict=strict)
    target = Drawing(filename=filename, profile=TINY_PROFILE, debug=debug)
    migrator.migrate(drawing, target)
    return MigrationResult(target, migrator.report)


class _TinyMigrator(object):
    def __init__(self, strict):
        self.strict = strict
        self.validator = get_validator(TINY_PROFILE, True)
        self.report = MigrationReport()
        self.source_ids = set()
        self.migrated_ids = set()
        self.references = []  # (path, elementname, attributename, refid)
        self.target = None

    # -- public driver ----------------------------------------------------

    def migrate(self, source, target):
        self.target = target
        self._collect_source_ids(source)
        self._migrate_root_attributes(source, target)
        for child in source.elements:
            migrated = self._migrate_element(
                child, parent=target, path=self._child_path(source, child, '/svg'))
            if migrated is not None and migrated is not target.defs:
                target.elements.append(migrated)
        self._check_references()

    # -- root -------------------------------------------------------------

    def _migrate_root_attributes(self, source, target):
        for key, value in source.attribs.items():
            if key in _ROOT_REEMITTED:
                continue
            self._migrate_attribute(target, 'svg', key, value, '/svg')

    # -- elements ---------------------------------------------------------

    def _migrate_element(self, element, parent, path):
        name = element.elementname
        if not self.validator.is_valid_elementname(name):
            self._fail(path, name, None,
                       "element is not part of the SVG Tiny 1.2 profile")
            return None
        if not self.validator.is_valid_children(parent.elementname, name):
            self._fail(path, name, None,
                       "element is not valid content of <%s> in SVG Tiny 1.2"
                       % parent.elementname)
            return None

        if isinstance(element, Defs) and parent is self.target:
            # shared defs of the target drawing already exists: reuse it
            new = self.target.defs
        else:
            new = copy.copy(element)  # shallow: keeps text content etc.
            new.attribs = {}
            new.elements = []
            new._parameter = self.target._parameter

        for key, value in element.attribs.items():
            self._migrate_attribute(new, name, key, value, path)

        for child in element.elements:
            migrated = self._migrate_element(
                child, parent=new, path=self._child_path(element, child, path))
            if migrated is not None:
                new.elements.append(migrated)
        return new

    def _migrate_attribute(self, new, elementname, key, value, path):
        if not self.validator.is_valid_svg_attribute(elementname, key):
            self._fail(path, elementname, key,
                       "attribute is not part of the SVG Tiny 1.2 profile")
            return
        try:
            self.validator.check_svg_attribute_value(elementname, key, value)
        except (TypeError, ValueError) as err:
            self._fail(path, elementname, key,
                       "value %r is not expressible in SVG Tiny 1.2 (%s)"
                       % (value, err))
            return
        new.attribs[key] = value
        self._track(elementname, key, value, path)

    # -- reference and id tracking ----------------------------------------

    def _track(self, elementname, key, value, path):
        value = str(value)
        if key == 'id':
            self.migrated_ids.add(value)
        elif key == 'xlink:href':
            if value.startswith('#'):
                self.references.append((path, elementname, key, value[1:]))
        for match in _FUNCIRI_RE.finditer(value):
            self.references.append((path, elementname, key, match.group(1)))

    def _collect_source_ids(self, root):
        stack = [root]
        while stack:
            element = stack.pop()
            element_id = element.attribs.get('id')
            if element_id is not None:
                self.source_ids.add(str(element_id))
            stack.extend(element.elements)

    def _check_references(self):
        for path, elementname, attribute, refid in self.references:
            if refid in self.migrated_ids:
                continue
            if refid in self.source_ids:
                reason = ("dangling reference '#%s': the referenced element "
                          "was removed by the migration" % refid)
            else:
                reason = ("unresolvable reference '#%s': no element with "
                          "this id exists in the document" % refid)
            self._fail(path, elementname, attribute, reason)

    # -- issue handling ----------------------------------------------------

    def _fail(self, path, element, attribute, reason):
        action = REJECTED if self.strict else DEGRADED
        issue = self.report.add(
            MigrationIssue(path, element, attribute, reason, action))
        if self.strict:
            raise MigrationError(issue, self.report)

    # -- path helper --------------------------------------------------------

    @staticmethod
    def _child_path(parent, child, parent_path):
        name = child.elementname
        siblings = [e for e in parent.elements
                    if getattr(e, 'elementname', None) == name]
        if 'id' in child.attribs:
            segment = "%s#%s" % (name, child.attribs['id'])
        elif len(siblings) > 1:
            segment = "%s[%d]" % (name, siblings.index(child) + 1)
        else:
            segment = name
        return "%s/%s" % (parent_path, segment)
