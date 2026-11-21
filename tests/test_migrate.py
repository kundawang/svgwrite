#!/usr/bin/env python
#coding:utf-8
# Purpose: test controlled migration from Full profile to SVG Tiny 1.2
# License: MIT License
from __future__ import unicode_literals

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                'fixtures', 'migration'))

import svgwrite
from svgwrite.migrate import (
    DEGRADED,
    REJECTED,
    MigrationError,
    MigrationReport,
    migrate_to_tiny,
)
from full_documents import BUILDERS

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures', 'migration')


def read_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), 'r') as fp:
        return fp.read().strip()


class TestPairedFixtures(unittest.TestCase):
    """Each Full fixture document is migrated and compared against its
    paired expected Tiny document (and expected issue list)."""

    def check_case(self, name):
        source = BUILDERS[name]()
        result = migrate_to_tiny(source)
        expected_svg = read_fixture('%s_tiny.svg' % name)
        self.assertEqual(result.drawing.tostring(), expected_svg)
        report_name = '%s_report.txt' % name
        if os.path.exists(os.path.join(FIXTURE_DIR, report_name)):
            expected_report = read_fixture(report_name)
            actual_report = '\n'.join(str(issue) for issue in result.report)
            self.assertEqual(actual_report, expected_report)
        else:
            self.assertTrue(result.report.ok,
                            'unexpected issues:\n%s' % result.report)
        return source, result

    def test_shapes_group_text(self):
        self.check_case('shapes_group_text')

    def test_gradients_use_defs(self):
        self.check_case('gradients_use_defs')

    def test_unsupported_content(self):
        self.check_case('unsupported_content')

    def test_dangling_reference(self):
        self.check_case('dangling_reference')


class TestMigrationResult(unittest.TestCase):
    def test_result_unpacking(self):
        drawing, report = migrate_to_tiny(BUILDERS['shapes_group_text']())
        self.assertEqual(drawing.profile, 'tiny')
        self.assertTrue(report.ok)

    def test_target_is_tiny_profile(self):
        result = migrate_to_tiny(BUILDERS['shapes_group_text']())
        self.assertEqual(result.drawing.profile, 'tiny')
        self.assertEqual(result.drawing.version, '1.2')
        xml = result.drawing.tostring()
        self.assertIn('baseProfile="tiny"', xml)
        self.assertIn('version="1.2"', xml)

    def test_size_and_viewbox_preserved(self):
        result = migrate_to_tiny(BUILDERS['shapes_group_text']())
        root = result.drawing
        self.assertEqual(root['width'], '200px')
        self.assertEqual(root['height'], '100px')
        self.assertEqual(root['viewBox'], '0,0,200,100')

    def test_filename_and_debug_inherited(self):
        source = BUILDERS['shapes_group_text']()
        result = migrate_to_tiny(source)
        self.assertEqual(result.drawing.filename, source.filename)
        self.assertEqual(result.drawing.debug, source.debug)
        result = migrate_to_tiny(source, filename='tiny.svg', debug=False)
        self.assertEqual(result.drawing.filename, 'tiny.svg')
        self.assertFalse(result.drawing.debug)

    def test_namespaces_and_casing_preserved(self):
        result = migrate_to_tiny(BUILDERS['gradients_use_defs']())
        xml = result.drawing.tostring()
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', xml)
        self.assertIn('xmlns:xlink="http://www.w3.org/1999/xlink"', xml)
        # camelCase element and attribute names survive
        self.assertIn('<linearGradient', xml)
        self.assertIn('<radialGradient', xml)
        self.assertIn('viewBox="0,0,100,50"', xml)
        # xlink references survive verbatim
        self.assertIn('xlink:href="#tile"', xml)

    def test_shared_defs_and_references(self):
        result = migrate_to_tiny(BUILDERS['gradients_use_defs']())
        defs = result.drawing.defs
        ids = [e['id'] for e in defs.elements]
        self.assertEqual(ids, ['grad-lin', 'grad-rad', 'tile'])
        # only one defs section in the output
        xml = result.drawing.tostring()
        self.assertEqual(xml.count('<defs'), 1)
        self.assertIn('fill="url(#grad-rad)"', xml)
        self.assertTrue(result.report.ok)


class TestSourceNotModified(unittest.TestCase):
    def test_source_serialization_unchanged(self):
        for name in BUILDERS:
            source = BUILDERS[name]()
            before = source.tostring()
            migrate_to_tiny(source)
            self.assertEqual(source.tostring(), before,
                             'source modified by migration: %s' % name)

    def test_source_structure_unchanged(self):
        source = BUILDERS['unsupported_content']()
        child_count = len(source.elements)
        defs_count = len(source.defs.elements)
        grad = source.defs.elements[3]
        self.assertEqual(grad.elementname, 'linearGradient')
        attrib_count = len(grad.attribs)
        migrate_to_tiny(source)
        self.assertEqual(len(source.elements), child_count)
        self.assertEqual(len(source.defs.elements), defs_count)
        self.assertEqual(len(grad.attribs), attrib_count)
        self.assertIn('gradientTransform', grad.attribs)
        self.assertEqual(source.profile, 'full')


class TestStrictMode(unittest.TestCase):
    def test_strict_raises_on_unsupported_element(self):
        source = BUILDERS['unsupported_content']()
        with self.assertRaises(MigrationError) as ctx:
            migrate_to_tiny(source, strict=True)
        issue = ctx.exception.issue
        self.assertEqual(issue.action, REJECTED)
        self.assertEqual(issue.element, 'marker')
        self.assertEqual(issue.path, '/svg/defs/marker#arrow')
        self.assertIs(ctx.exception.report, ctx.exception.report)

    def test_strict_rejects_dangling_reference(self):
        source = BUILDERS['dangling_reference']()
        with self.assertRaises(MigrationError) as ctx:
            migrate_to_tiny(source, strict=True)
        self.assertEqual(ctx.exception.issue.element, 'symbol')

    def test_strict_passes_fully_migratable_documents(self):
        for name in ('shapes_group_text', 'gradients_use_defs'):
            result = migrate_to_tiny(BUILDERS[name](), strict=True)
            self.assertTrue(result.report.ok)

    def test_degraded_issues_are_reported_not_raised(self):
        result = migrate_to_tiny(BUILDERS['unsupported_content']())
        self.assertFalse(result.report.ok)
        for issue in result.report:
            self.assertEqual(issue.action, DEGRADED)


class TestReport(unittest.TestCase):
    def test_issue_paths_identify_elements(self):
        result = migrate_to_tiny(BUILDERS['unsupported_content']())
        paths = [issue.path for issue in result.report]
        self.assertIn('/svg/defs/marker#arrow', paths)
        self.assertIn('/svg/defs/linearGradient#grad1', paths)
        self.assertIn('/svg/line#l1', paths)
        self.assertIn('/svg/text#t1/tref', paths)

    def test_attribute_issues_name_the_attribute(self):
        result = migrate_to_tiny(BUILDERS['unsupported_content']())
        attrs = dict()
        for issue in result.report:
            attrs.setdefault(issue.path, set()).add(issue.attribute)
        self.assertIn('spreadMethod', attrs['/svg/defs/linearGradient#grad1'])
        self.assertIn('gradientTransform',
                      attrs['/svg/defs/linearGradient#grad1'])
        self.assertIn('marker-end', attrs['/svg/line#l1'])

    def test_report_str(self):
        report = MigrationReport()
        self.assertEqual(str(report), 'migration report: ok, no issues')
        result = migrate_to_tiny(BUILDERS['unsupported_content']())
        text = str(result.report)
        self.assertIn('%d issue(s)' % len(result.report), text)
        self.assertIn('/svg/defs/marker#arrow', text)

    def test_report_by_action(self):
        result = migrate_to_tiny(BUILDERS['unsupported_content']())
        self.assertEqual(len(result.report.by_action(DEGRADED)),
                         len(result.report))
        self.assertEqual(result.report.by_action(REJECTED), [])


class TestElementPaths(unittest.TestCase):
    def test_sibling_index_in_path(self):
        dwg = svgwrite.Drawing(profile='full')
        group = dwg.g()
        group.add(dwg.filter())
        group.add(dwg.filter())
        dwg.add(group)
        result = migrate_to_tiny(dwg)
        paths = [issue.path for issue in result.report]
        self.assertEqual(paths, ['/svg/g/filter[1]', '/svg/g/filter[2]'])


if __name__ == '__main__':
    unittest.main()
