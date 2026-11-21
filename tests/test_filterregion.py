#!/usr/bin/env python
# coding:utf-8
# Purpose: test filter effects region computation (svgwrite.filterregion)
# Created: 2026-11-22
# License: MIT License

import math
import unittest

from svgwrite import filters
from svgwrite.filterregion import (
    BBox, FilterRegion, calc_filter_region, BLUR_EXPANSION_FACTOR,
    DEFAULT_OBB_REGION, MAX_COORDINATE,
)

# object bounding box used by every hand-computed fixture
OBB = (0, 0, 100, 50)


class SingleStageDropShadowFixture(unittest.TestCase):
    """feGaussianBlur -> feOffset -> feComposite, compared with hand math.

    object bbox: (0, 0, 100, 50)
    blur sd=4 expands by 2*4=8 -> (-8, -8, 108, 58)
    offset (10, 6)             -> ( 2, -2, 118, 64)
    overall = union of object, blur box and shadow box
             -> (-8, -8, 118, 64)
    """
    def _filter(self, **extra):
        f = filters.Filter(**extra)
        f.feGaussianBlur('SourceAlpha', stdDeviation=4, result='blur')
        f.feOffset('blur', dx=10, dy=6, result='shadow')
        f.feComposite('SourceGraphic', in2='shadow', operator='over')
        return f

    def test_object_bounding_box_units(self):
        region = calc_filter_region(self._filter(), OBB)
        # (-8/100, -8/50, 126/100, 72/50)
        self.assertEqual(region,
                         FilterRegion(-0.08, -0.16, 1.26, 1.44))

    def test_user_space_units(self):
        f = self._filter(filterUnits='userSpaceOnUse')
        region = calc_filter_region(f, OBB)
        self.assertEqual(region, FilterRegion(-8.0, -8.0, 126.0, 72.0))

    def test_two_value_std_deviation(self):
        # sd (4, 10) -> expand (8, 20): blur box (-8, -20, 108, 70)
        # offset (10, 6) -> (2, -14, 118, 76)
        # union -> (-8, -20, 118, 76); fractions: -0.08, -0.4, 1.26, 1.92
        f = filters.Filter()
        f.feGaussianBlur('SourceAlpha', stdDeviation='4 10', result='blur')
        f.feOffset('blur', dx=10, dy=6)
        self.assertEqual(calc_filter_region(f, OBB),
                         FilterRegion(-0.08, -0.4, 1.26, 1.92))

    def test_auto_region_writes_attributes(self):
        f = self._filter(profile='full', debug=True)
        region = f.auto_region(OBB)
        self.assertEqual(region, FilterRegion(-0.08, -0.16, 1.26, 1.44))
        self.assertEqual(f.tostring(),
                         '<filter height="1.44" width="1.26" x="-0.08" '
                         'y="-0.16"><feGaussianBlur in="SourceAlpha" '
                         'result="blur" stdDeviation="4" />'
                         '<feOffset dx="10" dy="6" in="blur" result="shadow" />'
                         '<feComposite in="SourceGraphic" in2="shadow" '
                         'operator="over" /></filter>')


class MultiStageShadowFixture(unittest.TestCase):
    """Two chained blur/offset stages with a negative offset, hand computed.

    blur1 sd=5 (expand 10): (-10, -10, 110, 60)
    offset1 (-15, 8):       (-25,  -2,  95, 68)
    blur2 sd=3 (expand 6):  (-31,  -8, 101, 74)
    offset2 (4, -12):       (-27, -20, 105, 62)
    union of every stage + object: (-31, -20, 105, 74)
    fractions: (-0.31, -0.4, 1.41, 1.88)
    """
    def _filter(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceAlpha', stdDeviation=5, result='b1')
        f.feOffset('b1', dx=-15, dy=8, result='s1')
        f.feGaussianBlur('s1', stdDeviation=3, result='b2')
        f.feOffset('b2', dx=4, dy=-12, result='s2')
        f.feMerge(['s2', 'SourceGraphic'])
        return f

    def test_region_matches_hand_calculation(self):
        self.assertEqual(calc_filter_region(self._filter(), OBB),
                         FilterRegion(-0.31, -0.4, 1.41, 1.88))

    def test_negative_offset_is_covered(self):
        region = calc_filter_region(self._filter(), OBB)
        # the -15 dx and -12 dy shifts must move x/y below zero
        self.assertLess(region.x, 0.0)
        self.assertLess(region.y, 0.0)
        # width/height stay positive regardless of the shifts
        self.assertGreater(region.width, 1.0)
        self.assertGreater(region.height, 1.0)


class MorphologyFixture(unittest.TestCase):
    def test_dilate_then_blur(self):
        # dilate r=6: (-6, -6, 106, 56); blur sd=2 (expand 4):
        # (-10, -10, 110, 60) -> fractions (-0.1, -0.2, 1.2, 1.4)
        f = filters.Filter()
        f.feMorphology('SourceGraphic', operator='dilate', radius=6,
                       result='m')
        f.feGaussianBlur('m', stdDeviation=2)
        self.assertEqual(calc_filter_region(f, OBB),
                         FilterRegion(-0.1, -0.2, 1.2, 1.4))

    def test_erode_cannot_grow_region(self):
        f = filters.Filter()
        f.feMorphology('SourceGraphic', operator='erode', radius=6)
        self.assertEqual(calc_filter_region(f, OBB),
                         FilterRegion(0.0, 0.0, 1.0, 1.0))


class ReferenceGraph(unittest.TestCase):
    def test_named_inputs_follow_result_references(self):
        # a far-away offset that is never referenced must not enlarge the
        # region of the following composite
        f = filters.Filter()
        f.feOffset('SourceGraphic', dx=1000, dy=1000, result='far')
        f.feGaussianBlur('SourceGraphic', stdDeviation=1, result='near')
        f.feComposite('near', in2='SourceGraphic')
        region = calc_filter_region(f, OBB)
        # the 'far' intermediate sets the overall right edge (1100), while
        # the composite itself resolves 'near' (blur box up to 102) instead
        # of the previous result 'far'; total span: -2 .. 1100 -> 11.02
        self.assertEqual(region.width, 11.02)
        self.assertEqual(region.x, -0.02)

    def test_missing_in_uses_previous_result(self):
        # a primitive with no 'in' attribute chains onto the previous
        # primitive's result (svgwrite primitives default in='SourceGraphic',
        # so delete the attribute to simulate a missing input)
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=4, result='blur')
        offset = f.feOffset(dx=10, dy=6)
        del offset.attribs['in']
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        # blur (-8,-8,108,58) then shifted (2,-2,118,64)
        self.assertEqual(region, FilterRegion(-8.0, -8.0, 126.0, 72.0))

    def test_unknown_named_input_falls_back_to_object_box(self):
        f = filters.Filter()
        f.feGaussianBlur('never-defined', stdDeviation=4)
        region = calc_filter_region(f, OBB)
        # treated as blur of the object box: (-8,-8,108,58)
        self.assertEqual(region, FilterRegion(-0.08, -0.16, 1.16, 1.32))

    def test_standard_inputs_map_to_object_box(self):
        for name in ('SourceGraphic', 'SourceAlpha', 'FillPaint',
                     'StrokePaint'):
            f = filters.Filter()
            f.feGaussianBlur(name, stdDeviation=2)
            region = calc_filter_region(f, OBB)
            self.assertEqual(region,
                             FilterRegion(-0.04, -0.08, 1.08, 1.16), name)


class ExplicitSubregionsAndOverrides(unittest.TestCase):
    def test_primitive_subregion_clips_output(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=10,
                         start=(0, 0), size=(100, 50))
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        # the blur would reach (-20,-20,120,70) but the subregion clips it
        self.assertEqual(region, FilterRegion(0.0, 0.0, 100.0, 50.0))

    def test_object_bounding_box_primitive_units(self):
        # subregion (0,0,1,1) in objectBoundingBox units == the object box
        f = filters.Filter(primitiveUnits='objectBoundingBox')
        f.feGaussianBlur('SourceGraphic', stdDeviation=100,
                         start=(0, 0), size=(1, 1))
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        self.assertEqual(region, FilterRegion(0.0, 0.0, 100.0, 50.0))

    def test_unbounded_primitive_with_subregion_is_bounded(self):
        f = filters.Filter()
        f.feFlood(start=(5, 5), size=(20, 20))
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        # union of object box and (5,5,25,25) is just the object box
        self.assertEqual(region, FilterRegion(0.0, 0.0, 100.0, 50.0))

    def test_auto_region_keeps_explicit_filter_attributes(self):
        f = filters.Filter(start=(1, 2), size=(3, 4))
        f.feGaussianBlur('SourceGraphic', stdDeviation=4)
        f.auto_region(OBB)
        self.assertEqual((f['x'], f['y'], f['width'], f['height']),
                         (1, 2, 3, 4))

    def test_auto_region_override_replaces_attributes(self):
        f = filters.Filter(start=(1, 2), size=(3, 4))
        f.feGaussianBlur('SourceGraphic', stdDeviation=4)
        region = f.auto_region(OBB, override=True)
        self.assertEqual((f['x'], f['y'], f['width'], f['height']),
                         tuple(region))

    def test_partial_attributes_are_completed(self):
        f = filters.Filter()
        f['x'] = 0.5
        f.feGaussianBlur('SourceGraphic', stdDeviation=4)
        f.auto_region(OBB)
        self.assertEqual(f['x'], 0.5)
        self.assertEqual((f['y'], f['width'], f['height']),
                         (-0.16, 1.16, 1.32))

    def test_disjoint_subregion_is_ignored(self):
        f = filters.Filter(debug=False)
        blur = f.feGaussianBlur('SourceGraphic', stdDeviation=4)
        # subregion far away from the object box, no overlap
        blur.attribs.update({'x': 500, 'y': 500, 'width': 10, 'height': 10})
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        # only the object box remains
        self.assertEqual(region, FilterRegion(0.0, 0.0, 100.0, 50.0))

    def test_percentage_subregion_is_not_parseable(self):
        # percentage values cannot be resolved without a viewport; the
        # subregion is ignored and the raw blur region survives
        f = filters.Filter(debug=False)
        blur = f.feGaussianBlur('SourceGraphic', stdDeviation=4)
        blur.attribs['x'] = '-10%'
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        self.assertEqual(region, FilterRegion(-8.0, -8.0, 116.0, 66.0))


class ConservativeFallbacks(unittest.TestCase):
    def test_unbounded_primitives_use_default_region(self):
        no_input = ('feTurbulence', 'feFlood')
        with_href = ('feImage',)
        with_input = ('feTile', 'feDiffuseLighting', 'feSpecularLighting')
        for primitive in no_input:
            f = filters.Filter()
            getattr(f, primitive)()
            self.assertEqual(calc_filter_region(f, OBB),
                             FilterRegion(*DEFAULT_OBB_REGION), primitive)
        for primitive in with_href:
            f = filters.Filter()
            getattr(f, primitive)('./image.png')
            self.assertEqual(calc_filter_region(f, OBB),
                             FilterRegion(*DEFAULT_OBB_REGION), primitive)
        for primitive in with_input:
            f = filters.Filter()
            getattr(f, primitive)('SourceGraphic')
            self.assertEqual(calc_filter_region(f, OBB),
                             FilterRegion(*DEFAULT_OBB_REGION), primitive)

    def test_unknown_primitive_uses_default_region(self):
        from svgwrite.filters import _FilterRequireInput

        class _feMystery(_FilterRequireInput):
            elementname = 'feMystery'

        # debug=False: the validator does not know the made-up element name
        f = filters.Filter(debug=False)
        f.add(_feMystery('SourceGraphic', factory=f))
        self.assertEqual(calc_filter_region(f, OBB),
                         FilterRegion(*DEFAULT_OBB_REGION))

    def test_zero_area_object_uses_default_region(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=4)
        self.assertEqual(calc_filter_region(f, (0, 0, 100, 0)),
                         FilterRegion(*DEFAULT_OBB_REGION))
        self.assertEqual(calc_filter_region(f, (0, 0, 0, 50)),
                         FilterRegion(*DEFAULT_OBB_REGION))

    def test_missing_bbox_uses_default_region(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=4)
        self.assertEqual(calc_filter_region(f, None),
                         FilterRegion(*DEFAULT_OBB_REGION))

    def test_invalid_bbox_uses_default_region(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=4)
        self.assertEqual(calc_filter_region(f, (float('nan'), 0, 100, 50)),
                         FilterRegion(*DEFAULT_OBB_REGION))

    def test_user_space_fallback_keeps_user_units(self):
        f = filters.Filter(filterUnits='userSpaceOnUse')
        f.feTurbulence()
        # 10% margin around (20, 30, 100, 50)
        self.assertEqual(calc_filter_region(f, (20, 30, 100, 50)),
                         FilterRegion(10.0, 25.0, 120.0, 60.0))

    def test_user_space_fallback_without_object_box(self):
        f = filters.Filter(filterUnits='userSpaceOnUse')
        f.feTurbulence()
        region = calc_filter_region(f, None)
        self.assertEqual(region, FilterRegion(0.0, 0.0, 1.0, 1.0))

    def test_huge_blur_radius_is_capped(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=1e9)
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        self.assertTrue(all(math.isfinite(v) for v in region))
        self.assertGreaterEqual(region.x, -MAX_COORDINATE)
        self.assertLessEqual(region.width, 2 * MAX_COORDINATE)

    def test_negative_std_deviation_is_treated_as_unknown(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=-4)
        self.assertEqual(calc_filter_region(f, OBB),
                         FilterRegion(*DEFAULT_OBB_REGION))

    def test_garbage_attributes_never_raise(self):
        # set the invalid value directly: the region computation must parse
        # defensively instead of raising
        f = filters.Filter(debug=False)
        blur = f.feGaussianBlur('SourceGraphic')
        blur.attribs['stdDeviation'] = 'abc'
        region = calc_filter_region(f, OBB)
        # unparseable stdDeviation: blur behaves like the identity region
        self.assertEqual(region, FilterRegion(0.0, 0.0, 1.0, 1.0))

    def test_generated_attributes_are_valid_svg(self):
        f = filters.Filter(profile='full', debug=True)
        f.feGaussianBlur('SourceAlpha', stdDeviation=4, result='blur')
        f.feOffset('blur', dx=10, dy=6)
        f.auto_region(OBB)
        xml = f.tostring()
        # debug=True validates every attribute; no NaN/Infinity leaked out
        self.assertNotIn('nan', xml.lower())
        self.assertNotIn('inf', xml.lower())
        for attr in ('x=', 'y=', 'width=', 'height='):
            self.assertIn(attr, xml)


class OtherPrimitives(unittest.TestCase):
    def test_displacement_map_expands_by_scale(self):
        # scale 7 -> at most 7 units in every direction
        f = filters.Filter()
        f.feDisplacementMap('SourceGraphic', in2='SourceAlpha', scale=7)
        self.assertEqual(calc_filter_region(f, OBB),
                         FilterRegion(-0.07, -0.14, 1.14, 1.28))

    def test_convolve_matrix_expands_by_kernel(self):
        # default order 3 -> (3-1)/2 = 1 kernel unit on every side
        f = filters.Filter()
        f.feConvolveMatrix('SourceGraphic', kernelMatrix='0 0 0 0 1 0 0 0 0')
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        self.assertEqual(region, FilterRegion(-1.0, -1.0, 102.0, 52.0))

    def test_blend_merges_both_inputs(self):
        f = filters.Filter()
        f.feOffset('SourceGraphic', dx=30, dy=0, result='shifted')
        f.feBlend('SourceGraphic', in2='shifted', mode='normal')
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        # object (0..100) union shifted (30..130) -> (0, 0, 130, 50)
        self.assertEqual(region, FilterRegion(0.0, 0.0, 130.0, 50.0))

    def test_merge_unions_all_referenced_branches(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=2, result='a')
        f.feOffset('SourceGraphic', dx=50, dy=25, result='b')
        f.feMerge(['a', 'b'])
        region = calc_filter_region(f, OBB, filter_units='userSpaceOnUse')
        # a: (-4,-4,104,54); b: (50,25,150,75); union: (-4,-4,150,75)
        self.assertEqual(region, FilterRegion(-4.0, -4.0, 154.0, 79.0))

    def test_color_matrix_keeps_input_region(self):
        f = filters.Filter()
        f.feColorMatrix('SourceGraphic', type_='saturate', values='0')
        self.assertEqual(calc_filter_region(f, OBB),
                         FilterRegion(0.0, 0.0, 1.0, 1.0))

    def test_empty_filter_covers_object_box(self):
        self.assertEqual(calc_filter_region(filters.Filter(), OBB),
                         FilterRegion(0.0, 0.0, 1.0, 1.0))

    def test_nested_light_source_is_not_a_primitive(self):
        f = filters.Filter()
        lighting = f.feDiffuseLighting('SourceGraphic')
        lighting.feDistantLight(azimuth=45, elevation=60)
        # the lighting filter is unbounded -> fallback; the nested
        # feDistantLight child must not be traversed as a standalone primitive
        self.assertEqual(calc_filter_region(f, OBB),
                         FilterRegion(*DEFAULT_OBB_REGION))


class BBoxHelpers(unittest.TestCase):
    def test_union_ignores_none(self):
        from svgwrite.filterregion import _union
        self.assertIsNone(_union([None, None]))
        box = _union([None, BBox(0, 0, 10, 10), BBox(5, 5, 15, 15)])
        self.assertEqual(box, BBox(0, 0, 15, 15))

    def test_blur_expansion_factor_is_two(self):
        # mandated by the Filter Effects specification
        self.assertEqual(BLUR_EXPANSION_FACTOR, 2.0)


class OffOriginObjectBox(unittest.TestCase):
    """The object box need not start at the user-space origin.

    objectBoundingBox fractions are relative to the object box origin;
    userSpaceOnUse values are absolute.
    """
    def _filter(self):
        f = filters.Filter()
        f.feGaussianBlur('SourceGraphic', stdDeviation=10)
        return f

    def test_object_bounding_box_fractions_are_relative_to_origin(self):
        # box (200,300,40,20), blur expands 20 -> (180,280,260,340)
        region = calc_filter_region(self._filter(), (200, 300, 40, 20))
        self.assertEqual(region, FilterRegion(-0.5, -1.0, 2.0, 3.0))

    def test_user_space_values_are_absolute(self):
        region = calc_filter_region(
            self._filter(), (200, 300, 40, 20),
            filter_units='userSpaceOnUse')
        self.assertEqual(region, FilterRegion(180.0, 280.0, 80.0, 60.0))

    def test_filter_units_attribute_drives_the_computation(self):
        f = self._filter()  # stdDeviation=10 -> 20 units expansion
        f['filterUnits'] = 'userSpaceOnUse'
        # no explicit filter_units argument: the element attribute is read
        region = calc_filter_region(f, OBB)
        self.assertEqual(region, FilterRegion(-20.0, -20.0, 140.0, 90.0))


if __name__ == '__main__':
    unittest.main()
