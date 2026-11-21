#!/usr/bin/env python
#coding:utf-8
# Purpose: test incremental (streaming) drawing write
# License: MIT License
import unittest
import xml.etree.ElementTree as ET
from io import StringIO
from pathlib import Path

import svgwrite
from svgwrite.container import Script, Group, Defs
from svgwrite.utils import AutoID


def build_fixture():
    """Drawing covering: stylesheets, namespaces, nested defs, gradients,
    script/CDATA, path, polyline, text with escaping and mixed content,
    use with object reference (auto id)."""
    AutoID._set_value(1)
    dwg = svgwrite.Drawing('fixture.svg', debug=False)
    dwg.add_stylesheet('s.css', 'S')
    dwg.set_desc('t&<>', 'd öäü')
    grad = dwg.defs.add(dwg.linearGradient(id='grad'))
    grad.add_stop_color(0, 'red')
    grad.add_stop_color(1, 'blue')
    nested = dwg.defs.add(Group(id='n'))
    inner_defs = nested.add(Defs(id='d2'))
    inner_defs.add(dwg.rect((0, 0), (5, 5), id='r0'))
    dwg.defs.add(Script(content='if (a < b && c > d) { go(); }'))
    layer = dwg.add(dwg.g(id='L'))
    layer.add(dwg.rect((1, 2), (3, 4), fill='url(#grad)'))
    path = dwg.path(d='M0,0')
    path.push('L', 5, 5)
    layer.add(path)
    layer.add(dwg.polyline([(0, 0), (1, 1), (2, 0)]))
    text = dwg.add(dwg.text('A&B <mix> ', insert=(3, 3)))
    text.add(dwg.tspan('öäü "q"', fill='red'))
    anonymous = dwg.add(dwg.g())  # gets an auto id through the use reference
    dwg.add(dwg.use(layer))
    dwg.add(dwg.use(anonymous))
    return dwg


def classic_output(dwg, **kwargs):
    stream = StringIO()
    dwg.write(stream, **kwargs)
    return stream.getvalue()


def incremental_output(dwg, **kwargs):
    stream = StringIO()
    dwg.write(stream, incremental=True, **kwargs)
    return stream.getvalue()


def canonical(root, strip_text=False):
    """Canonical parse tree for comparison, ignores tails (whitespace)."""
    text = root.text or ''
    if strip_text:
        text = text.strip()
    return (root.tag, tuple(sorted(root.attrib.items())), text,
            tuple(canonical(e, strip_text) for e in root))


class NullSink(object):
    """Write-only, non-seekable stream discarding all data."""

    def __init__(self):
        self.bytes = 0

    def write(self, data):
        self.bytes += len(data)


class TestIncrementalMatchesClassic(unittest.TestCase):
    def test_compact_byte_identical(self):
        expected = classic_output(build_fixture())
        result = incremental_output(build_fixture())
        self.assertEqual(expected, result)

    def test_fixed_serialization(self):
        # fixed reference output, verified against the classic write path
        self.assertEqual(incremental_output(build_fixture()), FIXTURE_COMPACT)

    def test_parse_tree_identical(self):
        classic_tree = ET.fromstring(classic_output(build_fixture()))
        incremental_tree = ET.fromstring(incremental_output(build_fixture()))
        self.assertEqual(canonical(classic_tree), canonical(incremental_tree))

    def test_repeated_writes_are_identical(self):
        dwg = build_fixture()
        first = incremental_output(dwg)
        second = incremental_output(dwg)
        self.assertEqual(first, second)

    def test_namespaced_metadata_parse_tree_identical(self):
        # namespace declarations of user supplied etree content are emitted
        # on the using element instead of being hoisted to the root element,
        # so the bytes differ but the parsed documents are equivalent
        def build():
            dwg = build_fixture()
            rdf = ET.Element('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF')
            ET.SubElement(rdf, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
            dwg.set_metadata(rdf)
            return dwg

        classic_tree = ET.fromstring(classic_output(build()))
        incremental_tree = ET.fromstring(incremental_output(build()))
        self.assertEqual(canonical(classic_tree), canonical(incremental_tree))


class TestIncrementalPretty(unittest.TestCase):
    def test_fixed_pretty_output(self):
        self.assertEqual(
            incremental_output(build_fixture(), pretty=True), FIXTURE_PRETTY)

    def test_pretty_indent(self):
        result = incremental_output(build_fixture(), pretty=True, indent=4)
        self.assertIn('\n    <defs>\n', result)

    def test_mixed_text_not_broken(self):
        dwg = build_fixture()
        result = incremental_output(dwg, pretty=True)
        # text and tspan stay on one line, no whitespace is injected
        self.assertIn('<text x="3" y="3">A&amp;B &lt;mix&gt; '
                      '<tspan fill="red">öäü "q"</tspan></text>\n', result)
        # character data survives parsing exactly as authored
        text_element = ET.fromstring(result).find(
            '{http://www.w3.org/2000/svg}text')
        self.assertEqual(text_element.text, 'A&B <mix> ')
        self.assertEqual(text_element.find(
            '{http://www.w3.org/2000/svg}tspan').text, 'öäü "q"')

    def test_pretty_parse_tree_matches_compact(self):
        dwg = build_fixture()
        compact_tree = ET.fromstring(incremental_output(dwg))
        pretty_tree = ET.fromstring(incremental_output(dwg, pretty=True))
        self.assertEqual(canonical(compact_tree, strip_text=True),
                         canonical(pretty_tree, strip_text=True))


class TestIncrementalRobustness(unittest.TestCase):
    def test_empty_drawing(self):
        self.assertEqual(classic_output(svgwrite.Drawing(debug=False)),
                         incremental_output(svgwrite.Drawing(debug=False)))

    def test_drawing_without_any_elements(self):
        def build():
            dwg = svgwrite.Drawing(debug=False)
            dwg.elements.clear()
            return dwg

        self.assertEqual(classic_output(build()), incremental_output(build()))

    def test_non_seekable_stream(self):
        class NonSeekableStream(object):
            def __init__(self):
                self.chunks = []

            def write(self, data):
                self.chunks.append(data)

        stream = NonSeekableStream()
        build_fixture().write(stream, incremental=True)
        self.assertEqual(''.join(stream.chunks), FIXTURE_COMPACT)

    def test_writer_exception_keeps_drawing_unmodified(self):
        class FailingStream(object):
            def __init__(self, fail_at):
                self.calls = 0
                self.fail_at = fail_at

            def write(self, data):
                self.calls += 1
                if self.calls >= self.fail_at:
                    raise IOError('stream is broken')

        dwg = build_fixture()
        before = dwg.tostring()  # classic path, establishes the baseline
        attribs_before = dict(dwg.attribs)
        self.assertRaises(IOError, dwg.write, FailingStream(fail_at=20),
                          incremental=True)
        self.assertEqual(before, dwg.tostring())
        self.assertEqual(attribs_before, dwg.attribs)
        self.assert_structure_intact(dwg)

    def test_deep_nesting(self):
        depth = 3000  # classic get_xml() recursion fails far below this
        dwg = svgwrite.Drawing(debug=False)
        group = dwg
        for _ in range(depth):
            group = group.add(Group())
        group.add(dwg.rect((0, 0), (1, 1)))
        result = incremental_output(dwg)
        self.assertEqual(result.count('<g>'), depth)
        self.assertEqual(result.count('</g>'), depth)
        # document is well formed and has the expected nesting depth
        root = ET.fromstring(result)
        level = 0
        while len(root):
            root = root[-1]  # rect was added last
            level += 1
        self.assertEqual(level, depth + 1)

    def test_drawing_not_modified(self):
        dwg = build_fixture()
        incremental_output(dwg)
        self.assert_drawing_clean(dwg)

    def assert_drawing_clean(self, dwg):
        # no root namespace attributes leaked into the drawing
        for key in ('xmlns', 'xmlns:xlink', 'xmlns:ev',
                    'baseProfile', 'version'):
            self.assertNotIn(key, dwg.attribs)
        # no cached serialization attributes leaked into the elements
        for element in dwg.elements:
            if isinstance(element, svgwrite.base.BaseElement):
                self.assertNotIn('d', element.attribs)
                self.assertNotIn('points', element.attribs)
        self.assert_structure_intact(dwg)

    def assert_structure_intact(self, dwg):
        self.assertTrue(any(e is dwg.defs for e in dwg.elements))


class TestIncrementalSave(unittest.TestCase):
    def test_save_and_saveas(self):
        fn = Path('test_incremental.svg')
        try:
            dwg = build_fixture()
            dwg.saveas(str(fn), incremental=True)
            self.assertEqual(fn.read_text(encoding='utf-8'), FIXTURE_COMPACT)
            fn.unlink()
            dwg.filename = str(fn)
            dwg.save(incremental=True, pretty=True)
            self.assertEqual(fn.read_text(encoding='utf-8'), FIXTURE_PRETTY)
        finally:
            if fn.exists():
                fn.unlink()


class TestIncrementalMemory(unittest.TestCase):
    def test_peak_memory_is_bounded(self):
        import tracemalloc

        def peak(elements):
            dwg = svgwrite.Drawing(debug=False)
            for i in range(elements):
                dwg.add(dwg.rect((i, i), (1, 1)))
            tracemalloc.start()
            dwg.write(NullSink(), incremental=False)
            _, classic_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            tracemalloc.start()
            dwg.write(NullSink(), incremental=True)
            _, incremental_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            return classic_peak, incremental_peak

        classic_peak, incremental_peak = peak(10000)
        self.assertLess(incremental_peak * 4, classic_peak)


FIXTURE_COMPACT = (
    '<?xml version="1.0" encoding="utf-8" ?>\n'
    '<?xml-stylesheet href="s.css" type="text/css" title="S" alternate="no" media="screen"?>\n'
    '<svg baseProfile="full" height="100%" version="1.1" width="100%" '
    'xmlns="http://www.w3.org/2000/svg" '
    'xmlns:ev="http://www.w3.org/2001/xml-events" '
    'xmlns:xlink="http://www.w3.org/1999/xlink">'
    '<title>t&amp;&lt;&gt;</title><desc>d öäü</desc><defs>'
    '<linearGradient id="grad">'
    '<stop offset="0" stop-color="red" /><stop offset="1" stop-color="blue" />'
    '</linearGradient>'
    '<g id="n"><defs id="d2">'
    '<rect height="5" id="r0" width="5" x="0" y="0" />'
    '</defs></g>'
    '<script><![CDATA[if (a < b && c > d) { go(); }]]></script>'
    '</defs>'
    '<g id="L">'
    '<rect fill="url(#grad)" height="4" width="3" x="1" y="2" />'
    '<path d="M0,0 L 5 5" />'
    '<polyline points="0,0 1,1 2,0" />'
    '</g>'
    '<text x="3" y="3">A&amp;B &lt;mix&gt; '
    '<tspan fill="red">öäü "q"</tspan></text>'
    '<g id="id1" />'
    '<use xlink:href="#L" /><use xlink:href="#id1" />'
    '</svg>'
)

FIXTURE_PRETTY = (
    '<?xml version="1.0" encoding="utf-8" ?>\n'
    '<?xml-stylesheet href="s.css" type="text/css" title="S" alternate="no" media="screen"?>\n'
    '<svg baseProfile="full" height="100%" version="1.1" width="100%" '
    'xmlns="http://www.w3.org/2000/svg" '
    'xmlns:ev="http://www.w3.org/2001/xml-events" '
    'xmlns:xlink="http://www.w3.org/1999/xlink">\n'
    '  <title>t&amp;&lt;&gt;</title>\n'
    '  <desc>d öäü</desc>\n'
    '  <defs>\n'
    '    <linearGradient id="grad">\n'
    '      <stop offset="0" stop-color="red" />\n'
    '      <stop offset="1" stop-color="blue" />\n'
    '    </linearGradient>\n'
    '    <g id="n">\n'
    '      <defs id="d2">\n'
    '        <rect height="5" id="r0" width="5" x="0" y="0" />\n'
    '      </defs>\n'
    '    </g>\n'
    '    <script><![CDATA[if (a < b && c > d) { go(); }]]></script>\n'
    '  </defs>\n'
    '  <g id="L">\n'
    '    <rect fill="url(#grad)" height="4" width="3" x="1" y="2" />\n'
    '    <path d="M0,0 L 5 5" />\n'
    '    <polyline points="0,0 1,1 2,0" />\n'
    '  </g>\n'
    '  <text x="3" y="3">A&amp;B &lt;mix&gt; '
    '<tspan fill="red">öäü "q"</tspan></text>\n'
    '  <g id="id1" />\n'
    '  <use xlink:href="#L" />\n'
    '  <use xlink:href="#id1" />\n'
    '</svg>\n'
)


if __name__ == '__main__':
    unittest.main()
