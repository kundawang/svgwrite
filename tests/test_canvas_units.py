#!/usr/bin/env python
# coding:utf-8
# Purpose: test canvas unit conversion (svgwrite.units + Drawing.set_canvas_unit)
# License: MIT License
import math
import unittest
import xml.etree.ElementTree as ET

from svgwrite.drawing import Drawing
from svgwrite import units


# paper sizes as (width, height, unit)
A4 = ('210mm', '297mm')
A4_PX = (210.0 / 25.4 * 96.0, 297.0 / 25.4 * 96.0)
LETTER = ('8.5in', '11in')
LETTER_PX = (8.5 * 96.0, 11.0 * 96.0)


class TestUnitConversion(unittest.TestCase):
    def test_absolute_units_at_default_dpi(self):
        # all absolute units for the same physical length: 1 inch
        self.assertAlmostEqual(units.to_px('1in'), 96.0)
        self.assertAlmostEqual(units.to_px('2.54cm'), 96.0)
        self.assertAlmostEqual(units.to_px('25.4mm'), 96.0)
        self.assertAlmostEqual(units.to_px('72pt'), 96.0)
        self.assertAlmostEqual(units.to_px('6pc'), 96.0)
        self.assertAlmostEqual(units.to_px('96px'), 96.0)
        self.assertAlmostEqual(units.to_px(96), 96.0)  # unitless == px

    def test_configurable_dpi(self):
        self.assertAlmostEqual(units.to_px('1in', dpi=300), 300.0)
        self.assertAlmostEqual(units.to_px('25.4mm', dpi=300), 300.0)
        self.assertAlmostEqual(units.to_px('1px', dpi=300), 1.0)
        self.assertAlmostEqual(units.convert_length('1in', 'mm'), 25.4)
        self.assertAlmostEqual(units.convert_length('300px', 'in', dpi=300), 1.0)

    def test_relative_units_rejected(self):
        for value in ('100%', '1.5em', '2ex'):
            self.assertRaises(ValueError, units.to_px, value)
            self.assertRaises(ValueError, units.convert_length, value, 'mm')

    def test_invalid_dpi_rejected(self):
        for dpi in (0, -1, -96.0, float('inf'), float('nan'), 'abc', None,
                    units.MAX_DPI + 1):
            self.assertRaises(ValueError, units.to_px, '10mm', dpi)
        self.assertAlmostEqual(units.to_px('1in', dpi=units.MAX_DPI), units.MAX_DPI)

    def test_invalid_and_huge_values(self):
        self.assertRaises(ValueError, units.to_px, 'abc')
        self.assertRaises(ValueError, units.to_px, '10q')  # unknown unit
        self.assertRaises(ValueError, units.to_px, float('inf'))
        self.assertRaises(ValueError, units.to_px, float('nan'))
        self.assertRaises(ValueError, units.to_px, '1e308cm')  # overflow
        # finite huge values are preserved/converted
        self.assertTrue(math.isfinite(units.to_px('1e10px')))

    def test_format_number(self):
        self.assertEqual(units.format_number(10.0), '10')
        self.assertEqual(units.format_number(0.0), '0')
        self.assertEqual(units.format_number(377.9527559055118), '377.952755906')
        self.assertRaises(ValueError, units.format_number, float('inf'))

    def test_parse_viewbox(self):
        self.assertEqual(units.parse_viewbox('0,0,100,200'), (0.0, 0.0, 100.0, 200.0))
        self.assertEqual(units.parse_viewbox('-5 -5 10 10'), (-5.0, -5.0, 10.0, 10.0))
        self.assertRaises(ValueError, units.parse_viewbox, '0,0,100')
        self.assertRaises(ValueError, units.parse_viewbox, 'a,b,c,d')
        self.assertRaises(ValueError, units.parse_viewbox, 42)


def parse_svg(dwg):
    """ Parse the generated XML, returns the root element. """
    return ET.fromstring(dwg.tostring())


class TestViewportMode(unittest.TestCase):
    def test_a4_mm_to_px(self):
        dwg = Drawing(size=A4)
        dwg.viewbox(0, 0, 210, 297)
        dwg.set_canvas_unit('px')
        self.assertAlmostEqual(units.to_px(dwg['width']), A4_PX[0], places=6)
        self.assertAlmostEqual(units.to_px(dwg['height']), A4_PX[1], places=6)
        # viewBox untouched
        self.assertEqual(dwg['viewBox'], '0,0,210,297')

    def test_letter_in_to_cm(self):
        dwg = Drawing(size=LETTER)
        dwg.set_canvas_unit('cm')
        self.assertAlmostEqual(units.convert_length(dwg['width'], 'in'), 8.5, places=6)
        self.assertAlmostEqual(units.convert_length(dwg['height'], 'in'), 11.0, places=6)

    def test_content_untouched(self):
        dwg = Drawing(size=A4)
        line = dwg.add(dwg.line((0, 0), (96, 0), stroke='black'))
        line['stroke-width'] = 2
        dwg.set_canvas_unit('px')
        self.assertEqual(line['x2'], 96)
        self.assertEqual(line['stroke-width'], 2)

    def test_roundtrip_mm_px_mm(self):
        dwg = Drawing(size=A4)
        dwg.set_canvas_unit('px')
        dwg.set_canvas_unit('mm')
        self.assertAlmostEqual(units.convert_length(dwg['width'], 'mm'), 210.0, places=6)
        self.assertAlmostEqual(units.convert_length(dwg['height'], 'mm'), 297.0, places=6)

    def test_custom_dpi(self):
        dwg = Drawing(size=('1in', '1in'))
        dwg.set_canvas_unit('px', dpi=300)
        self.assertEqual(dwg['width'], '300px')
        self.assertEqual(dwg['height'], '300px')

    def test_zero_size_preserved(self):
        dwg = Drawing(size=(0, '10cm'))
        dwg.set_canvas_unit('mm')
        self.assertEqual(dwg['width'], '0mm')
        self.assertAlmostEqual(units.convert_length(dwg['height'], 'mm'), 100.0, places=6)

    def test_relative_size_rejected(self):
        for size in (('100%', '100%'), ('10em', '10cm'), ('10cm', '5ex')):
            dwg = Drawing(size=size)
            self.assertRaises(ValueError, dwg.set_canvas_unit, 'px')

    def test_invalid_unit_dpi_mode_rejected(self):
        dwg = Drawing(size=A4)
        self.assertRaises(ValueError, dwg.set_canvas_unit, 'q')
        self.assertRaises(ValueError, dwg.set_canvas_unit, '%')
        self.assertRaises(ValueError, dwg.set_canvas_unit, 'mm', dpi=0)
        self.assertRaises(ValueError, dwg.set_canvas_unit, 'mm', mode='invalid')


class TestScaleMode(unittest.TestCase):
    def test_px_canvas_to_mm_known_coordinates(self):
        # known fixture: 96 px == 1 inch == 25.4 mm at 96 dpi
        dwg = Drawing(size=(960, 720))
        line = dwg.add(dwg.line((0, 0), (96, 0), stroke='black'))
        line['stroke-width'] = 2
        dwg.set_canvas_unit('mm', mode='scale')

        self.assertEqual(dwg['width'], '254mm')   # 960 px == 10 in == 254 mm
        self.assertEqual(dwg['height'], '190.5mm')
        # generated viewBox: user space in mm -> 1 user unit == 1 mm
        self.assertEqual(dwg['viewBox'], '0,0,254,190.5')
        # the 1 inch long line is now 25.4 user units (mm) long
        self.assertAlmostEqual(float(line['x2']), 25.4, places=6)
        # stroke width scaled by the same factor
        self.assertAlmostEqual(float(line['stroke-width']), 2 * 25.4 / 96, places=6)
        # physical size of the stroke is preserved
        self.assertAlmostEqual(units.to_px(line['stroke-width'] * units.px_per_unit('mm')),
                               2.0, places=6)

    def test_existing_viewbox_nonzero_origin(self):
        dwg = Drawing(size=('10cm', '10cm'))
        dwg.viewbox(-5, -5, 10, 10)  # 1 user unit == 1 cm
        rect = dwg.add(dwg.rect(insert=(-5, -5), size=(1, 1)))
        dwg.set_canvas_unit('mm', mode='scale')

        self.assertEqual(dwg['width'], '100mm')
        self.assertEqual(dwg['height'], '100mm')
        self.assertEqual(dwg['viewBox'], '-50,-50,100,100')
        # 1 cm == 10 mm -> coordinates multiplied by 10
        self.assertAlmostEqual(float(rect['x']), -50.0, places=6)
        self.assertAlmostEqual(float(rect['width']), 10.0, places=6)

    def test_nested_groups_and_transform(self):
        dwg = Drawing(size=(960, 720))
        group = dwg.add(dwg.g())
        group.translate(96, 48)
        inner = group.add(dwg.g())
        inner.rotate(45, (96, 96))
        rect = inner.add(dwg.rect(insert=(96, 0), size=(192, 96)))
        rect['stroke-width'] = '1pt'  # absolute unit keeps its physical size
        factor = 25.4 / 96.0
        dwg.set_canvas_unit('mm', mode='scale')

        self.assertEqual(group['transform'], 'translate(%s,%s)'
                         % (units.format_number(96 * factor), units.format_number(48 * factor)))
        # rotate angle preserved, center scaled
        self.assertEqual(inner['transform'], 'rotate(45,%s,%s)'
                         % (units.format_number(96 * factor), units.format_number(96 * factor)))
        self.assertAlmostEqual(float(rect['x']), 96 * factor, places=6)
        self.assertAlmostEqual(float(rect['width']), 192 * factor, places=6)
        # 1pt stays 1pt physically, expressed in mm user units
        self.assertAlmostEqual(float(rect['stroke-width']), 25.4 / 72.0, places=6)

    def test_path_data_and_points(self):
        dwg = Drawing(size=(960, 720))
        path = dwg.add(dwg.path(d='M 0,0 L 10,0 A 5,5 0 0 1 20,0 z'))
        poly = dwg.add(dwg.polyline(points=[(0, 0), (10, 0), (10, 10)]))
        dwg.set_canvas_unit('mm', mode='scale')
        factor = 25.4 / 96.0

        def nums(d):
            return [float(t) for t in d.replace(',', ' ').split()
                    if t not in ('M', 'L', 'A', 'z')]
        # M 0 0 | L 10 0 | A rx ry rot flags x y | -> radii and coords scaled,
        # rotation (0) and flags (0 1) preserved
        # (svgwrite.path.Path stores its data in the command stack)
        expected = [0, 0, 10 * factor, 0, 5 * factor, 5 * factor, 0, 0, 1, 20 * factor]
        for got, want in zip(nums(path.commands[0]), expected):
            self.assertAlmostEqual(got, want, places=6)
        # (svgwrite Polyline stores its points in a list)
        flat_points = [coord for point in poly.points for coord in point]
        for got, want in zip(flat_points,
                             [0, 0, 10 * factor, 0, 10 * factor, 10 * factor]):
            self.assertAlmostEqual(got, want, places=6)

    def test_relative_content_preserved(self):
        dwg = Drawing(size=(960, 720))
        rect = dwg.add(dwg.rect(insert=('10%', 0), size=('50%', '1em')))
        dwg.set_canvas_unit('mm', mode='scale')
        self.assertEqual(rect['x'], '10%')
        self.assertEqual(rect['width'], '50%')
        self.assertEqual(rect['height'], '1em')

    def test_zero_size_rejected(self):
        dwg = Drawing(size=(0, 100))
        self.assertRaises(ValueError, dwg.set_canvas_unit, 'mm', mode='scale')
        dwg = Drawing(size=('10cm', '10cm'))
        dwg.viewbox(0, 0, 0, 10)
        self.assertRaises(ValueError, dwg.set_canvas_unit, 'mm', mode='scale')

    def test_relative_size_rejected(self):
        dwg = Drawing()  # default size is ('100%', '100%')
        self.assertRaises(ValueError, dwg.set_canvas_unit, 'mm', mode='scale')

    def test_roundtrip_px_mm_px(self):
        dwg = Drawing(size=(960, 720))
        line = dwg.add(dwg.line((0, 0), (96, 0)))
        line['stroke-width'] = 2
        dwg.set_canvas_unit('mm', mode='scale')
        dwg.set_canvas_unit('px', mode='scale')
        # roundtrip error stays below 1e-6 px
        self.assertAlmostEqual(units.to_px(dwg['width']), 960.0, places=6)
        self.assertAlmostEqual(units.to_px(dwg['height']), 720.0, places=6)
        self.assertAlmostEqual(float(line['x2']), 96.0, places=6)
        self.assertAlmostEqual(float(line['stroke-width']), 2.0, places=6)
        minx, miny, vbw, vbh = units.parse_viewbox(dwg['viewBox'])
        self.assertAlmostEqual(minx, 0.0, places=6)
        self.assertAlmostEqual(vbw, 960.0, places=6)
        self.assertAlmostEqual(vbh, 720.0, places=6)

    def test_generated_xml_parses(self):
        dwg = Drawing(size=A4)
        dwg.viewbox(0, 0, 210, 297)
        dwg.add(dwg.line((0, 0), (210, 297)))
        dwg.set_canvas_unit('px', mode='scale')
        root = parse_svg(dwg)
        self.assertTrue(root.tag.endswith('svg'))
        minx, miny, vbw, vbh = units.parse_viewbox(root.get('viewBox'))
        self.assertAlmostEqual(vbw, A4_PX[0], places=4)
        self.assertAlmostEqual(vbh, A4_PX[1], places=4)
        self.assertEqual(root.get('width'), dwg['width'])
        # physical canvas size is still A4
        self.assertAlmostEqual(units.convert_length(root.get('width'), 'mm'), 210.0, places=4)
        self.assertAlmostEqual(units.convert_length(root.get('height'), 'mm'), 297.0, places=4)

    def test_nested_svg_with_own_viewbox_not_rescaled(self):
        dwg = Drawing(size=(960, 720))
        nested = dwg.add(dwg.svg(insert=(96, 96), size=(192, 192)))
        nested.viewbox(0, 0, 10, 10)
        inner_rect = nested.add(dwg.rect(insert=(1, 1), size=(8, 8)))
        dwg.set_canvas_unit('mm', mode='scale')
        factor = 25.4 / 96.0
        # position/size of the nested viewport scaled ...
        self.assertAlmostEqual(float(nested['x']), 96 * factor, places=6)
        self.assertAlmostEqual(float(nested['width']), 192 * factor, places=6)
        # ... but its content lives in its own user space
        self.assertEqual(nested['viewBox'], '0,0,10,10')
        self.assertEqual(inner_rect['x'], 1)
        self.assertEqual(inner_rect['width'], 8)


if __name__ == '__main__':
    unittest.main()
