#!/usr/bin/env python
#coding:utf-8
# Purpose: keyframe timeline - align SMIL animations of several elements
#          on one shared time axis.
# Copyright (C) 2026, Manfred Moitzi
# License: MIT License
"""Keyframe timeline on top of the existing SMIL elements.

A :class:`KeyframeTimeline` collects keyframes for the numeric attributes
and the ``translate``/``rotate`` transforms of several elements under one
shared duration and expands them into :class:`~svgwrite.animate.Animate` and
:class:`~svgwrite.animate.AnimateTransform` elements that all share the same
``dur`` and a common ``keyTimes`` grid, so the animations of all elements
stay aligned on one time axis::

    timeline = KeyframeTimeline(duration=2.0, loop=True)
    timeline.add_keyframe(ring, 0.0, opacity=0.2, r=8)
    timeline.add_keyframe(ring, 1.0, opacity=1.0, r=10)
    timeline.add_keyframe(ring, 2.0, opacity=0.2, r=8)
    timeline.add_keyframe(pointer, 0.0, rotate=0)
    timeline.add_keyframe(pointer, 1.0, rotate=180)
    timeline.add_keyframe(pointer, 2.0, rotate=360)
    for animation in timeline.animations():
        drawing.add(animation)
"""

from svgwrite.animate import Animate, AnimateTransform
from svgwrite.utils import is_string


class KeyframeError(ValueError):
    """ Raised for invalid keyframe timelines: descending or duplicate
    keyframe times, missing targets, conflicting value types, zero duration
    and keyframes outside of the timeline.
    """


#: transform names supported as keyframe properties
TRANSFORM_PROPERTIES = ('translate', 'rotate')

_CALC_MODES = ('linear', 'discrete')


def _format_number(value):
    """ Stable string for a number: integers without decimal point, floats
    rounded to 6 decimal places with trailing zeros removed, so serialized
    ``values`` and ``keyTimes`` do not depend on float repr details.
    """
    number = float(value)
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    text = '%.6f' % number
    return text.rstrip('0').rstrip('.')


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class _Track(object):
    """ Keyframes of one property of one element. """

    def __init__(self, element, name, kind):
        self.element = element
        self.name = name
        self.kind = kind  # 'number', 'translate', 'rotate', 'rotate3'
        self.times = []
        self.values = []

    def append(self, time, value):
        self.times.append(time)
        self.values.append(value)


def _classify_value(name, value):
    """ Return (kind, normalized value) for a keyframe property value or
    raise KeyframeError for unsupported value types.
    """
    if name == 'translate':
        if (isinstance(value, (tuple, list)) and len(value) == 2
                and all(_is_number(v) for v in value)):
            return 'translate', tuple(value)
        raise KeyframeError(
            "translate keyframe values must be (x, y) number pairs, "
            "got %r" % (value,))
    if name == 'rotate':
        if _is_number(value):
            return 'rotate', value
        if (isinstance(value, (tuple, list)) and len(value) == 3
                and all(_is_number(v) for v in value)):
            return 'rotate3', tuple(value)
        raise KeyframeError(
            "rotate keyframe values must be an angle or an "
            "(angle, cx, cy) triple, got %r" % (value,))
    if _is_number(value):
        return 'number', value
    raise KeyframeError(
        "keyframe values for attribute %r must be numbers, got %r"
        % (name, value))


class KeyframeTimeline(object):
    """ Keyframes of several elements aligned on one shared time axis.

    :param duration: length of the timeline in seconds, must be > 0
    :param calc_mode: ``'linear'`` (interpolated) or ``'discrete'`` (steps)
    :param loop: repeat the animation forever (``repeatCount="indefinite"``)
    :param freeze: hold the final value after the duration
        (``fill="freeze"``), mutually exclusive with **loop**
    """

    def __init__(self, duration, calc_mode='linear', loop=False, freeze=False):
        if not _is_number(duration) or duration <= 0:
            raise KeyframeError(
                "timeline duration must be a number > 0 seconds, got %r"
                % (duration,))
        if calc_mode not in _CALC_MODES:
            raise KeyframeError(
                "calc_mode must be one of %s, got %r"
                % ('/'.join(_CALC_MODES), calc_mode))
        if loop and freeze:
            raise KeyframeError(
                "loop and freeze are mutually exclusive: a looping "
                "animation has no final state to freeze")
        self.duration = float(duration)
        self.calc_mode = calc_mode
        self.loop = bool(loop)
        self.freeze = bool(freeze)
        self._tracks = {}  # (element, name) -> _Track, insertion ordered
        self._last_time = {}  # element -> time of its previous keyframe

    def add_keyframe(self, element, time, **properties):
        """ Add one keyframe for **element** at **time** seconds.

        Every keyword argument is one animated property: a numeric SVG
        attribute (e.g. ``opacity=0.5``), ``translate=(x, y)`` or
        ``rotate=angle`` / ``rotate=(angle, cx, cy)``. Keyframes of one
        element must be added in strictly ascending time order within
        ``[0, duration]``.
        """
        if element is None:
            raise KeyframeError("keyframe is missing its target element")
        if not _is_number(time):
            raise KeyframeError("keyframe time must be a number of "
                                "seconds, got %r" % (time,))
        time = float(time)
        if time < 0 or time > self.duration:
            raise KeyframeError(
                "keyframe at %gs is outside of the timeline "
                "[0s, %gs]" % (time, self.duration))
        previous = self._last_time.get(element)
        if previous is not None:
            if time < previous:
                raise KeyframeError(
                    "keyframe times of one element must be ascending: "
                    "%gs follows %gs" % (time, previous))
            if time == previous:
                raise KeyframeError(
                    "duplicate keyframe time %gs for one element" % time)
        if not properties:
            raise KeyframeError("keyframe at %gs animates no property" % time)
        self._last_time[element] = time
        for name, value in properties.items():
            if not is_string(name):
                raise KeyframeError("property names must be strings, "
                                    "got %r" % (name,))
            kind, value = _classify_value(name, value)
            key = (element, name)
            track = self._tracks.get(key)
            if track is None:
                track = _Track(element, name, kind)
                self._tracks[key] = track
            elif track.kind != kind:
                raise KeyframeError(
                    "conflicting value types for property %r of one "
                    "element: %r does not match the earlier %r keyframes"
                    % (name, kind, track.kind))
            track.append(time, value)

    def _key_times(self, track):
        """ Stable ``keyTimes`` list: seconds mapped into [0, 1]. """
        return ';'.join(_format_number(t / self.duration)
                        for t in track.times)

    def _set_timing(self, animation):
        animation['dur'] = '%ss' % _format_number(self.duration)
        if self.loop:
            animation['repeatCount'] = 'indefinite'
        if self.freeze:
            animation.freeze()

    def _build_track(self, track):
        key_times = self._key_times(track)
        if track.kind == 'number':
            animation = Animate(track.name, href=track.element)
            animation.set_value(
                values=[_format_number(v) for v in track.values],
                calcMode=self.calc_mode,
                keyTimes=key_times)
        else:
            transform = 'rotate' if track.kind in ('rotate', 'rotate3') \
                else 'translate'
            animation = AnimateTransform(transform, href=track.element)
            animation.set_target('transform', 'XML')
            animation.set_value(
                values=[self._format_transform_value(v) for v in track.values],
                calcMode=self.calc_mode,
                keyTimes=key_times)
        self._set_timing(animation)
        return animation

    @staticmethod
    def _format_transform_value(value):
        if _is_number(value):
            return _format_number(value)
        return ' '.join(_format_number(v) for v in value)

    def animations(self):
        """ Expand the timeline into SMIL animation elements, one
        :class:`Animate` per numeric attribute track and one
        :class:`AnimateTransform` per translate/rotate track. The elements
        reference their targets by object, so ``xlink:href`` is resolved to
        a valid id when the drawing is serialized.
        """
        if not self._tracks:
            raise KeyframeError("timeline contains no keyframes")
        return [self._build_track(track) for track in self._tracks.values()]

    def add_to(self, parent):
        """ Build the animation elements, add them to **parent** (e.g. a
        drawing or group) and return them.
        """
        animations = self.animations()
        for animation in animations:
            parent.add(animation)
        return animations
