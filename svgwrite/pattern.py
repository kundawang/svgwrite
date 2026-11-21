#!/usr/bin/env python
#coding:utf-8
# Author:  mozman --<mozman@gmx.at>
# Purpose: pattern module
# Created: 29.10.2010
# Copyright (C) 2010, Manfred Moitzi
# License: MIT License

from svgwrite.base import BaseElement
from svgwrite.mixins import XLink, ViewBox, Transform, Presentation
from svgwrite.patternlayout import (
    PatternLayoutError, PatternLimitError, PatternTile,
    compute_pattern_tiles,
)
from svgwrite.utils import is_string

class Pattern(BaseElement, XLink, ViewBox, Transform, Presentation):
    """
    A pattern is used to fill or stroke an object using a pre-defined graphic
    object which can be replicated ("tiled") at fixed intervals in x and y to
    cover the areas to be painted. Patterns are defined using a `pattern` element
    and then referenced by properties `fill` and `stroke` on a given graphics
    element to indicate that the given element shall be filled or stroked with
    the referenced pattern.
    """
    elementname = 'pattern'
    transformname = 'patternTransform'

    def __init__(self, insert=None, size=None, inherit=None, **extra):
        """
        :param 2-tuple insert: base point of the pattern (**x**, **y**)
        :param 2-tuple size: size of the pattern (**width**, **height**)
        :param inherit: pattern inherits properties from `inherit` see: **xlink:href**

        """
        super(Pattern, self).__init__(**extra)
        if insert is not None:
            self['x'] = insert[0]
            self['y'] = insert[1]
        if size is not None:
            self['width'] = size[0]
            self['height'] = size[1]
        if inherit is not None:
            if is_string(inherit):
                self.set_href(inherit)
            else:
                # keep the object reference for the inheritance chain used by
                # visible_tiles() while serializing the IRI as before
                self.set_href(inherit.get_iri())
                self.href = inherit

        if self.debug:
            self.validator.check_all_svg_attribute_values(self.elementname, self.attribs)

    def get_paint_server(self, default='none'):
        """ Returns the <FuncIRI> of the gradient. """
        return "%s %s" % (self.get_funciri(), default)

    def visible_tiles(self, bbox, viewport=None, **kwargs):
        """ Compute the pattern tiles visible on a canvas.

        :param bbox: target object bounding box ``(x, y, width, height)`` in
            user space; also defines the ``objectBoundingBox`` reference
        :param viewport: canvas viewport ``(x, y, width, height)``; defaults
            to *bbox*. Only tiles intersecting both rectangles are returned.
        :param int max_tiles: candidate tile cap (default 100000), see
            :class:`svgwrite.patternlayout.PatternLimitError`
        :param dict registry: mapping ``'#id' -> Pattern`` for resolving
            string ``xlink:href`` inheritance references
        :returns: row-major ordered ``list`` of
            :class:`svgwrite.patternlayout.PatternTile`; each tile carries
            its integer index ``(i, j)`` and the full content-to-user-space
            transform matrix.

        Supports ``patternUnits`` (``userSpaceOnUse`` /
        ``objectBoundingBox``), ``patternTransform``, ``viewBox`` with
        ``preserveAspectRatio``, negative coordinates, non integer sizes and
        the ``xlink:href`` inheritance chain. Raises
        :class:`svgwrite.patternlayout.PatternLayoutError` for zero size
        tiles, singular transforms or broken inheritance and
        :class:`PatternLimitError` when *max_tiles* is exceeded.
        """
        return compute_pattern_tiles(self, bbox, viewport, **kwargs)
