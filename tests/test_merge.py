#!/usr/bin/env python
#coding:utf-8
# Author: kundawang
# Purpose: test drawing merge module (id namespacing)
# Created: 19.11.2026
# License: MIT License
import unittest

import svgwrite
from svgwrite.container import Use, Group
from svgwrite.merge import (
    DrawingMerger, MergeReport, Issue, is_valid_id,
    MergeError, DuplicateIdError, InvalidIdError,
    NamespaceCollisionError, MissingReferenceError,
    CircularReferenceError,
)


def make_drawing(gradient_color, suffix=''):
    """A drawing with a same-named gradient and clip path as the other ones."""
    dwg = svgwrite.Drawing(debug=False)
    grad = dwg.defs.add(dwg.linearGradient(id='mygrad'))
    grad.add_stop_color(0, 'white')
    grad.add_stop_color(1, gradient_color)
    clip = dwg.defs.add(dwg.clipPath(id='myclip'))
    clip.add(dwg.rect(insert=(0, 0), size=(10, 10)))
    dwg.add(dwg.rect(
        insert=(0, 0), size=(20, 20),
        fill='url(#mygrad)',
        clip_path='url(#myclip)',
        id='shape%s' % suffix,
    ))
    dwg.add(Use('#myclip', id='user%s' % suffix))
    return dwg


class TestIsValidId(unittest.TestCase):
    def test_valid(self):
        for name in ('a', 'A', '_', 'a1', '_x.y-z', 'Größe', 'id-2'):
            self.assertTrue(is_valid_id(name), name)

    def test_invalid(self):
        for name in ('', '1abc', '-x', '.x', 'a b', 'a#b', 'x,y'):
            self.assertFalse(is_valid_id(name), name)


class TestBasicMerge(unittest.TestCase):
    def setUp(self):
        AutoID = svgwrite.utils.AutoID
        AutoID(1)

    def test_single_merge_structure(self):
        target = svgwrite.Drawing(debug=False)
        target.merge(make_drawing('red'))
        result = target.tostring()
        # independent nested svg wrapper
        self.assertIn('<svg height="100%" id="merge1" width="100%">', result)
        # source defs moved inside the wrapper
        self.assertIn('<defs><linearGradient id="merge1_mygrad">', result)
        self.assertIn('id="merge1_myclip"', result)
        # internal references rewritten
        self.assertIn('fill="url(#merge1_mygrad)"', result)
        self.assertIn('clip-path="url(#merge1_myclip)"', result)
        self.assertIn('xlink:href="#merge1_myclip"', result)
        # target keeps its own empty defs as first element
        self.assertTrue(result.index('<defs />') < result.index('id="merge1"'))

    def test_two_same_named_drawings_get_distinct_namespaces(self):
        target = svgwrite.Drawing(debug=False)
        target.merge_all([make_drawing('red', 'a'),
                          make_drawing('blue', 'b')])
        result = target.tostring()
        self.assertIn('id="merge1_mygrad"', result)
        self.assertIn('id="merge2_mygrad"', result)
        self.assertIn('id="merge1_myclip"', result)
        self.assertIn('id="merge2_myclip"', result)
        # each rect points at its own gradient / clip, never the other's
        self.assertIn('fill="url(#merge1_mygrad)"', result)
        self.assertIn('fill="url(#merge2_mygrad)"', result)
        # every id is unique in the merged document
        import re
        ids = re.findall(r'id="([^"]+)"', result)
        self.assertEqual(len(ids), len(set(ids)))
        # every url/fragment reference resolves to an existing id
        referenced = set(re.findall(r'[#"]([A-Za-z_][\w.-]*)["\)]', result))
        for fragment in re.findall(r'url\(#([^\)]+)\)', result):
            self.assertIn(fragment, ids)
        for fragment in re.findall(r'xlink:href="#([^"]+)"', result):
            self.assertIn(fragment, ids)

    def test_explicit_prefix(self):
        target = svgwrite.Drawing(debug=False)
        target.merge(make_drawing('red'), prefix='src')
        self.assertIn('id="src_mygrad"', target.tostring())

    def test_explicit_prefix_list_gets_suffixes(self):
        target = svgwrite.Drawing(debug=False)
        a = make_drawing('red'); b = make_drawing('blue')
        target.merge([a, b], prefix='ns')
        result = target.tostring()
        self.assertIn('id="ns_mygrad"', result)
        self.assertIn('id="ns_2_mygrad"', result)
        self.assertNotIn('ns_1_', result)

    def test_drawing_order_preserved(self):
        target = svgwrite.Drawing(debug=False)
        first = svgwrite.Drawing(debug=False)
        first.add(first.rect(id='first', size=(1, 1)))
        second = svgwrite.Drawing(debug=False)
        second.add(second.rect(id='second', size=(1, 1)))
        target.merge_all([first, second])
        result = target.tostring()
        self.assertLess(result.index('merge1_first'),
                        result.index('merge2_second'))

    def test_size_and_viewbox_preserved(self):
        source = svgwrite.Drawing(size=(120, 80), debug=False)
        source.viewbox(0, 0, 12, 8)
        target = svgwrite.Drawing(debug=False)
        target.merge(source)
        result = target.tostring()
        self.assertIn('width="120"', result)
        self.assertIn('height="80"', result)
        self.assertIn('viewBox="0,0,12,8"', result)

    def test_insert_position_nested_svg(self):
        source = svgwrite.Drawing(debug=False)
        source.add(source.rect(size=(1, 1)))
        target = svgwrite.Drawing(debug=False)
        target.merge(source, insert=(15, 25))
        result = target.tostring()
        self.assertIn('x="15"', result)
        self.assertIn('y="25"', result)
        self.assertNotIn('translate(15,25)', result)

    def test_insert_position_plain_group(self):
        source = svgwrite.Drawing(debug=False)
        source.add(source.rect(size=(1, 1)))
        target = svgwrite.Drawing(debug=False)
        target.merge(source, insert=(15, 25), as_nested_svg=False)
        self.assertIn('transform="translate(15,25)"', target.tostring())

    def test_plain_group_mode(self):
        source = svgwrite.Drawing(debug=False)
        g = source.g(transform='rotate(30)')
        g.add(source.rect(size=(2, 2)))
        source.add(g)
        target = svgwrite.Drawing(debug=False)
        target.merge(source, as_nested_svg=False)
        result = target.tostring()
        self.assertIn('<g id="merge1">', result)
        self.assertIn('transform="rotate(30)"', result)
        self.assertNotIn('<svg id="merge1"', result)

    def test_external_urls_untouched(self):
        source = svgwrite.Drawing(debug=False)
        source.add(source.image('http://example.com/pic.png',
                                insert=(0, 0), size=(5, 5)))
        source.add(source.image('data:image/png;base64,AAAA',
                                insert=(0, 0), size=(5, 5)))
        target = svgwrite.Drawing(debug=False)
        target.merge(source)
        result = target.tostring()
        self.assertIn('http://example.com/pic.png', result)
        self.assertIn('data:image/png;base64,AAAA', result)


class TestSourceReuseAndDeterminism(unittest.TestCase):
    def test_source_not_modified(self):
        source = make_drawing('red')
        before = source.tostring()
        svgwrite.Drawing(debug=False).merge(source)
        self.assertEqual(source.tostring(), before)
        # gradient still reachable under its original id
        self.assertIn('url(#mygrad)', source.tostring())

    def test_source_can_be_merged_again(self):
        source = make_drawing('red')
        t1 = svgwrite.Drawing(debug=False)
        t2 = svgwrite.Drawing(debug=False)
        t1.merge(source)
        t2.merge(source)
        self.assertEqual(t1.tostring(), t2.tostring())

    def test_deterministic_output(self):
        builds = []
        for _ in range(3):
            target = svgwrite.Drawing(debug=False)
            target.merge_all([make_drawing('red'), make_drawing('blue')])
            builds.append(target.tostring())
        self.assertEqual(builds[0], builds[1])
        self.assertEqual(builds[1], builds[2])

    def test_no_global_autoid_consumed(self):
        svgwrite.utils.AutoID(700)
        target = svgwrite.Drawing(debug=False)
        target.merge(make_drawing('red'))
        # merging must not change the global AutoID sequence
        element = Group(debug=False)
        self.assertEqual(element.next_id(), 'id700')


class TestReferenceRewriting(unittest.TestCase):
    def test_gradient_inheritance(self):
        source = svgwrite.Drawing(debug=False)
        source.defs.add(source.linearGradient(id='base'))
        source.defs.add(source.linearGradient(id='child', inherit='#base'))
        target = svgwrite.Drawing(debug=False)
        target.merge(source)
        result = target.tostring()
        self.assertIn('id="merge1_base"', result)
        self.assertIn('id="merge1_child"', result)
        self.assertIn('xlink:href="#merge1_base"', result)

    def test_href_attribute_object_sync(self):
        source = svgwrite.Drawing(debug=False)
        use = source.use('#myclip')
        source.add(use)
        source.defs.add(source.clipPath(id='myclip'))
        target = svgwrite.Drawing(debug=False)
        target.merge(source)
        # the python level href must stay in sync with the attribute
        merged_use = target.elements[-1].elements[-1]
        self.assertEqual(merged_use.href, '#merge1_myclip')

    def test_embedded_css_url_rewritten(self):
        source = svgwrite.Drawing(debug=False)
        source.embed_stylesheet('.box { fill: url(#mygrad); }')
        source.defs.add(source.linearGradient(id='mygrad'))
        target = svgwrite.Drawing(debug=False)
        target.merge(source)
        self.assertIn('url(#merge1_mygrad)', target.tostring())

    def test_smil_timing_syncbase_rewritten(self):
        source = svgwrite.Drawing(debug=False)
        rect = source.rect(id='r1', size=(1, 1))
        rect.add(source.animate(href='#r1', begin='r1.click+1s'))
        source.add(rect)
        target = svgwrite.Drawing(debug=False)
        target.merge(source)
        result = target.tostring()
        self.assertIn('begin="merge1_r1.click+1s"', result)
        self.assertIn('xlink:href="#merge1_r1"', result)

    def test_marker_and_filter_funciri(self):
        source = svgwrite.Drawing(debug=False)
        source.defs.add(source.marker(id='mk'))
        source.defs.add(source.filter(id='flt'))
        rect = source.rect(size=(1, 1))
        rect['marker-start'] = 'url(#mk)'
        rect['filter'] = 'url(#flt)'
        source.add(rect)
        target = svgwrite.Drawing(debug=False)
        target.merge(source)
        result = target.tostring()
        self.assertIn('marker-start="url(#merge1_mk)"', result)
        self.assertIn('filter="url(#merge1_flt)"', result)


class TestValidationErrors(unittest.TestCase):
    def _fresh(self):
        return svgwrite.Drawing(debug=False)

    def test_duplicate_id_strict(self):
        source = self._fresh()
        source.defs.add(source.g(id='x'))
        source.add(source.rect(id='x'))
        with self.assertRaises(DuplicateIdError):
            self._fresh().merge(source)

    def test_duplicate_id_report_mode_repairs(self):
        source = self._fresh()
        source.defs.add(source.g(id='x'))
        source.add(source.rect(id='x'))
        target = self._fresh()
        report = target.merge(source, strict=False)
        self.assertEqual(report.duplicate_ids[0].kind, 'duplicate_id')
        import re
        ids = re.findall(r'id="([^"]+)"', target.tostring())
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('merge1_x-2', ids)

    def test_invalid_id_strict(self):
        source = self._fresh()
        source.add(source.rect(id='1bad'))
        with self.assertRaises(InvalidIdError):
            self._fresh().merge(source)

    def test_invalid_prefix_always_raises(self):
        with self.assertRaises(InvalidIdError):
            self._fresh().merge(self._fresh(), prefix='9bad')
        with self.assertRaises(InvalidIdError):
            self._fresh().merge(self._fresh(), prefix='a b')

    def test_invalid_id_report_mode(self):
        source = self._fresh()
        source.add(source.rect(id='1bad'))
        report = self._fresh().merge(source, strict=False)
        self.assertEqual(report.invalid_ids[0].kind, 'invalid_id')

    def test_namespace_collision_strict(self):
        source = self._fresh()
        source.add(source.rect(id='k'))
        target = self._fresh()
        target.add(target.rect(id='ns_k'))
        merger = DrawingMerger(target, strict=True)
        merger._prefix_taken = lambda prefix: False
        with self.assertRaises(NamespaceCollisionError):
            merger.merge(source, prefix='ns')

    def test_namespace_collision_report_mode_repairs(self):
        source = self._fresh()
        source.add(source.rect(id='k'))
        target = self._fresh()
        target.add(target.rect(id='ns_k'))
        merger = DrawingMerger(target, strict=False)
        merger._prefix_taken = lambda prefix: False
        report = merger.merge(source, prefix='ns')
        self.assertEqual(report.namespace_collisions[0].kind,
                         'namespace_collision')
        self.assertIn('ns_k-2', target.tostring())

    def test_missing_reference_strict(self):
        source = self._fresh()
        source.add(source.use('#ghost'))
        with self.assertRaises(MissingReferenceError):
            self._fresh().merge(source)

    def test_missing_reference_report_mode_keeps_fragment(self):
        source = self._fresh()
        source.add(source.use('#ghost'))
        target = self._fresh()
        report = target.merge(source, strict=False)
        self.assertEqual(report.missing_references[0].kind,
                         'missing_reference')
        self.assertIn('#ghost', target.tostring())

    def test_circular_use_strict(self):
        source = self._fresh()
        a = source.g(id='a'); b = source.g(id='b')
        a.add(source.use('#b')); b.add(source.use('#a'))
        source.defs.add(a); source.defs.add(b)
        with self.assertRaises(CircularReferenceError):
            self._fresh().merge(source)

    def test_self_cycle(self):
        source = self._fresh()
        g = source.g(id='s')
        g.add(source.use('#s'))
        source.add(g)
        with self.assertRaises(CircularReferenceError):
            self._fresh().merge(source)

    def test_cycle_report_mode(self):
        source = self._fresh()
        a = source.g(id='a'); b = source.g(id='b')
        a.add(source.use('#b')); b.add(source.use('#a'))
        source.defs.add(a); source.defs.add(b)
        report = self._fresh().merge(source, strict=False)
        self.assertEqual(report.circular_references[0].kind,
                         'circular_reference')
        self.assertEqual(len(report.groups), 1)

    def test_acyclic_chain_is_accepted(self):
        source = self._fresh()
        a = source.g(id='a')
        a.add(source.use('#b'))
        source.defs.add(a)
        source.defs.add(source.g(id='b'))
        report = self._fresh().merge(source)
        self.assertTrue(report.ok)

    def test_strict_failure_is_atomic(self):
        target = self._fresh()
        target.add(target.rect(id='keep'))
        before = target.tostring()
        bad = self._fresh()
        bad.add(bad.use('#nope'))
        with self.assertRaises(MergeError):
            target.merge(bad)
        self.assertEqual(target.tostring(), before)

    def test_merge_error_hierarchy(self):
        for cls in (DuplicateIdError, InvalidIdError,
                    NamespaceCollisionError, MissingReferenceError,
                    CircularReferenceError):
            self.assertTrue(issubclass(cls, MergeError))


class TestReport(unittest.TestCase):
    def test_clean_report(self):
        report = MergeReport()
        self.assertTrue(report.ok)
        self.assertTrue(bool(report))
        self.assertEqual(report.issues, [])
        self.assertEqual(report.issues_by_kind('x'), [])

    def test_report_with_issues_is_falsy(self):
        report = MergeReport()
        report.add_issue('duplicate_id', 'msg')
        self.assertFalse(report.ok)
        self.assertFalse(bool(report))
        self.assertEqual(len(report.duplicate_ids), 1)

    def test_issue_equality(self):
        self.assertEqual(Issue('a', 'b'), Issue('a', 'b'))
        self.assertNotEqual(Issue('a', 'b'), Issue('a', 'c'))


if __name__ == '__main__':
    unittest.main()
