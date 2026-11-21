#!/usr/bin/env python
# coding: utf-8
# Purpose: tests for per-document ID management
# License: MIT License
"""Tests for :mod:`svgwrite.idscope` and the scope aware ID handling.

The tests cover the four acceptance areas:

1. independent ID scope per drawing, auto IDs avoid explicit IDs, legacy
   behavior when the scope is disabled,
2. references (defs, use, textPath, gradients, markers) stay consistent,
   also when elements are moved between documents,
3. concurrent generation, copying, remove/re-add and serialization order
   never create duplicate or non deterministic IDs,
4. interleaved construction of two documents, hand written conflicts and
   reference chains.
"""

import re
import threading
import unittest

from svgwrite.base import BaseElement
from svgwrite.container import Group, Marker, Use
from svgwrite.drawing import Drawing
from svgwrite.gradients import LinearGradient
from svgwrite.idscope import (
    IDScope,
    IDConflict,
    IDConflictError,
    attribute_key,
)
from svgwrite.mixins import Markers
from svgwrite.path import Path
from svgwrite.pattern import Pattern
from svgwrite.text import Text, TextPath
from svgwrite.utils import AutoID


def ids_of(document):
    return re.findall(r' id="([^"]+)"', document.tostring())


class TestIndependentScope(unittest.TestCase):
    def setUp(self):
        # A populated legacy counter must not influence scoped drawings.
        AutoID(5000)

    def test_drawings_have_separate_counters(self):
        first = Drawing()
        second = Drawing()
        for _ in range(3):
            first.add(first.g()).get_id()
            second.add(second.g()).get_id()
        self.assertEqual([e['id'] for e in first.elements[1:]], ['id1', 'id2', 'id3'])
        self.assertEqual([e['id'] for e in second.elements[1:]], ['id1', 'id2', 'id3'])

    def test_interleaved_construction_is_reproducible(self):
        def build():
            left = Drawing()
            right = Drawing()
            for index in range(4):
                left.add(left.g())
                right.add(right.g())
                left.add(left.use(left.path(d='M0,0 L1,1')))
                right.add(right.use(right.path(d='M1,1 L2,2')))
            return left.tostring(), right.tostring()

        expected = build()
        for _ in range(5):
            self.assertEqual(build(), expected)

    def test_auto_ids_avoid_explicit_ids(self):
        dwg = Drawing()
        dwg.add(Group(id='id1'))
        dwg.add(Group(id='id3'))
        first = dwg.add(dwg.g())
        self.assertEqual(first.get_id(), 'id2')
        self.assertEqual(dwg.add(dwg.g()).get_id(), 'id4')
        self.assertEqual(len(set(ids_of(dwg))), len(ids_of(dwg)))

    def test_auto_ids_avoid_explicit_ids_added_later(self):
        dwg = Drawing()
        dwg.add(dwg.g()).get_id()  # id1
        dwg.add(Group(id='id2'))
        self.assertEqual(dwg.add(dwg.g()).get_id(), 'id3')

    def test_explicit_id_kept(self):
        dwg = Drawing()
        group = dwg.add(Group(id='keep-me'))
        self.assertEqual(group['id'], 'keep-me')
        self.assertIn('keep-me', dwg.id_scope.ids)

    def test_legacy_behavior_without_scope(self):
        dwg = Drawing(id_scope=False)
        self.assertIsNone(dwg.id_scope)
        AutoID(700)
        first = dwg.add(dwg.g())
        self.assertEqual(first.get_id(), 'id700')
        second = dwg.add(dwg.g())
        self.assertEqual(second.get_id(), 'id701')

    def test_detached_elements_keep_legacy_counter(self):
        AutoID(800)
        group = Group()
        self.assertEqual(group.get_id(), 'id800')

    def test_custom_scope_can_be_shared_explicitly(self):
        scope = IDScope()
        first = Drawing(id_scope=scope)
        second = Drawing(id_scope=scope)
        self.assertEqual(first.add(first.g()).get_id(), 'id1')
        self.assertEqual(second.add(second.g()).get_id(), 'id2')


class TestReferences(unittest.TestCase):
    def test_defs_and_use(self):
        dwg = Drawing()
        shape = dwg.defs.add(dwg.g())
        use = dwg.add(dwg.use(shape))
        self.assertEqual(shape.get_id(), 'id1')
        self.assertEqual(use['xlink:href'], '#id1')

    def test_use_with_scope_allocated_object(self):
        dwg = Drawing()
        shape = dwg.add(dwg.g())
        use = dwg.add(dwg.use(shape))
        self.assertEqual(use['xlink:href'], '#' + shape['id'])

    def test_text_path(self):
        dwg = Drawing()
        path = dwg.add(dwg.path(d='M0,0 L1,1'))
        text = dwg.add(Text('label'))
        text_path = text.add(dwg.textPath(path, 'label'))
        self.assertEqual(text_path['xlink:href'], '#' + path['id'])

    def test_gradient_reference(self):
        dwg = Drawing()
        gradient = dwg.defs.add(dwg.linearGradient())
        rect = dwg.add(dwg.rect((0, 0), (10, 10), fill=gradient.get_paint_server()))
        self.assertEqual(rect['fill'], 'url(#%s) none' % gradient['id'])
        self.assertIn('id="%s"' % gradient['id'], dwg.tostring())

    def test_gradient_inherit_object_tracks_id_change(self):
        dwg = Drawing()
        base = dwg.defs.add(dwg.linearGradient())
        derived = dwg.defs.add(dwg.linearGradient(inherit=base))
        self.assertEqual(derived['xlink:href'], '#' + base['id'])
        base['id'] = 'renamed'
        # update_id() is performed during serialization
        self.assertIn('xlink:href="#renamed"', dwg.tostring())

    def test_gradient_and_pattern_reference_chain(self):
        dwg = Drawing()
        base = dwg.defs.add(LinearGradient(id='base'))
        middle = dwg.defs.add(LinearGradient(inherit=base))
        top = dwg.defs.add(LinearGradient(inherit=middle))
        pattern = dwg.defs.add(Pattern(inherit=middle))
        self.assertEqual(middle['xlink:href'], '#base')
        self.assertEqual(top['xlink:href'], '#' + middle['id'])
        self.assertEqual(pattern['xlink:href'], '#' + middle['id'])

    def test_string_reference_chain_is_unchanged(self):
        dwg = Drawing()
        dwg.defs.add(LinearGradient(id='base'))
        dwg.defs.add(LinearGradient(id='middle', inherit='#base'))
        dwg.defs.add(LinearGradient(id='top', inherit='#middle'))
        self.assertEqual([g['xlink:href'] for g in dwg.defs.elements[1:]],
                         ['#base', '#middle'])

    def test_marker_reference(self):
        class Line(BaseElement, Markers):
            elementname = 'line'

        dwg = Drawing()
        marker = dwg.defs.add(dwg.marker())
        line = dwg.add(Line())
        line.set_markers(marker)
        self.assertEqual(line['marker'], 'url(#%s)' % marker['id'])

    def test_marker_with_explicit_id(self):
        class Line(BaseElement, Markers):
            elementname = 'line'

        dwg = Drawing()
        marker = dwg.defs.add(Marker(id='arrow'))
        line = dwg.add(Line())
        line.set_markers(marker)
        self.assertEqual(line['marker'], 'url(#arrow)')


class TestConflicts(unittest.TestCase):
    def test_explicit_collision_is_renamed_and_recorded(self):
        dwg = Drawing()
        dwg.add(Group(id='dup'))
        second = dwg.add(dwg.g())
        second['id'] = 'dup'
        self.assertEqual(second['id'], 'dup_1')
        conflicts = dwg.id_scope.conflicts
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertIsInstance(conflict, IDConflict)
        self.assertEqual((conflict.old_id, conflict.new_id, conflict.reason),
                         ('dup', 'dup_1', 'explicit'))
        self.assertIs(conflict.element, second)
        self.assertEqual(dwg.id_scope.ids, ('dup', 'dup_1'))

    def test_duplicate_subelement_id_is_renamed_and_recorded(self):
        dwg = Drawing()
        dwg.add(Group(id='dup'))
        dwg.add(Group(id='dup'))
        self.assertEqual(ids_of(dwg), ['dup', 'dup_1'])
        conflict = dwg.id_scope.conflicts[0]
        self.assertEqual(conflict.reason, 'duplicate')

    def test_raise_policy_reports_conflict(self):
        dwg = Drawing(id_scope=IDScope(conflict_policy='raise'))
        dwg.add(Group(id='x'))
        with self.assertRaises(IDConflictError):
            dwg.add(Group(id='x'))
        self.assertEqual(ids_of(dwg), ['x'])
        self.assertEqual(dwg.id_scope.conflicts, ())

    def test_raise_policy_does_not_modify_document(self):
        dwg = Drawing(id_scope=IDScope(conflict_policy='raise'))
        dwg.add(Group(id='x'))
        conflicting = Group(id='x')
        conflicting.add(Group(id='y'))
        with self.assertRaises(IDConflictError):
            dwg.add(conflicting)
        self.assertEqual(conflicting['id'], 'x')
        self.assertEqual(ids_of(dwg), ['x'])

    def test_invalid_conflict_policy(self):
        self.assertRaises(ValueError, IDScope, 'ignore')


class TestDeterminism(unittest.TestCase):
    def test_copy_generates_new_ids_for_whole_subtree(self):
        dwg = Drawing()
        original = dwg.add(Group(id='orig'))
        original.add(Group(id='inner'))
        duplicate = dwg.add(original.copy())
        self.assertNotEqual(duplicate['id'], 'orig')
        self.assertNotEqual(duplicate.elements[0]['id'], 'inner')
        self.assertEqual(len(set(ids_of(dwg))), 4)

    def test_copy_references_point_to_copied_targets(self):
        dwg = Drawing()
        original = dwg.add(Group(id='target'))
        original.add(Group())
        duplicate = original.copy()
        dwg.add(duplicate)
        use = dwg.add(dwg.use(duplicate))
        self.assertEqual(use['xlink:href'], '#' + duplicate['id'])
        self.assertNotEqual(use['xlink:href'], '#target')

    def test_copy_without_ids_does_not_consume_ids(self):
        element = Group()
        element.add(Group())
        copied = element.copy()
        self.assertNotIn('id', copied.attribs)
        self.assertNotIn('id', copied.elements[0].attribs)

    def test_copy_ids_are_registered_when_added(self):
        dwg = Drawing()
        original = dwg.add(Group(id='orig'))
        duplicate = original.copy()
        self.assertNotIn(duplicate['id'], dwg.id_scope.ids)
        dwg.add(duplicate)
        self.assertIn(duplicate['id'], dwg.id_scope.ids)
        self.assertEqual(dwg.id_scope.conflicts, ())

    def test_two_copies_never_share_an_id(self):
        dwg = Drawing()
        original = dwg.add(Group(id='orig'))
        first = original.copy()
        second = original.copy()
        self.assertNotEqual(first['id'], second['id'])
        dwg.add(first)
        dwg.add(second)
        self.assertEqual(len(set(ids_of(dwg))), len(ids_of(dwg)))

    def test_remove_then_readd_is_stable(self):
        dwg = Drawing()
        group = dwg.add(Group(id='temp'))
        dwg.remove(group)
        self.assertNotIn('temp', dwg.id_scope.ids)
        dwg.add(group)
        self.assertEqual(group['id'], 'temp')
        self.assertEqual(ids_of(dwg), ['temp'])

    def test_remove_frees_id_for_reuse(self):
        dwg = Drawing()
        group = dwg.add(Group(id='reuse'))
        dwg.remove(group)
        replacement = dwg.add(Group(id='reuse'))
        self.assertEqual(replacement['id'], 'reuse')
        self.assertEqual(dwg.id_scope.conflicts, ())

    def test_serialization_is_idempotent(self):
        dwg = Drawing()
        dwg.add(dwg.g())
        dwg.add(dwg.use(dwg.path(d='M0,0 L1,1')))
        first = dwg.tostring()
        for _ in range(5):
            self.assertEqual(dwg.tostring(), first)
        self.assertEqual(len(set(ids_of(dwg))), len(ids_of(dwg)))

    def test_serialization_order_matches_construction_order(self):
        dwg = Drawing()
        groups = [dwg.add(dwg.g()) for _ in range(5)]
        for group in groups:
            group.get_id()
        self.assertEqual([group['id'] for group in groups],
                         ['id1', 'id2', 'id3', 'id4', 'id5'])
        self.assertEqual(ids_of(dwg), ['id1', 'id2', 'id3', 'id4', 'id5'])

    def test_concurrent_drawings_produce_identical_output(self):
        def build():
            dwg = Drawing()
            for _ in range(10):
                dwg.add(dwg.g())
                dwg.add(dwg.use(dwg.path(d='M0,0 L1,1')))
            return dwg.tostring()

        results = {}
        errors = []

        def worker(number):
            try:
                results[number] = build()
            except Exception as error:  # pragma: no cover - failure path
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results.values())), 1)
        self.assertEqual(len(results), 12)

    def test_threads_add_to_the_same_drawing(self):
        dwg = Drawing()
        barrier = threading.Barrier(8)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(20):
                    dwg.add(dwg.g()).get_id()
            except Exception as error:  # pragma: no cover - failure path
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        ids = ids_of(dwg)
        self.assertEqual(len(ids), 160)
        self.assertEqual(len(set(ids)), 160)


class TestCrossDocumentMove(unittest.TestCase):
    def test_move_element_between_documents(self):
        source = Drawing()
        target = Drawing()
        group = source.add(Group(id='moved'))
        source.remove(group)
        target.add(group)
        self.assertIs(group.id_scope, target.id_scope)
        self.assertEqual(target.id_scope.ids, ('moved',))
        self.assertEqual(source.id_scope.ids, ())

    def test_move_renames_on_collision_and_keeps_use_consistent(self):
        source = Drawing()
        target = Drawing()
        group = source.add(source.g())
        use = source.add(source.use(group))
        target.add(Group(id=group['id']))
        source.remove(group)
        source.remove(use)
        target.add(group)
        target.add(use)
        self.assertEqual(group['id'], 'id1_1')
        # object based references are refreshed during serialization
        self.assertIn('<use xlink:href="#id1_1" />', target.tostring())
        self.assertEqual(use['xlink:href'], '#id1_1')
        self.assertEqual(target.id_scope.ids, ('id1', 'id1_1'))

    def test_move_subtree_with_reference_chain(self):
        source = Drawing()
        target = Drawing()
        wrapper = source.add(source.g())
        path = wrapper.add(source.path(d='M0,0 L1,1'))
        text = wrapper.add(Text('x'))
        text_path = text.add(TextPath(path, 'x'))
        gradient = wrapper.add(LinearGradient(inherit=path))
        target.add(Group(id=path['id']))
        source.remove(wrapper)
        target.add(wrapper)
        self.assertNotEqual(path['id'], 'id1')
        self.assertEqual(text_path['xlink:href'], '#' + path['id'])
        self.assertEqual(gradient['xlink:href'], '#' + path['id'])
        self.assertEqual(len(set(ids_of(target))), len(ids_of(target)))

    def test_move_subtree_with_string_references(self):
        source = Drawing()
        target = Drawing()
        wrapper = source.add(source.g())
        path = wrapper.add(Path(d='M0,0 L1,1', id='id1'))
        use = wrapper.add(Use('#id1'))
        target.add(Group(id='id1'))
        source.remove(wrapper)
        target.add(wrapper)
        self.assertEqual(path['id'], 'id1_1')
        self.assertEqual(use['xlink:href'], '#id1_1')
        self.assertIn('id="id1_1"', target.tostring())

    def test_move_subtree_with_animation_timing_reference(self):
        from svgwrite.animate import Animate

        source = Drawing()
        target = Drawing()
        wrapper = source.add(source.g())
        wrapper.add(Group(id='id1'))
        animation = wrapper.add(Animate(begin='id1.click', end='id1.end'))
        target.add(Group(id='id1'))
        source.remove(wrapper)
        target.add(wrapper)
        self.assertEqual(animation['begin'], 'id1_1.click')
        self.assertEqual(animation['end'], 'id1_1.end')

    def test_move_subtree_with_pattern_reference(self):
        source = Drawing()
        target = Drawing()
        wrapper = source.add(source.g())
        base = wrapper.add(LinearGradient(id='id1'))
        pattern = wrapper.add(Pattern(inherit=base))
        target.add(Group(id='id1'))
        source.remove(wrapper)
        target.add(wrapper)
        self.assertEqual(base['id'], 'id1_1')
        self.assertEqual(pattern['xlink:href'], '#id1_1')

    def test_legacy_detached_element_moved_into_drawing(self):
        group = Group()
        group.get_id()  # allocate from the legacy counter
        dwg = Drawing()
        dwg.add(group)
        self.assertIs(group.id_scope, dwg.id_scope)
        self.assertIn(group['id'], dwg.id_scope.ids)


class TestIDScopeUnit(unittest.TestCase):
    def test_allocate_skips_existing_ids(self):
        scope = IDScope()
        scope.request('id1', BaseElement())
        self.assertEqual(scope.allocate(), 'id2')

    def test_allocate_start(self):
        scope = IDScope()
        self.assertEqual(scope.allocate(start=10), 'id10')
        self.assertEqual(scope.allocate(), 'id11')

    def test_release_only_matching_owner(self):
        scope = IDScope()
        first = BaseElement()
        second = BaseElement()
        scope.request('x', first)
        scope.release('x', second)
        self.assertTrue(scope.is_registered('x'))
        scope.release('x', first)
        self.assertFalse(scope.is_registered('x'))

    def test_ids_are_strings(self):
        self.assertEqual(attribute_key(1), '1')
        self.assertEqual(attribute_key('1'), '1')


if __name__ == '__main__':
    unittest.main()
