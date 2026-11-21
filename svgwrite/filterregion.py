#!/usr/bin/env python
# coding:utf-8
# Author: Manfred Moitzi --<mozman@gmx.at>
# Purpose: compute the filter effects region (x/y/width/height) of a
#          <filter> element from the object bounding box and the
#          contained filter primitives
# Created: 2026-11-22
# License: MIT License
"""
This module computes the **filter effects region** (the ``x``, ``y``,
``width`` and ``height`` attributes of a ``<filter>`` element) from the
bounding box of the filtered object and the filter primitives attached to the
filter.

The default SVG filter region is ``x="-10%" y="-10%" width="120%"
height="120%"`` (relative to the object bounding box), which is often too
small: the blurred part of a drop shadow gets clipped at the border of the
filter region. Computing the region from the primitives avoids guessing the
offsets by hand.

The region is the bounding box of everything the primitives may produce:

* :class:`feGaussianBlur` expands the region by ``2 * stdDeviation``
  (the expansion factor required by the Filter Effects specification),
* :class:`feOffset` shifts the region by ``dx``/``dy`` -- negative offsets
  are handled,
* :class:`feMorphology` expands/shrinks the region by its radius,
* :class:`feBlend`, :class:`feComposite`, :class:`feMerge` and the other
  multi-input primitives merge the regions of every referenced input,
  following the ``in``/``in2``/``result`` reference graph.

Conservative fallbacks are used whenever an exact result cannot be computed,
so the returned values are always valid SVG attributes:

* an unbounded/unknown primitive (:class:`feTile`, :class:`feTurbulence`,
  :class:`feFlood`, :class:`feImage`, lighting filters, a convolve matrix
  without usable ``order`` ...) falls back to the spec default region
  ``(-10%, -10%, 120%, 120%)`` in objectBoundingBox units or to an equally
  sized region in user space for ``userSpaceOnUse``,
* a zero area object bounding box falls back to that same default region,
* very large blur/morphology radii are capped so that no infinite or absurd
  coordinate values are produced.
"""

import math
from collections import namedtuple

__all__ = ['BBox', 'FilterRegion', 'calc_filter_region']

#: bounding box as a named 4-tuple (minx, miny, maxx, maxy) in user units
BBox = namedtuple('BBox', ['minx', 'miny', 'maxx', 'maxy'])

#: filter effects region as a named 4-tuple (x, y, width, height)
FilterRegion = namedtuple('FilterRegion', ['x', 'y', 'width', 'height'])

# spec default filter region, as fractions of the object bounding box
DEFAULT_OBB_REGION = (-0.1, -0.1, 1.2, 1.2)

#: expansion factor for feGaussianBlur, as mandated by the Filter Effects
#: specification: the bounding box grows by 2 * stdDeviation on every side
BLUR_EXPANSION_FACTOR = 2.0

#: largest absolute coordinate the computed region may reach; keeps huge
#: radii from producing infinite or unusable SVG attributes
MAX_COORDINATE = 1.0e6

#: named inputs that always exist, even without a matching ``result``
STANDARD_INPUTS = frozenset([
    'SourceGraphic', 'SourceAlpha', 'BackgroundImage', 'BackgroundAlpha',
    'FillPaint', 'StrokePaint',
])

UNBOUNDED_PRIMITIVES = frozenset([
    'feTile', 'feTurbulence', 'feFlood', 'feImage',
    'feDiffuseLighting', 'feSpecularLighting',
])


def _to_float(value):
    """Return *value* as float or ``None`` if it cannot be parsed."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        # only plain numbers are supported; values with units (em, %, ...)
        # cannot be converted to user units without a rendering context
        if text.endswith('%'):
            return None
        result = float(text)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _clean(value):
    """Round to a stable precision so str() stays free of float artifacts."""
    rounded = round(value, 6)
    if rounded == int(rounded) and abs(rounded) < 1e15:
        return float(int(rounded))
    return rounded


def _pair(value, count):
    """Parse an SVG 1- or 2-value attribute into a ``count``-tuple.

    ``"4"`` -> (4.0, 4.0); ``"4 7"`` -> (4.0, 7.0). Returns ``None`` if the
    value is missing or cannot be parsed.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return (v,) * count
    try:
        numbers = [float(part) for part in str(value).replace(',', ' ').split()]
    except ValueError:
        return None
    if not numbers or any(not math.isfinite(v) for v in numbers):
        return None
    if len(numbers) == 1:
        numbers = numbers * count
    if len(numbers) != count:
        return None
    return tuple(numbers)


def _union(boxes):
    """Union of an iterable of :class:`BBox`; ``None`` entries are ignored.

    Returns ``None`` if none of the boxes is usable.
    """
    minx = miny = float('inf')
    maxx = maxy = float('-inf')
    found = False
    for box in boxes:
        if box is None:
            continue
        found = True
        minx = min(minx, box.minx)
        miny = min(miny, box.miny)
        maxx = max(maxx, box.maxx)
        maxy = max(maxy, box.maxy)
    if not found:
        return None
    return BBox(minx, miny, maxx, maxy)


def _expand(box, dx, dy):
    """Grow *box* by *dx*/*dy* on every side, capped to MAX_COORDINATE."""
    if box is None:
        return None
    dx = max(0.0, min(float(dx), MAX_COORDINATE))
    dy = max(0.0, min(float(dy), MAX_COORDINATE))
    return BBox(
        max(-MAX_COORDINATE, box.minx - dx),
        max(-MAX_COORDINATE, box.miny - dy),
        min(MAX_COORDINATE, box.maxx + dx),
        min(MAX_COORDINATE, box.maxy + dy),
    )


def _shift(box, dx, dy):
    """Translate *box* by *dx*/*dy*, capped to MAX_COORDINATE."""
    if box is None:
        return None
    return BBox(
        max(-MAX_COORDINATE, box.minx + dx),
        max(-MAX_COORDINATE, box.miny + dy),
        min(MAX_COORDINATE, box.maxx + dx),
        min(MAX_COORDINATE, box.maxy + dy),
    )


def _clip_to_subregion(box, subregion):
    """Intersect *box* with an explicit primitive subregion.

    An unknown/unparseable subregion is ignored. A disjoint intersection
    produces a degenerate rectangle, which is treated as ``None`` so the
    primitive cannot contribute negative width/height.
    """
    if subregion is None:
        return box
    if box is None:
        return subregion
    clipped = BBox(
        max(box.minx, subregion.minx),
        max(box.miny, subregion.miny),
        min(box.maxx, subregion.maxx),
        min(box.maxy, subregion.maxy),
    )
    if clipped.maxx < clipped.minx or clipped.maxy < clipped.miny:
        return None
    return clipped


def _object_bbox(bbox):
    """Normalize the object bounding box argument to a :class:`BBox`."""
    if bbox is None:
        return None
    try:
        minx, miny, width, height = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (minx, miny, width, height)):
        return None
    return BBox(minx, miny, minx + width, miny + height)


def _default_region(filter_units, object_box):
    """The conservative fallback region.

    Returns the spec default region ``(-10%, -10%, 120%, 120%)`` in
    objectBoundingBox units, or an equally sized region in user space.
    """
    if filter_units != 'userSpaceOnUse':
        return FilterRegion(*DEFAULT_OBB_REGION)
    if object_box is not None and object_box.maxx > object_box.minx \
            and object_box.maxy > object_box.miny:
        width = object_box.maxx - object_box.minx
        height = object_box.maxy - object_box.miny
        minx = object_box.minx - 0.1 * width
        miny = object_box.miny - 0.1 * height
        return FilterRegion(minx, miny, 1.2 * width, 1.2 * height)
    # a userSpaceOnUse filter without a usable object box has no origin to
    # anchor to; fall back to the origin-based default
    return FilterRegion(0.0, 0.0, 1.0, 1.0)


def _parse_subregion(primitive, object_box, primitive_units):
    """Parse the explicit x/y/width/height subregion of a primitive.

    Returns a :class:`BBox` in user units, or ``None`` if the primitive has
    no complete, parseable subregion.
    """
    x = _to_float(primitive.attribs.get('x'))
    y = _to_float(primitive.attribs.get('y'))
    width = _to_float(primitive.attribs.get('width'))
    height = _to_float(primitive.attribs.get('height'))
    if None in (x, y, width, height):
        return None
    if primitive_units == 'objectBoundingBox' and object_box is not None:
        obw = object_box.maxx - object_box.minx
        obh = object_box.maxy - object_box.miny
        x = object_box.minx + x * obw
        y = object_box.miny + y * obh
        width *= obw
        height *= obh
    if width < 0.0 or height < 0.0:
        return None
    return BBox(x, y, x + width, y + height)


def _resolve_input(name, results, object_box):
    """Look up the region produced by an input reference.

    * missing input (``name is None``): the previous primitive's result, or
      the object bounding box for the first primitive;
    * standard input (``SourceGraphic`` ...): the object bounding box;
    * named input without a matching ``result``: conservatively the object
      bounding box (e.g. a reference that was never bound to a result).
    """
    if name is None:
        return results.get('__last__', object_box)
    if name in STANDARD_INPUTS:
        return object_box
    return results.get(name, object_box)


def _blur_region(primitive, input_box):
    values = _pair(primitive.attribs.get('stdDeviation'), 2)
    if values is None:
        return input_box
    sx, sy = values
    if sx < 0.0 or sy < 0.0:
        # negative stdDeviation is invalid; treat the primitive as unknown
        return None
    return _expand(input_box, BLUR_EXPANSION_FACTOR * sx,
                   BLUR_EXPANSION_FACTOR * sy)


def _morphology_region(primitive, input_box):
    values = _pair(primitive.attribs.get('radius'), 2)
    if values is None:
        return None
    rx, ry = values
    if rx < 0.0 or ry < 0.0:
        return None
    operator = primitive.attribs.get('operator', 'erode')
    if operator == 'dilate':
        return _expand(input_box, rx, ry)
    # erode can only shrink the region; the input region stays a safe bound
    return input_box


def _offset_region(primitive, input_box):
    dx = _to_float(primitive.attribs.get('dx'))
    dy = _to_float(primitive.attribs.get('dy'))
    if dx is None or dy is None:
        return input_box
    return _shift(input_box, dx, dy)


def _convolve_region(primitive, input_box):
    order = _pair(primitive.attribs.get('order', 3), 2)
    if order is None:
        return None
    columns, rows = order
    if columns < 1.0 or rows < 1.0:
        return None
    kernel = _pair(primitive.attribs.get('kernelUnitLength'), 2)
    if kernel is None:
        # kernelUnitLength defaults to one px
        kernel = (1.0, 1.0)
    kx, ky = kernel
    if kx <= 0.0 or ky <= 0.0:
        return None
    # the kernel reaches (order-1)/2 kernel units past every edge
    return _expand(input_box, (columns - 1.0) / 2.0 * kx,
                   (rows - 1.0) / 2.0 * ky)


def _displacement_region(primitive, input_box):
    scale = _to_float(primitive.attribs.get('scale'))
    if scale is None or scale < 0.0:
        return None
    # a displacement of at most ``scale`` user units may occur in any
    # direction; this is a deliberately conservative bound
    return _expand(input_box, scale, scale)


def _primitive_region(primitive, results, object_box):
    """Compute the output region of one filter primitive.

    Returns a :class:`BBox` in user units, or ``None`` if the primitive is
    unbounded/unknown and the conservative default region has to be used.
    """
    name = primitive.elementname

    if name in UNBOUNDED_PRIMITIVES:
        return None

    if name == 'feGaussianBlur':
        return _blur_region(primitive, _resolve_input(
            primitive.attribs.get('in'), results, object_box))

    if name == 'feOffset':
        return _offset_region(primitive, _resolve_input(
            primitive.attribs.get('in'), results, object_box))

    if name == 'feMorphology':
        return _morphology_region(primitive, _resolve_input(
            primitive.attribs.get('in'), results, object_box))

    if name == 'feConvolveMatrix':
        return _convolve_region(primitive, _resolve_input(
            primitive.attribs.get('in'), results, object_box))

    if name == 'feDisplacementMap':
        box = _union([
            _resolve_input(primitive.attribs.get('in'), results, object_box),
            _resolve_input(primitive.attribs.get('in2'), results, object_box),
        ])
        return _displacement_region(primitive, box)

    if name in ('feBlend', 'feComposite', 'feColorMatrix',
                'feComponentTransfer'):
        # these primitives never paint outside their input region(s);
        # 'arithmetic' compositing is bounded by the subregion clipping rule
        return _union([
            _resolve_input(primitive.attribs.get('in'), results, object_box),
            _resolve_input(primitive.attribs.get('in2'), results, object_box),
        ])

    if name == 'feMerge':
        boxes = []
        for node in primitive.elements:
            if node.elementname == 'feMergeNode':
                boxes.append(_resolve_input(
                    node.attribs.get('in'), results, object_box))
        return _union(boxes)

    # unknown primitive: do not guess
    return None


def _iter_primitives(filter_element):
    """Yield the filter primitives in document order.

    Every child whose element name starts with ``fe`` is treated as a
    primitive, so unknown/future primitives are not silently ignored (they
    trigger the conservative fallback region instead). feMergeNode children
    stay nested inside their feMerge parent.
    """
    for element in filter_element.elements:
        if element.elementname.startswith('fe') \
                and element.elementname != 'feMergeNode':
            yield element


def calc_filter_region(filter_element, bbox, filter_units=None,
                       primitive_units=None):
    """Compute the filter effects region of *filter_element*.

    :param filter_element: a :class:`svgwrite.filters.Filter` object with the
        filter primitives as subelements
    :param bbox: object bounding box as a 4-tuple
        ``(minx, miny, width, height)`` in user units, or ``None`` if the
        object geometry is unknown
    :param filter_units: ``'objectBoundingBox'`` (default) or
        ``'userSpaceOnUse'``; overrides the ``filterUnits`` attribute of the
        filter element when given
    :param primitive_units: ``'userSpaceOnUse'`` (default) or
        ``'objectBoundingBox'``; overrides the ``primitiveUnits`` attribute
        when given
    :returns: :class:`FilterRegion` ``(x, y, width, height)``. The values are
        fractions of the object bounding box for objectBoundingBox units and
        user space coordinates for userSpaceOnUse. A conservative fallback
        region (see module docstring) is returned whenever the exact region
        cannot be computed.
    """
    if filter_units is None:
        filter_units = filter_element.attribs.get(
            'filterUnits', 'objectBoundingBox')
    if primitive_units is None:
        primitive_units = filter_element.attribs.get(
            'primitiveUnits', 'userSpaceOnUse')

    object_box = _object_bbox(bbox)
    fallback = _default_region(filter_units, object_box)

    # zero area objects cannot define an objectBoundingBox coordinate system
    if object_box is None or object_box.maxx <= object_box.minx \
            or object_box.maxy <= object_box.miny:
        return fallback

    # ``in``/``result`` name -> output BBox (user units);
    # __last__ tracks the result of the previous primitive
    results = {}
    overall = object_box
    unknown = False

    for primitive in _iter_primitives(filter_element):
        subregion = _parse_subregion(
            primitive, object_box, primitive_units)
        box = _primitive_region(primitive, results, object_box)

        if box is None:
            # unbounded/unknown primitive: an explicit subregion still bounds
            # its output, otherwise the whole filter gets the fallback region
            if subregion is None:
                unknown = True
                continue
            box = subregion
        else:
            box = _clip_to_subregion(box, subregion)
            if box is None:
                # explicit subregion disjoint from the computed region:
                # the primitive paints nothing relevant
                continue

        overall = _union([overall, box])
        result_name = primitive.attribs.get('result')
        if result_name is not None:
            results[result_name] = box
        results['__last__'] = box

    if unknown or overall is None:
        return fallback

    x, y = overall.minx, overall.miny
    width = overall.maxx - overall.minx
    height = overall.maxy - overall.miny

    if filter_units == 'userSpaceOnUse':
        return FilterRegion(_clean(x), _clean(y), _clean(width),
                            _clean(height))

    obw = object_box.maxx - object_box.minx
    obh = object_box.maxy - object_box.miny
    # objectBoundingBox coordinates are relative to the object box origin
    return FilterRegion(
        _clean((x - object_box.minx) / obw),
        _clean((y - object_box.miny) / obh),
        _clean(width / obw),
        _clean(height / obh))
