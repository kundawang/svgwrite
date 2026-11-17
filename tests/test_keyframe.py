#!/usr/bin/env python
#coding:utf-8
# Purpose: test keyframe timeline
# Copyright (C) 2026, Manfred Moitzi
# License: MIT License

import unittest

import svgwrite
from svgwrite.keyframe import KeyframeTimeline, KeyframeError


def status_icon_timeline(ring, pointer, **kwargs):
    """ Three keyframes on one 2s time axis for a status icon made of a
    ring (circle) and a pointer (line).
    """
    timeline = KeyframeTimeline(duration=2.0, **kwargs)
    timeline.add_keyframe(ring, 0.0, opacity=0.2, r=8)
    timeline.add_keyframe(ring, 1.0, opacity=1.0, r=10)
    timeline.add_keyframe(ring, 2.0, opacity=0.2, r=8)
    timeline.add_keyframe(pointer, 0.0, rotate=0, translate=(0, 0))
    timeline.add_keyframe(pointer, 1.0, rotate=180, translate=(1, 2))
    timeline.add_keyframe(pointer, 2.0, rotate=360, translate=(0, 0))
    return timeline


class TestStatusIconTimeline(unittest.TestCase):
    """ Two elements, three keyframes each, one shared time axis. """

    def setUp(self):
        self.dwg = svgwrite.Drawing('status-icon.svg', size=(24, 24),
                                    debug=True)
        self.ring = self.dwg.circle(center=(12, 12), r=8, id='ring')
        self.pointer = self.dwg.line(start=(12, 12), end=(12, 4),
                                     id='pointer')
        self.dwg.add(self.ring)
        self.dwg.add(self.pointer)

    def build(self, **kwargs):
        timeline = status_icon_timeline(self.ring, self.pointer, **kwargs)
        return timeline, timeline.add_to(self.dwg)

    def test_track_count(self):
        # ring: opacity + r, pointer: rotate + translate
        _, animations = self.build()
        self.assertEqual(len(animations), 4)

    def test_numeric_attribute_xml(self):
        _, animations = self.build(loop=True)
        opacity = animations[0]
        self.assertEqual(
            opacity.tostring(),
            '<animate attributeName="opacity" calcMode="linear" dur="2s" '
            'keyTimes="0;0.5;1" repeatCount="indefinite" '
            'values="0.2;1;0.2" xlink:href="#ring" />')

    def test_transform_xml(self):
        _, animations = self.build(loop=True)
        rotate = animations[2]
        self.assertEqual(
            rotate.tostring(),
            '<animateTransform attributeName="transform" '
            'attributeType="XML" calcMode="linear" dur="2s" '
            'keyTimes="0;0.5;1" repeatCount="indefinite" type="rotate" '
            'values="0;180;360" xlink:href="#pointer" />')
        translate = animations[3]
        self.assertEqual(
            translate.tostring(),
            '<animateTransform attributeName="transform" '
            'attributeType="XML" calcMode="linear" dur="2s" '
            'keyTimes="0;0.5;1" repeatCount="indefinite" type="translate" '
            'values="0 0;1 2;0 0" xlink:href="#pointer" />')

    def test_shared_time_axis(self):
        # all animations of both elements share dur and keyTimes
        _, animations = self.build()
        for animation in animations:
            self.assertEqual(animation['dur'], '2s')
            self.assertEqual(animation['keyTimes'], '0;0.5;1')
            self.assertEqual(animation['calcMode'], 'linear')

    def test_discrete_mode(self):
        _, animations = self.build(calc_mode='discrete')
        for animation in animations:
            self.assertEqual(animation['calcMode'], 'discrete')

    def test_freeze(self):
        _, animations = self.build(freeze=True)
        for animation in animations:
            self.assertEqual(animation['fill'], 'freeze')
            self.assertFalse('repeatCount' in animation.attribs)

    def test_loop(self):
        _, animations = self.build(loop=True)
        for animation in animations:
            self.assertEqual(animation['repeatCount'], 'indefinite')

    def test_references_valid_after_serialization(self):
        # targets have no explicit id: serialization must still produce
        # valid xlink:href references
        dwg = svgwrite.Drawing(debug=True)
        ring = dwg.circle(center=(12, 12), r=8)
        pointer = dwg.line(start=(12, 12), end=(12, 4))
        dwg.add(ring)
        dwg.add(pointer)
        status_icon_timeline(ring, pointer).add_to(dwg)
        xml = dwg.tostring()
        ring_id = ring['id']
        pointer_id = pointer['id']
        self.assertIn('id="%s"' % ring_id, xml)
        self.assertIn('id="%s"' % pointer_id, xml)
        self.assertEqual(xml.count('xlink:href="#%s"' % ring_id), 2)
        self.assertEqual(xml.count('xlink:href="#%s"' % pointer_id), 2)

    def test_fractional_key_times_are_stable(self):
        circle = self.dwg.circle(center=(0, 0), r=1)
        timeline = KeyframeTimeline(duration=3.0)
        timeline.add_keyframe(circle, 0.0, opacity=0)
        timeline.add_keyframe(circle, 1.0, opacity=0.5)
        timeline.add_keyframe(circle, 3.0, opacity=1)
        (animation,) = timeline.animations()
        self.assertEqual(animation['keyTimes'], '0;0.333333;1')
        # building twice yields identical output
        (again,) = timeline.animations()
        self.assertEqual(again['keyTimes'], '0;0.333333;1')


class TestKeyframeErrors(unittest.TestCase):
    def setUp(self):
        self.element = svgwrite.Drawing(debug=True).circle((0, 0), 1)

    def test_zero_duration(self):
        with self.assertRaises(KeyframeError):
            KeyframeTimeline(duration=0)

    def test_negative_duration(self):
        with self.assertRaises(KeyframeError):
            KeyframeTimeline(duration=-1)

    def test_descending_time(self):
        timeline = KeyframeTimeline(duration=2)
        timeline.add_keyframe(self.element, 1.5, opacity=1)
        with self.assertRaises(KeyframeError):
            timeline.add_keyframe(self.element, 0.5, opacity=0)

    def test_duplicate_time(self):
        timeline = KeyframeTimeline(duration=2)
        timeline.add_keyframe(self.element, 1.0, opacity=1)
        with self.assertRaises(KeyframeError):
            timeline.add_keyframe(self.element, 1.0, r=2)

    def test_missing_target(self):
        timeline = KeyframeTimeline(duration=2)
        with self.assertRaises(KeyframeError):
            timeline.add_keyframe(None, 0.0, opacity=1)

    def test_conflicting_value_types(self):
        timeline = KeyframeTimeline(duration=2)
        timeline.add_keyframe(self.element, 0.0, rotate=0)
        with self.assertRaises(KeyframeError):
            timeline.add_keyframe(self.element, 1.0, rotate=(90, 12, 12))

    def test_non_numeric_attribute_value(self):
        timeline = KeyframeTimeline(duration=2)
        with self.assertRaises(KeyframeError):
            timeline.add_keyframe(self.element, 0.0, opacity='high')

    def test_time_before_timeline(self):
        timeline = KeyframeTimeline(duration=2)
        with self.assertRaises(KeyframeError):
            timeline.add_keyframe(self.element, -0.1, opacity=1)

    def test_time_after_timeline(self):
        timeline = KeyframeTimeline(duration=2)
        with self.assertRaises(KeyframeError):
            timeline.add_keyframe(self.element, 2.1, opacity=1)

    def test_loop_and_freeze_conflict(self):
        with self.assertRaises(KeyframeError):
            KeyframeTimeline(duration=2, loop=True, freeze=True)

    def test_invalid_calc_mode(self):
        with self.assertRaises(KeyframeError):
            KeyframeTimeline(duration=2, calc_mode='spline')

    def test_empty_timeline(self):
        with self.assertRaises(KeyframeError):
            KeyframeTimeline(duration=2).animations()

    def test_keyframe_without_properties(self):
        timeline = KeyframeTimeline(duration=2)
        with self.assertRaises(KeyframeError):
            timeline.add_keyframe(self.element, 0.0)


if __name__ == '__main__':
    unittest.main()
