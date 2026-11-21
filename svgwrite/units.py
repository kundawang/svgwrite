#!/usr/bin/env python
# coding:utf-8
# Purpose: canvas unit conversion (px, mm, cm, in, pt, pc) with configurable DPI
# License: MIT License
"""
Canvas unit conversion helpers.

The physical size of a printed drawing is kept constant when the canvas
unit changes: a line that is 1 inch long on a pixel canvas stays 1 inch
long on a centimeter canvas.

Supported absolute units: ``px``, ``mm``, ``cm``, ``in``, ``pt``, ``pc``.
The pixel is the SVG user unit, its relation to physical units is defined
by the configurable DPI (dots per inch), default is the CSS reference
value of 96 dpi.

Explicit rules for edge cases:

* relative units (``%``, ``em``, ``ex``) can not be converted without a
  reference context and are **rejected** by the conversion functions
  (:class:`ValueError`); inside drawing *content* they are **preserved**
  unchanged by :meth:`svgwrite.Drawing.set_canvas_unit`, because the
  physical viewport size does not change
* zero sizes are valid for the ``'viewport'`` mode (0 stays 0), but are
  **rejected** for the ``'scale'`` mode, because the user unit size can
  not be determined
* DPI must be a finite number in the range ``0 < dpi <= MAX_DPI``,
  everything else is **rejected**
* non-finite lengths (``inf``, ``nan``) and lengths whose conversion
  overflows to infinity are **rejected**; finite values of any magnitude
  are **preserved**/converted
* values that can not be parsed as a length are **preserved** unchanged
  inside drawing content
"""
import math
import re

from svgwrite.utils import split_coordinate, is_string, iterflatlist, strlist

DEFAULT_DPI = 96.0
#: upper bound for accepted DPI values (explicit reject rule)
MAX_DPI = 100000.0

#: absolute units supported by the canvas conversion
ABSOLUTE_UNITS = frozenset(['px', 'mm', 'cm', 'in', 'pt', 'pc'])
#: relative units, rejected by conversion, preserved in content
RELATIVE_UNITS = frozenset(['%', 'em', 'ex'])

# 1 unit = factor * 1 inch
_INCH_PER_UNIT = {
    'in': 1.0,
    'pc': 1.0 / 6.0,
    'pt': 1.0 / 72.0,
    'cm': 1.0 / 2.54,
    'mm': 1.0 / 25.4,
}


def check_dpi(dpi):
    """ Validate **dpi**, returns the value as `float`.

    :raises ValueError: for non numeric, non finite, zero, negative or
      unreasonably large (> :data:`MAX_DPI`) values
    """
    try:
        value = float(dpi)
    except (TypeError, ValueError):
        raise ValueError("Invalid DPI value: %r" % (dpi,))
    if not math.isfinite(value) or value <= 0.0 or value > MAX_DPI:
        raise ValueError("Invalid DPI value: %r" % (dpi,))
    return value


def px_per_unit(unit, dpi=DEFAULT_DPI):
    """ How many pixels (SVG user units) represent 1 **unit** at **dpi**.

    :raises ValueError: for relative or unknown units and invalid DPI
    """
    dpi = check_dpi(dpi)
    if unit is None or unit == 'px':
        return 1.0
    if unit in RELATIVE_UNITS:
        raise ValueError("Can not convert relative unit: %r" % (unit,))
    try:
        return _INCH_PER_UNIT[unit] * dpi
    except KeyError:
        raise ValueError("Unsupported unit: %r" % (unit,))


def to_px(value, dpi=DEFAULT_DPI):
    """ Convert **value** (number or length string like ``'10cm'``) to pixels.

    Unitless values are pixels by definition (SVG user units).

    :raises ValueError: for relative units, invalid formats, non finite
      values and conversion overflows
    """
    number, unit = split_coordinate(value)
    if not math.isfinite(number):
        raise ValueError("Non finite length: %r" % (value,))
    result = number * px_per_unit(unit, dpi)
    if not math.isfinite(result):
        raise ValueError("Length overflow: %r" % (value,))
    return result


def convert_length(value, unit, dpi=DEFAULT_DPI):
    """ Convert **value** to the absolute **unit**, returns a `float`.

    :raises ValueError: for relative/unknown units, invalid DPI or
      invalid/overflowing values
    """
    return to_px(value, dpi) / px_per_unit(unit, dpi)


def format_number(value):
    """ Format a float as compact string, rounding noise beyond 9 decimal
    places is removed (``377.9527559055118`` -> ``'377.952755906'``).
    """
    if not math.isfinite(value):
        raise ValueError("Non finite number: %r" % (value,))
    value = round(value, 9)
    if value == int(value) and abs(value) < 1e16:
        return str(int(value))
    return ('%.9f' % value).rstrip('0').rstrip('.')


def format_length(value, unit):
    """ Format **value** as length string with **unit** appended. """
    return "%s%s" % (format_number(value), unit)


# ---------------------------------------------------------------------------
# content scaling helpers (used by Drawing.set_canvas_unit in 'scale' mode)
# ---------------------------------------------------------------------------

#: simple length attributes scaled by the user space factor
LENGTH_ATTRIBUTES = frozenset([
    'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry',
    'width', 'height', 'stroke-width', 'stroke-dashoffset', 'font-size',
])

#: attributes holding a list of lengths
NUMBER_LIST_ATTRIBUTES = frozenset(['points', 'stroke-dasharray'])


def scale_length_value(value, factor, ppu_new, dpi=DEFAULT_DPI):
    """ Scale a single content length for the ``'scale'`` canvas mode.

    * unitless numbers are user space coordinates -> multiplied by **factor**
    * lengths with an absolute unit keep their physical size -> converted
      into the new user unit (``to_px(value) / ppu_new``)
    * relative units and unparseable values are preserved unchanged
    """
    if isinstance(value, (int, float)):
        return value * factor
    if not is_string(value):
        return value
    try:
        number, unit = split_coordinate(value)
    except ValueError:
        return value  # unparseable -> preserve
    if not math.isfinite(number):
        return value
    if unit in RELATIVE_UNITS:
        return value  # relative to viewport/font -> preserve
    if unit is None:
        return number * factor
    # absolute unit: keep the physical size
    return to_px(value, dpi) / ppu_new


def scale_number_list(value, factor, ppu_new, dpi=DEFAULT_DPI):
    """ Scale a list of lengths (e.g. **points**, **stroke-dasharray**).
    Returns a comma separated string; unparseable values are preserved.
    """
    if isinstance(value, (int, float)):
        return value * factor
    if not is_string(value):
        try:  # e.g. points given as list of (x, y) tuples
            value = ','.join(str(v) for v in iterflatlist(value))
        except TypeError:
            return value
    tokens = [token for token in re.split(r'[,\s]+', value.strip()) if token]
    if not tokens:
        return value
    scaled = []
    for token in tokens:
        new_token = scale_length_value(token, factor, ppu_new, dpi)
        scaled.append(format_number(new_token) if isinstance(new_token, float)
                      else str(new_token))
    return ','.join(scaled)


_PATH_TOKEN = re.compile(
    r"([AaCcHhLlMmQqSsTtVvZz])|([-+]?(?:[0-9]*\.?[0-9]+)(?:[eE][-+]?[0-9]+)?)")
# arc parameters: rx ry x-axis-rotation large-arc-flag sweep-flag x y
_ARC_SKIP = frozenset([2, 3, 4])  # rotation and flags are not scaled


def scale_path_data(d, factor):
    """ Scale all coordinates in path data **d** by **factor**.

    Arc rotation angles and arc flags are preserved, everything else
    (including arc radii) is scaled. Non string values are preserved.
    """
    if not is_string(d):
        return d
    result = []
    pos = 0
    command = None
    arg_index = 0
    for match in _PATH_TOKEN.finditer(d):
        result.append(d[pos:match.start()])  # separators
        pos = match.end()
        if match.group(1):
            command = match.group(1)
            arg_index = 0
            result.append(command)
        else:
            token = match.group(2)
            skip = command in ('A', 'a') and (arg_index % 7) in _ARC_SKIP
            arg_index += 1
            if skip:
                result.append(token)
            else:
                result.append(format_number(float(token) * factor))
    result.append(d[pos:])
    return ''.join(result)


_TRANSFORM = re.compile(
    r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def scale_transform(value, factor):
    """ Scale the user space coordinates inside a **transform** string.

    ``translate`` offsets, ``matrix`` e/f parts and ``rotate`` centers are
    scaled; angles and unitless scale factors are preserved. Unknown
    transform functions and unparseable values are preserved unchanged.
    """
    if not is_string(value):
        return value

    def replace(match):
        name, args = match.group(1), match.group(2)
        try:
            numbers = [float(arg) for arg in re.split(r'[,\s]+', args.strip()) if arg]
        except ValueError:
            return match.group(0)  # unparseable -> preserve
        if name == 'translate':
            numbers = [number * factor for number in numbers]
        elif name == 'matrix' and len(numbers) == 6:
            numbers[4] *= factor
            numbers[5] *= factor
        elif name == 'rotate' and len(numbers) == 3:
            numbers[1] *= factor
            numbers[2] *= factor
        # 'scale' and 'skewX'/'skewY' carry no user space coordinates
        return "%s(%s)" % (name, ','.join(format_number(n) for n in numbers))

    return _TRANSFORM.sub(replace, value)


def scale_element(element, factor, ppu_new, dpi=DEFAULT_DPI):
    """ Recursively scale all user space coordinates of **element** and its
    subelements (nested groups included) by **factor**.

    Elements which establish their own viewport/user space (they carry a
    **viewBox** attribute, e.g. nested ``svg``, ``symbol`` or ``marker``)
    get their position and size scaled, but their content is defined in
    their own user space and is left unchanged.
    """
    attribs = element.attribs
    # svgwrite.path.Path keeps its data in a command stack and creates the
    # 'd' attribute not before serialization: join the commands, scale the
    # resulting path data and replace the stack
    commands = getattr(element, 'commands', None)
    if commands is not None:
        element.commands = [scale_path_data(strlist(commands, ' '), factor)]
    # svgwrite Polyline/Polygon keep their points in a list and create the
    # 'points' attribute not before serialization
    points = getattr(element, 'points', None)
    if points is not None:
        scaled_points = []
        for point in points:
            try:
                scaled = tuple(scale_length_value(coord, factor, ppu_new, dpi)
                               for coord in point)
            except TypeError:
                scaled = scale_length_value(point, factor, ppu_new, dpi)
            scaled_points.append(scaled)
        element.points = scaled_points
    for name in list(attribs):
        if name in LENGTH_ATTRIBUTES:
            attribs[name] = scale_length_value(attribs[name], factor, ppu_new, dpi)
        elif name in NUMBER_LIST_ATTRIBUTES:
            attribs[name] = scale_number_list(attribs[name], factor, ppu_new, dpi)
        elif name == 'd':
            attribs['d'] = scale_path_data(attribs['d'], factor)
        elif name == 'transform':
            attribs['transform'] = scale_transform(attribs['transform'], factor)
    if 'viewBox' in attribs:
        return  # nested viewport: content lives in its own user space
    for child in element.elements:
        scale_element(child, factor, ppu_new, dpi)


def parse_viewbox(value):
    """ Parse a **viewBox** string into a 4-tuple of floats.

    :raises ValueError: for invalid formats
    """
    if not is_string(value):
        raise ValueError("Invalid viewBox: %r" % (value,))
    parts = [part for part in re.split(r'[,\s]+', value.strip()) if part]
    if len(parts) != 4:
        raise ValueError("Invalid viewBox: %r" % (value,))
    try:
        numbers = tuple(float(part) for part in parts)
    except ValueError:
        raise ValueError("Invalid viewBox: %r" % (value,))
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError("Invalid viewBox: %r" % (value,))
    return numbers
