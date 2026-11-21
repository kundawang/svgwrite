#!/usr/bin/env python
# coding:utf-8
# Purpose: per-document auto ID management
# License: MIT License
"""
The **IDScope** manages the ``id`` attributes of one SVG document.

The historical :class:`svgwrite.utils.AutoID` implementation uses one process
wide counter.  That is convenient while building a single document, but two
documents built at the same time (for example by two worker threads) consume
from the same counter and therefore produce different output depending on the
interleaving of both threads.

An :class:`IDScope` belongs to exactly one document (usually a
:class:`svgwrite.Drawing`) and has its own counter.  In addition it keeps track
of the IDs which are already present in the document, so automatically
generated IDs never collide with explicitly assigned ones.

Scopes are thread safe.  Allocation order still determines the generated ID,
therefore a deterministic construction order produces deterministic output.
"""

import logging
import re
import threading
from collections import namedtuple

logger = logging.getLogger(__name__)

_IRI_ATTRIBUTE = re.compile(r"url\(\s*#([^)\s]+)\s*\)")
# Animation timing values use references like ``id1.click`` or ``id1.end``,
# therefore a trailing '.' is allowed, but the identifier must not be part of
# a longer name.
_TIMING_IDENTIFIER_TEMPLATE = r"(?<![A-Za-z0-9_.-]){0}(?![A-Za-z0-9_-])"
_TIMING_ATTRIBUTES = frozenset(("begin", "end"))


class IDConflictError(ValueError):
    """Raised if an ID conflict can not be resolved.

    Only raised if :class:`IDScope` is configured with
    ``conflict_policy='raise'``.
    """


class IDConflict(namedtuple("IDConflict", "old_id new_id element reason")):
    """Description of one resolved ID conflict.

    The entries are stored at :attr:`IDScope.conflicts` and can be used to
    trace why an element received a different ID than requested.

    .. attribute:: old_id

       The ID which was requested by the element.

    .. attribute:: new_id

       The ID which was actually assigned.

    .. attribute:: element

       The :class:`~svgwrite.base.BaseElement` which was renamed.

    .. attribute:: reason

       ``'explicit'`` for IDs assigned through an element constructor or
       through ``element['id'] = ...``, ``'duplicate'`` if two elements of a
       document use the same ID (for example after moving an element into
       another document).
    """

    __slots__ = ()

    def __str__(self):
        return "ID conflict: '%s' -> '%s' (%s)" % (
            self.old_id,
            self.new_id,
            self.reason,
        )


def _walk_document(root):
    """Yield *root* and all subelements in document order."""
    stack = [root]
    while stack:
        element = stack.pop()
        yield element
        children = getattr(element, "elements", None)
        if children:
            stack.extend(reversed(children))


def attribute_key(identifier):
    """Return the canonical dictionary key for an ID value.

    SVG IDs are serialized as strings, therefore ``id=1`` and ``id='1'`` have
    to be treated as the same ID.
    """
    return identifier if isinstance(identifier, str) else str(identifier)


class IDScope(object):
    """Independent ID namespace of one SVG document.

    :param string conflict_policy: ``'rename'`` assigns a free ID if an ID is
        already in use.  ``'raise'`` raises :class:`IDConflictError` instead.
        In both cases the conflict is recorded at :attr:`conflicts`.
    """

    def __init__(self, conflict_policy="rename"):
        if conflict_policy not in ("rename", "raise"):
            raise ValueError(
                "Invalid conflict policy '%s', use 'rename' or 'raise'."
                % conflict_policy
            )
        self.conflict_policy = conflict_policy
        self._lock = threading.RLock()
        self._ids = {}  # ID string -> element object
        self._reserved = set()  # allocated but not yet attached
        self._next_number = 1
        self._conflicts = []

    @property
    def conflicts(self):
        """All resolved ID conflicts as tuple of :class:`IDConflict`."""
        with self._lock:
            return tuple(self._conflicts)

    @property
    def ids(self):
        """All IDs which are currently registered, sorted alphabetically."""
        with self._lock:
            return tuple(sorted(self._ids))

    def is_registered(self, identifier):
        """Return ``True`` if *identifier* is already in use."""
        with self._lock:
            return attribute_key(identifier) in self._ids

    def allocate(self, element=None, start=None):
        """Allocate and register a new automatically generated ID.

        The returned ID has the form ``id<N>`` and is guaranteed to be unused
        inside this scope.  Explicit IDs and IDs of already existing elements
        are skipped.

        :param element: element which owns the generated ID (optional)
        :param int start: start the search at this number instead of the
            internal counter
        """
        with self._lock:
            if start is not None:
                try:
                    start = int(start)
                except (TypeError, ValueError):
                    raise TypeError("'start' has to be an integer.")
                self._next_number = start
            identifier = self._allocate_number()
            self._ids[identifier] = element
            self._reserved.discard(identifier)
            return identifier

    def _allocate_number(self):
        number = self._next_number
        while True:
            identifier = "id%d" % number
            number += 1
            if identifier not in self._ids and identifier not in self._reserved:
                self._next_number = number
                return identifier

    def reserve(self):
        """Allocate a fresh ID without registering an owner.

        Used by :meth:`svgwrite.base.BaseElement.copy`: the copy is not part
        of the document yet, therefore its IDs must not show up in
        :attr:`ids`. The counter is advanced nevertheless, so two copies can
        never receive the same ID. The ID is registered once the copy is added
        to a document.
        """
        with self._lock:
            identifier = self._allocate_number()
            self._reserved.add(identifier)
            return identifier

    def _unique_name(self, base):
        """Return a free derivative of *base* (``base_1``, ``base_2``, ...)."""
        identifier = attribute_key(base)
        candidate = identifier
        counter = 1
        while candidate in self._ids:
            candidate = "%s_%d" % (identifier, counter)
            counter += 1
        return candidate

    def request(self, identifier, element, reason="explicit"):
        """Register *identifier* for *element* and return the used ID.

        If the requested ID is already owned by another element, the conflict
        is resolved according to :attr:`conflict_policy` and recorded at
        :attr:`conflicts`.
        """
        key = attribute_key(identifier)
        with self._lock:
            current = self._ids.get(key)
            if current is None or current is element:
                self._ids[key] = element
                return identifier
            return self._resolve_conflict(key, element, reason)

    def _resolve_conflict(self, old_id, element, reason):
        new_id = self._unique_name(old_id)
        conflict = IDConflict(old_id, new_id, element, reason)
        if self.conflict_policy == "raise":
            # Do not mutate the scope; the exception carries the details.
            raise IDConflictError(str(conflict))
        self._conflicts.append(conflict)
        logger.warning(str(conflict))
        self._ids[new_id] = element
        return new_id

    def release(self, identifier, element=None):
        """Release *identifier*.

        If *element* is given, the ID is only released if it belongs to this
        element.  This prevents releasing the ID of a different element by
        accident.
        """
        key = attribute_key(identifier)
        with self._lock:
            if element is None or self._ids.get(key) is element:
                self._ids.pop(key, None)

    def _detach_nodes(self, nodes):
        """Release the IDs of *nodes* from this scope."""
        with self._lock:
            for node in nodes:
                attribs = getattr(node, "attribs", None)
                if attribs is None or "id" not in attribs:
                    continue
                key = attribute_key(attribs["id"])
                self._ids.pop(key, None)
                self._reserved.discard(key)

    def detach_tree(self, root):
        """Remove *root* and its subelements from this document.

        The IDs are released and each element is detached from the scope, so
        that the element can be re-added later or can be moved to another
        document.  The elements keep their ``id`` attributes; registering them
        again restores the IDs (unless a conflict requires a rename).
        """
        nodes = list(_walk_document(root))
        self._detach_nodes(nodes)
        for node in nodes:
            if getattr(node, "_id_scope", None) is self:
                node._id_scope = None

    def adopt_tree(self, root):
        """Register *root* and all subelements in this scope.

        Elements coming from another scope are detached from their old scope
        first.  IDs which already exist in this document are handled according
        to :attr:`conflict_policy`.  String references inside the adopted
        subtree are rewritten if a target had to be renamed, so that references
        and targets stay consistent.

        :returns: mapping of old IDs to renamed IDs, empty if nothing changed
        """
        nodes = list(_walk_document(root))
        if not nodes:
            return {}

        if self.conflict_policy == "raise":
            self._preflight_conflicts(nodes)

        # Detach elements which belong to a different document before taking
        # this scope's lock.  This avoids holding two scope locks at once.
        foreign = {}
        for node in nodes:
            scope = getattr(node, "_id_scope", None)
            if scope is not None and scope is not self:
                foreign.setdefault(scope, []).append(node)
        for scope, foreign_nodes in foreign.items():
            scope._detach_nodes(foreign_nodes)

        renames = {}
        with self._lock:
            for node in nodes:
                node._id_scope = self
                attribs = getattr(node, "attribs", None)
                if attribs is None or "id" not in attribs:
                    continue
                identifier = attribs["id"]
                key = attribute_key(identifier)
                current = self._ids.get(key)
                if current is node:
                    continue
                if current is None:
                    self._ids[key] = node
                    self._reserved.discard(key)
                    continue
                identifier = self._resolve_conflict(key, node, "duplicate")
                attribs["id"] = identifier
                renames[key] = identifier

        if renames:
            # Object references are resolved through the referenced object, so
            # they follow a renamed target automatically. Only plain string
            # references have to be rewritten explicitly.
            refresh_object_references(nodes)
            rewrite_references(root, renames)
        return renames

    def _preflight_conflicts(self, nodes):
        """Raise before any element is modified if a conflict is detected."""
        incoming = set()
        with self._lock:
            for node in nodes:
                attribs = getattr(node, "attribs", None)
                if attribs is None or "id" not in attribs:
                    continue
                key = attribute_key(attribs["id"])
                current = self._ids.get(key)
                if current is not None and current is not node:
                    raise IDConflictError(
                        "ID '%s' is already in use in this document." % key
                    )
                if key in incoming:
                    raise IDConflictError(
                        "ID '%s' is used by more than one element." % key
                    )
                incoming.add(key)


def rewrite_references(root, renames):
    """Rewrite string IRI references in *root* and all subelements.

    *renames* maps old IDs (as returned by :func:`attribute_key`) to new IDs.
    Handles ``#id`` references, ``url(#id)`` FuncIRIs and animation timing
    identifiers in ``begin``/``end`` attributes.
    """
    if not renames:
        return
    for node in _walk_document(root):
        href = getattr(node, "href", None)
        if isinstance(href, str) and href.startswith("#") and href[1:] in renames:
            # Keep the cached reference in sync, otherwise update_id() would
            # restore the old ID when the element is serialized.
            node.href = "#" + renames[href[1:]]
        attribs = getattr(node, "attribs", None)
        if not attribs:
            continue
        for name, value in list(attribs.items()):
            if name == "id" or not isinstance(value, str):
                continue
            new_value = _rewrite_value(name, value, renames)
            if new_value != value:
                attribs[name] = new_value


def refresh_object_references(nodes):
    """Re-evaluate object based references (``href``) of *nodes*.

    Elements like ``Use``, ``TextPath`` or gradients may keep the referenced
    object instead of a string. If that object received a new ID, the cached
    ``xlink:href`` attribute has to be refreshed.
    """
    for node in nodes:
        update = getattr(node, "update_id", None)
        if update is not None:
            update()


def _rewrite_value(name, value, renames):
    if not value:
        return value
    if value.startswith("#") and value[1:] in renames:
        return "#" + renames[value[1:]]
    if "url(" in value:
        value = _IRI_ATTRIBUTE.sub(
            lambda match: "url(#%s)" % renames.get(match.group(1), match.group(1)),
            value,
        )
    if name in _TIMING_ATTRIBUTES:
        value = _rewrite_timing(value, renames)
    return value


def _rewrite_timing(value, renames):
    """Rewrite animation timing identifiers in a single pass.

    A single pass avoids cascading replacements when a new ID is also used as
    an old ID by another entry.
    """
    if not renames:
        return value
    alternatives = "|".join(
        _TIMING_IDENTIFIER_TEMPLATE.format(re.escape(old_id))
        for old_id in sorted(renames, key=len, reverse=True)
    )

    def replace(match):
        identifier = match.group(0)
        return renames.get(identifier, identifier)

    return re.sub(alternatives, replace, value)
