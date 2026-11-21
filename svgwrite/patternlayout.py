#!/usr/bin/env python
# coding:utf-8
# Author: kundawang
# Purpose: compute the visible tiles of an SVG pattern
# Created: 2026-11-22
# License: MIT License
"""
Pattern tile layout computation.

This module turns the *serialized* attributes of a :class:`svgwrite.pattern.Pattern`
element (``x``, ``y``, ``width``, ``height``, ``patternUnits``,
``patternContentUnits``, ``patternTransform``, ``viewBox``,
``preserveAspectRatio`` and the ``xlink:href`` inheritance chain) into the
concrete list of tiles a previewer has to paint on a given canvas.

The geometry follows SVG 1.1 / SVG 2:

* a tile at index ``(i, j)`` has its origin at
  ``(x + i*width, y + j*height)`` in pattern coordinate space;
* ``patternUnits='objectBoundingBox'`` maps that space through the target
  object bounding box, ``userSpaceOnUse`` leaves it in user space;
* ``patternTransform`` wraps the whole tiling;
* ``viewBox`` / ``preserveAspectRatio`` and ``patternContentUnits`` map the
  pattern contents inside each tile.

Every number is read back through the same string forms that svgwrite
serializes (``str(number)``, comma/white space separated lists and the
transform list produced by :class:`svgwrite.mixins.Transform`), so computed
geometry always matches the written SVG attributes.
"""

import math
import re
from collections import namedtuple

__all__ = [
    'AffineMatrix',
    'PatternTile',
    'PatternLayoutError',
    'PatternLimitError',
    'compute_pattern_tiles',
    'parse_transform_list',
]

#: Hard cap for the inheritance chain, mirrors the SVG reference loop rule.
MAX_INHERIT_DEPTH = 16

#: CSS length units expressed in user units (px).
_UNIT_FACTORS = {
    'px': 1.0,
    'in': 96.0,
    'cm': 96.0 / 2.54,
    'mm': 96.0 / 25.4,
    'pt': 96.0 / 72.0,
    'pc': 96.0 / 6.0,
}

#: Units that depend on font metrics and cannot be resolved without a
#: rendering environment.
_FONT_UNITS = frozenset(('em', 'ex'))

_NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
_LENGTH_RE = re.compile(
    r'^(?P<number>' + _NUMBER + r')(?P<unit>cm|em|ex|in|mm|pc|pt|px|%)?$'
)
_TRANSFORM_RE = re.compile(
    r'(matrix|translate|scale|rotate|skewX|skewY)\s*'
    r'\(\s*([^)]*?)\s*\)'
)
_SPLIT_RE = re.compile(r'[\s,]+')

# Determinant below ``_SINGULAR_EPS * linear_scale**2`` counts as singular.
_SINGULAR_EPS = 1e-12
# Overlap below ``_OVERLAP_EPS * scene_scale`` counts as boundary tangency,
# i.e. the tile only touches the clip rectangle and is not returned.
_OVERLAP_EPS = 1e-10


class PatternLayoutError(ValueError):
    """Raised when a pattern cannot be laid out (bad attributes, zero tile
    size, singular transform, broken inheritance chain, ...)."""


class PatternLimitError(PatternLayoutError):
    """Raised when the number of candidate tiles exceeds *max_tiles*.

    This guarantees that huge viewports or degenerate geometry can never
    produce an unbounded tiling loop.
    """


class AffineMatrix(namedtuple('AffineMatrix', 'a b c d e f')):
    """A 2D affine transform in SVG ``matrix(a, b, c, d, e, f)`` convention.

    Column vectors are used, so for :meth:`compose` the right hand matrix is
    applied first.
    """

    @classmethod
    def identity(cls):
        return cls(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    @classmethod
    def translate(cls, tx, ty):
        return cls(1.0, 0.0, 0.0, 1.0, float(tx), float(ty))

    @classmethod
    def scale(cls, sx, sy=None):
        if sy is None:
            sy = sx
        return cls(float(sx), 0.0, 0.0, float(sy), 0.0, 0.0)

    def apply(self, x, y):
        """Transform a single point."""
        return (self.a * x + self.c * y + self.e,
                self.b * x + self.d * y + self.f)

    def compose(self, other):
        """Return ``self * other`` (*other* is applied first)."""
        a, b, c, d, e, f = self
        g, h, i, j, k, l = other
        return AffineMatrix(
            a * g + c * h,
            b * g + d * h,
            a * i + c * j,
            b * i + d * j,
            a * k + c * l + e,
            b * k + d * l + f,
        )

    def determinant(self):
        return self.a * self.d - self.b * self.c

    def inverse(self):
        det = self.determinant()
        lin_scale = max(abs(self.a), abs(self.b), abs(self.c), abs(self.d),
                        1e-12)
        if abs(det) <= _SINGULAR_EPS * lin_scale * lin_scale:
            raise PatternLayoutError(
                "pattern transform is singular (determinant %g)" % det)
        a, b, c, d, e, f = self
        return AffineMatrix(
            d / det, -b / det, -c / det, a / det,
            (c * f - d * e) / det,
            (b * e - a * f) / det,
        )

    def to_list(self):
        """Return the matrix as the SVG ``matrix()`` argument tuple."""
        return list(self)


class PatternTile(namedtuple('PatternTile', 'i j matrix bounds')):
    """One visible tile.

    :param int i: horizontal tile index (may be negative)
    :param int j: vertical tile index (may be negative)
    :param AffineMatrix matrix: full content-to-user-space transform of
        this tile, including ``patternTransform``, units, ``viewBox`` and
        ``patternContentUnits``
    :param tuple bounds: user-space axis aligned bounding box
        ``(xmin, ymin, xmax, ymax)`` of the (possibly rotated) tile
    """


# ----------------------------------------------------------------------
# transform list parsing
# ----------------------------------------------------------------------

def _parse_number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        raise PatternLayoutError("invalid number %r" % text)


def parse_transform_list(transform):
    """Parse an SVG transform-list into one :class:`AffineMatrix`.

    Accepts exactly the syntax written by :class:`svgwrite.mixins.Transform`
    (``translate``, ``scale``, ``rotate`` with optional center, ``skewX``,
    ``skewY`` and ``matrix``), separated by white space or commas.
    Returns the identity for ``None`` or an empty string.
    """
    if not transform:
        return AffineMatrix.identity()
    if isinstance(transform, AffineMatrix):
        return transform

    result = AffineMatrix.identity()
    pos = 0
    text = str(transform)
    for match in _TRANSFORM_RE.finditer(text):
        if text[pos:match.start()].strip():
            raise PatternLayoutError(
                "invalid patternTransform %r" % transform)
        cmd = match.group(1)
        args = [v for v in _SPLIT_RE.split(match.group(2).strip()) if v]
        values = [_parse_number(v) for v in args]
        if cmd == 'matrix':
            if len(values) != 6:
                raise PatternLayoutError(
                    "matrix() needs 6 arguments in %r" % transform)
            step = AffineMatrix(*values)
        elif cmd == 'translate':
            if len(values) not in (1, 2):
                raise PatternLayoutError(
                    "translate() needs 1 or 2 arguments in %r" % transform)
            tx = values[0]
            ty = values[1] if len(values) == 2 else 0.0
            step = AffineMatrix.translate(tx, ty)
        elif cmd == 'scale':
            if len(values) not in (1, 2):
                raise PatternLayoutError(
                    "scale() needs 1 or 2 arguments in %r" % transform)
            step = AffineMatrix.scale(
                values[0], values[1] if len(values) == 2 else None)
        elif cmd == 'rotate':
            if len(values) not in (1, 3):
                raise PatternLayoutError(
                    "rotate() needs 1 or 3 arguments in %r" % transform)
            angle = math.radians(values[0])
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            rotation = AffineMatrix(cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(values) == 3:
                cx, cy = values[1], values[2]
                step = (AffineMatrix.translate(cx, cy)
                        .compose(rotation)
                        .compose(AffineMatrix.translate(-cx, -cy)))
            else:
                step = rotation
        elif cmd == 'skewX':
            if len(values) != 1:
                raise PatternLayoutError(
                    "skewX() needs 1 argument in %r" % transform)
            step = AffineMatrix(1.0, 0.0, math.tan(math.radians(values[0])),
                                1.0, 0.0, 0.0)
        else:  # skewY
            if len(values) != 1:
                raise PatternLayoutError(
                    "skewY() needs 1 argument in %r" % transform)
            step = AffineMatrix(1.0, math.tan(math.radians(values[0])),
                                0.0, 1.0, 0.0, 0.0)
        result = result.compose(step)
        pos = match.end()
    if text[pos:].strip():
        raise PatternLayoutError("invalid patternTransform %r" % transform)
    return result


# ----------------------------------------------------------------------
# length / coordinate parsing
# ----------------------------------------------------------------------

def _parse_length(value):
    """Return ``(number, unit)``; unit is ``None`` or a CSS unit / '%'."""
    if isinstance(value, bool):
        raise PatternLayoutError("invalid length %r" % value)
    if isinstance(value, (int, float)):
        return float(value), None
    if not isinstance(value, str):
        raise PatternLayoutError("invalid length %r" % value)
    match = _LENGTH_RE.match(value.strip())
    if match is None:
        raise PatternLayoutError("invalid length %r" % value)
    return float(match.group('number')), match.group('unit')


def _resolve_length(value, percentage_reference):
    """Resolve a length attribute to user units.

    Percentages are taken against *percentage_reference*; plain numbers keep
    their numeric value; CSS units are converted to px.
    """
    number, unit = _parse_length(value)
    if unit is None or unit == 'px':
        return number
    if unit == '%':
        return number / 100.0 * percentage_reference
    if unit in _FONT_UNITS:
        raise PatternLayoutError(
            "font relative unit %r cannot be resolved without a rendering "
            "environment" % value)
    return number * _UNIT_FACTORS[unit]


# ----------------------------------------------------------------------
# viewBox / preserveAspectRatio
# ----------------------------------------------------------------------

_PAR_ALIGN_X = {'xMin': -1, 'xMid': 0, 'xMax': 1}
_PAR_ALIGN_Y = {'YMin': -1, 'YMid': 0, 'YMax': 1}


def _parse_preserve_aspect_ratio(text):
    """Parse preserveAspectRatio into ``(align_x, align_y, meet_or_slice)``.

    ``align_x`` / ``align_y`` are ``-1`` (Min), ``0`` (Mid) or ``1`` (Max).
    A value of ``'none'`` is returned as ``(None, None, None)``.
    """
    if not text:
        text = 'xMidYMid meet'
    tokens = text.replace(',', ' ').split()
    if tokens and tokens[0] == 'defer':
        tokens = tokens[1:]
    if not tokens:
        tokens = ['xMidYMid', 'meet']
    align = tokens[0]
    meet_or_slice = tokens[1] if len(tokens) > 1 else 'meet'
    if meet_or_slice not in ('meet', 'slice'):
        raise PatternLayoutError(
            "invalid preserveAspectRatio %r" % text)
    if align == 'none':
        return None, None, None
    # align is '<xAlign><YAlign>', each part is four characters
    # ('xMin' / 'xMid' / 'xMax' and 'YMin' / 'YMid' / 'YMax')
    x_token, y_token = align[:4], align[4:]
    if x_token not in _PAR_ALIGN_X or y_token not in _PAR_ALIGN_Y:
        raise PatternLayoutError(
            "invalid preserveAspectRatio %r" % text)
    return _PAR_ALIGN_X[x_token], _PAR_ALIGN_Y[y_token], meet_or_slice


def _viewbox_matrix(viewbox, par_text, viewport_w, viewport_h):
    """Map viewBox coordinates to a viewport of ``viewport_w x viewport_h``."""
    try:
        vb_x, vb_y, vb_w, vb_h = (
            _parse_number(v) for v in _SPLIT_RE.split(str(viewbox).strip())
            if v)
    except ValueError:
        raise PatternLayoutError("invalid viewBox %r" % viewbox)
    if vb_w <= 0 or vb_h <= 0:
        raise PatternLayoutError(
            "viewBox width and height must be positive, got %r" % viewbox)

    align_x, align_y, meet_or_slice = _parse_preserve_aspect_ratio(par_text)
    if align_x is None:  # 'none' -> stretch
        sx, sy = viewport_w / vb_w, viewport_h / vb_h
        tx, ty = 0.0, 0.0
    else:
        if meet_or_slice == 'meet':
            scale = min(viewport_w / vb_w, viewport_h / vb_h)
        else:
            scale = max(viewport_w / vb_w, viewport_h / vb_h)
        sx = sy = scale
        tx = _align_offset(align_x, viewport_w, vb_w * scale)
        ty = _align_offset(align_y, viewport_h, vb_h * scale)
    # p_viewport = scale * (p_viewbox - (vb_x, vb_y)) + (tx, ty)
    return AffineMatrix(sx, 0.0, 0.0, sy,
                        tx - sx * vb_x, ty - sy * vb_y)


def _align_offset(align, viewport_size, scaled_size):
    if align < 0:
        return 0.0
    if align == 0:
        return (viewport_size - scaled_size) / 2.0
    return viewport_size - scaled_size


# ----------------------------------------------------------------------
# inheritance chain
# ----------------------------------------------------------------------

def _resolve_chain(pattern, registry):
    """Return the pattern chain from *pattern* to its last href ancestor.

    Attributes are looked up front to back, so the first element has the
    highest priority. *registry* maps ``'#id'`` strings to pattern objects.
    """
    chain = []
    seen = set()
    node = pattern
    while node is not None:
        marker = id(node)
        if marker in seen:
            raise PatternLayoutError(
                "cyclic xlink:href reference in pattern inheritance chain")
        seen.add(marker)
        chain.append(node)
        if len(chain) > MAX_INHERIT_DEPTH:
            raise PatternLayoutError(
                "pattern inheritance chain exceeds %d ancestors"
                % MAX_INHERIT_DEPTH)

        href_object = getattr(node, 'href', None)
        if href_object is not None and not isinstance(href_object, str):
            node = href_object
            continue
        iri = node.attribs.get('xlink:href')
        if not iri:
            break
        if not isinstance(iri, str):
            raise PatternLayoutError("invalid xlink:href %r" % iri)
        if iri.startswith('url(') and iri.endswith(')'):
            iri = iri[4:-1]
        if not iri.startswith('#'):
            iri = '#' + iri
        if registry is None or iri not in registry:
            raise PatternLayoutError(
                "cannot resolve inherited pattern reference %r; pass the "
                "referenced patterns in the registry argument" % iri)
        node = registry[iri]
    return chain


def _effective_attribute(chain, name, default=None):
    for node in chain:
        value = node.attribs.get(name)
        if value is not None:
            return value
    return default


# ----------------------------------------------------------------------
# geometry helpers
# ----------------------------------------------------------------------

def _as_rect(value, name):
    try:
        x, y, width, height = value
    except (TypeError, ValueError):
        raise PatternLayoutError(
            "%s must be a 4-tuple (x, y, width, height)" % name)
    x, y, width, height = float(x), float(y), float(width), float(height)
    for number in (x, y, width, height):
        if not math.isfinite(number):
            raise PatternLayoutError("%s contains non-finite values" % name)
    if width <= 0.0 or height <= 0.0:
        raise PatternLayoutError(
            "%s width and height must be positive" % name)
    return x, y, width, height


def _intersect_rects(left, right):
    xmin = max(left[0], right[0])
    ymin = max(left[1], right[1])
    xmax = min(left[0] + left[2], right[0] + right[2])
    ymax = min(left[1] + left[3], right[1] + right[3])
    if xmax <= xmin or ymax <= ymin:
        return None
    return xmin, ymin, xmax - xmin, ymax - ymin


def _quad_overlaps_rect(corners, clip, tolerance):
    """SAT test between a convex quad and the axis aligned clip rectangle."""
    clip_corners = (
        (clip[0], clip[1]),
        (clip[0] + clip[2], clip[1]),
        (clip[0] + clip[2], clip[1] + clip[3]),
        (clip[0], clip[1] + clip[3]),
    )
    axes = [(1.0, 0.0), (0.0, 1.0)]
    for index in range(2):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % 4]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length > 0.0:
            axes.append((-dy / length, dx / length))

    for axis_x, axis_y in axes:
        tile_min = min(x * axis_x + y * axis_y for x, y in corners)
        tile_max = max(x * axis_x + y * axis_y for x, y in corners)
        rect_min = min(x * axis_x + y * axis_y for x, y in clip_corners)
        rect_max = max(x * axis_x + y * axis_y for x, y in clip_corners)
        if min(tile_max, rect_max) - max(tile_min, rect_min) <= tolerance:
            return False
    return True


# ----------------------------------------------------------------------
# main entry point
# ----------------------------------------------------------------------

def compute_pattern_tiles(pattern, bbox, viewport=None, *,
                          max_tiles=100000, registry=None):
    """Compute the visible tiles of *pattern* on a canvas.

    :param pattern: a :class:`svgwrite.pattern.Pattern` instance
    :param bbox: target object bounding box ``(x, y, width, height)`` in user
        space; also defines the ``objectBoundingBox`` reference
    :param viewport: canvas viewport ``(x, y, width, height)``; defaults to
        *bbox*. Only tiles intersecting the intersection of *bbox* and
        *viewport* are returned.
    :param int max_tiles: maximum number of candidate tiles, protecting
        against huge viewports and near degenerate transforms. Raises
        :class:`PatternLimitError` when exceeded.
    :param dict registry: optional mapping of ``'#id'`` to pattern objects,
        used to resolve string ``xlink:href`` inheritance references.
    :returns: stable, row-major ordered ``list`` of :class:`PatternTile`
        (``j`` ascending, then ``i`` ascending). Tiles that only touch the
        clip rectangle along a boundary are not included.
    """
    bbox = _as_rect(bbox, 'bbox')
    if viewport is None:
        viewport = bbox
    else:
        viewport = _as_rect(viewport, 'viewport')
    clip = _intersect_rects(bbox, viewport)
    if clip is None:
        return []

    chain = _resolve_chain(pattern, registry)

    def attr(name, default=None):
        return _effective_attribute(chain, name, default)

    pattern_units = attr('patternUnits', 'userSpaceOnUse')
    if pattern_units not in ('userSpaceOnUse', 'objectBoundingBox'):
        raise PatternLayoutError(
            "invalid patternUnits %r" % pattern_units)
    content_units = attr('patternContentUnits', 'userSpaceOnUse')
    if content_units not in ('userSpaceOnUse', 'objectBoundingBox'):
        raise PatternLayoutError(
            "invalid patternContentUnits %r" % content_units)

    # -- pattern space -> user space (without the per-tile translation) ----
    # The SVG transform chain, from outer to inner:
    #   patternTransform * units * translate(x, y) * tile(i, j) * content
    # where *units* is the objectBoundingBox mapping (identity for
    # userSpaceOnUse) and *content* is the viewBox / patternContentUnits
    # mapping applied inside the tile.
    if pattern_units == 'objectBoundingBox':
        x_value = _resolve_length(attr('x', 0), bbox[2])
        y_value = _resolve_length(attr('y', 0), bbox[3])
        tile_w = _resolve_length(attr('width', 0), bbox[2])
        tile_h = _resolve_length(attr('height', 0), bbox[3])
        units_matrix = (AffineMatrix.translate(bbox[0], bbox[1])
                        .compose(AffineMatrix.scale(bbox[2], bbox[3])))
    else:
        x_value = _resolve_length(attr('x', 0), viewport[2])
        y_value = _resolve_length(attr('y', 0), viewport[3])
        tile_w = _resolve_length(attr('width', 0), viewport[2])
        tile_h = _resolve_length(attr('height', 0), viewport[3])
        units_matrix = AffineMatrix.identity()
    insert_matrix = AffineMatrix.translate(x_value, y_value)

    if tile_w <= 0.0 or tile_h <= 0.0:
        raise PatternLayoutError(
            "pattern tile size must be positive (got width=%r, height=%r)"
            % (attr('width', 0), attr('height', 0)))

    pattern_transform = parse_transform_list(attr('patternTransform'))
    # outer wrapper, used both for the inverse index mapping and tile builds
    outer_matrix = (pattern_transform
                    .compose(units_matrix)
                    .compose(insert_matrix))
    # raises PatternLayoutError on a singular transform
    pattern_inverse = outer_matrix.inverse()

    # -- content -> tile-local pattern coordinates -------------------------
    # Following SVG 1.1 section 13.4, a content point reaches user space as
    #   PT * units * T(x,y) * T(iw,jh) * content(p_c)
    # where *content* is one of:
    #   * the viewBox/preserveAspectRatio mapping onto the tile viewport
    #     (patternContentUnits is ignored when a viewBox is present);
    #   * the objectBoundingBox mapping, but placed *inside* the units
    #     mapping so the bbox scale is applied exactly once;
    #   * the identity for plain user space content.
    viewbox = attr('viewBox')
    if viewbox is not None:
        content_matrix = _viewbox_matrix(
            viewbox, attr('preserveAspectRatio'), tile_w, tile_h)
    elif content_units == 'objectBoundingBox':
        # undo the outer units mapping after translating the bbox origin:
        # units * B^-1 maps bbox fractions to the same fractions in pattern
        # space, leaving a single bbox scaling for the content.
        content_matrix = (units_matrix.inverse()
                          .compose(AffineMatrix.translate(bbox[0], bbox[1]))
                          .compose(AffineMatrix.scale(bbox[2], bbox[3])))
    else:
        content_matrix = AffineMatrix.identity()

    # -- candidate index range from the clip corners in pattern space ------
    clip_corners = (
        (clip[0], clip[1]),
        (clip[0] + clip[2], clip[1]),
        (clip[0] + clip[2], clip[1] + clip[3]),
        (clip[0], clip[1] + clip[3]),
    )
    mapped = [pattern_inverse.apply(x, y) for x, y in clip_corners]
    i_min = int(math.floor(min(p[0] for p in mapped) / tile_w))
    i_max = int(math.ceil(max(p[0] for p in mapped) / tile_w))
    j_min = int(math.floor(min(p[1] for p in mapped) / tile_h))
    j_max = int(math.ceil(max(p[1] for p in mapped) / tile_h))

    candidate_count = (i_max - i_min + 1) * (j_max - j_min + 1)
    if candidate_count > max_tiles:
        raise PatternLimitError(
            "pattern needs at least %d candidate tiles, exceeding max_tiles=%d"
            % (candidate_count, max_tiles))

    tolerance = _OVERLAP_EPS * max(
        1.0, clip[2], clip[3],
        abs(tile_w) * max(abs(outer_matrix.a), abs(outer_matrix.c), 1.0),
        abs(tile_h) * max(abs(outer_matrix.b), abs(outer_matrix.d), 1.0),
    )

    local_corners = (
        (0.0, 0.0), (tile_w, 0.0),
        (tile_w, tile_h), (0.0, tile_h),
    )
    tiles = []
    for j in range(j_min, j_max + 1):
        for i in range(i_min, i_max + 1):
            tile_origin = AffineMatrix.translate(i * tile_w, j * tile_h)
            tile_matrix = (outer_matrix
                           .compose(tile_origin)
                           .compose(content_matrix))
            corners = [tile_matrix.apply(x, y) for x, y in local_corners]
            if not _quad_overlaps_rect(corners, clip, tolerance):
                continue
            xs = [point[0] for point in corners]
            ys = [point[1] for point in corners]
            tiles.append(PatternTile(
                i, j, tile_matrix,
                (min(xs), min(ys), max(xs), max(ys))))
    return tiles
