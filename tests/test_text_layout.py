#!/usr/bin/env python
#coding:utf-8
# Author:  kundawang
# Purpose: test multiline text layout (TextLayout / Text.wrap)
# Created: 19.11.2026
# License: MIT License
import unittest

import svgwrite
from svgwrite.text import (
    Text, TextLayout, TextLayoutError, TextLine, wrap_text,
)


def is_cjk(character):
    return 0x4E00 <= ord(character) <= 0x9FFF


def fixed_measure(text):
    """Deterministic metrics: CJK 10, space 4, every other char 6."""
    return sum(10 if is_cjk(c) else (4 if c == ' ' else 6) for c in text)


class TestEnglishWrapping(unittest.TestCase):
    def test_wraps_at_word_boundaries(self):
        layout = TextLayout('alpha beta gamma delta', 76, 12, fixed_measure)
        self.assertEqual([line.text for line in layout.lines],
                         ['alpha beta', 'gamma delta'])
        # 5 chars * 6 + space 4 + 4 chars * 6 = 58 / 5*6 + 4 + 5*6 = 64
        self.assertEqual([line.width for line in layout.lines], [58, 64])
        self.assertFalse(layout.is_truncated)

    def test_every_line_fits_and_width_matches_marker(self):
        text = 'map labels are often longer than the label box'
        layout = TextLayout(text, 70, 12, fixed_measure)
        for line in layout.lines:
            self.assertLessEqual(line.width, 70)
            self.assertEqual(line.width, fixed_measure(line.text))
            self.assertNotIn('  ', line.text)
        # joining with single spaces reproduces the words
        self.assertEqual(' '.join(l.text for l in layout.lines),
                         text)

    def test_whitespace_collapsed_in_default_mode(self):
        layout = TextLayout('  one   two\tthree  ', 100, 12, fixed_measure)
        self.assertEqual([line.text for line in layout.lines],
                         ['one two three'])

    def test_long_word_is_split_between_characters(self):
        layout = TextLayout('abcdefghijkl', 30, 12, fixed_measure)
        self.assertEqual([line.text for line in layout.lines],
                         ['abcde', 'fghij', 'kl'])
        self.assertTrue(all(line.width <= 30 for line in layout.lines))

    def test_long_word_can_overflow_instead(self):
        layout = TextLayout('abcdefghijkl', 30, 12, fixed_measure,
                            break_long_words=False)
        self.assertEqual([line.text for line in layout.lines],
                         ['abcdefghijkl'])
        self.assertEqual(layout.lines[0].width, 72)


class TestCJKWrapping(unittest.TestCase):
    def test_breaks_between_characters(self):
        layout = TextLayout('地图上的地名有长有短', 40, 14, fixed_measure)
        self.assertEqual([line.text for line in layout.lines],
                         ['地图上的', '地名有长', '有短'])
        self.assertEqual([line.width for line in layout.lines], [40, 40, 20])
        for line in layout.lines:
            self.assertLessEqual(line.width, 40)

    def test_mixed_chinese_and_english(self):
        layout = TextLayout('Map 地图 City 地名', 50, 14, fixed_measure)
        self.assertEqual([line.text for line in layout.lines],
                         ['Map 地图', 'City 地名'])
        # "Map" 18 + space 4 + "地图" 20 = 42
        self.assertEqual(layout.lines[0].width, 42)

    def test_latin_word_is_not_broken_between_cjk_characters(self):
        layout = TextLayout('地图 Map 地图', 40, 14, fixed_measure)
        self.assertEqual([line.text for line in layout.lines],
                         ['地图', 'Map 地', '图'])
        # the Latin word "Map" is never split, but a CJK run may be


class TestExplicitLineBreaks(unittest.TestCase):
    def test_explicit_newlines(self):
        layout = TextLayout('first\nsecond\nthird', 100, 10, fixed_measure,
                            origin=(5, 6))
        self.assertEqual([line.text for line in layout.lines],
                         ['first', 'second', 'third'])
        self.assertEqual([line.y for line in layout.lines], ['6', '16', '26'])

    def test_blank_line_is_kept(self):
        layout = TextLayout('a\n\nb', 100, 10, fixed_measure)
        self.assertEqual([line.text for line in layout.lines], ['a', '', 'b'])
        self.assertEqual(layout.lines[1].width, 0)

    def test_crlf_normalized(self):
        layout = TextLayout('a\r\nb\rc', 100, 10, fixed_measure)
        self.assertEqual([line.text for line in layout.lines], ['a', 'b', 'c'])


class TestPreserveWhitespace(unittest.TestCase):
    def test_preserve_keeps_runs_and_edges(self):
        layout = TextLayout(' one  two ', 100, 10, fixed_measure,
                            xml_space='preserve')
        self.assertEqual([line.text for line in layout.lines], [' one  two '])
        self.assertEqual(layout.lines[0].width, 52)

    def test_preserve_moves_separator_to_next_line(self):
        layout = TextLayout('aa  bb  cc', 30, 10, fixed_measure,
                            xml_space='preserve')
        self.assertEqual([line.text for line in layout.lines],
                         ['aa', '  bb', '  cc'])


class TestTruncation(unittest.TestCase):
    def test_max_lines_with_ellipsis(self):
        layout = TextLayout('alpha beta gamma delta', 76, 12, fixed_measure,
                            max_lines=1, ellipsis='…')
        self.assertEqual(len(layout.lines), 1)
        line = layout.lines[0]
        self.assertEqual(line.text, 'alpha beta…')
        self.assertTrue(line.truncated)
        self.assertLessEqual(line.width, 76)
        self.assertTrue(layout.is_truncated)

    def test_truncation_prefers_word_boundary(self):
        layout = TextLayout('one two three four', 40, 12, fixed_measure,
                            max_lines=2, ellipsis='...')
        self.assertEqual([line.text for line in layout.lines],
                         ['one two', 'thr...'])
        self.assertTrue(layout.lines[1].truncated)
        self.assertFalse(layout.lines[0].truncated)

    def test_cjk_truncation_between_characters(self):
        layout = TextLayout('地图上的地名', 30, 12, fixed_measure,
                            max_lines=1, ellipsis='…')
        self.assertEqual(layout.lines[0].text, '地图…')
        self.assertTrue(layout.lines[0].truncated)

    def test_no_ellipsis_still_reports_truncation(self):
        layout = TextLayout('aa bb cc dd', 20, 10, fixed_measure, max_lines=1)
        self.assertEqual(layout.lines[0].text, 'aa')
        self.assertTrue(layout.lines[0].truncated)
        self.assertTrue(layout.is_truncated)

    def test_no_truncation_when_lines_fit(self):
        layout = TextLayout('aa bb', 20, 10, fixed_measure, max_lines=5)
        self.assertEqual([line.text for line in layout.lines], ['aa', 'bb'])
        self.assertFalse(layout.is_truncated)
        self.assertFalse(any(line.truncated for line in layout.lines))


class TestCoordinates(unittest.TestCase):
    def test_tspan_coordinates_and_units(self):
        layout = TextLayout('aa bb cc', 12, 14, fixed_measure,
                            origin=('10px', '20px'))
        self.assertEqual([(line.x, line.y) for line in layout.lines],
                         [('10px', '20px'), ('10px', '34px'),
                          ('10px', '48px')])

    def test_fractional_origin_and_line_height(self):
        layout = TextLayout('ab cd', 12, 0.5, fixed_measure,
                            origin=(0.25, 1.5))
        self.assertEqual([(line.x, line.y) for line in layout.lines],
                         [('0.25', '1.5'), ('0.25', '2')])

    def test_zero_line_height_repeats_baseline(self):
        layout = TextLayout('a b c', 10, 0, fixed_measure, origin=(2, 3))
        self.assertEqual([line.y for line in layout.lines], ['3', '3', '3'])


class TestEmptyAndZeroWidth(unittest.TestCase):
    def test_empty_text_produces_no_lines(self):
        layout = TextLayout('', 10, 12, fixed_measure)
        self.assertEqual(layout.lines, [])
        self.assertTrue(layout.is_empty)
        self.assertFalse(layout.is_truncated)

    def test_zero_width_measure_still_terminates(self):
        def zero_measure(text):
            return 0

        layout = TextLayout('abc def', 10, 12, zero_measure)
        self.assertEqual([line.text for line in layout.lines], ['abc def'])
        self.assertEqual([line.width for line in layout.lines], [0])

    def test_max_width_must_be_positive(self):
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 0, 12, fixed_measure)
        with self.assertRaises(TextLayoutError):
            TextLayout('text', -5, 12, fixed_measure)

    def test_measure_not_called_for_empty_text(self):
        calls = []

        def measure(text):
            calls.append(text)
            return len(text) * 6

        TextLayout('', 10, 12, measure)
        self.assertEqual(calls, [])


class TestMeasurementFailures(unittest.TestCase):
    def test_non_finite_widths(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(TextLayoutError):
                TextLayout('text', 10, 12, lambda text, v=value: v)

    def test_negative_width(self):
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 10, 12, lambda text: -1)

    def test_non_numeric_width(self):
        for value in ('10', None, [10], True, 1j):
            with self.assertRaises(TextLayoutError):
                TextLayout('text', 10, 12, lambda text, v=value: v)

    def test_decimal_and_fraction_widths_are_accepted(self):
        import decimal
        import fractions

        for value in (decimal.Decimal('6'), fractions.Fraction(6, 1)):
            layout = TextLayout('ab', 10, 12, lambda text, v=value: v)
            self.assertEqual([line.text for line in layout.lines], ['ab'])
            self.assertEqual(layout.lines[0].width, 6)

    def test_measure_exception_is_wrapped(self):
        def broken(text):
            raise RuntimeError('no font')

        with self.assertRaises(TextLayoutError) as context:
            TextLayout('text', 10, 12, broken)
        self.assertIn('no font', str(context.exception))
        self.assertIsInstance(context.exception.__cause__, RuntimeError)

    def test_measure_must_be_callable(self):
        with self.assertRaises(TypeError):
            TextLayout('text', 10, 12, 'not callable')


class TestParameterValidation(unittest.TestCase):
    def test_text_must_be_string(self):
        with self.assertRaises(TypeError):
            TextLayout(None, 10, 12, fixed_measure)

    def test_line_height_must_not_be_negative(self):
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 10, -1, fixed_measure)

    def test_non_finite_max_width(self):
        for value in (float('nan'), float('inf')):
            with self.assertRaises(TextLayoutError):
                TextLayout('text', value, 12, fixed_measure)

    def test_max_lines_must_be_positive_integer(self):
        for value in (0, -1, 1.5, True, '2'):
            with self.assertRaises(TextLayoutError):
                TextLayout('text', 10, 12, fixed_measure, max_lines=value)

    def test_ellipsis_must_be_string_and_fit(self):
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 10, 12, fixed_measure, ellipsis=5)
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 10, 12, fixed_measure, max_lines=1,
                       ellipsis='WWW')

    def test_invalid_xml_space(self):
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 10, 12, fixed_measure, xml_space='collapse')

    def test_invalid_origin(self):
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 10, 12, fixed_measure, origin=5)
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 10, 12, fixed_measure, origin=(1, 2, 3))
        with self.assertRaises(TextLayoutError):
            TextLayout('text', 10, 12, fixed_measure, origin=('x', 2))


class TestTextLine(unittest.TestCase):
    def test_fields(self):
        line = TextLine('abc', '10', '20', 18.0, False)
        self.assertEqual(line.text, 'abc')
        self.assertEqual((line.x, line.y), ('10', '20'))
        self.assertFalse(line.is_empty)


class TestTSPanOutput(unittest.TestCase):
    def test_tspans_have_line_coordinates(self):
        layout = TextLayout('地图地名', 20, 14, fixed_measure,
                            origin=(5, 10))
        tspans = layout.tspans()
        self.assertEqual([t.tostring() for t in tspans],
                         ['<tspan x="5" y="10">地图</tspan>',
                          '<tspan x="5" y="24">地名</tspan>'])

    def test_tspans_accept_style(self):
        layout = TextLayout('aa bb', 12, 10, fixed_measure)
        tspans = layout.tspans({'font-size': 8, 'fill': 'red'})
        self.assertEqual(tspans[0].tostring(),
                         '<tspan fill="red" font-size="8" x="0" y="0">aa</tspan>')

    def test_wrap_text_helper(self):
        tspans = wrap_text('aa bb', 12, 10, fixed_measure, origin=(1, 2))
        self.assertEqual([t.text for t in tspans], ['aa', 'bb'])
        self.assertEqual(tspans[1]['y'], '12')


class TestTextWrap(unittest.TestCase):
    def test_wrap_replaces_content_with_tspans(self):
        text = Text('地图地名', insert=(5, 10))
        text.wrap(max_width=20, line_height=14, measure=fixed_measure)
        self.assertEqual(
            text.tostring(),
            '<text x="5" y="10"><tspan x="5" y="10">地图</tspan>'
            '<tspan x="5" y="24">地名</tspan></text>')
        self.assertEqual([line.text for line in text.lines], ['地图', '地名'])

    def test_style_anchor_and_baseline_are_inherited(self):
        text = Text('aa bb', insert=(50, 60), font_size=12, fill='red',
                    text_anchor='middle', dominant_baseline='central')
        text.wrap(max_width=12, line_height=15, measure=fixed_measure)
        self.assertEqual(
            text.tostring(),
            '<text dominant-baseline="central" fill="red" font-size="12" '
            'text-anchor="middle" x="50" y="60">'
            '<tspan dominant-baseline="central" fill="red" font-size="12" '
            'text-anchor="middle" x="50" y="60">aa</tspan>'
            '<tspan dominant-baseline="central" fill="red" font-size="12" '
            'text-anchor="middle" x="50" y="75">bb</tspan></text>')

    def test_inherit_style_can_be_disabled(self):
        text = Text('aa bb', insert=(0, 0), fill='red')
        text.wrap(max_width=12, line_height=10, measure=fixed_measure,
                  inherit_style=False)
        self.assertEqual(
            text.tostring(),
            '<text fill="red" x="0" y="0"><tspan x="0" y="0">aa</tspan>'
            '<tspan x="0" y="10">bb</tspan></text>')

    def test_origin_overrides_insert_point(self):
        text = Text('one two three', insert=(0, 0))
        text.wrap(max_width=30, line_height=10, measure=fixed_measure,
                  origin=(7, 9))
        self.assertEqual([t['x'] for t in text.elements], ['7', '7', '7'])
        self.assertEqual([t['y'] for t in text.elements], ['9', '19', '29'])

    def test_preserve_sets_xml_space(self):
        text = Text(' one  two ')
        text.wrap(max_width=100, line_height=10, measure=fixed_measure,
                  xml_space='preserve')
        self.assertEqual(text['xml:space'], 'preserve')
        self.assertEqual(text.elements[0]['xml:space'], 'preserve')
        self.assertEqual(text.elements[0].text, ' one  two ')

    def test_repeated_wrap_replaces_previous_tspans(self):
        text = Text('aa bb cc', insert=(0, 0))
        text.wrap(max_width=12, line_height=10, measure=fixed_measure)
        self.assertEqual(len(text.elements), 3)
        text.wrap(max_width=12, line_height=10, measure=fixed_measure)
        self.assertEqual(len(text.elements), 3)
        text.wrap(text='xx', max_width=12, line_height=10,
                  measure=fixed_measure)
        self.assertEqual([t.text for t in text.elements], ['xx'])

    def test_wrap_keeps_explicit_text_breaks(self):
        text = Text('first\nsecond')
        text.wrap(max_width=100, line_height=12, measure=fixed_measure)
        self.assertEqual([t.text for t in text.elements],
                         ['first', 'second'])
        self.assertEqual([t['y'] for t in text.elements], ['0', '12'])

    def test_truncated_wrap(self):
        text = Text('地图上的地名', insert=(0, 0))
        text.wrap(max_width=30, line_height=12, measure=fixed_measure,
                  max_lines=1, ellipsis='…')
        self.assertEqual(text.tostring(),
                         '<text x="0" y="0"><tspan x="0" y="0">地图…</tspan></text>')
        self.assertTrue(text.is_truncated)

    def test_empty_text_wrap(self):
        text = Text('')
        text.wrap(max_width=20, line_height=10, measure=fixed_measure)
        self.assertEqual(text.tostring(), '<text />')
        self.assertEqual(text.lines, [])

    def test_requires_layout_arguments(self):
        with self.assertRaises(TypeError):
            Text('x').wrap(max_width=10, line_height=10)
        with self.assertRaises(TypeError):
            Text('x').wrap(measure=fixed_measure)

    def test_break_long_words_can_be_disabled_in_wrap(self):
        text = Text('abcdefghijkl', insert=(0, 0))
        text.wrap(max_width=30, line_height=10, measure=fixed_measure,
                  break_long_words=False)
        self.assertEqual([t.text for t in text.elements], ['abcdefghijkl'])

    def test_debug_validation_of_wrapped_tspans(self):
        # profile='full' and debug=True validate every generated attribute
        drawing = svgwrite.Drawing(profile='full', debug=True)
        text = drawing.text('地图地名', insert=(5, 10), font_size=12,
                            fill='red', text_anchor='middle')
        text.wrap(max_width=20, line_height=14, measure=fixed_measure)
        drawing.add(text)
        result = drawing.tostring()
        self.assertIn('<tspan', result)
        self.assertEqual(result.count('<tspan'), 2)
