#!/usr/bin/env python
#coding:utf-8
# Author:  mozman --<mozman@gmx.at>
# Purpose: test document editing transactions
# Created: 22.11.2026
# Copyright (C) 2026, Manfred Moitzi
# License: MIT License
from __future__ import unicode_literals

import threading
import unittest

import svgwrite
from svgwrite import Drawing, Transaction, TransactionError
from svgwrite.container import Group, Symbol
from svgwrite.transaction import (
    _registry, TrackingDict, TrackingList,
)
from svgwrite.utils import AutoID


class TransactionTestBase(unittest.TestCase):
    def setUp(self):
        AutoID._set_value(1)
        self.dwg = Drawing()
        self.group = self.dwg.add(Group(id='level1'))
        self.before = self.dwg.tostring()

    def assertTreeRestored(self):
        self.assertEqual(self.dwg.tostring(), self.before)


class TestCommitAndRollback(TransactionTestBase):
    def test_multi_step_commit_visible_at_once(self):
        with self.dwg.transaction() as txn:
            self.assertFalse(txn.closed)
            symbol = self.dwg.defs.add(self.dwg.symbol(id='icon'))
            symbol.add(self.dwg.rect(insert=(0, 0), size=(5, 5)))
            use = self.dwg.add(self.dwg.use(symbol, insert=(10, 10)))
            self.group['stroke'] = 'red'
        xml = self.dwg.tostring()
        self.assertIn('id="icon"', xml)
        self.assertIn('xlink:href="#icon"', xml)
        self.assertIn('stroke="red"', xml)
        self.assertIn(use, self.dwg.elements)

    def test_exception_rolls_back_tree_attributes_and_ids(self):
        nextid = AutoID._nextid
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.dwg.defs.add(self.dwg.symbol(id='icon'))
                self.dwg.add(self.dwg.circle(center=(1, 1), r=2))
                self.group['stroke'] = 'red'
                raise RuntimeError("validation failed midway")
        self.assertTreeRestored()
        self.assertEqual(AutoID._nextid, nextid)
        self.assertNotIn('stroke', self.group.attribs)

    def test_explicit_rollback(self):
        txn = self.dwg.transaction().begin()
        self.group['fill'] = 'blue'
        self.dwg.add(self.dwg.line(start=(0, 0), end=(1, 1)))
        txn.rollback()
        self.assertTrue(txn.closed)
        self.assertTreeRestored()

    def test_context_manager_does_not_swallow_exception(self):
        class Boom(Exception):
            pass
        with self.assertRaises(Boom):
            with self.dwg.transaction():
                raise Boom()
        self.assertTreeRestored()

    def test_rollback_after_commit_rejected(self):
        txn = self.dwg.transaction().begin()
        self.group['fill'] = 'green'
        txn.commit()
        with self.assertRaises(TransactionError):
            txn.rollback()
        with self.assertRaises(TransactionError):
            txn.commit()
        # committed edit persists
        self.assertEqual(self.group['fill'], 'green')

    def test_begin_twice_rejected(self):
        txn = self.dwg.transaction().begin()
        with self.assertRaises(TransactionError):
            txn.begin()
        txn.rollback()


class TestAttributeAndStructureUndo(TransactionTestBase):
    def test_direct_dict_and_list_writes_are_recorded(self):
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.group.attribs['fill'] = 'blue'
                del self.group.attribs['id']
                self.dwg.elements.append(
                    self.dwg.path('M 0 0 L 1 1'))
                raise RuntimeError("abort")
        self.assertTreeRestored()
        self.assertEqual(self.group.attribs.get('id'), 'level1')

    def test_update_pop_setdefault_clear_on_attribs(self):
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.group.attribs.update({'opacity': '0.5', 'fill': 'red'})
                self.assertEqual(
                    self.group.attribs.setdefault('opacity', '0.9'), '0.5')
                self.group.attribs.pop('opacity')
                self.group.attribs['temp'] = 'x'
                del self.group.attribs['temp']
                self.group.attribs.clear()
                raise RuntimeError("abort")
        self.assertTreeRestored()
        self.assertEqual(self.group.attribs.get('id'), 'level1')

    def test_popitem_returns_pair_and_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.group.attribs['extra'] = 'x'
                key, value = self.group.attribs.popitem()
                self.assertEqual((key, value), ('id', 'level1'))
                self.assertEqual(self.group.attribs.pop('nope', 'dflt'), 'dflt')
                raise RuntimeError("abort")
        self.assertTreeRestored()
        self.assertEqual(self.group.attribs['id'], 'level1')

    def test_list_pop_remove_slice_and_clear(self):
        child = self.group.add(self.dwg.rect(id='child'))
        baseline = self.dwg.tostring()
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                extra = [self.dwg.rect(id='e%d' % i) for i in range(4)]
                self.group.elements.extend(extra)
                # list is [child, e0, e1, e2, e3]
                self.group.elements.pop()          # removes e3
                del self.group.elements[1:2]        # removes e0
                self.group.elements.remove(extra[1])  # removes e1
                raise RuntimeError("abort")
        self.assertEqual(self.dwg.tostring(), baseline)
        self.assertEqual(list(self.group.elements), [child])

    def test_remove_and_re_add_element(self):
        child = self.group.add(self.dwg.rect(id='child'))
        baseline = self.dwg.tostring()
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.group.remove(child)
                self.dwg.add(child)
                raise RuntimeError("abort")
        self.assertEqual(self.dwg.tostring(), baseline)
        self.assertIs(self.group.elements[0], child)

    def test_remove_missing_raises_value_error(self):
        outsider = self.dwg.rect(id='outsider')
        with self.assertRaises(ValueError):
            self.group.remove(outsider)

    def test_element_delitem_and_indexed_insert(self):
        self.group['fill'] = 'red'
        del self.group['fill']
        self.assertNotIn('fill', self.group.attribs)
        with self.assertRaises(KeyError):
            del self.group['no-such-attr']
        first = self.group.add(self.dwg.rect(id='first'), index=0)
        second = self.group.add(self.dwg.rect(id='second'))
        self.assertIs(self.group.elements[0], first)
        self.assertIs(self.group.elements[-1], second)


class TestIdentityAndReferences(TransactionTestBase):
    def test_pre_existing_element_identity_stable(self):
        defs = self.dwg.defs
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.group['fill'] = 'red'
                self.dwg.add(self.dwg.g())
                raise RuntimeError("abort")
        self.assertIs(self.dwg.elements[1], self.group)
        self.assertIs(self.dwg.defs, defs)

    def test_shared_reference_kept_and_restored(self):
        symbol = self.dwg.defs.add(Symbol(id='sym'))
        use_a = self.dwg.add(self.dwg.use(symbol, insert=(0, 0)))
        use_b = self.dwg.add(self.dwg.use(symbol, insert=(5, 5)))
        baseline = self.dwg.tostring()
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                symbol['id'] = 'renamed'
                use_a.update_id()
                use_b.update_id()
                raise RuntimeError("abort")
        self.assertEqual(self.dwg.tostring(), baseline)
        with self.dwg.transaction():
            symbol['id'] = 'renamed'
            use_a.update_id()
            use_b.update_id()
        xml = self.dwg.tostring()
        self.assertEqual(xml.count('xlink:href="#renamed"'), 2)

    def test_nested_groups_and_defs_walked(self):
        nested = self.dwg.add(self.dwg.svg(insert=(0, 0), size=(10, 10)))
        inner_group = nested.add(Group(id='inner'))
        baseline = self.dwg.tostring()
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                nested.defs.add(self.dwg.linearGradient(id='grad'))
                inner_group['fill'] = 'black'
                nested.add(self.dwg.rect(id='n'))
                raise RuntimeError("abort")
        self.assertEqual(self.dwg.tostring(), baseline)
        self.assertIs(nested.elements[0], nested.defs)


class TestAutoID(TransactionTestBase):
    def test_ids_handed_back_on_rollback(self):
        AutoID._set_value(500)
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.dwg.defs.add(self.dwg.symbol()).get_id()
                self.dwg.add(self.dwg.g()).get_id()
                raise RuntimeError("abort")
        self.assertEqual(AutoID._nextid, 500)

    def test_retry_produces_stable_ids(self):
        AutoID._set_value(700)
        txn = self.dwg.transaction().begin()
        first = self.dwg.defs.add(self.dwg.symbol()).get_id()
        txn.rollback()
        with self.dwg.transaction():
            second = self.dwg.defs.add(self.dwg.symbol()).get_id()
        self.assertEqual(first, second)
        self.assertEqual(first, 'id700')

    def test_committed_ids_are_not_reused(self):
        with self.dwg.transaction():
            committed = self.dwg.add(self.dwg.g()).get_id()
        nextid_after_commit = AutoID._nextid
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.dwg.add(self.dwg.g()).get_id()
                raise RuntimeError("abort")
        self.assertEqual(AutoID._nextid, nextid_after_commit)
        self.assertNotEqual(self.dwg.add(self.dwg.g()).get_id(), committed)


class TestMutableStateSnapshots(TransactionTestBase):
    def test_path_commands_rollback(self):
        path = self.dwg.add(self.dwg.path('M 0 0'))
        baseline = self.dwg.tostring()
        original_commands = list(path.commands)
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                path.push('L', 5, 5)
                raise RuntimeError("abort")
        self.assertEqual(path.commands, original_commands)
        self.assertEqual(self.dwg.tostring(), baseline)

    def test_polyline_points_rollback(self):
        poly = self.dwg.add(self.dwg.polyline(points=[(0, 0)]))
        baseline = self.dwg.tostring()
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                poly.points.append((3, 3))
                raise RuntimeError("abort")
        self.assertEqual(poly.points, [(0, 0)])
        self.assertEqual(self.dwg.tostring(), baseline)

    def test_script_content_and_stylesheets_rollback(self):
        script = self.dwg.embed_stylesheet("base")
        baseline = self.dwg.tostring()
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                script.append("/* extra */")
                self.dwg.add_stylesheet('extra.css', 'Extra')
                raise RuntimeError("abort")
        self.assertEqual(script._content, "base")
        self.assertEqual(self.dwg._stylesheets, [])
        self.assertEqual(self.dwg.tostring(), baseline)


class TestTransactionConflicts(TransactionTestBase):
    def test_nested_transaction_rejected(self):
        with self.assertRaises(TransactionError):
            with self.dwg.transaction():
                with self.dwg.transaction():
                    pass
        # outer transaction still usable
        self.group['fill'] = 'red'
        self.assertEqual(self.group['fill'], 'red')

    def test_concurrent_transaction_same_document_rejected(self):
        errors = []

        def worker():
            try:
                with self.dwg.transaction():
                    errors.append("should not happen")
            except TransactionError:
                errors.append("blocked")

        with self.dwg.transaction():
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        self.assertEqual(errors, ["blocked"])

    def test_cross_thread_mutation_rejected(self):
        errors = []

        def writer():
            try:
                self.group['fill'] = 'blue'
            except TransactionError:
                errors.append("blocked")

        with self.dwg.transaction():
            thread = threading.Thread(target=writer)
            thread.start()
            thread.join()
        self.assertEqual(errors, ["blocked"])
        self.assertNotIn('fill', self.group.attribs)

    def test_independent_documents_get_independent_transactions(self):
        other = Drawing()
        errors = []

        def worker():
            try:
                with other.transaction():
                    other.add(other.g())
                    errors.append("ok")
            except TransactionError as exc:
                errors.append(str(exc))

        with self.dwg.transaction():
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        self.assertEqual(errors, ["ok"])

    def test_element_adopted_by_other_transaction_rejected(self):
        other = Drawing()
        shared = self.dwg.rect(id='shared')
        errors = []
        with self.dwg.transaction():
            self.dwg.add(shared)

            def worker():
                try:
                    with other.transaction():
                        other.add(shared)
                except TransactionError:
                    errors.append("blocked")

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        self.assertEqual(errors, ["blocked"])

    def test_registry_clean_after_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                added = self.group.add(self.dwg.rect(id='tmp'))
                self.group.remove(added)
                raise RuntimeError("abort")
        self.assertEqual(_registry._documents, {})
        self.assertEqual(_registry._elements, {})
        self.assertEqual(_registry.thread_stack(), [])


class TestLevelIconEditorScenario(TransactionTestBase):
    def _build_level(self):
        # defs symbol + layer group referencing it, like a level-icon editor
        symbol = self.dwg.defs.add(self.dwg.symbol(id='icon'))
        symbol.add(self.dwg.rect(insert=(0, 0), size=(8, 8)))
        layer = self.dwg.add(Group(id='layer'))
        layer.add(self.dwg.use(symbol, insert=(0, 0)))
        return symbol, layer

    def _edit_level(self, symbol, layer, fail=False):
        with self.dwg.transaction():
            symbol.add(self.dwg.circle(center=(4, 4), r=2))
            new_use = layer.add(self.dwg.use(symbol, insert=(16, 0)))
            layer['opacity'] = '0.8'
            if fail:
                raise ValueError("validator rejected the new icon")
            return new_use

    def test_failure_midway_rolls_back_defs_layer_and_references(self):
        symbol, layer = self._build_level()
        baseline = self.dwg.tostring()
        with self.assertRaises(ValueError):
            self._edit_level(symbol, layer, fail=True)
        self.assertEqual(self.dwg.tostring(), baseline)
        # no orphan ids leaked from the failed attempt
        self.assertNotIn('circle', self.dwg.tostring())

    def test_retry_after_failure_succeeds(self):
        symbol, layer = self._build_level()
        with self.assertRaises(ValueError):
            self._edit_level(symbol, layer, fail=True)
        new_use = self._edit_level(symbol, layer, fail=False)
        xml = self.dwg.tostring()
        self.assertIn('<circle', xml)
        self.assertEqual(xml.count('xlink:href="#icon"'), 2)
        self.assertEqual(layer['opacity'], '0.8')
        self.assertIn(new_use, layer.elements)
        # the symbol gained exactly one circle and kept its original rect
        self.assertEqual(len(symbol.elements), 2)
        self.assertEqual(symbol.elements[0].elementname, 'rect')
        self.assertEqual(symbol.elements[1].elementname, 'circle')


class TestDetachedElementsAndLegacyAPI(TransactionTestBase):
    def test_detached_element_edits_are_not_tracked(self):
        detached = Group()
        with self.dwg.transaction():
            detached['opacity'] = 0.9
        self.assertEqual(detached['opacity'], 0.9)

    def test_legacy_behavior_without_transaction(self):
        # containers still quack like dict/list
        self.assertIsInstance(self.group.attribs, dict)
        self.assertIsInstance(self.group.elements, list)
        self.assertIsInstance(self.group.attribs, TrackingDict)
        self.assertIsInstance(self.group.elements, TrackingList)
        group_copy = self.group.copy()
        self.assertIsInstance(group_copy.attribs, TrackingDict)
        self.assertIsInstance(group_copy.elements, TrackingList)
        group_copy['id'] = group_copy.next_id()
        self.assertNotEqual(group_copy['id'], self.group['id'])

    def test_non_transactional_writes_still_work(self):
        self.group['fill'] = 'red'
        del self.group.attribs['id']
        rect = self.dwg.rect(id='plain')
        self.dwg.add(rect)
        self.dwg.elements.remove(rect)
        self.assertNotIn(rect, self.dwg.elements)

    def test_tostring_inside_transaction_shows_live_edits(self):
        with self.assertRaises(RuntimeError):
            with self.dwg.transaction():
                self.group['fill'] = 'red'
                # read-your-writes: live edits are serialized during the txn
                self.assertIn('fill="red"', self.dwg.tostring())
                raise RuntimeError("abort")
        self.assertTreeRestored()


if __name__ == '__main__':
    unittest.main()
