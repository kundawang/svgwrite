#!/usr/bin/env python
#coding:utf-8
# Author:  mozman
# Purpose: document editing transactions
# Created: 22.11.2026
# Copyright (C) 2026, Manfred Moitzi
# License: MIT License
"""
Document editing transactions
=============================

A :class:`Transaction` wraps a set of edits to a
:class:`svgwrite.Drawing` object tree so that they either all become permanent
(:meth:`Transaction.commit`) or are all undone
(:meth:`Transaction.rollback`, or an exception escaping the ``with`` block).

Typical usage::

    with dwg.transaction():
        symbol = dwg.defs.add(dwg.symbol(id='icon'))
        symbol.add(dwg.rect(insert=(0, 0), size=(10, 10)))
        dwg.add(dwg.use(symbol, insert=(20, 20)))
        dwg['data-name'] = 'level-icon'
    # all four changes are visible together after the block

Semantics
---------

* **Live edits, atomic outcome.** Mutations inside a transaction are applied to
  the live objects immediately (read-your-writes, so the existing fluent API
  keeps working) and are recorded. On commit the record is discarded; on
  rollback every action is undone in reverse order, restoring the original
  tree, attribute values, shared references and the :class:`AutoID` counter.
* **Identity stability.** Elements that existed before the transaction are
  never replaced or copied -- only their ``attribs``/``elements`` containers
  are restored. Elements created inside a rolled-back transaction are removed
  from the tree again.
* **Automatic IDs.** IDs consumed inside a rolled-back transaction are handed
  back to :class:`AutoID`; retried transactions therefore produce the same IDs
  a fresh run would, and never collide with IDs that escaped a failed attempt.
* **One transaction per document.** Opening a second transaction for the same
  document (also from another thread) raises :class:`TransactionError`.
* **No nested transactions.** Opening a transaction while another one is open
  in the same thread raises :class:`TransactionError`.
* **Writes while a transaction is open.** From the owning thread, mutations
  implicitly join the open transaction and are rolled back with it. From any
  other thread they raise :class:`TransactionError`. With no transaction open,
  the legacy non-transactional API behaves exactly as before.
* Edits on detached elements (not reachable from a document with an open
  transaction) are not part of any transaction.
"""
from __future__ import unicode_literals, absolute_import

import threading

from svgwrite.utils import AutoID


class TransactionError(RuntimeError):
    """Invalid transaction usage.

    Raised for nested transactions, concurrent transactions on the same
    document, cross-thread mutations of a document that has an open
    transaction, and operating on a transaction that has already been closed.
    """


def _is_base_element(obj):
    # duck-typed check to avoid a circular import with svgwrite.base
    return hasattr(obj, 'attribs') and hasattr(obj, 'elements')


def _walk_elements(element):
    """Yield *element* and all BaseElement descendants in document order."""
    yield element
    for child in element.elements:
        if _is_base_element(child):
            for sub in _walk_elements(child):
                yield sub


class _Registry(object):
    """Process-wide bookkeeping of open transactions."""

    def __init__(self):
        self._lock = threading.RLock()
        self._local = threading.local()
        # id(document) -> Transaction
        self._documents = {}
        # id(element) -> owning Transaction
        self._elements = {}

    def thread_stack(self):
        stack = getattr(self._local, 'stack', None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def begin(self, transaction):
        docid = id(transaction.document)
        with self._lock:
            if self.thread_stack():
                raise TransactionError(
                    "Nested transactions are not supported; commit or rollback "
                    "the open transaction first")
            existing = self._documents.get(docid)
            if existing is not None:
                raise TransactionError(
                    "A transaction for this document is already open in "
                    "thread %r" % existing.thread_id)
            self._documents[docid] = transaction
            for element in _walk_elements(transaction.document):
                self._elements[id(element)] = transaction
                transaction._take_snapshot(element)
            self.thread_stack().append(transaction)

    def finish(self, transaction):
        docid = id(transaction.document)
        with self._lock:
            for element in _walk_elements(transaction.document):
                # only remove mappings still owned by this transaction
                if self._elements.get(id(element)) is transaction:
                    del self._elements[id(element)]
            # elements added during the transaction but removed again before
            # close are no longer reachable from the document
            for eid in transaction.owned_element_ids:
                if self._elements.get(eid) is transaction:
                    del self._elements[eid]
            if self._documents.get(docid) is transaction:
                del self._documents[docid]
            stack = self.thread_stack()
            if stack and stack[-1] is transaction:
                stack.pop()

    def register(self, transaction, element):
        """Register *element* (and its subtree) as owned by *transaction*."""
        with self._lock:
            for sub in _walk_elements(element):
                eid = id(sub)
                owner = self._elements.get(eid)
                if owner is transaction:
                    continue
                if owner is not None:
                    raise TransactionError(
                        "Element %r is already part of another open "
                        "transaction" % sub)
                self._elements[eid] = transaction
                transaction.owned_element_ids.add(eid)
                transaction._take_snapshot(sub)

    def owner_of(self, element):
        """Return the open transaction owning *element*, or ``None``.

        Raises :class:`TransactionError` if the element is owned by a
        transaction open in another thread.
        """
        with self._lock:
            owner = self._elements.get(id(element))
        if owner is None:
            return None
        if owner.thread_id != threading.current_thread().ident:
            raise TransactionError(
                "Cannot mutate %r: its document has an open transaction in "
                "another thread" % element)
        return owner


# process-wide singleton registry
_registry = _Registry()


class Transaction(object):
    """An atomic set of edits on one :class:`svgwrite.Drawing`.

    Use :meth:`svgwrite.Drawing.transaction` to create one::

        with dwg.transaction() as txn:
            ...
            if something_is_wrong:
                txn.rollback()

    The transaction can also be driven manually with :meth:`begin`,
    :meth:`commit` and :meth:`rollback`.
    """

    def __init__(self, document):
        self.document = document
        self.thread_id = threading.current_thread().ident
        self._undo = []          # reverse-ordered undo actions
        self.owned_element_ids = set()
        # id(element) -> (state_name, copied value) for mutable state living
        # outside element.attribs (Path.commands, Polyline.points, ...)
        self._state_snapshots = {}
        self._saved_nextid = AutoID._nextid
        self._closed = False
        self._active = False

    @staticmethod
    def _snapshot_state(element):
        """Return a dict copy of the element's transaction-tracked state."""
        names = getattr(element, '_transaction_state', ())
        if not names:
            return None
        snapshot = {}
        for name in names:
            value = getattr(element, name)
            # strings and tuples are immutable; lists/tuples of coordinates
            # may be mutated in place and therefore need a shallow copy
            snapshot[name] = list(value) if isinstance(value, list) else value
        return snapshot

    def _take_snapshot(self, element):
        snapshot = self._snapshot_state(element)
        if snapshot is not None and id(element) not in self._state_snapshots:
            self._state_snapshots[id(element)] = snapshot

    @property
    def closed(self):
        """``True`` after :meth:`commit` or :meth:`rollback`."""
        return self._closed

    def _check_open(self, action):
        if self._closed:
            raise TransactionError(
                "Cannot %s a transaction that has already been closed" % action)
        if not self._active:
            raise TransactionError(
                "Transaction has not been started; use it as a context "
                "manager or call begin()")

    def begin(self):
        """Start the transaction. Called automatically by ``__enter__``."""
        if self._active:
            raise TransactionError("Transaction is already open")
        _registry.begin(self)
        self._active = True
        return self

    def commit(self):
        """Make all recorded edits permanent and close the transaction."""
        self._check_open('commit')
        self._closed = True
        self._active = False
        self._undo = []
        _registry.finish(self)

    def rollback(self):
        """Undo all recorded edits and close the transaction."""
        self._check_open('rollback')
        self._closed = True
        self._active = False
        try:
            # undo actions manipulate the objects directly, bypassing the
            # hooks, so they cannot re-enter the closing transaction
            while self._undo:
                self._undo.pop()()
            # restore mutable state that lives outside element.attribs
            for element in _walk_elements(self.document):
                snapshot = self._state_snapshots.get(id(element))
                if snapshot is not None:
                    for name, value in snapshot.items():
                        setattr(element, name, value)
            AutoID._set_value(self._saved_nextid)
        finally:
            _registry.finish(self)

    # context manager protocol
    def __enter__(self):
        return self.begin()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._closed:
            return None
        if exc_type is None:
            self.commit()
        else:
            # never swallow the original exception
            self.rollback()
        return None

    # -- hooks used by the tracked mutation helpers ------------------------

    def record(self, undo_action):
        """Record a zero-argument callable that undoes one mutation."""
        if self._closed:
            raise TransactionError(
                "Cannot record edits on a closed transaction")
        self._undo.append(undo_action)

    def register(self, element):
        """Register a newly attached element (and subtree) with the txn."""
        _registry.register(self, element)


# ---------------------------------------------------------------------------
# Transaction-aware containers
#
# BaseElement.attribs / .elements are public storage attributes and legacy
# code (and users) mutate them directly, so interception happens inside dict
# and list subclasses rather than only in wrapper methods. Outside an open
# transaction these behave exactly like the built-in containers.
# ---------------------------------------------------------------------------

def _owner(element):
    """Open transaction owning *element*, enforcing the cross-thread rule."""
    return _registry.owner_of(element)


class TrackingDict(dict):
    """:class:`dict` subclass recording every mutation in the owning txn."""

    def __init__(self, owner):
        super(TrackingDict, self).__init__()
        self._owner_element = owner

    def _txn(self):
        return _owner(self._owner_element)

    def __setitem__(self, key, value):
        txn = self._txn()
        if txn is None:
            return super(TrackingDict, self).__setitem__(key, value)
        existed = key in self
        old_value = self.get(key)
        ref = self._owner_element

        def undo():
            if existed:
                dict.__setitem__(ref.attribs, key, old_value)
            else:
                dict.pop(ref.attribs, key, None)

        txn.record(undo)
        super(TrackingDict, self).__setitem__(key, value)

    def __delitem__(self, key):
        txn = self._txn()
        if txn is None:
            return super(TrackingDict, self).__delitem__(key)
        if key not in self:
            raise KeyError(key)
        old_value = self[key]
        ref = self._owner_element

        def undo():
            dict.__setitem__(ref.attribs, key, old_value)

        txn.record(undo)
        super(TrackingDict, self).__delitem__(key)

    def pop(self, key, *default):
        txn = self._txn()
        if txn is None:
            return super(TrackingDict, self).pop(key, *default)
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        old_value = self[key]
        ref = self._owner_element

        def undo():
            dict.__setitem__(ref.attribs, key, old_value)

        txn.record(undo)
        return super(TrackingDict, self).pop(key, *default)

    def popitem(self):
        txn = self._txn()
        if txn is None:
            return super(TrackingDict, self).popitem()
        if not self:
            raise KeyError('popitem(): dictionary is empty')
        key = next(iter(self))
        old_value = self[key]
        ref = self._owner_element

        def undo():
            dict.__setitem__(ref.attribs, key, old_value)

        txn.record(undo)
        super(TrackingDict, self).pop(key)
        return key, old_value

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return default

    def update(self, *args, **kwargs):
        # route through __setitem__ so every key is recorded individually
        data = dict(*args, **kwargs)
        for key, value in data.items():
            self[key] = value

    def clear(self):
        txn = self._txn()
        if txn is None:
            return super(TrackingDict, self).clear()
        items = list(self.items())
        ref = self._owner_element

        def undo():
            for key, value in items:
                dict.__setitem__(ref.attribs, key, value)

        txn.record(undo)
        super(TrackingDict, self).clear()

    def copy(self):
        # a plain dict copy, matching the legacy dict.copy() type
        return dict(self)


class TrackingList(list):
    """:class:`list` subclass recording structural mutations in the txn."""

    def __init__(self, owner):
        super(TrackingList, self).__init__()
        self._owner_element = owner

    def _txn(self):
        return _owner(self._owner_element)

    def _register(self, value):
        txn = self._txn()
        if txn is not None and _is_base_element(value):
            txn.register(value)

    def append(self, value):
        self._register(value)
        txn = self._txn()
        if txn is not None:
            ref = self._owner_element

            def undo():
                list.__delitem__(ref.elements,
                                  list.index(ref.elements, value))

            txn.record(undo)
        super(TrackingList, self).append(value)

    def insert(self, index, value):
        self._register(value)
        txn = self._txn()
        if txn is not None:
            ref = self._owner_element

            def undo():
                list.__delitem__(ref.elements,
                                  list.index(ref.elements, value))

            txn.record(undo)
        super(TrackingList, self).insert(index, value)

    def extend(self, iterable):
        for value in iterable:
            self.append(value)

    def __setitem__(self, index, value):
        txn = self._txn()
        if txn is None:
            return super(TrackingList, self).__setitem__(index, value)
        if isinstance(index, slice):
            old_values = list(self[index])
            new_values = list(value)
            for item in new_values:
                self._register(item)
            ref = self._owner_element

            def undo():
                list.__setitem__(ref.elements, index, old_values)

            txn.record(undo)
            return super(TrackingList, self).__setitem__(index, new_values)
        old_value = self[index]
        self._register(value)
        ref = self._owner_element

        def undo():
            list.__setitem__(ref.elements, index, old_value)

        txn.record(undo)
        super(TrackingList, self).__setitem__(index, value)

    def __delitem__(self, index):
        txn = self._txn()
        if txn is None:
            return super(TrackingList, self).__delitem__(index)
        if isinstance(index, slice):
            old_values = list(self[index])
            start = index.indices(len(self))[0]
            ref = self._owner_element

            def undo():
                # re-insert the removed slice in original order
                for offset, item in enumerate(old_values):
                    list.insert(ref.elements, start + offset, item)

            txn.record(undo)
            return super(TrackingList, self).__delitem__(index)
        old_value = self[index]
        pos = index if index >= 0 else len(self) + index
        ref = self._owner_element

        def undo():
            list.insert(ref.elements, pos, old_value)

        txn.record(undo)
        super(TrackingList, self).__delitem__(index)

    def remove(self, value):
        txn = self._txn()
        if txn is None:
            return super(TrackingList, self).remove(value)
        pos = self.index(value)  # ValueError if absent
        ref = self._owner_element

        def undo():
            list.insert(ref.elements, pos, value)

        txn.record(undo)
        super(TrackingList, self).remove(value)

    def pop(self, index=-1):
        txn = self._txn()
        if txn is None:
            return super(TrackingList, self).pop(index)
        pos = index if index >= 0 else len(self) + index
        value = self[index]  # IndexError if out of range
        ref = self._owner_element

        def undo():
            list.insert(ref.elements, pos, value)

        txn.record(undo)
        return super(TrackingList, self).pop(index)

    def clear(self):
        txn = self._txn()
        if txn is None:
            return super(TrackingList, self).clear()
        old_values = list(self)
        ref = self._owner_element

        def undo():
            list.extend(ref.elements, old_values)

        txn.record(undo)
        super(TrackingList, self).clear()

    def sort(self, *args, **kwargs):
        txn = self._txn()
        if txn is None:
            return super(TrackingList, self).sort(*args, **kwargs)
        old_order = list(self)
        ref = self._owner_element

        def undo():
            list.__setitem__(ref.elements, slice(None), old_order)

        txn.record(undo)
        super(TrackingList, self).sort(*args, **kwargs)

    def reverse(self):
        txn = self._txn()
        if txn is None:
            return super(TrackingList, self).reverse()
        old_order = list(self)
        ref = self._owner_element

        def undo():
            list.__setitem__(ref.elements, slice(None), old_order)

        txn.record(undo)
        super(TrackingList, self).reverse()
