#!/usr/bin/env python
# coding:utf-8
# Purpose: tests for the SMIL animation quality assurance layer
# Created: 2026-11-22
# License: MIT License

import re
import unittest

from svgwrite import Drawing
from svgwrite.animate import Animate
from svgwrite.animationcheck import (
    check_animations, parse_clock_value, parse_timing_entry,
)

import smil_fixtures as fx


# ---------------------------------------------------------------------------
# Independent timeline reference
#
# This is deliberately written from scratch (own regex, plain dict +
# recursive evaluation, different data structures and code paths from
# svgwrite.animationcheck._compute_start_times) so it can act as an
# independent oracle for the deterministic begin-time timeline.
# ---------------------------------------------------------------------------

CLOCK_UNITS = {'ms': 0.001, 's': 1.0, 'min': 60.0, 'h': 3600.0}
_SYNC = re.compile(r'^#?([-\w]+)\.(begin|end)([+-].*)?$')


def _reference_clock(token):
    """Standalone clock-value parser used only by the test oracle."""
    token = token.strip()
    if ':' in token:
        parts = token.split(':')
        factors = (3600.0, 60.0, 1.0)[-len(parts):]
        total = 0.0
        for factor, part in zip(factors, parts):
            total += factor * float(part)
        return total
    for unit, factor in CLOCK_UNITS.items():
        if token.endswith(unit):
            return float(token[:-len(unit)]) * factor
    return float(token)


def _reference_entry(token):
    """Standalone timing-entry classifier used only by the test oracle."""
    match = _SYNC.match(token.strip())
    if match:
        target, event, offset = match.groups()
        return ('syncbase', target, event,
                _reference_clock(offset) if offset else 0.0)
    if token.strip()[0].isdigit() or token.strip()[0] in '+-':
        return ('offset', _reference_clock(token))
    return ('external',)


class ReferenceTimeline(object):
    """Oracle: earliest absolute begin time of deterministic animations."""

    def __init__(self, specs):
        # specs: id -> {'begin': [(kind, ...)], 'dur': float|None}
        self.specs = specs
        self.memo = {}

    def begin_of(self, ident, seen=None):
        if ident in self.memo:
            return self.memo[ident]
        if seen is None:
            seen = set()
        if ident in seen:
            return None                 # cycle: no start time
        seen = seen | {ident}
        spec = self.specs[ident]
        if not spec['begin']:
            result = 0.0                # SMIL document-begin default
        else:
            candidates = []
            for entry in spec['begin']:
                if entry[0] == 'offset':
                    candidates.append(entry[1])
                elif entry[0] == 'syncbase':
                    _, target, event, offset = entry
                    base = self.begin_of(target, seen)
                    if base is None:
                        candidates = None
                        break
                    if event == 'end':
                        duration = self.specs[target]['dur']
                        if duration is None:
                            candidates = None
                            break
                        base += duration
                    candidates.append(base + offset)
                else:
                    candidates = None   # event/repeat: not deterministic
                    break
            result = min(candidates) if candidates else None
        self.memo[ident] = result
        return result


def build_reference(drawing):
    """Translate a drawing into the oracle's own spec format."""
    specs = {}
    stack = [drawing]
    while stack:
        element = stack.pop()
        if getattr(element, 'elementname', '') in (
                'set', 'animate', 'animateColor', 'animateMotion',
                'animateTransform'):
            ident = element.attribs.get('id')
            raw_begin = element.attribs.get('begin')
            begin = ([_reference_entry(token)
                      for token in raw_begin.replace(';', ',').split(',')
                      if token.strip()]
                     if raw_begin else [])
            dur = element.attribs.get('dur')
            specs[ident] = {
                'begin': begin,
                'dur': _reference_clock(dur)
                if dur is not None and dur not in ('indefinite', 'media')
                else None}
        stack.extend(reversed(getattr(element, 'elements', ())))
    return ReferenceTimeline(specs)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestClockParser(unittest.TestCase):
    def test_time_counts(self):
        self.assertEqual(parse_clock_value('1s'), 1.0)
        self.assertEqual(parse_clock_value('2.5s'), 2.5)
        self.assertEqual(parse_clock_value('500ms'), 0.5)
        self.assertEqual(parse_clock_value('2min'), 120.0)
        self.assertEqual(parse_clock_value('1h'), 3600.0)
        self.assertEqual(parse_clock_value('3'), 3.0)
        self.assertEqual(parse_clock_value('-1s'), -1.0)

    def test_partial_and_full_clock(self):
        self.assertEqual(parse_clock_value('02:33'), 153.0)
        self.assertEqual(parse_clock_value('01:00:00.5'), 3600.5)

    def test_invalid(self):
        self.assertIsNone(parse_clock_value('soon'))
        self.assertIsNone(parse_clock_value('1day'))


class TestTimingEntryParser(unittest.TestCase):
    def test_offset(self):
        entry = parse_timing_entry('1.5s')
        self.assertEqual(entry['kind'], 'offset')
        self.assertEqual(entry['offset'], 1.5)

    def test_syncbase(self):
        entry = parse_timing_entry('#a.begin+2s')
        self.assertEqual(entry['kind'], 'syncbase')
        self.assertEqual(entry['target'], 'a')
        self.assertEqual(entry['sync'], 'begin')
        self.assertEqual(entry['offset'], 2.0)
        entry = parse_timing_entry('b.end-500ms')
        self.assertEqual(entry['target'], 'b')
        self.assertEqual(entry['sync'], 'end')
        self.assertEqual(entry['offset'], -0.5)

    def test_repeat(self):
        entry = parse_timing_entry('a.repeat(3)')
        self.assertEqual(entry['kind'], 'repeat')
        self.assertEqual(entry['target'], 'a')
        self.assertEqual(entry['iteration'], 3)

    def test_event(self):
        entry = parse_timing_entry('rect1.click+1s')
        self.assertEqual(entry['kind'], 'event')
        self.assertEqual(entry['target'], 'rect1')
        self.assertEqual(entry['event'], 'click')

    def test_indefinite_and_accesskey(self):
        self.assertEqual(parse_timing_entry('indefinite')['kind'],
                         'indefinite')
        entry = parse_timing_entry('accessKey(a)')
        self.assertEqual(entry['kind'], 'accesskey')

    def test_unknown(self):
        self.assertEqual(parse_timing_entry('not-a-timing')['kind'],
                         'unknown')


class TestHealthyFixtureNoFalsePositives(unittest.TestCase):
    def setUp(self):
        self.drawing = fx.healthy_drawing()
        self.report = check_animations(self.drawing)

    def test_no_findings_at_all(self):
        # event sync, parallel animations, zero dur and intentional
        # indefinite repetition must not be flagged
        self.assertEqual(self.report.findings, [])
        self.assertTrue(self.report.ok)

    def test_independent_timeline_reference(self):
        oracle = build_reference(self.drawing)
        expected = {
            'a': oracle.begin_of('a'),   # 0.0
            'b': oracle.begin_of('b'),   # a.end = 0 + 2
            'c': oracle.begin_of('c'),   # b.begin + 0.5
            'e': oracle.begin_of('e'),   # 1s
            'g': oracle.begin_of('g'),   # 2s
            'h': oracle.begin_of('h'),   # 0.5s
        }
        self.assertEqual(expected, {'a': 0.0, 'b': 2.0, 'c': 2.5,
                                    'e': 1.0, 'g': 2.0, 'h': 0.5})
        # the checker must agree with the independent oracle on every
        # deterministic animation
        self.assertEqual(self.report.start_times(), expected)

    def test_event_and_indefinite_animations_have_no_start_time(self):
        # externally driven starts are reachable but not deterministic
        self.assertNotIn('d', self.report.start_times())
        self.assertNotIn('f', self.report.start_times())


class TestMutualWaitFixture(unittest.TestCase):
    def setUp(self):
        self.report = check_animations(fx.mutual_wait_drawing())

    def test_cycle_is_reported_for_both_animations(self):
        cycles = [f for f in self.report.findings
                  if f.code == 'circular-timing-dependency']
        self.assertEqual({f.element_id for f in cycles},
                         {'spinA', 'spinB'})
        for finding in cycles:
            self.assertEqual(finding.attribute, 'begin')
        self.assertFalse(self.report.ok)

    def test_no_duplicate_unreachable_finding_for_cycle_members(self):
        self.assertEqual(
            [f.code for f in self.report.findings].count(
                'unreachable-animation'), 0)


class TestMissingReferenceFixture(unittest.TestCase):
    def setUp(self):
        self.report = check_animations(fx.missing_reference_drawing())

    def test_missing_begin_and_end_references(self):
        missing = [f for f in self.report.findings
                   if f.code == 'missing-timing-reference']
        self.assertEqual({f.element_id for f in missing}, {'m1', 'm2'})
        attributes = {(f.element_id, f.attribute) for f in missing}
        self.assertIn(('m1', 'begin'), attributes)
        self.assertIn(('m1', 'end'), attributes)
        self.assertIn(('m2', 'begin'), attributes)

    def test_missing_refs_render_animations_unreachable(self):
        unreachable = [f for f in self.report.findings
                       if f.code == 'unreachable-animation']
        self.assertEqual({f.element_id for f in unreachable}, {'m1', 'm2'})


class TestUnreachableChainFixture(unittest.TestCase):
    def setUp(self):
        self.report = check_animations(fx.unreachable_chain_drawing())

    def test_cycle_members_and_stranded_tail(self):
        codes = {(f.element_id, f.code) for f in self.report.findings}
        for ident in ('u1', 'u2', 'u3'):
            self.assertIn((ident, 'circular-timing-dependency'), codes)
        self.assertIn(('u4', 'unreachable-animation'), codes)


class TestNonAnimationSyncbaseFixture(unittest.TestCase):
    def test_syncbase_into_shape_is_rejected(self):
        report = check_animations(fx.non_animation_syncbase_drawing())
        codes = [f.code for f in report.findings]
        self.assertIn('invalid-timing-reference', codes)
        self.assertIn('unreachable-animation', codes)
        finding = next(f for f in report.findings
                       if f.code == 'invalid-timing-reference')
        self.assertEqual(finding.element_id, 'n1')
        self.assertEqual(finding.attribute, 'begin')


class TestBoundaryFixture(unittest.TestCase):
    def setUp(self):
        self.report = check_animations(fx.boundary_violations_drawing())

    def _findings(self, element_id):
        return [f for f in self.report.findings
                if f.element_id == element_id]

    def test_every_element_and_attribute_is_named(self):
        by_id = {}
        for finding in self.report.findings:
            self.assertIsNotNone(finding.element_id)
            self.assertIsNotNone(finding.attribute)
            by_id.setdefault(finding.element_id, []).append(finding.attribute)
        for ident in ('b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8'):
            self.assertTrue(by_id.get(ident), ident)

    def test_keytimes_count_mismatch(self):
        attrs = [f.attribute for f in self._findings('b1')]
        self.assertIn('keyTimes', attrs)

    def test_keytimes_order_and_span(self):
        attrs = [f.attribute for f in self._findings('b2')]
        self.assertIn('keyTimes', attrs)
        self.assertTrue(any('ascending' in f.message
                            or 'start with 0' in f.message
                            for f in self._findings('b2')))
        self.assertTrue(any('start with 0' in f.message
                            for f in self._findings('b3')))

    def test_keysplines_out_of_range(self):
        self.assertTrue(any(f.attribute == 'keySplines'
                            for f in self._findings('b4')))

    def test_spline_requires_keytimes(self):
        self.assertTrue(any(f.attribute in ('calcMode', 'keySplines')
                            for f in self._findings('b5')))

    def test_dur_and_repeatcount(self):
        self.assertTrue(any(f.attribute == 'dur'
                            for f in self._findings('b6')))
        self.assertTrue(any(f.attribute == 'repeatCount'
                            for f in self._findings('b7')))
        self.assertTrue(any(f.attribute == 'dur'
                            for f in self._findings('b8')))

    def test_zero_dur_and_indefinite_repeat_are_legal(self):
        # the healthy fixture proves both, but assert it explicitly here
        report = check_animations(fx.healthy_drawing())
        flagged = {f.element_id for f in report.findings}
        self.assertNotIn('e', flagged)      # dur="0s"
        self.assertNotIn('a', flagged)      # repeatCount="indefinite"


class TestAdditionalBoundaryCases(unittest.TestCase):
    def _animate(self, ident, **timing):
        a = Animate('x', values='0;1;2', id=ident, debug=False)
        a.set_value(values='0;1;2', **{k: v for k, v in timing.items()
                                       if k in ('calcMode', 'keyTimes',
                                                'keySplines')})
        timing_attrs = {k: v for k, v in timing.items()
                        if k in ('dur', 'repeatCount')}
        if timing_attrs:
            a.set_timing(**timing_attrs)
        return a

    def test_keytimes_out_of_range(self):
        dwg = Drawing(debug=False)
        dwg.add(self._animate('x1', keyTimes='0;1.2;1'))
        report = check_animations(dwg)
        self.assertTrue(any(f.attribute == 'keyTimes'
                            and '0..1' in f.message
                            for f in report.findings))

    def test_keysplines_wrong_number_of_control_points(self):
        dwg = Drawing(debug=False)
        # 3 keyTimes => 2 intervals, only 1 spline set given
        dwg.add(self._animate('x2', calcMode='spline',
                              keyTimes='0;0.5;1', keySplines='0 0 1 1'))
        report = check_animations(dwg)
        self.assertTrue(any(f.code == 'invalid-attribute-combination'
                            for f in report.findings))

    def test_non_numeric_repeatcount(self):
        dwg = Drawing(debug=False)
        dwg.add(self._animate('x3', dur='1s', repeatCount='many'))
        report = check_animations(dwg)
        self.assertTrue(any(f.attribute == 'repeatCount'
                            for f in report.findings))

    def test_indefinite_dur_is_legal(self):
        dwg = Drawing(debug=False)
        dwg.add(self._animate('x4', dur='indefinite'))
        self.assertTrue(check_animations(dwg).ok)

    def test_spline_keysplines_warns_without_spline_calcmode(self):
        dwg = Drawing(debug=False)
        dwg.add(self._animate('x5', keyTimes='0;0.5;1',
                              keySplines='0 0 1 1;0 0 1 1'))
        report = check_animations(dwg)
        self.assertTrue(any(f.severity == 'warning'
                            and f.attribute == 'keySplines'
                            for f in report.findings))
        self.assertTrue(report.ok)     # warnings do not fail the report

    def test_paced_keytimes_is_warning_only(self):
        dwg = Drawing(debug=False)
        dwg.add(self._animate('x6', calcMode='paced', keyTimes='0;0.5;1'))
        report = check_animations(dwg)
        self.assertTrue(any(f.severity == 'warning'
                            and f.attribute == 'keyTimes'
                            for f in report.findings))
        self.assertTrue(report.ok)

    def test_invalid_timing_syntax(self):
        dwg = Drawing(debug=False)
        a = Animate('x', values='0;1', id='bad', debug=False)
        a.set_timing(begin='not-a-timing-value')
        dwg.add(a)
        report = check_animations(dwg)
        self.assertTrue(any(f.code == 'invalid-timing-syntax'
                            for f in report.findings))


# ---------------------------------------------------------------------------
# Fault injection: start from the healthy drawing, mutate one property at a
# time and prove the checker catches each class of defect.
# ---------------------------------------------------------------------------

class TestFaultInjection(unittest.TestCase):
    def _clone_drawing(self):
        # fresh construction because element ids are unique per process
        return fx.healthy_drawing()

    def _by_id(self, drawing, ident):
        for element in drawing.elements:
            if getattr(element, 'attribs', {}).get('id') == ident:
                return element
        raise KeyError(ident)

    def test_inject_circular_dependency(self):
        dwg = self._clone_drawing()
        # turn the healthy a -> b chain into a mutual wait
        self._by_id(dwg, 'a').set_timing(begin='b.begin', dur='2s')
        report = check_animations(dwg)
        self.assertIn('circular-timing-dependency', report.codes())
        self.assertFalse(report.ok)

    def test_inject_missing_reference(self):
        dwg = self._clone_drawing()
        self._by_id(dwg, 'c').set_timing(begin='phantom.end', dur='3s')
        report = check_animations(dwg)
        self.assertIn('missing-timing-reference', report.codes())
        self.assertTrue(any(f.element_id == 'c'
                            and "phantom" in f.message
                            for f in report.findings))

    def test_inject_unreachable_chain(self):
        dwg = self._clone_drawing()
        # 'a' rooted the parallel group; reroute it into an unknown target so
        # everything depending on it loses its startup chain
        self._by_id(dwg, 'a').set_timing(begin='nowhere.begin', dur='2s')
        report = check_animations(dwg)
        self.assertIn('missing-timing-reference', report.codes())
        self.assertIn('unreachable-animation', report.codes())

    def test_inject_keytimes_boundary_defect(self):
        dwg = self._clone_drawing()
        g = self._by_id(dwg, 'g')
        g.set_value(values='0;0.5;1', calcMode='spline',
                    keyTimes='0;0.25;0.75',          # does not end at 1
                    keySplines='0.5 0 0.5 1;0 0 1 1')
        report = check_animations(dwg)
        self.assertTrue(any(f.element_id == 'g'
                            and f.attribute == 'keyTimes'
                            for f in report.findings))

    def test_inject_negative_dur(self):
        dwg = self._clone_drawing()
        self._by_id(dwg, 'e').set_timing(begin='1s', dur='-5s')
        report = check_animations(dwg)
        self.assertTrue(any(f.element_id == 'e' and f.attribute == 'dur'
                            for f in report.findings))


class TestSerializationUnchanged(unittest.TestCase):
    def test_checker_does_not_modify_markup(self):
        dwg = fx.healthy_drawing()
        before = dwg.tostring()
        check_animations(dwg)
        check_animations(dwg)   # idempotent
        after = dwg.tostring()
        self.assertEqual(before, after)
        self.assertEqual(after, fx.HEALTHY_GOLDEN)

    def test_broken_fixtures_still_serialize(self):
        # diagnostics must never prevent or alter serialization
        for factory in (fx.mutual_wait_drawing,
                        fx.missing_reference_drawing,
                        fx.unreachable_chain_drawing,
                        fx.boundary_violations_drawing):
            dwg = factory()
            snapshot = dwg.tostring()
            check_animations(dwg)
            self.assertEqual(dwg.tostring(), snapshot)

    def test_legacy_animate_api_output_unchanged(self):
        # the original test_animate expectations still hold after running
        # the checker
        a = Animate(debug=True)
        a.set_value('0;1;2', 'linear', '0', '0 0 0 0', 0, 0, 0)
        check_animations(a)
        self.assertEqual(
            a.tostring(),
            '<animate by="0" calcMode="linear" from="0" '
            'keySplines="0 0 0 0" keyTimes="0" to="0" values="0;1;2" />')


class TestNestedContainers(unittest.TestCase):
    def test_animations_inside_groups_are_found(self):
        dwg = Drawing(debug=False)
        group = dwg.g(id='grp')
        dwg.add(group)
        a = Animate('x', values='0;1', id='deep', debug=False)
        a.set_timing(begin='ghost.begin')
        group.add(a)
        report = check_animations(dwg)
        self.assertTrue(any(f.element_id == 'deep'
                            and f.code == 'missing-timing-reference'
                            for f in report.findings))


class TestReferenceKinds(unittest.TestCase):
    def test_repeat_reference_is_a_valid_startup_edge(self):
        dwg = Drawing(debug=False)
        a = Animate('x', values='0;1', id='r1', debug=False)
        a.set_timing(begin='0s', dur='1s')
        b = Animate('y', values='0;1', id='r2', debug=False)
        b.set_timing(begin='r1.repeat(2)')
        dwg.add(a)
        dwg.add(b)
        report = check_animations(dwg)
        self.assertTrue(report.ok)
        self.assertNotIn('r2',
                         {f.element_id for f in report.findings})

    def test_end_only_missing_reference_does_not_break_startup(self):
        dwg = Drawing(debug=False)
        a = Animate('x', values='0;1', id='z1', debug=False)
        a.set_timing(begin='0s', end='ghost.end')
        dwg.add(a)
        report = check_animations(dwg)
        codes = report.codes()
        self.assertIn('missing-timing-reference', codes)
        self.assertNotIn('unreachable-animation', codes)

    def test_debug_mode_drawing_is_checkable(self):
        dwg = fx.healthy_drawing()
        dwg._parameter.debug = True
        self.assertTrue(check_animations(dwg).ok)

    def test_multiple_begin_values_mixed_roots(self):
        dwg = Drawing(debug=False)
        a = Animate('x', values='0;1', id='k1', debug=False)
        a.set_timing(begin='0s; 1s')
        b = Animate('y', values='0;1', id='k2', debug=False)
        b.set_timing(begin='k1.begin; 2s')   # clock offset keeps it rooted
        dwg.add(a)
        dwg.add(b)
        report = check_animations(dwg)
        self.assertTrue(report.ok)
        self.assertEqual(report.start_times()['k1'], 0.0)
        self.assertEqual(report.start_times()['k2'], 0.0)


if __name__ == '__main__':
    unittest.main()
