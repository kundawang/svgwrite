#!/usr/bin/env python
# coding:utf-8
# Purpose: fixed SMIL animation fixtures for the animation QA checks
# Created: 2026-11-22
# License: MIT License
"""
Hand-built, deterministic SMIL animation documents.

Every fixture is constructed with ``debug=False`` so that even deliberately
broken timing/value combinations reach the checker instead of being rejected
up-front by the svgwrite attribute validator.  The *golden* strings freeze the
serialization behaviour: the checker must never alter generated markup.
"""

from svgwrite import Drawing
from svgwrite.animate import Animate, Set, AnimateMotion, AnimateTransform


def healthy_drawing():
    """A document containing only legal, reachable animations.

    Covered constructs that must never produce a finding:

    * clock-offset starts and parallel animations (both start at 0 s)
    * a healthy syncbase chain (``b.begin = a.end``)
    * DOM event synchronisation (``rect.click``)
    * a zero-duration animation
    * intentional ``repeatCount="indefinite"``
    * ``begin="indefinite"`` (script/hyperlink driven start)
    * spline interpolation with matching values/keyTimes/keySplines
    * an ``end`` syncbase reference
    """
    dwg = Drawing('healthy.svg', debug=False)
    rect = dwg.rect(insert=(10, 10), size=(40, 40), id='rect1')
    dwg.add(rect)
    circle = dwg.circle(center=(80, 80), r=5, id='circle1')
    dwg.add(circle)

    # parallel animation starting at document begin
    a = Animate('x', values='0;100', href='#rect1', id='a', debug=False)
    a.set_timing(begin='0s', dur='2s', repeatCount='indefinite')
    dwg.add(a)

    # healthy chain: starts when 'a' ends (a: begin 0s + dur 2s)
    b = Animate('y', values='0;50', href='#rect1', id='b', debug=False)
    b.set_timing(begin='a.end', dur='1s')
    dwg.add(b)

    # chained with a clock offset
    c = Animate('opacity', values='1;0', href='#rect1', id='c', debug=False)
    c.set_timing(begin='b.begin+0.5s', dur='3s')
    dwg.add(c)

    # DOM event synchronisation: user driven, therefore reachable
    d = Set(href='#rect1', id='d', debug=False)
    d.set_target('fill')
    d.set_timing(begin='rect1.click+1s', dur='1s')
    dwg.add(d)

    # zero-duration animation is legal SMIL
    e = Set(href='#circle1', id='e', debug=False)
    e.set_target('r')
    e.set_timing(begin='1s', dur='0s')
    dwg.add(e)

    # begin="indefinite" is started by script/hyperlink on purpose
    f = Animate('cx', values='0;10', href='#circle1', id='f', debug=False)
    f.set_timing(begin='indefinite', dur='2s')
    dwg.add(f)

    # spline interpolation with fully matching boundary data
    g = AnimateTransform('translate', id='g', debug=False)
    g.set_value(values='0;0.5;1', calcMode='spline',
                keyTimes='0;0.5;1', keySplines='0.5 0 0.5 1;0 0 1 1')
    g.set_timing(begin='2s', dur='4s')
    dwg.add(g)

    # end syncbase reference into an existing animation (start still rooted
    # at document begin via its own clock offset)
    h = AnimateMotion(path='m 0 0 l 5 5', href='#circle1', id='h',
                      debug=False)
    h.set_timing(begin='0.5s', end='a.end', dur='1.5s')
    dwg.add(h)

    return dwg


HEALTHY_GOLDEN = (
    '<svg baseProfile="full" height="100%" version="1.1" width="100%" '
    'xmlns="http://www.w3.org/2000/svg" '
    'xmlns:ev="http://www.w3.org/2001/xml-events" '
    'xmlns:xlink="http://www.w3.org/1999/xlink">'
    '<defs />'
    '<rect height="40" id="rect1" width="40" x="10" y="10" />'
    '<circle cx="80" cy="80" id="circle1" r="5" />'
    '<animate attributeName="x" begin="0s" dur="2s" id="a" '
    'repeatCount="indefinite" values="0;100" xlink:href="#rect1" />'
    '<animate attributeName="y" begin="a.end" dur="1s" id="b" '
    'values="0;50" xlink:href="#rect1" />'
    '<animate attributeName="opacity" begin="b.begin+0.5s" dur="3s" id="c" '
    'values="1;0" xlink:href="#rect1" />'
    '<set attributeName="fill" begin="rect1.click+1s" dur="1s" id="d" '
    'xlink:href="#rect1" />'
    '<set attributeName="r" begin="1s" dur="0s" id="e" '
    'xlink:href="#circle1" />'
    '<animate attributeName="cx" begin="indefinite" dur="2s" id="f" '
    'values="0;10" xlink:href="#circle1" />'
    '<animateTransform begin="2s" calcMode="spline" dur="4s" id="g" '
    'keySplines="0.5 0 0.5 1;0 0 1 1" keyTimes="0;0.5;1" '
    'type="translate" values="0;0.5;1" />'
    '<animateMotion begin="0.5s" dur="1.5s" end="a.end" id="h" '
    'path="m 0 0 l 5 5" xlink:href="#circle1" />'
    '</svg>'
)


def mutual_wait_drawing():
    """The icon from the issue report: two animations waiting on each other.

    SVG-legal markup, but the begin graph is a cycle, so neither animation
    ever starts in a real SMIL player.
    """
    dwg = Drawing('mutual-wait.svg', debug=False)
    icon = dwg.g(id='icon')
    dwg.add(icon)
    a = Animate('width', values='0;10', href='#icon', id='spinA',
                debug=False)
    a.set_timing(begin='spinB.begin', dur='1s')
    b = Animate('height', values='0;10', href='#icon', id='spinB',
                debug=False)
    b.set_timing(begin='spinA.begin', dur='1s')
    icon.add(a)
    icon.add(b)
    return dwg


def missing_reference_drawing():
    """begin/end references pointing at ids that do not exist."""
    dwg = Drawing('missing-ref.svg', debug=False)
    a = Animate('x', values='0;1', id='m1', debug=False)
    a.set_timing(begin='ghost.begin', end='#vanish.end')
    dwg.add(a)
    b = Animate('y', values='0;1', id='m2', debug=False)
    b.set_timing(begin='ghost.click')   # missing event target
    dwg.add(b)
    return dwg


def unreachable_chain_drawing():
    """A chain whose root is itself unreachable."""
    dwg = Drawing('unreachable.svg', debug=False)
    root = Animate('x', values='0;1', id='u1', debug=False)
    root.set_timing(begin='u3.end')            # cycle of length 3 below
    middle = Animate('y', values='0;1', id='u2', debug=False)
    middle.set_timing(begin='u1.begin')
    leaf = Animate('width', values='0;1', id='u3', debug=False)
    leaf.set_timing(begin='u2.begin')
    # extra element depending on the broken chain, but not part of the cycle
    tail = Animate('height', values='0;1', id='u4', debug=False)
    tail.set_timing(begin='u3.begin')
    for element in (root, middle, leaf, tail):
        dwg.add(element)
    return dwg


def non_animation_syncbase_drawing():
    """Syncbase reference to a plain shape: no begin/end events can fire."""
    dwg = Drawing('bad-syncbase.svg', debug=False)
    dwg.add(dwg.rect(insert=(0, 0), size=(5, 5), id='plain'))
    a = Animate('x', values='0;1', id='n1', debug=False)
    a.set_timing(begin='plain.begin')
    dwg.add(a)
    return dwg


def boundary_violations_drawing():
    """One element per values/keyTimes/keySplines/dur/repeatCount defect."""
    dwg = Drawing('boundaries.svg', debug=False)

    bad_keytimes_count = Animate('x', values='0;1;2', id='b1', debug=False)
    bad_keytimes_count.set_value(values='0;1;2', keyTimes='0;1')
    bad_keytimes_count.set_timing(dur='1s')
    dwg.add(bad_keytimes_count)

    bad_keytimes_order = Animate('x', values='0;1;2', id='b2', debug=False)
    bad_keytimes_order.set_value(values='0;1;2', keyTimes='0;0.8;0.5')
    bad_keytimes_order.set_timing(dur='1s')
    dwg.add(bad_keytimes_order)

    bad_keytimes_span = Animate('x', values='0;1', id='b3', debug=False)
    bad_keytimes_span.set_value(values='0;1', keyTimes='0.1;0.9')
    bad_keytimes_span.set_timing(dur='1s')
    dwg.add(bad_keytimes_span)

    bad_splines = Animate('x', values='0;1', id='b4', debug=False)
    bad_splines.set_value(values='0;1', calcMode='spline',
                          keyTimes='0;1', keySplines='2 0 0 1')
    bad_splines.set_timing(dur='1s')
    dwg.add(bad_splines)

    spline_without_keytimes = Animate('x', values='0;1', id='b5',
                                      debug=False)
    spline_without_keytimes.set_value(values='0;1', calcMode='spline')
    spline_without_keytimes.set_timing(dur='1s')
    dwg.add(spline_without_keytimes)

    bad_dur = Animate('x', values='0;1', id='b6', debug=False)
    bad_dur.set_timing(dur='-1s')
    dwg.add(bad_dur)

    bad_repeat = Animate('x', values='0;1', id='b7', debug=False)
    bad_repeat.set_timing(dur='1s', repeatCount='-2')
    dwg.add(bad_repeat)

    unparseable_dur = Animate('x', values='0;1', id='b8', debug=False)
    unparseable_dur.set_timing(dur='soon')
    dwg.add(unparseable_dur)

    return dwg
