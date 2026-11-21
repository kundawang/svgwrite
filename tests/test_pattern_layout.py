#!/usr/bin/env python
# coding:utf-8
# Author: kundawang
# Purpose: test pattern tile layout computation
# Created: 2026-11-22
# License: MIT License

import math
import unittest

from svgwrite.pattern import Pattern
from svgwrite.patternlayout import (
    AffineMatrix,
    PatternLayoutError,
    PatternLimitError,
    compute_pattern_tiles,
    parse_transform_list,
)

SQRT2_HALF = math.sqrt(2.0) / 2.0


def assert_matrix_near(testcase, matrix, expected, eps=1e-9):
    for got, want in zip(matrix, expected):
        testcase.assertAlmostEqual(got, want, delta=eps)


class TestTransformParser(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_transform_list(None), AffineMatrix.identity())
        self.assertEqual(parse_transform_list(''), AffineMatrix.identity())

    def test_mixin_serialized_order(self):
        # exactly the string written by the Transform mixin
        p = Pattern()
        p.rotate(45)
        p.translate(10)
        self.assertEqual(p['patternTransform'], 'rotate(45) translate(10)')
        matrix = parse_transform_list(p['patternTransform'])
        # matrix = R * T  (translate is applied first)
        x, y = matrix.apply(0.0, 0.0)
        self.assertAlmostEqual(x, 10.0 * SQRT2_HALF, places=9)
        self.assertAlmostEqual(y, 10.0 * SQRT2_HALF, places=9)

    def test_rotate_about_center(self):
        # rotate(90, 10, 10): the point (10, 10) is the fixed point
        matrix = parse_transform_list('rotate(90, 10, 10)')
        x, y = matrix.apply(10.0, 10.0)
        self.assertAlmostEqual(x, 10.0, places=9)
        self.assertAlmostEqual(y, 10.0, places=9)

    def test_all_commands_and_inverse(self):
        matrix = parse_transform_list(
            'matrix(1,2,3,4,5,6) translate(1,2) scale(2) rotate(30) '
            'skewX(10) skewY(5)')
        restored = matrix.inverse().compose(matrix)
        assert_matrix_near(self, restored, AffineMatrix.identity())

    def test_invalid(self):
        with self.assertRaises(PatternLayoutError):
            parse_transform_list('rot(45)')
        with self.assertRaises(PatternLayoutError):
            parse_transform_list('matrix(1,2,3)')


class TestBasicTiling(unittest.TestCase):
    """Hand computed tiling diagram.

    Pattern x=10, y=20, width=30, height=40; clip 0..100 x 0..100.

    tile origins (10 + 30i, 20 + 40j); the candidate window from the inverse
    transform is i in -1..3, j in -1..2. Visible tiles (X)::

        j=-1:  X  X  X  X  .      (i=-1..2 overlap the top strip)
        j= 0:  X  X  X  X  .      ((2,0) covers x[70,100))
        j= 1:  X  X  X  X  .      ((2,1) overlaps the strip y[60,100))
        j= 2:  .  .  .  .  .      (starts at y=100: tangent, excluded)

    The (i=3 / j=2) candidates only touch boundary lines and are excluded.
    """

    def setUp(self):
        self.pattern = Pattern(insert=(10, 20), size=(30, 40), debug=False)

    def test_hand_computed_indices(self):
        tiles = self.pattern.visible_tiles((0, 0, 100, 100))
        expected = {
            (-1, -1), (0, -1), (1, -1), (2, -1),
            (-1, 0), (0, 0), (1, 0), (2, 0),
            (-1, 1), (0, 1), (1, 1), (2, 1),
        }
        actual = {(t.i, t.j) for t in tiles}
        self.assertEqual(actual, expected)
        self.assertEqual(len(tiles), 12)

    def test_stable_row_major_order(self):
        tiles = self.pattern.visible_tiles((0, 0, 100, 100))
        keys = [(t.j, t.i) for t in tiles]
        self.assertEqual(keys, sorted(keys))

    def test_tile_transform_and_bounds(self):
        tiles = self.pattern.visible_tiles((0, 0, 100, 100))
        origin = {t.i: t for t in tiles if t.j == 0}
        # tile (1, 0) covers user x in [40, 70], y in [20, 60]
        tile = origin[1]
        x, y = tile.matrix.apply(0.0, 0.0)
        self.assertAlmostEqual(x, 40.0)
        self.assertAlmostEqual(y, 20.0)
        self.assertEqual(tile.bounds, (40.0, 20.0, 70.0, 60.0))

    def test_viewport_clipping(self):
        # viewport only shows the left strip of the object
        tiles = self.pattern.visible_tiles(
            bbox=(0, 0, 100, 100), viewport=(0, 0, 35, 100))
        # every visible tile must reach x < 35
        for tile in tiles:
            self.assertLess(tile.bounds[0], 35.0)
        indices = {(t.i, t.j) for t in tiles}
        # (-1, -1) overlaps: it spans x[-20,10] and y[-20,20]
        # (0, 2) starts at y=100: tangent to the clip and excluded
        self.assertEqual(indices, {
            (-1, -1), (0, -1),
            (-1, 0), (0, 0),
            (-1, 1), (0, 1)})

    def test_disjoint_viewport(self):
        tiles = self.pattern.visible_tiles(
            bbox=(0, 0, 100, 100), viewport=(200, 200, 10, 10))
        self.assertEqual(tiles, [])


class TestBoundaryTangency(unittest.TestCase):
    def setUp(self):
        # tiles align with the 50 unit grid
        self.pattern = Pattern(insert=(0, 0), size=(50, 50), debug=False)

    def test_tangent_tiles_are_excluded(self):
        # 100x100 object: i=2 / j=2 tiles only touch the far boundary
        tiles = self.pattern.visible_tiles((0, 0, 100, 100))
        self.assertEqual({(t.i, t.j) for t in tiles},
                         {(0, 0), (1, 0), (0, 1), (1, 1)})

    def test_partial_overlap_is_included(self):
        # object 5..105: three tiles overlap each axis, no tangency
        tiles = self.pattern.visible_tiles((5, 5, 100, 100))
        self.assertEqual({(t.i, t.j) for t in tiles},
                         {(i, j) for i in range(3) for j in range(3)})


class TestNegativeCoordinates(unittest.TestCase):
    def test_negative_insert(self):
        pattern = Pattern(insert=(-10, -20), size=(30, 40), debug=False)
        tiles = pattern.visible_tiles((0, 0, 100, 100))
        # i=4 starts at x=110 (outside), j=3 starts at y=100 (tangent)
        self.assertEqual({(t.i, t.j) for t in tiles},
                         {(i, j) for i in range(4) for j in range(3)})
        first = tiles[0]
        self.assertEqual((first.i, first.j), (0, 0))
        x, y = first.matrix.apply(0.0, 0.0)
        self.assertAlmostEqual(x, -10.0)
        self.assertAlmostEqual(y, -20.0)


class TestRotatedPattern(unittest.TestCase):
    """Hand computed rotation diagram.

    20x20 pattern, patternTransform rotate(45), clip the 0..40 square.
    Let s = 20 / sqrt(2) ~= 14.1421. The inverse-mapped clip window spans
    i, j in -2..2; after the SAT overlap test the visible diamonds are::

        (0,-1): touches the clip at (0,0), covers the top-left edge
        (1,-1): center (21.21, 14.14), fully inside the clip
        (2,-1): right edge diamond, corner (42.43, 14.14)
        (0, 0): corner diamond at the origin
        (1, 0): center (14.14, 28.28)
        (2, 0): bottom-right edge diamond
        (1, 1): left/bottom edge diamond
        (1,-2) and (0, 1) only touch a boundary and stay excluded.
    """

    def setUp(self):
        self.pattern = Pattern(insert=(0, 0), size=(20, 20), debug=False)
        self.pattern.rotate(45)

    def test_hand_computed_rotation(self):
        tiles = self.pattern.visible_tiles((0, 0, 40, 40))
        self.assertEqual({(t.i, t.j) for t in tiles},
                         {(0, -1), (1, -1), (2, -1),
                          (0, 0), (1, 0), (2, 0),
                          (1, 1), (1, -2)})

    def test_rotated_tile_corners(self):
        tiles = self.pattern.visible_tiles((0, 0, 40, 40))
        tile00 = next(t for t in tiles if (t.i, t.j) == (0, 0))
        corners = [tile00.matrix.apply(x, y)
                   for x, y in ((0, 0), (20, 0), (20, 20), (0, 20))]
        expected = (
            (0.0, 0.0),
            (20.0 * SQRT2_HALF, 20.0 * SQRT2_HALF),
            (0.0, 40.0 * SQRT2_HALF),
            (-20.0 * SQRT2_HALF, 20.0 * SQRT2_HALF),
        )
        for (got_x, got_y), (want_x, want_y) in zip(corners, expected):
            self.assertAlmostEqual(got_x, want_x, places=9)
            self.assertAlmostEqual(got_y, want_y, places=9)
        self.assertAlmostEqual(tile00.bounds[0], -20.0 * SQRT2_HALF, places=9)
        self.assertAlmostEqual(tile00.bounds[2], 20.0 * SQRT2_HALF, places=9)

    def test_rotated_tangency_excluded(self):
        # shift the clip so tile (0, 0) only touches it at one corner:
        # its rightmost x is s, clip starts exactly there
        x0 = 20.0 * SQRT2_HALF
        tiles = self.pattern.visible_tiles((x0, 0, 40, 40))
        self.assertNotIn((0, 0), {(t.i, t.j) for t in tiles})
        for tile in tiles:
            self.assertGreater(tile.bounds[2], x0)


class TestObjectBoundingBox(unittest.TestCase):
    def test_fractional_tiles(self):
        pattern = Pattern(insert=(0, 0), size=(0.5, 0.5),
                          patternUnits='objectBoundingBox', debug=False)
        bbox = (100, 200, 80, 40)
        tiles = pattern.visible_tiles(bbox)
        self.assertEqual({(t.i, t.j) for t in tiles},
                         {(0, 0), (1, 0), (0, 1), (1, 1)})
        tile10 = next(t for t in tiles if (t.i, t.j) == (1, 0))
        # tile is 0.5 * bbox -> 40 x 20 user units, origin at (140, 200)
        x, y = tile10.matrix.apply(0.0, 0.0)
        self.assertAlmostEqual(x, 140.0)
        self.assertAlmostEqual(y, 200.0)
        self.assertEqual(tile10.bounds, (140.0, 200.0, 180.0, 220.0))

    def test_percentage_tiles(self):
        # in userSpaceOnUse percentages resolve against the viewport size
        pattern = Pattern(insert=('0%', '0%'), size=('50%', '50%'),
                          patternUnits='userSpaceOnUse', debug=False)
        tiles = pattern.visible_tiles(
            bbox=(0, 0, 100, 100), viewport=(0, 0, 50, 50))
        # tile == 25 x 25 user units, four tiles cover the 50 unit viewport
        self.assertEqual({(t.i, t.j) for t in tiles},
                         {(0, 0), (1, 0), (0, 1), (1, 1)})

    def test_insert_offset_is_scaled(self):
        pattern = Pattern(insert=(0.25, 0.5), size=(0.5, 0.5),
                          patternUnits='objectBoundingBox', debug=False)
        tiles = pattern.visible_tiles((100, 200, 80, 40))
        tile00 = next(t for t in tiles if (t.i, t.j) == (0, 0))
        x, y = tile00.matrix.apply(0.0, 0.0)
        self.assertAlmostEqual(x, 100 + 0.25 * 80)
        self.assertAlmostEqual(y, 200 + 0.5 * 40)


class TestViewBox(unittest.TestCase):
    def test_viewbox_stretch(self):
        pattern = Pattern(insert=(0, 0), size=(100, 100), debug=False)
        pattern.viewbox(0, 0, 50, 50)
        tile = pattern.visible_tiles((0, 0, 100, 100))[0]
        # content 50 units stretch across the 100 unit tile
        x, _ = tile.matrix.apply(50.0, 0.0)
        self.assertAlmostEqual(x, 100.0)

    def test_viewbox_meet_align_topleft(self):
        pattern = Pattern(insert=(0, 0), size=(100, 100), debug=False)
        pattern.viewbox(0, 0, 50, 100)
        pattern.fit(horiz='left', vert='top', scale='meet')
        tile = pattern.visible_tiles((0, 0, 100, 100))[0]
        x, y = tile.matrix.apply(0.0, 0.0)
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 0.0)
        _, y100 = tile.matrix.apply(0.0, 100.0)
        self.assertAlmostEqual(y100, 100.0)

    def test_viewbox_slice_align_bottom(self):
        pattern = Pattern(insert=(0, 0), size=(100, 100), debug=False)
        pattern.viewbox(0, 0, 50, 100)
        pattern.fit(horiz='left', vert='bottom', scale='slice')
        tile = pattern.visible_tiles((0, 0, 100, 100))[0]
        # scale 2 -> scaled viewBox is 100 x 200, YMax pins content y=100
        # to the tile bottom
        _, y100 = tile.matrix.apply(0.0, 100.0)
        self.assertAlmostEqual(y100, 100.0)


class TestPatternContentUnits(unittest.TestCase):
    def test_object_bounding_box_content_units(self):
        pattern = Pattern(insert=(0, 0), size=(50, 50),
                          patternContentUnits='objectBoundingBox',
                          debug=False)
        tile = pattern.visible_tiles((0, 0, 100, 100))[0]
        # one content unit equals one bbox unit (100 user units)
        x, y = tile.matrix.apply(1.0, 1.0)
        self.assertAlmostEqual(x, 100.0)
        self.assertAlmostEqual(y, 100.0)

    def test_bbox_scale_applied_only_once(self):
        # patternUnits and patternContentUnits both objectBoundingBox:
        # the bbox scaling must wrap the content exactly once, not twice
        pattern = Pattern(insert=(0, 0), size=(0.5, 0.5),
                          patternUnits='objectBoundingBox',
                          patternContentUnits='objectBoundingBox',
                          debug=False)
        tile = pattern.visible_tiles((0, 0, 200, 100))[0]
        x, y = tile.matrix.apply(1.0, 1.0)
        self.assertAlmostEqual(x, 200.0)
        self.assertAlmostEqual(y, 100.0)
        self.assertNotAlmostEqual(tile.matrix.a, 200.0 * 200.0)

    def test_viewbox_overrides_content_units(self):
        # a viewBox makes patternContentUnits irrelevant
        pattern = Pattern(insert=(0, 0), size=(0.5, 0.5),
                          patternUnits='objectBoundingBox',
                          patternContentUnits='objectBoundingBox',
                          debug=False)
        pattern.viewbox(0, 0, 10, 10)
        pattern.fit('left', 'top')
        tile = pattern.visible_tiles((0, 0, 200, 100))[0]
        # tile is 100 x 50 user units; content x=10 maps to its right edge
        x, _ = tile.matrix.apply(10.0, 0.0)
        self.assertAlmostEqual(x, 100.0)


class TestInheritance(unittest.TestCase):
    def test_inherit_size_from_object(self):
        parent = Pattern(id='parent', insert=(0, 0), size=(10, 10),
                         debug=False)
        child = Pattern(inherit=parent, debug=False)
        tiles = child.visible_tiles((0, 0, 25, 25))
        self.assertEqual({(t.i, t.j) for t in tiles},
                         {(i, j) for i in range(3) for j in range(3)})

    def test_inherit_via_registry(self):
        parent = Pattern(id='parent', insert=(0, 0), size=(10, 10),
                         debug=False)
        child = Pattern(inherit='#parent', debug=False)
        tiles = child.visible_tiles(
            (0, 0, 25, 25), registry={'#parent': parent})
        self.assertEqual(len(tiles), 9)

    def test_unresolved_reference(self):
        child = Pattern(inherit='#missing', debug=False)
        with self.assertRaises(PatternLayoutError):
            child.visible_tiles((0, 0, 25, 25))

    def test_cyclic_reference(self):
        a = Pattern(id='a', debug=False)
        b = Pattern(id='b', debug=False)
        a.set_href(b)
        b.set_href(a)
        with self.assertRaises(PatternLayoutError):
            a.visible_tiles((0, 0, 25, 25))

    def test_own_attributes_override_parent(self):
        parent = Pattern(id='parent', insert=(0, 0), size=(10, 10),
                         debug=False)
        child = Pattern(size=(25, 25), inherit=parent, debug=False)
        tiles = child.visible_tiles((0, 0, 50, 50))
        self.assertEqual({(t.i, t.j) for t in tiles},
                         {(0, 0), (1, 0), (0, 1), (1, 1)})


class TestErrorsAndLimits(unittest.TestCase):
    def test_zero_size_tile(self):
        pattern = Pattern(insert=(0, 0), size=(0, 10), debug=False)
        with self.assertRaises(PatternLayoutError):
            pattern.visible_tiles((0, 0, 100, 100))

    def test_negative_size_tile(self):
        pattern = Pattern(insert=(0, 0), size=(10, -5), debug=False)
        with self.assertRaises(PatternLayoutError):
            pattern.visible_tiles((0, 0, 100, 100))

    def test_singular_pattern_transform(self):
        pattern = Pattern(insert=(0, 0), size=(10, 10), debug=False)
        pattern['patternTransform'] = 'matrix(1, 0, 0, 0, 0, 0)'
        with self.assertRaises(PatternLayoutError):
            pattern.visible_tiles((0, 0, 100, 100))

    def test_max_tiles_cap(self):
        pattern = Pattern(insert=(0, 0), size=(1, 1), debug=False)
        with self.assertRaises(PatternLimitError):
            pattern.visible_tiles((0, 0, 1000, 1000), max_tiles=100)

    def test_bad_bbox(self):
        pattern = Pattern(insert=(0, 0), size=(10, 10), debug=False)
        with self.assertRaises(PatternLayoutError):
            pattern.visible_tiles((0, 0, 0, 100))

    def test_invalid_units(self):
        pattern = Pattern(insert=(0, 0), size=('2em', 10), debug=False)
        with self.assertRaises(PatternLayoutError):
            pattern.visible_tiles((0, 0, 100, 100))


class TestNonIntegerAndUnits(unittest.TestCase):
    def test_non_integer_size(self):
        pattern = Pattern(insert=(0, 0), size=(33.3, 12.5), debug=False)
        tiles = pattern.visible_tiles((0, 0, 100, 25))
        tile2 = next(t for t in tiles if (t.i, t.j) == (2, 0))
        x, _ = tile2.matrix.apply(0.0, 0.0)
        self.assertAlmostEqual(x, 66.6)
        # ceil(100 / 33.3) = 4, but i=4 starts at 133.2 and is excluded
        self.assertEqual(max(t.i for t in tiles), 3)

    def test_css_units(self):
        pattern = Pattern(insert=(0, 0), size=('1in', '1in'), debug=False)
        # 1in == 96 user units; clip 96 wide -> (1, *) tiles are tangent only
        tangent = pattern.visible_tiles((0, 0, 96, 96))
        self.assertEqual({(t.i, t.j) for t in tangent}, {(0, 0)})
        # one extra user unit of overlap brings the edge tiles back
        overlapping = pattern.visible_tiles((0, 0, 97, 97))
        self.assertEqual({(t.i, t.j) for t in overlapping},
                         {(0, 0), (1, 0), (0, 1), (1, 1)})


class TestSerializationUnchanged(unittest.TestCase):
    def test_original_constructor_output(self):
        # regression: the layout extension must not alter serialization
        pattern = Pattern(insert=(10, 20), size=(30, 40), inherit='#test',
                          debug=True, profile='full')
        self.assertEqual(
            pattern.tostring(),
            '<pattern height="40" width="30" x="10" xlink:href="#test" '
            'y="20" />')

    def test_layout_uses_serialized_attribute_strings(self):
        pattern = Pattern(debug=False)
        pattern['width'] = '30'
        pattern['height'] = '40'
        pattern['x'] = '10'
        pattern['y'] = '20'
        pattern['patternTransform'] = 'translate(5)'
        tiles = compute_pattern_tiles(pattern, (0, 0, 100, 100))
        self.assertTrue(tiles)
        tile00 = next(t for t in tiles if (t.i, t.j) == (0, 0))
        x, y = tile00.matrix.apply(0.0, 0.0)
        self.assertAlmostEqual(x, 15.0)
        self.assertAlmostEqual(y, 20.0)


if __name__ == '__main__':
    unittest.main()
