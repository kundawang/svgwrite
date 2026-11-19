#!/usr/bin/env python
# coding:utf-8
# Purpose: test svgwrite.audit reference auditing
# License: MIT License
import unittest
from io import StringIO

import svgwrite
from svgwrite import Drawing
from svgwrite.audit import (
    audit_references,
    ReferenceAuditor,
    AuditReport,
    Finding,
    ReferenceAuditError,
    ERROR,
    WARNING,
)
from svgwrite.container import Group, Use, Hyperlink
from svgwrite.solidcolor import SolidColor


def set_raw_id(element, value):
    """Set an id bypassing the debug validator (empty/whitespace ids)."""
    element.attribs['id'] = value


class TestCleanDrawings(unittest.TestCase):
    """Legal SVG must produce an empty report."""

    def test_empty_drawing_is_clean(self):
        report = Drawing().audit_references()
        self.assertTrue(report.ok)
        self.assertEqual(len(report), 0)

    def test_full_featured_drawing_is_clean(self):
        dwg = Drawing()
        grad = dwg.defs.add(dwg.linearGradient(id='grad'))
        grad.add_stop_color(0, 'red').add_stop_color(1, 'blue')
        pattern = dwg.defs.add(dwg.pattern(id='pat', size=(5, 5)))
        pattern.add(dwg.circle(center=(2, 2), r=1))
        clip = dwg.defs.add(dwg.clipPath(id='clip'))
        clip.add(dwg.rect(insert=(0, 0), size=(3, 3)))
        mask = dwg.defs.add(dwg.mask(id='msk'))
        mask.add(dwg.rect(insert=(0, 0), size=(3, 3)))
        marker = dwg.defs.add(dwg.marker(id='mk'))
        filt = dwg.defs.add(dwg.filter(id='flt'))
        filt.feGaussianBlur()
        # solidColor is tiny-profile only and not a legal full-profile
        # defs child, so the paint-server type check is covered separately
        solid = SolidColor(id='sol', color='green', profile='tiny')
        dwg.elements.append(solid)

        line = dwg.add(dwg.line((0, 0), (1, 1),
                                fill='url(#grad)',
                                stroke='url(#pat)',
                                **{'clip-path': 'url(#clip)',
                                   'mask': 'url(#msk)',
                                   'filter': 'url(#flt)',
                                   'marker-start': 'url(#mk)'}))
        line['style'] = 'fill: url(#sol); stroke-width: 2px'
        self.assertTrue(dwg.audit_references().ok)

    def test_external_urls_are_legal(self):
        dwg = Drawing()
        dwg.add(dwg.image('http://example.com/a.png'))
        dwg.add(dwg.image('https://example.com/b.png'))
        dwg.add(dwg.use('other.svg#frag'))
        dwg.add(dwg.use('/abs/path.svg#x'))
        dwg.add(dwg.rect(fill='url(data:image/png;base64,AAAA)'))
        dwg.add(Hyperlink('https://example.com'))
        dwg.add(dwg.script(href='code.js'))
        self.assertTrue(dwg.audit_references().ok)

    def test_quoted_funciri_resolves(self):
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='g1'))
        dwg.add(dwg.rect(fill='url("#g1")'))
        dwg.add(dwg.rect(fill="url('#g1')"))
        dwg.add(dwg.rect(fill='url( #g1 )'))
        self.assertTrue(dwg.audit_references().ok)


class TestForwardReferences(unittest.TestCase):
    def test_forward_href_in_defs(self):
        # referencing element is defined before the target
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='g1', inherit='#g2'))
        dwg.defs.add(dwg.linearGradient(id='g2'))
        self.assertTrue(dwg.audit_references().ok)

    def test_forward_use_reference(self):
        dwg = Drawing()
        dwg.add(dwg.use('#later'))
        dwg.defs.add(dwg.g(id='later'))
        self.assertTrue(dwg.audit_references().ok)

    def test_forward_funciri(self):
        dwg = Drawing()
        dwg.add(dwg.rect(fill='url(#grad)'))
        dwg.defs.add(dwg.linearGradient(id='grad'))
        self.assertTrue(dwg.audit_references().ok)


class TestMissingReferences(unittest.TestCase):
    def test_missing_funciri_reports_path(self):
        dwg = Drawing()
        dwg.add(dwg.rect(fill='url(#nope)'))
        report = dwg.audit_references()
        self.assertFalse(report.ok)
        finding = report.findings[0]
        self.assertEqual(finding.code, 'missing-reference')
        self.assertEqual(finding.severity, ERROR)
        self.assertEqual(finding.path, 'svg[0]/rect[1]/@fill')
        self.assertEqual(finding.ref, '#nope')
        self.assertIn('nope', finding.message)

    def test_missing_clip_path(self):
        dwg = Drawing()
        dwg.add(dwg.rect(**{'clip-path': 'url(#gone)'}))
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'missing-reference')
        self.assertIn('@clip-path', report.findings[0].path)

    def test_missing_href(self):
        dwg = Drawing()
        dwg.add(dwg.use('#ghost'))
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'missing-reference')
        self.assertIn('@xlink:href', report.findings[0].path)

    def test_empty_href_and_empty_funciri(self):
        dwg = Drawing()
        dwg.add(dwg.use(''))
        rect = dwg.add(dwg.rect(debug=False))
        rect.attribs['fill'] = 'url()'  # invalid in debug; audit still sees it
        report = dwg.audit_references()
        codes = [f.code for f in report]
        self.assertEqual(sorted(codes),
                         ['empty-reference', 'empty-reference'])

    def test_missing_reference_in_style_attribute(self):
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='g1'))
        r = dwg.add(dwg.rect())
        r['style'] = 'fill: url(#missing); stroke: url(#g1)'
        report = dwg.audit_references()
        self.assertEqual(len(report), 1)
        self.assertEqual(report.findings[0].code, 'missing-reference')
        self.assertIn('@style/fill', report.findings[0].path)

    def test_missing_reference_in_style_element(self):
        dwg = Drawing()
        dwg.embed_stylesheet('.x { fill: url(#missing); }')
        report = dwg.audit_references()
        self.assertEqual(len(report), 1)
        self.assertEqual(report.findings[0].code, 'missing-reference')
        self.assertIn('::style/fill', report.findings[0].path)


class TestDuplicateAndEmptyIds(unittest.TestCase):
    def test_duplicate_id(self):
        dwg = Drawing()
        dwg.defs.add(dwg.g(id='dup'))
        dwg.defs.add(dwg.g(id='dup'))
        report = dwg.audit_references()
        self.assertEqual(len(report), 1)
        finding = report.findings[0]
        self.assertEqual(finding.code, 'duplicate-id')
        self.assertEqual(finding.path, 'svg[0]/defs[0]/g[1]')
        self.assertIn('svg[0]/defs[0]/g[0]', finding.message)

    def test_empty_id(self):
        dwg = Drawing()
        element = dwg.add(dwg.g(debug=False))
        set_raw_id(element, '')
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'empty-id')

    def test_none_id(self):
        dwg = Drawing()
        element = dwg.add(dwg.g(debug=False))
        set_raw_id(element, None)
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'empty-id')


class TestTypeMismatch(unittest.TestCase):
    def test_fill_pointing_at_rect(self):
        dwg = Drawing()
        dwg.defs.add(dwg.rect(id='r1', insert=(0, 0), size=(1, 1)))
        dwg.add(dwg.rect(fill='url(#r1)'))
        report = dwg.audit_references(strict=False)
        self.assertEqual(len(report), 1)
        self.assertEqual(report.findings[0].code, 'type-mismatch')
        self.assertEqual(report.findings[0].severity, WARNING)

    def test_clip_path_pointing_at_gradient(self):
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='grad'))
        dwg.add(dwg.rect(**{'clip-path': 'url(#grad)'}))
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'type-mismatch')

    def test_use_accepts_any_type(self):
        dwg = Drawing()
        dwg.defs.add(dwg.rect(id='r1', insert=(0, 0), size=(1, 1)))
        dwg.add(dwg.use('#r1'))
        self.assertTrue(dwg.audit_references().ok)

    def test_gradient_inherit_type_check(self):
        dwg = Drawing()
        dwg.defs.add(dwg.rect(id='r1', insert=(0, 0), size=(1, 1)))
        dwg.defs.add(dwg.linearGradient(id='g1', inherit='#r1'))
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'type-mismatch')

    def test_textpath_accepts_shapes(self):
        from svgwrite.text import TextPath
        dwg = Drawing()
        dwg.defs.add(dwg.path(id='p1', d='M0,0 L1,1'))
        dwg.defs.add(dwg.circle(id='c1', center=(0, 0), r=2))
        text1 = dwg.add(dwg.text(''))
        text1.add(TextPath('#p1', 'hello'))
        text2 = dwg.add(dwg.text(''))
        text2.add(TextPath('#c1', 'hello'))
        self.assertTrue(dwg.audit_references().ok)

    def test_correct_types_pass(self):
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='lg'))
        dwg.defs.add(dwg.radialGradient(id='rg'))
        dwg.defs.add(dwg.pattern(id='pt', size=(2, 2)))
        # solidColor only exists in the SVG 1.2 tiny profile; audit its
        # reference without adding it to the full-profile defs tree
        solid = SolidColor(id='sc', color='red', profile='tiny')
        dwg.elements.append(solid)
        dwg.add(dwg.rect(fill='url(#lg)'))
        dwg.add(dwg.rect(fill='url(#rg)'))
        dwg.add(dwg.rect(fill='url(#pt)'))
        dwg.add(dwg.rect(fill='url(#sc)'))
        self.assertTrue(dwg.audit_references().ok)


class TestUseCycles(unittest.TestCase):
    def test_direct_self_cycle(self):
        dwg = Drawing()
        dwg.add(dwg.use('#self', id='self'))
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'use-cycle')

    def test_two_node_cycle(self):
        dwg = Drawing()
        dwg.add(dwg.use('#b', id='a'))
        dwg.add(dwg.use('#a', id='b'))
        report = dwg.audit_references()
        self.assertEqual(len(report.findings), 1)
        finding = report.findings[0]
        self.assertEqual(finding.code, 'use-cycle')
        self.assertIn('use[1]', finding.message)
        self.assertIn('use[2]', finding.message)

    def test_three_node_cycle(self):
        dwg = Drawing()
        dwg.add(dwg.use('#b', id='a'))
        dwg.add(dwg.use('#c', id='b'))
        dwg.add(dwg.use('#a', id='c'))
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'use-cycle')

    def test_shared_target_is_not_a_cycle(self):
        # several use elements pointing at the same target is legal sharing
        dwg = Drawing()
        shared = dwg.defs.add(dwg.g(id='shared'))
        shared.add(dwg.rect(insert=(0, 0), size=(1, 1)))
        dwg.add(dwg.use('#shared'))
        nested = dwg.add(dwg.g())
        nested.add(dwg.use('#shared'))
        nested.add(dwg.use('#shared'))
        self.assertTrue(dwg.audit_references().ok)

    def test_use_chain_without_cycle(self):
        dwg = Drawing()
        dwg.defs.add(dwg.rect(id='base', insert=(0, 0), size=(1, 1)))
        dwg.defs.add(dwg.use('#base', id='u1'))
        dwg.defs.add(dwg.use('#u1', id='u2'))
        dwg.add(dwg.use('#u2'))
        self.assertTrue(dwg.audit_references().ok)

    def test_cycle_via_nested_groups(self):
        dwg = Drawing()
        g1 = dwg.defs.add(dwg.g(id='g1'))
        g2 = g1.add(dwg.g(id='g2'))
        g2.add(dwg.use('#g1'))
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'use-cycle')

    def test_self_cycle_message_is_compact(self):
        dwg = Drawing()
        dwg.add(dwg.use('#self', id='self'))
        report = dwg.audit_references()
        message = report.findings[0].message
        self.assertNotIn('svg[0] ->', message)
        self.assertIn('use[1]', message)

    def test_cycle_detection_survives_deep_nesting(self):
        # iterative DFS must not hit Python's recursion limit
        import sys
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(200)
        try:
            deep = Drawing()
            node = deep
            for index in range(300):
                node = node.add(deep.g(id='n%d' % index))
            node.add(deep.use('#n299'))  # self-cycle at depth ~301
            report = audit_references(deep, max_depth=1000)
            self.assertEqual([f.code for f in report], ['use-cycle'])
        finally:
            sys.setrecursionlimit(old_limit)


class TestEscapedUrls(unittest.TestCase):
    def test_escaped_fragment_resolves_but_warns(self):
        dwg = Drawing()
        grad = dwg.defs.add(dwg.linearGradient(debug=False))
        set_raw_id(grad, 'my grad')
        dwg.add(dwg.rect(fill='url(#my%20grad)'))
        report = dwg.audit_references(strict=False)
        codes = [f.code for f in report]
        self.assertEqual(codes, ['escaped-url'])
        self.assertEqual(report.findings[0].severity, WARNING)

    def test_escaped_fragment_strict_is_error(self):
        dwg = Drawing()
        grad = dwg.defs.add(dwg.linearGradient(debug=False))
        set_raw_id(grad, 'my grad')
        dwg.add(dwg.rect(fill='url(#my%20grad)'))
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].severity, ERROR)

    def test_escaped_but_missing_is_both_warning_and_error(self):
        dwg = Drawing()
        dwg.add(dwg.rect(fill='url(#no%20such)'))
        report = dwg.audit_references(strict=False)
        codes = sorted(f.code for f in report)
        self.assertEqual(codes, ['escaped-url', 'missing-reference'])


class TestNestedGroupsAndPaths(unittest.TestCase):
    def test_deep_nesting_report(self):
        dwg = Drawing()
        node = dwg
        for _ in range(5):
            node = node.add(dwg.g())
        report = audit_references(dwg, strict=False, max_depth=3)
        depth_findings = [f for f in report
                          if f.code == 'max-depth-exceeded']
        # exactly one finding even though the tree is walked in phases
        self.assertEqual(len(depth_findings), 1)
        self.assertEqual(depth_findings[0].severity, WARNING)
        # traversal stays iterative: a very deep tree must not raise
        deep = Drawing()
        node = deep
        for _ in range(1000):
            node = node.add(dwg.g())
        deep_report = audit_references(deep, strict=True, max_depth=50)
        self.assertFalse(deep_report.ok)

    def test_reference_inside_nested_group_path(self):
        dwg = Drawing()
        level = dwg
        for _ in range(3):
            level = level.add(dwg.g())
        level.add(dwg.rect(fill='url(#deep-missing)'))
        report = dwg.audit_references()
        path = report.findings[0].path
        self.assertEqual(
            path,
            'svg[0]/g[1]/g[0]/g[0]/rect[0]/@fill')

    def test_shared_subtree_visited_once(self):
        dwg = Drawing()
        shared = dwg.defs.add(dwg.g(id='shared'))
        shared.add(dwg.use('#shared-missing'))
        dwg.add(dwg.use('#shared'))
        dwg.add(dwg.use('#shared'))
        report = dwg.audit_references()
        # the broken inner reference must be reported exactly once
        missing = [f for f in report
                   if f.code == 'missing-reference']
        self.assertEqual(len(missing), 1)


class TestStrictModes(unittest.TestCase):
    def _drawing_with_mixed_issues(self):
        dwg = Drawing()
        dwg.defs.add(dwg.g(id='dup'))
        dwg.defs.add(dwg.g(id='dup'))
        dwg.defs.add(dwg.rect(id='r1', insert=(0, 0), size=(1, 1)))
        dwg.add(dwg.rect(fill='url(#r1)'))       # type mismatch
        dwg.add(dwg.rect(fill='url(#gone)'))     # missing -> always error
        return dwg

    def test_strict_everything_is_error(self):
        report = self._drawing_with_mixed_issues().audit_references(
            strict=True)
        self.assertTrue(all(f.severity == ERROR for f in report))
        self.assertFalse(report.ok)

    def test_non_strict_warns_for_soft_issues(self):
        report = self._drawing_with_mixed_issues().audit_references(
            strict=False)
        severities = {f.code: f.severity for f in report}
        self.assertEqual(severities['duplicate-id'], WARNING)
        self.assertEqual(severities['type-mismatch'], WARNING)
        self.assertEqual(severities['missing-reference'], ERROR)
        self.assertFalse(report.ok)
        self.assertEqual(len(report.warnings), 2)
        self.assertEqual(len(report.errors), 1)

    def test_non_strict_clean_with_warnings_is_ok(self):
        dwg = Drawing()
        dwg.defs.add(dwg.g(id='dup'))
        dwg.defs.add(dwg.g(id='dup'))
        report = dwg.audit_references(strict=False)
        self.assertTrue(report.ok)
        self.assertEqual(len(report.errors), 0)


class TestReportOrder(unittest.TestCase):
    """The fixed nested icon fixture pins down the deterministic order."""

    def _build_icon(self):
        # document order of defects:
        #   defs: duplicate id 'dup' (g[0], g[1])
        #   body: missing fill ref, type mismatch, missing href
        dwg = Drawing()
        dwg.defs.add(dwg.g(id='dup'))
        dwg.defs.add(dwg.g(id='dup'))
        dwg.defs.add(dwg.rect(id='r1', insert=(0, 0), size=(1, 1)))
        dwg.add(dwg.rect(fill='url(#missing-grad)'))
        dwg.add(dwg.rect(fill='url(#r1)'))
        dwg.add(dwg.use('#missing-target'))
        return dwg

    def test_finding_order_ids_then_refs_then_cycles(self):
        report = self._build_icon().audit_references(strict=False)
        codes = [f.code for f in report]
        self.assertEqual(codes, [
            'duplicate-id',
            'missing-reference',
            'type-mismatch',
            'missing-reference',
        ])

    def test_finding_paths_are_stable(self):
        report = self._build_icon().audit_references(strict=False)
        paths = [f.path for f in report]
        self.assertEqual(paths, [
            'svg[0]/defs[0]/g[1]',
            'svg[0]/rect[1]/@fill',
            'svg[0]/rect[2]/@fill',
            'svg[0]/use[3]/@xlink:href',
        ])

    def test_report_str_is_readable(self):
        report = self._build_icon().audit_references(strict=False)
        text = str(report)
        self.assertIn('missing-reference', text)
        self.assertIn('svg[0]/rect[1]/@fill', text)

    def test_finding_equality(self):
        f1 = Finding('x', ERROR, 'm', 'p', 'r')
        f2 = Finding('x', ERROR, 'm', 'p', 'r')
        f3 = Finding('y', ERROR, 'm', 'p', 'r')
        self.assertEqual(f1, f2)
        self.assertNotEqual(f1, f3)


class TestSaveIntegration(unittest.TestCase):
    def test_write_with_audit_raises(self):
        dwg = Drawing()
        dwg.add(dwg.rect(fill='url(#missing)'))
        buf = StringIO()
        with self.assertRaises(ReferenceAuditError) as cm:
            dwg.write(buf, audit=True)
        self.assertEqual(len(cm.exception.report.errors), 1)
        self.assertEqual(buf.getvalue(), '')

    def test_write_with_audit_passes_clean_drawing(self):
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='g'))
        dwg.add(dwg.rect(fill='url(#g)'))
        buf = StringIO()
        dwg.write(buf, audit=True)
        self.assertIn('<svg', buf.getvalue())

    def test_save_audit_failure_leaves_no_file(self):
        import os
        import tempfile
        fn = os.path.join(tempfile.gettempdir(), 'audit_broken.svg')
        if os.path.exists(fn):
            os.unlink(fn)
        dwg = Drawing(fn)
        dwg.add(dwg.rect(fill='url(#missing)'))
        with self.assertRaises(ReferenceAuditError):
            dwg.save(audit=True)
        self.assertFalse(os.path.exists(fn))

    def test_save_non_strict_still_writes(self):
        import os
        import tempfile
        fn = os.path.join(tempfile.gettempdir(), 'audit_soft.svg')
        if os.path.exists(fn):
            os.unlink(fn)
        dwg = Drawing(fn)
        dwg.defs.add(dwg.g(id='dup'))
        dwg.defs.add(dwg.g(id='dup'))
        dwg.save(audit=True, strict=False)  # warnings only -> ok
        self.assertTrue(os.path.exists(fn))
        os.unlink(fn)

    def test_audit_default_does_not_change_behavior(self):
        # existing call signature must keep working unchanged
        dwg = Drawing()
        dwg.add(dwg.rect(fill='url(#missing)'))
        buf = StringIO()
        dwg.write(buf)  # no audit -> writes anyway
        self.assertIn('<svg', buf.getvalue())


class TestAdditionalReferences(unittest.TestCase):
    def test_svg2_href_attribute_without_xlink_prefix(self):
        dwg = Drawing()
        dwg.defs.add(dwg.g(id='t'))
        use = dwg.add(Use('#t', debug=False))
        del use.attribs['xlink:href']
        use.attribs['href'] = '#t'
        self.assertTrue(dwg.audit_references().ok)
        use.attribs['href'] = '#missing'
        self.assertEqual(dwg.audit_references().findings[0].code,
                         'missing-reference')

    def test_multiple_funciris_in_one_attribute(self):
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='a'))
        rect = dwg.add(dwg.rect(debug=False))
        rect.attribs['fill'] = 'url(#a) url(#b)'
        report = dwg.audit_references()
        self.assertEqual(len(report), 1)
        self.assertEqual(report.findings[0].code, 'missing-reference')

    def test_marker_references_via_mixin(self):
        dwg = Drawing()
        marker = dwg.defs.add(dwg.marker(id='mk'))
        line = dwg.add(dwg.line((0, 0), (1, 1)))
        line.set_markers((marker, marker, marker))
        self.assertTrue(dwg.audit_references().ok)
        broken = dwg.add(dwg.line((0, 0), (1, 1), debug=False))
        broken.attribs['marker-end'] = 'url(#nomarker)'
        report = dwg.audit_references()
        self.assertEqual(report.findings[0].code, 'missing-reference')

    def test_feimage_external_href_is_legal(self):
        dwg = Drawing()
        filt = dwg.defs.add(dwg.filter(id='f1'))
        filt.feImage('http://example.com/texture.png')
        self.assertTrue(dwg.audit_references().ok)

    def test_tref_type_mismatch_and_valid_target(self):
        dwg = Drawing()
        dwg.defs.add(dwg.rect(id='r', insert=(0, 0), size=(1, 1)))
        dwg.defs.add(dwg.text('x', id='t'))
        text = dwg.add(dwg.text(''))
        text.add(dwg.tref('#r'))   # rect is not a text element
        text.add(dwg.tref('#t'))   # text element is fine
        report = dwg.audit_references()
        self.assertEqual(len(report), 1)
        self.assertEqual(report.findings[0].code, 'type-mismatch')

    def test_filter_inherit_type_mismatch(self):
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='g'))
        dwg.defs.add(dwg.filter(id='f2', inherit='#g'))
        self.assertEqual(dwg.audit_references().findings[0].code,
                         'type-mismatch')

    def test_gradient_and_pattern_inherit_chains_are_clean(self):
        dwg = Drawing()
        dwg.defs.add(dwg.linearGradient(id='base'))
        dwg.defs.add(dwg.linearGradient(id='child', inherit='#base'))
        dwg.defs.add(dwg.pattern(id='p1', size=(2, 2)))
        dwg.defs.add(dwg.pattern(id='p2', size=(2, 2), inherit='#p1'))
        dwg.add(dwg.rect(fill='url(#child)'))
        self.assertTrue(dwg.audit_references().ok)

    def test_quoted_external_url_in_funciri(self):
        dwg = Drawing()
        dwg.add(dwg.rect(debug=False)).attribs['fill'] = (
            'url("https://example.com/paint.svg#x")')
        self.assertTrue(dwg.audit_references().ok)

    def test_unknown_css_property_with_url_is_resolved_but_not_typechecked(self):
        dwg = Drawing()
        dwg.defs.add(dwg.rect(id='r1', insert=(0, 0), size=(1, 1)))
        rect = dwg.add(dwg.rect(debug=False))
        rect.attribs['style'] = 'custom-paint: url(#r1); color: black'
        report = dwg.audit_references()
        self.assertTrue(report.ok)

    def test_style_element_without_urls_is_ignored(self):
        dwg = Drawing()
        dwg.embed_stylesheet('.red { fill: red; stroke: none; }')
        self.assertTrue(dwg.audit_references().ok)


class TestModuleApi(unittest.TestCase):
    def test_module_level_function(self):
        dwg = Drawing()
        dwg.add(dwg.use('#x'))
        report = audit_references(dwg)
        self.assertIsInstance(report, AuditReport)

    def test_auditor_class_reusable(self):
        auditor = ReferenceAuditor()
        dwg1 = Drawing()
        dwg1.add(dwg1.use('#x'))
        report1 = auditor.audit(dwg1)
        self.assertFalse(report1.ok)
        # second run must not leak state from the first
        dwg2 = Drawing()
        dwg2.defs.add(dwg2.g(id='x'))
        dwg2.add(dwg2.use('#x'))
        report2 = auditor.audit(dwg2)
        self.assertTrue(report2.ok)

    def test_audit_base_element_directly(self):
        g = Group()
        g.add(Use('#missing'))
        report = audit_references(g)
        self.assertEqual(report.findings[0].code, 'missing-reference')
        self.assertTrue(report.findings[0].path.startswith('g[0]/'))


if __name__ == '__main__':
    unittest.main()
