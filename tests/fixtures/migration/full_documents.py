#!/usr/bin/env python
#coding:utf-8
# Purpose: Full-profile fixture documents for the Tiny 1.2 migration tests.
#          Each builder is paired with an expected Tiny 1.2 output file
#          ``<case>_tiny.svg`` and, for degrading cases, an expected issue
#          list ``<case>_report.txt`` in the same directory.
# License: MIT License
from __future__ import unicode_literals

import svgwrite


def shapes_group_text():
    """Common shapes, nested groups and text: fully migratable."""
    dwg = svgwrite.Drawing('shapes_group_text.svg', profile='full',
                           size=('200px', '100px'))
    dwg.viewbox(0, 0, 200, 100)

    panel = dwg.g(id='panel', transform='translate(5,5)', opacity=0.9)
    panel.add(dwg.rect((10, 10), (50, 20), id='r1', rx=2,
                       fill='#ff0000', stroke='black', stroke_width=2))
    panel.add(dwg.circle((100, 30), 15, id='c1', fill='green',
                         fill_opacity=0.5))
    panel.add(dwg.ellipse((150, 30), (20, 10), id='e1', fill='blue'))
    panel.add(dwg.line((10, 40), (60, 40), id='l1', stroke='navy',
                       stroke_dasharray='4 2'))
    panel.add(dwg.polyline([(10, 50), (20, 60), (30, 50)], id='pl1',
                           fill='none', stroke='black'))
    panel.add(dwg.polygon([(40, 50), (50, 60), (60, 50)], id='pg1',
                          fill='yellow', stroke='black'))
    panel.add(dwg.path(d='M70 50 L80 60 L90 50 Z', id='p1', fill='orange'))

    label = dwg.text('Hello', insert=(10, 80), id='t1',
                     font_family='Arial', font_size=12, fill='black')
    label.add(dwg.tspan('world', fill='blue'))
    panel.add(label)

    inner = dwg.g(id='inner', transform='scale(0.5)')
    inner.add(dwg.rect((0, 0), (10, 10), id='r2', fill='gray'))
    panel.add(inner)

    dwg.add(panel)
    return dwg


def gradients_use_defs():
    """Shared defs, gradients and use references: fully migratable."""
    dwg = svgwrite.Drawing('gradients_use_defs.svg', profile='full',
                           size=('100%', '100%'))
    dwg.viewbox(0, 0, 100, 50)

    lin = dwg.linearGradient(id='grad-lin', start=(0, 0), end=(1, 0))
    lin.add_stop_color(0, 'red')
    lin.add_stop_color(1, 'blue', opacity=0.5)
    dwg.defs.add(lin)

    rad = dwg.radialGradient(id='grad-rad', center=(0.5, 0.5), r=0.5)
    rad.add_stop_color(0, 'white')
    rad.add_stop_color(1, 'black')
    dwg.defs.add(rad)

    dwg.defs.add(dwg.rect((0, 0), (10, 10), id='tile', fill='url(#grad-lin)'))

    dwg.add(dwg.use('#tile', insert=(5, 5), id='u1'))
    dwg.add(dwg.use('#tile', insert=(20, 5), id='u2'))
    dwg.add(dwg.circle((50, 25), 20, id='c1', fill='url(#grad-rad)'))
    return dwg


def unsupported_content():
    """Elements and attributes Tiny 1.2 cannot express."""
    dwg = svgwrite.Drawing('unsupported_content.svg', profile='full',
                           size=('100px', '100px'))

    # unsupported elements
    marker = dwg.marker(id='arrow', insert=(0, 0), size=(4, 4))
    marker.add(dwg.path(d='M0 0 L4 2 L0 4 Z', id='arrow-shape'))
    dwg.defs.add(marker)
    dwg.defs.add(dwg.filter(id='blur'))
    dwg.defs.add(dwg.clipPath(id='clip1'))

    # unsupported attributes on otherwise supported elements
    grad = dwg.linearGradient(id='grad1', start=(0, 0), end=(1, 0),
                              spreadMethod='reflect')
    grad['gradientTransform'] = 'rotate(45)'
    grad.add_stop_color(0, 'red')
    dwg.defs.add(grad)

    line = dwg.line((0, 0), (100, 0), id='l1', stroke='black',
                    marker_end='url(#arrow)')
    line['marker-start'] = 'url(#arrow)'
    dwg.add(line)

    text = dwg.text('ref:', insert=(0, 20), id='t1')
    text.add(dwg.tref('#t1'))  # tref is not part of Tiny 1.2
    dwg.add(text)
    return dwg


def dangling_reference():
    """A use reference whose target cannot be migrated (symbol)."""
    dwg = svgwrite.Drawing('dangling_reference.svg', profile='full',
                           size=('100px', '100px'))
    symbol = dwg.symbol(id='sym1')
    symbol.add(dwg.rect((0, 0), (10, 10), id='sym-rect', fill='red'))
    dwg.defs.add(symbol)
    dwg.add(dwg.use('#sym1', insert=(5, 5), id='u1'))
    dwg.add(dwg.circle((50, 50), 10, id='c1', fill='green'))
    return dwg


BUILDERS = {
    'shapes_group_text': shapes_group_text,
    'gradients_use_defs': gradients_use_defs,
    'unsupported_content': unsupported_content,
    'dangling_reference': dangling_reference,
}
