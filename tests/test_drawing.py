#!/usr/bin/env python
#coding:utf-8
# Author:  mozman --<mozman@gmx.at>
# Purpose: test drawing module
# Created: 11.09.2010
# Copyright (C) 2010, Manfred Moitzi
# License: MIT License
import unittest
import xml.etree.ElementTree as ET
from io import StringIO
from pathlib import Path

from svgwrite.drawing import Drawing
from svgwrite.container import Group


class TestDrawingFullProfile(unittest.TestCase):
    def test_empty_drawing(self):
        dwg = Drawing()
        result = dwg.tostring()
        self.assertEqual(result, '<svg baseProfile="full" height="100%" version="1.1" '
                                 'width="100%" xmlns="http://www.w3.org/2000/svg" '
                                 'xmlns:ev="http://www.w3.org/2001/xml-events" '
                                 'xmlns:xlink="http://www.w3.org/1999/xlink"><defs /></svg>')

    def test_stylesheet(self):
        dwg = Drawing()
        dwg.add_stylesheet('test.css', 'Test')
        f = StringIO()
        dwg.write(f)
        result = f.getvalue()
        f.close()
        self.assertEqual(result, '<?xml version="1.0" encoding="utf-8" ?>\n'
                                 '<?xml-stylesheet href="test.css" type="text/css" title="Test" alternate="no" media="screen"?>\n'
                                 '<svg baseProfile="full" height="100%" version="1.1" width="100%" '
                                 'xmlns="http://www.w3.org/2000/svg" xmlns:ev="http://www.w3.org/2001/xml-events" '
                                 'xmlns:xlink="http://www.w3.org/1999/xlink"><defs /></svg>')

    def test_save(self):
        fn = Path('test_drawing.svg')
        if fn.exists():
            fn.unlink()
        dwg = Drawing(fn)
        dwg.save()
        self.assertTrue(fn.exists())
        fn.unlink()

    def test_save_as(self):
        fn = Path('test_drawing.svg')
        if fn.exists():
            fn.unlink()
        dwg = Drawing()
        dwg.saveas(str(fn))
        self.assertTrue(fn.exists())
        fn.unlink()

    def test_non_us_ascii_chars(self):
        dwg = Drawing()
        dwg.set_desc('öäü')
        f = StringIO()
        dwg.write(f)
        result = f.getvalue()
        f.close()
        self.assertEqual(result,
                         '<?xml version="1.0" encoding="utf-8" ?>\n'
                         '<svg baseProfile="full" height="100%" version="1.1" width="100%" '
                         'xmlns="http://www.w3.org/2000/svg" xmlns:ev="http://www.w3.org/2001/xml-events" '
                         'xmlns:xlink="http://www.w3.org/1999/xlink">'
                         '<title>öäü</title><defs /></svg>')


class TestStylesheetEscaping(unittest.TestCase):
    def write(self, dwg):
        f = StringIO()
        dwg.write(f)
        result = f.getvalue()
        f.close()
        return result

    def test_quotes_and_ampersand_are_escaped(self):
        dwg = Drawing()
        dwg.add_stylesheet('test.css?a=1&b=2', 'My "Fancy" <Style> & Co')
        result = self.write(dwg)
        self.assertIn(
            '<?xml-stylesheet href="test.css?a=1&amp;b=2" type="text/css" '
            'title="My &quot;Fancy&quot; &lt;Style&gt; &amp; Co" '
            'alternate="no" media="screen"?>\n', result)
        # the whole document must stay parseable XML
        ET.fromstring(result[result.index('<svg'):])

    def test_output_is_parseable_by_xml_parser(self):
        dwg = Drawing()
        dwg.add_stylesheet('a.css', 'Titel "öäü" & <ß>')
        dwg.add_stylesheet('b.css', 'second', alternate='yes', media='print')
        result = self.write(dwg)
        # strip the xml-stylesheet PIs (ElementTree can not resolve their
        # pseudo-attribute entities) and parse the rest
        svg_part = result[result.index('<svg'):]
        ET.fromstring(svg_part)
        # PIs keep their order and appear before the svg element
        first = result.index('href="a.css"')
        second = result.index('href="b.css"')
        self.assertLess(first, second)
        self.assertLess(second, result.index('<svg'))

    def test_non_ascii_title_passes_through(self):
        dwg = Drawing()
        dwg.add_stylesheet('test.css', '标准样式 “引用”')
        result = self.write(dwg)
        self.assertIn('title="标准样式 “引用”"', result)

    def test_newline_is_encoded_as_charref(self):
        dwg = Drawing()
        dwg.add_stylesheet('test.css', 'line1\nline2\tend')
        result = self.write(dwg)
        self.assertIn('title="line1&#10;line2&#9;end"', result)
        self.assertNotIn('line1\nline2', result)

    def test_processing_instruction_close_is_rejected(self):
        dwg = Drawing()
        dwg.add_stylesheet('test.css', 'x"?><script>alert(1)</script><?')
        f = StringIO()
        self.assertRaises(ValueError, dwg.write, f)
        # nothing may be written on error
        self.assertEqual(f.getvalue(), '')

    def test_invalid_xml_char_is_rejected(self):
        dwg = Drawing()
        dwg.add_stylesheet('test.css', 'bad\x08title')
        f = StringIO()
        self.assertRaises(ValueError, dwg.write, f)
        self.assertEqual(f.getvalue(), '')

    def test_non_string_value_is_rejected(self):
        dwg = Drawing()
        dwg.add_stylesheet(None, 'Test')
        f = StringIO()
        self.assertRaises(TypeError, dwg.write, f)
        self.assertEqual(f.getvalue(), '')

    def test_injection_can_not_escape_declaration(self):
        dwg = Drawing()
        dwg.add_stylesheet('x" media="all" onload="evil', 't')
        result = self.write(dwg)
        # quotes are neutralized, no extra pseudo-attributes can appear
        self.assertIn('href="x&quot; media=&quot;all&quot; '
                      'onload=&quot;evil"', result)
        self.assertEqual(result.count('<?xml-stylesheet'), 1)

    def test_save_and_saveas_produce_parseable_files(self):
        for filename in ('test_ss_save.svg', 'test_ss_saveas.svg'):
            fn = Path(filename)
            if fn.exists():
                fn.unlink()
        try:
            dwg = Drawing('test_ss_save.svg')
            dwg.add_stylesheet('s.css', 'A "quoted" & täg')
            dwg.save()
            dwg.saveas('test_ss_saveas.svg')
            for filename in ('test_ss_save.svg', 'test_ss_saveas.svg'):
                content = Path(filename).read_text(encoding='utf-8')
                ET.fromstring(content[content.index('<svg'):])
                self.assertIn('title="A &quot;quoted&quot; &amp; täg"',
                              content)
        finally:
            for filename in ('test_ss_save.svg', 'test_ss_saveas.svg'):
                fn = Path(filename)
                if fn.exists():
                    fn.unlink()

    def test_save_with_invalid_stylesheet_leaves_no_file_content(self):
        fn = Path('test_ss_broken.svg')
        if fn.exists():
            fn.unlink()
        try:
            dwg = Drawing(str(fn))
            dwg.add_stylesheet('s.css', 'broken ?> injection')
            self.assertRaises(ValueError, dwg.save)
            # file must not contain a partial, seemingly successful document
            self.assertEqual(fn.read_text(encoding='utf-8'), '')
        finally:
            if fn.exists():
                fn.unlink()


class TestDrawingTinyProfile(unittest.TestCase):
    def test_empty_drawing(self):
        dwg = Drawing(profile="tiny")
        result = dwg.tostring()
        self.assertEqual(result, '<svg baseProfile="tiny" height="100%" version="1.2" '
                                 'width="100%" xmlns="http://www.w3.org/2000/svg" '
                                 'xmlns:ev="http://www.w3.org/2001/xml-events" '
                                 'xmlns:xlink="http://www.w3.org/1999/xlink"><defs /></svg>')

    def test_stylesheet(self):
        dwg = Drawing(profile="tiny")
        dwg.add_stylesheet('test.css', 'Test')
        f = StringIO()
        dwg.write(f)
        result = f.getvalue()
        f.close()
        self.assertEqual(result, '<?xml version="1.0" encoding="utf-8" ?>\n'
                                 '<?xml-stylesheet href="test.css" type="text/css" title="Test" alternate="no" media="screen"?>\n'
                                 '<svg baseProfile="tiny" height="100%" version="1.2" width="100%" '
                                 'xmlns="http://www.w3.org/2000/svg" '
                                 'xmlns:ev="http://www.w3.org/2001/xml-events" '
                                 'xmlns:xlink="http://www.w3.org/1999/xlink"><defs /></svg>')


class TestDefs(unittest.TestCase):
    def test_simple_defs(self):
        dwg = Drawing()
        g = dwg.defs.add(Group(id='test'))
        inner_g = g.add(Group(id='innerTest'))
        result = dwg.tostring()
        self.assertEqual(result, '<svg baseProfile="full" height="100%" version="1.1" '
                                 'width="100%" xmlns="http://www.w3.org/2000/svg" '
                                 'xmlns:ev="http://www.w3.org/2001/xml-events" '
                                 'xmlns:xlink="http://www.w3.org/1999/xlink">'
                                 '<defs><g id="test"><g id="innerTest" /></g></defs></svg>')


if __name__ == '__main__':
    unittest.main()
