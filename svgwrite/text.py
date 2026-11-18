#!/usr/bin/env python
#coding:utf-8
# Author:  mozman --<mozman@gmx.at>
# Purpose: text objects
# Created: 20.09.2010
# Copyright (C) 2010, Manfred Moitzi
# License: MIT License
"""
Text that is to be rendered as part of an SVG document fragment is specified
using the **text** element. The characters to be drawn are expressed as XML
character data inside the **text** element.

"""

import math
import numbers
import re
from collections import namedtuple

from svgwrite.base import BaseElement
from svgwrite.data.full11 import presentation_attributes
from svgwrite.mixins import Presentation, Transform, XLink
from svgwrite.utils import iterflatlist, strlist, is_string, split_coordinate


class TSpan(BaseElement, Presentation):
    """
    Within a **Text** element, text and font properties
    and the current text position can be adjusted with absolute or relative
    coordinate values by using the **TSpan** element.
    The characters to be drawn are expressed as XML character data inside the
    **TSpan** element.

    """
    elementname = 'tspan'

    def __init__(self, text, insert=None, x=None, y=None, dx=None, dy=None, rotate=None,
                 **extra):
        """
        :param string text: **tspan** content
        :param 2-tuple insert: The **insert** parameter is the absolute insert point
                               of the text, don't use this parameter in combination
                               with the **x** or the **y** parameter.
        :param list x: list of absolute x-axis values for characters
        :param list y: list of absolute y-axis values for characters
        :param list dx: list of relative x-axis values for characters
        :param list dy: list of relative y-axis values for characters
        :param list rotate: list of rotation-values for characters (in degrees)

        """
        super(TSpan, self).__init__(**extra)
        self.text = text
        if insert is not None:
            if is_string(insert):
                raise TypeError("'insert' should be a <tuple> or a <list>  with"
                                " at least two elements.")
            if x or y:
                raise ValueError("Use 'insert' and 'x' or 'y' parameter not"
                                 " at the same time!")
            x = [insert[0]]
            y = [insert[1]]

        if x is not None:
            self['x'] = strlist(list(iterflatlist(x)), ' ')
        if y is not None:
            self['y'] = strlist(list(iterflatlist(y)), ' ')
        if dx is not None:
            self['dx'] = strlist(list(iterflatlist(dx)), ' ')
        if dy is not None:
            self['dy'] = strlist(list(iterflatlist(dy)), ' ')
        if rotate is not None:
            self['rotate'] = strlist(list(iterflatlist(rotate)), ' ')

    def get_xml(self):
        xml = super(TSpan, self).get_xml()
        xml.text = str(self.text)
        return xml


class TextLayoutError(ValueError):
    """Raised by :class:`TextLayout` for invalid layout parameters, invalid
    measured widths and failing measure callbacks.
    """


#: Unicode ranges of scripts whose text may be broken between any two
#: characters (CJK ideographs, kana, hangul and the related punctuation).
_CJK_RANGES = (
    (0x1100, 0x11FF),   # Hangul Jamo
    (0x2E80, 0x2EFF),   # CJK Radicals Supplement
    (0x2F00, 0x2FDF),   # Kangxi Radicals
    (0x2FF0, 0x2FFF),   # Ideographic Description Characters
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0x3100, 0x312F),   # Bopomofo
    (0x3130, 0x318F),   # Hangul Compatibility Jamo
    (0x31A0, 0x31BF),   # Bopomofo Extended
    (0x31F0, 0x31FF),   # Katakana Phonetic Extensions
    (0x3200, 0x32FF),   # Enclosed CJK Letters and Months
    (0x3300, 0x33FF),   # CJK Compatibility
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xA960, 0xA97F),   # Hangul Jamo Extended-A
    (0xAC00, 0xD7AF),   # Hangul Syllables
    (0xD7B0, 0xD7FF),   # Hangul Jamo Extended-B
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0xFE30, 0xFE4F),   # CJK Compatibility Forms
    (0xFF00, 0xFFEF),   # Halfwidth and Fullwidth Forms
    (0x20000, 0x2FA1F),  # CJK Unified Ideographs Extension B-F
)

_XML_SPACE = ('default', 'preserve')


def _is_cjk(character):
    """True if the text may be broken at either side of **character**."""
    code_point = ord(character)
    for first, last in _CJK_RANGES:
        if first <= code_point <= last:
            return True
    return False


def _format_number(value):
    """Stable string for a coordinate: integral values without a decimal
    point, other values with up to 6 decimal places and no trailing zeros.
    """
    value = float(value)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return ('%.6f' % value).rstrip('0').rstrip('.')


def _as_finite_number(value, name):
    if isinstance(value, bool) or isinstance(value, complex) or \
            not isinstance(value, numbers.Number):
        raise TextLayoutError("%s must be a real number, got %r" % (name, value))
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise TextLayoutError(
            "%s must be a real number, got %r" % (name, value))
    if not math.isfinite(value):
        raise TextLayoutError("%s must be finite, got %r" % (name, value))
    return value


def _as_coordinate(value, name):
    """Return ``(number, unit)`` for an origin coordinate or Text attribute."""
    if isinstance(value, bool):
        raise TextLayoutError("%s must be a coordinate, got %r" % (name, value))
    if isinstance(value, (int, float)):
        number, unit = float(value), None
    else:
        try:
            number, unit = split_coordinate(value)
        except (TypeError, ValueError):
            raise TextLayoutError(
                "%s must be a number or a coordinate string, got %r"
                % (name, value))
    if not math.isfinite(number):
        raise TextLayoutError("%s must be finite, got %r" % (name, value))
    return number, unit or ''


class TextLine(namedtuple('TextLine', 'text x y width truncated')):
    """One laid out line of a :class:`TextLayout`.

    :attr text: line content
    :attr x: absolute x coordinate (string, includes the origin unit if any)
    :attr y: absolute y coordinate (string, includes the origin unit if any)
    :attr width: measured width in user units
    :attr truncated: `True` for the last line if content was dropped by
        ``max_lines`` (the line then contains the ellipsis marker, if one
        was requested)
    """

    __slots__ = ()

    @property
    def is_empty(self):
        return not self.text


class TextLayout(object):
    """Lay out a text string as a list of lines of at most **max_width**.

    The caller supplies the font measurement: **measure** is a callable that
    accepts a string and returns its width in user units as a finite,
    non-negative number.  ``svgwrite`` itself does not load fonts, so the
    callback is the only source of glyph metrics::

        def measure(text):
            return sum(10 if '\\u4e00' <= c <= '\\u9fff' else 6 for c in text)

        layout = TextLayout('地图上的地名', 40, 14, measure)
        for line in layout.lines:
            print(line.text, line.x, line.y, line.width)

    Line breaking rules:

    * explicit ``'\\n'`` characters start a new line, ``'\\r\\n'`` and
      ``'\\r'`` are normalized to ``'\\n'``
    * Latin words are kept together and wrapped at word boundaries
    * CJK characters (Chinese, Japanese, Korean) are individual line break
      opportunities, so a long CJK run is wrapped per character
    * a Latin word wider than **max_width** is split between characters
      (unless ``break_long_words=False``)
    * ``xml_space='default'`` collapses whitespace runs to one space and
      strips leading/trailing whitespace; ``xml_space='preserve'`` keeps all
      whitespace verbatim (a separator moves to the next line when the line
      is broken)

    :param string text: text to lay out
    :param number max_width: maximum line width in user units, must be > 0
    :param number line_height: vertical distance between baselines, must be
        >= 0
    :param measure: callable ``measure(text) -> width``
    :param int max_lines: maximum number of lines or `None` for no limit
    :param string ellipsis: marker appended to the last line when
        **max_lines** drops content (e.g. ``'…'``); `None` or ``''`` adds no
        marker
    :param string xml_space: ``'default'`` or ``'preserve'``
    :param 2-tuple origin: ``(x, y)`` of the first baseline; coordinates may
        be numbers or coordinate strings like ``'10px'``
    :param bool break_long_words: split a word wider than **max_width**
        between characters (default) or let it overflow on its own line
    :raises TextLayoutError: for invalid parameters, a non-finite, negative
        or non-numeric measure result, a measure callback that raises, an
        ``ellipsis`` wider than **max_width**, or a zero **max_width**
    """

    def __init__(self, text, max_width, line_height, measure, max_lines=None,
                 ellipsis=None, xml_space='default', origin=(0, 0),
                 break_long_words=True):
        if not is_string(text):
            raise TypeError("text must be a string, got %r" % (text,))
        if not callable(measure):
            raise TypeError("measure must be a callable")
        if xml_space not in _XML_SPACE:
            raise TextLayoutError(
                "xml_space must be one of %s, got %r"
                % ('/'.join(_XML_SPACE), xml_space))
        self._max_width = _as_finite_number(max_width, 'max_width')
        if self._max_width <= 0:
            raise TextLayoutError(
                "max_width must be greater than 0, got %r" % (max_width,))
        self._line_height = _as_finite_number(line_height, 'line_height')
        if self._line_height < 0:
            raise TextLayoutError(
                "line_height must not be negative, got %r" % (line_height,))
        self._measure = measure
        self._cache = {}
        self._xml_space = xml_space
        self._preserve = xml_space == 'preserve'
        self._break_long_words = bool(break_long_words)
        self._max_lines = self._check_max_lines(max_lines)
        self._ellipsis = self._check_ellipsis(ellipsis)
        if not isinstance(origin, (tuple, list)) or len(origin) != 2:
            raise TextLayoutError(
                "origin must be a 2-tuple (x, y), got %r" % (origin,))
        self._x, self._x_unit = _as_coordinate(origin[0], 'origin x')
        self._y, self._y_unit = _as_coordinate(origin[1], 'origin y')
        self.text = text.replace('\r\n', '\n').replace('\r', '\n')
        self.lines, self.is_truncated = self._layout()

    def _check_max_lines(self, max_lines):
        if max_lines is None:
            return None
        if isinstance(max_lines, bool) or \
                not isinstance(max_lines, numbers.Integral):
            raise TextLayoutError(
                "max_lines must be an integer or None, got %r" % (max_lines,))
        if max_lines < 1:
            raise TextLayoutError(
                "max_lines must be at least 1, got %r" % (max_lines,))
        return int(max_lines)

    def _check_ellipsis(self, ellipsis):
        if ellipsis is None:
            return None
        if not is_string(ellipsis):
            raise TextLayoutError(
                "ellipsis must be a string or None, got %r" % (ellipsis,))
        if ellipsis and self._max_lines is not None and \
                self._width(ellipsis) > self._max_width:
            raise TextLayoutError(
                "ellipsis %r is wider than max_width %r"
                % (ellipsis, self._max_width))
        return ellipsis

    def _width(self, text):
        """Measured width of **text**, validated and cached."""
        if text == '':
            return 0.0
        try:
            return self._cache[text]
        except KeyError:
            pass
        try:
            width = self._measure(text)
        except Exception as error:
            raise TextLayoutError(
                "measure() failed for %r: %s" % (text, error)) from error
        if isinstance(width, bool) or isinstance(width, complex) or \
                not isinstance(width, numbers.Number):
            raise TextLayoutError(
                "measure() must return a real number for %r, got %r"
                % (text, width))
        try:
            width = float(width)
        except (TypeError, ValueError):
            raise TextLayoutError(
                "measure() must return a real number for %r, got %r"
                % (text, width))
        if not math.isfinite(width):
            raise TextLayoutError(
                "measure() returned a non-finite width for %r: %r"
                % (text, width))
        if width < 0:
            raise TextLayoutError(
                "measure() returned a negative width for %r: %r"
                % (text, width))
        self._cache[text] = width
        return width

    # -- tokenization ------------------------------------------------------

    @staticmethod
    def _break_units(word):
        """Split a whitespace free **word** into unbreakable units.

        Latin (and other non-CJK) runs stay together, every CJK character is
        its own unit.
        """
        units = []
        run = []
        for character in word:
            if _is_cjk(character):
                if run:
                    units.append(''.join(run))
                    run = []
                units.append(character)
            else:
                run.append(character)
        if run:
            units.append(''.join(run))
        return units

    def _atoms(self, segment):
        """Return ``(separator, unit)`` pairs for one explicit text line.

        The separator is the whitespace in front of the unit (collapsed to a
        single space in ``'default'`` mode, verbatim in ``'preserve'``
        mode).
        """
        if self._preserve:
            return self._preserve_atoms(segment)
        return self._default_atoms(segment)

    def _default_atoms(self, segment):
        atoms = []
        for word_index, word in enumerate(segment.split()):
            for unit_index, unit in enumerate(self._break_units(word)):
                separator = ' ' if word_index and unit_index == 0 else ''
                atoms.append((separator, unit))
        return atoms

    def _preserve_atoms(self, segment):
        atoms = []
        pending = ''
        for part in re.findall(r'\s+|\S+', segment):
            if part.isspace():
                pending += part
                continue
            for unit_index, unit in enumerate(self._break_units(part)):
                atoms.append((pending if unit_index == 0 else '', unit))
            pending = ''
        if pending:  # trailing whitespace is content in preserve mode
            atoms.append((pending, ''))
        return atoms

    # -- line filling ------------------------------------------------------

    def _wrap_segment(self, segment):
        atoms = self._atoms(segment)
        if not atoms:
            return ['']
        lines = []
        current = ''
        for separator, atom in atoms:
            prefix = separator if (current or self._preserve) else ''
            if self._width(current + prefix + atom) <= self._max_width:
                current += prefix + atom
                continue
            if current:
                lines.append(current)
                current = ''
                prefix = separator if self._preserve else ''
                if self._width(prefix + atom) <= self._max_width:
                    current = prefix + atom
                    continue
            # the unit does not fit on an empty line
            if self._break_long_words and len(atom) > 1:
                chunks = self._hard_split(prefix, atom)
                lines.extend(chunks[:-1])
                current = chunks[-1]
            else:
                current = prefix + atom
        lines.append(current)
        return lines

    def _hard_split(self, prefix, atom):
        """Split an over-wide word between characters."""
        chunks = []
        current = prefix
        for character in atom:
            if current and self._width(current + character) > self._max_width:
                chunks.append(current)
                current = character
            else:
                current += character
        chunks.append(current)
        return chunks

    # -- truncation --------------------------------------------------------

    def _truncate_line(self, line):
        marker = self._ellipsis or ''
        if self._width(line + marker) <= self._max_width:
            return line + marker
        # prefer a word boundary: break at the rightmost whitespace first
        for index in range(len(line) - 1, -1, -1):
            if line[index].isspace():
                prefix = line[:index].rstrip()
                if self._width(prefix + marker) <= self._max_width:
                    return prefix + marker
        # no suitable whitespace: trim between characters (works for CJK)
        for count in range(len(line) - 1, -1, -1):
            prefix = line[:count]
            if not self._preserve:
                prefix = prefix.rstrip()
            if self._width(prefix + marker) <= self._max_width:
                return prefix + marker
        return marker

    # -- results -----------------------------------------------------------

    def _layout(self):
        if self.text == '':
            return [], False
        texts = []
        for segment in self.text.split('\n'):
            texts.extend(self._wrap_segment(segment))
        truncated = False
        if self._max_lines is not None and len(texts) > self._max_lines:
            truncated = True
            texts = texts[:self._max_lines]
            if self._ellipsis:
                texts[-1] = self._truncate_line(texts[-1])
        lines = []
        for index, line_text in enumerate(texts):
            lines.append(TextLine(
                text=line_text,
                x=_format_number(self._x) + self._x_unit,
                y=_format_number(self._y + index * self._line_height) +
                self._y_unit,
                width=self._width(line_text),
                truncated=truncated and index == len(texts) - 1,
            ))
        return lines, truncated

    @property
    def is_empty(self):
        """`True` if the input text was empty and no lines were produced."""
        return not self.lines

    @property
    def max_width(self):
        """Maximum line width in user units."""
        return self._max_width

    @property
    def line_height(self):
        """Baseline distance between two lines in user units."""
        return self._line_height

    @property
    def max_lines(self):
        """Maximum number of lines or `None`."""
        return self._max_lines

    def tspans(self, style=None):
        """Create one :class:`TSpan` per line, positioned at the line's
        ``x`` and ``y``.

        :param dict style: optional attributes applied to every tspan
            (typically the inherited presentation attributes of a
            :class:`Text` element)
        :return: list of :class:`TSpan` objects
        """
        result = []
        for line in self.lines:
            tspan = TSpan(line.text, x=[line.x], y=[line.y])
            if style:
                tspan.update(style)
            result.append(tspan)
        return result


def wrap_text(text, max_width, line_height, measure, **kwargs):
    """Lay out **text** and return the list of :class:`TSpan` lines.

    This is a convenience wrapper around :class:`TextLayout` for callers that
    only need the SVG elements::

        tspans = wrap_text('地图上的地名', 40, 14, measure, origin=(5, 10))

    All other keyword arguments are passed to :class:`TextLayout`.
    """
    return TextLayout(text, max_width, line_height, measure, **kwargs).tspans()


class Text(TSpan, Transform):
    """
    The **Text** element defines a graphics element consisting of text.
    The characters to be drawn are expressed as XML character data inside the
    **Text** element.

    """
    elementname = 'text'

    def wrap(self, text=None, max_width=None, line_height=None, measure=None,
             max_lines=None, ellipsis=None, xml_space='default', origin=None,
             inherit_style=True, break_long_words=True):
        """Replace the text content by one **tspan** per wrapped line.

        The layout uses the caller supplied **measure** callback, see
        :class:`TextLayout` for the line breaking rules and raised errors.
        The first line's baseline is the ``y`` attribute (or **origin**), the
        ``x`` attribute is the horizontal anchor of every line, and the
        presentation attributes of this element - including **text-anchor**
        and **dominant-baseline** - are copied to every generated tspan when
        **inherit_style** is `True`, so the wrapping preserves the style,
        anchor and baseline position of this **Text** element.

        :param string text: text to wrap, defaults to the current text content
        :param number max_width: maximum line width in user units
        :param number line_height: baseline distance between lines
        :param measure: callable ``measure(text) -> width``
        :param int max_lines: maximum number of lines or `None`
        :param string ellipsis: marker for a truncated last line
        :param string xml_space: ``'default'`` or ``'preserve'``; preserve
            also sets ``xml:space="preserve"`` on this element
        :param 2-tuple origin: optional ``(x, y)`` replacing the element's
            own ``x`` / ``y`` attributes
        :param bool inherit_style: copy presentation attributes to the tspans
        :param bool break_long_words: split an over-wide word between
            characters (default) or let it overflow
        :returns: this element (for chaining)
        :raises TextLayoutError: see :class:`TextLayout`
        """
        if text is None:
            # a second wrap() reuses the original source text, because the
            # text content of a wrapped element is empty by then
            text = self.text or getattr(self, '_wrapped_text', '')
        if measure is None or max_width is None or line_height is None:
            raise TypeError(
                "wrap() requires max_width, line_height and measure")
        if origin is None:
            origin = self._baseline_origin()

        # drop tspans produced by a previous wrap() call
        for tspan in getattr(self, '_wrapped_tspans', ()):
            try:
                self.elements.remove(tspan)
            except ValueError:
                pass

        layout = TextLayout(
            text, max_width, line_height, measure, max_lines=max_lines,
            ellipsis=ellipsis, xml_space=xml_space, origin=origin,
            break_long_words=break_long_words)
        style = self._presentation_style() if inherit_style else None
        tspans = layout.tspans(style)
        for tspan in tspans:
            if xml_space == 'preserve':
                tspan['xml:space'] = 'preserve'
            self.add(tspan)
        self._wrapped_tspans = tspans
        self._wrapped_text = text
        self.text = ''
        if xml_space == 'preserve':
            self['xml:space'] = 'preserve'
        else:
            self.attribs.pop('xml:space', None)
        self.lines = layout.lines
        self.is_truncated = layout.is_truncated
        return self

    def _baseline_origin(self):
        """Return ``(x, y)`` from the element's own x / y attributes."""
        x = self.attribs.get('x', 0)
        y = self.attribs.get('y', 0)
        if is_string(x):
            x = x.split()[0] if x.split() else 0
        elif isinstance(x, (tuple, list)):
            x = x[0] if x else 0
        if is_string(y):
            y = y.split()[0] if y.split() else 0
        elif isinstance(y, (tuple, list)):
            y = y[0] if y else 0
        return x, y

    def _presentation_style(self):
        """Presentation attributes of this element, for the wrapped tspans."""
        return {name: value for name, value in self.attribs.items()
                if name in presentation_attributes}


class TRef(BaseElement, XLink, Presentation):
    """
    The textual content for a **Text** can be either character data directly
    embedded within the <text> element or the character data content of a
    referenced element, where the referencing is specified with a **TRef**
    element.

    """
    elementname = 'tref'

    def __init__(self, element, **extra):
        """
        :param element: create a reference this element, if element is a \
                        `string` its the **id** name of the referenced element, \
                        if element is a :class:`~svgwrite.base.BaseElement` \
                        the **id** SVG Attribute is used to create the reference.

        """
        super(TRef, self).__init__(**extra)
        self.set_href(element)

    def get_xml(self):
        self.update_id()  # if href is an object - 'id' - attribute may be changed!
        return super(TRef, self).get_xml()


class TextPath(BaseElement, XLink, Presentation):
    """
    In addition to text drawn in a straight line, SVG also includes the
    ability to place text along the shape of a **path** element. To specify that
    a block of text is to be rendered along the shape of a **path**, include
    the given text within a **textPath** element which includes an **xlink:href**
    attribute with a IRI reference to a **path** element.

    """
    elementname = 'textPath'

    def __init__(self, path, text, startOffset=None, method='align', spacing='exact',
                 **extra):
        """
        :param path: link to **path**, **id** string or **Path** object
        :param string text: **textPath** content
        :param number startOffset: text starts with offset from begin of path.
        :param string method: ``align|stretch``
        :param string spacing: ``exact|auto``

        """
        super(TextPath, self).__init__(**extra)
        self.text = text
        if method == 'stretch':
            self['method'] = method
        if spacing == 'auto':
            self['spacing'] = spacing
        if startOffset is not None:
            self['startOffset'] = startOffset
        self.set_href(path)

    def get_xml(self):
        self.update_id() # if href is an object - 'id' - attribute may be changed!
        xml = super(TextPath, self).get_xml()
        xml.text = str(self.text)
        return xml


class TBreak(BaseElement):
    elementname = 'tbreak'

    def __init__(self, **extra):
        super(TBreak, self).__init__(**extra)

    def __getitem__(self, key):
        raise NotImplementedError("__getitem__() not supported by TBreak class.")

    def __setitem__(self, key, value):
        raise NotImplementedError("__setitem__() not supported by TBreak class.")

    def add(self, element):
        raise NotImplementedError("add() not supported by TBreak class.")


class TextArea(BaseElement, Transform, Presentation):
    """
    At this time **textArea** is only available for SVG 1.2 Tiny profile.

    The **textArea**  element allows simplistic wrapping of text content within a
    given region. The `tiny` profile of SVG specifies a single rectangular region.
    Other profiles may allow a sequence of arbitrary shapes.

    Text wrapping via the **textArea** element is available as a lightweight and
    convenient facility for simple text wrapping where a complete box model layout
    engine is not required.

    The layout of wrapped text is user agent dependent; thus, content developers
    need to be aware that there might be different results, particularly with
    regard to where line breaks occur.

    The TextArea class wraps every text added by write() or writeline() as
    **tspan** element.

    """
    elementname = 'textArea'

    def __init__(self, text=None, insert=None, size=None, **extra):
        super(TextArea, self).__init__(**extra)
        if text is not None:
            self.write(text)
        if insert is not None:
            self['x'] = insert[0]
            self['y'] = insert[1]
        if size is not None:
            self['width'] = size[0]
            self['height'] = size[1]

    def line_increment(self, value):
        """ Set the line-spacing to *value*. """
        self['line-increment'] = value

    def write(self, text, **extra):
        """
        Add text as **tspan** elements, with extra-params for the **tspan** element.

        Use the '\\\\n' character for line breaks.
        """
        if '\n' not in text:
            self.add(TSpan(text, **extra))
        else:
            lines = text.split('\n')
            for line in lines[:-1]:
                if line:  # no text between '\n'+
                    self.add(TSpan(line, **extra))
                self.add(TBreak())
            # case "text\n" : last element is ''
            # case "texta\ntextb : last element is 'textb'
            if lines[-1]:
                self.add(TSpan(lines[-1], **extra))
