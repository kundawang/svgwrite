#!/usr/bin/env python
# coding:utf-8
# Purpose: test embedding of local image assets
# Copyright (C) 2026
# License: MIT License

import base64
import struct
import unittest
import xml.etree.ElementTree as etree
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

from svgwrite.drawing import Drawing
from svgwrite.image import Image
from svgwrite.embed import (
    embed_image, embed_images, AssetCache, EmbedReport,
    EmbedError, DocumentRootError, AssetNotFoundError, PathTraversalError,
    AssetTypeError, UnsupportedAssetError, AssetTooLargeError,
    CircularReferenceError, DEFAULT_MAX_ASSET_SIZE,
    PNG_MIMETYPE, JPEG_MIMETYPE,
)

# a minimal but valid 1x1 JPEG
JPEG_DATA = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")

XLINK = '{http://www.w3.org/1999/xlink}href'


def make_png(width=2, height=2, color=(255, 0, 0, 255)):
    """Create a valid RGBA PNG with one solid color."""
    def chunk(chunk_type, data):
        return (struct.pack('>I', len(data)) + chunk_type + data
                + struct.pack('>I', zlib.crc32(chunk_type + data) & 0xFFFFFFFF))

    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    raw = b''.join(b'\x00' + bytes(color) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(raw))
            + chunk(b'IEND', b''))


def decode_data_uri(uri):
    prefix, payload = uri.split(',', 1)
    return prefix, base64.b64decode(payload)


def png_chunks(data):
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    pos = 8
    while pos < len(data):
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        yield chunk_type, data[pos + 8:pos + 8 + length]
        pos += 12 + length


def png_pixels(data):
    """Decode the raw RGBA pixels of the (filter type 0) test PNGs."""
    header = None
    idat = b''
    for chunk_type, payload in png_chunks(data):
        if chunk_type == b'IHDR':
            header = struct.unpack('>IIBBBBB', payload)
        elif chunk_type == b'IDAT':
            idat += payload
    width, height, depth, color_type = header[:4]
    assert depth == 8 and color_type == 6
    raw = zlib.decompress(idat)
    stride = width * 4 + 1
    pixels = []
    for row in range(height):
        line = raw[row * stride:(row + 1) * stride]
        assert line[0] == 0  # filter type: none
        for col in range(width):
            pixels.append(tuple(line[1 + col * 4:5 + col * 4]))
    return pixels


class AssetTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.assets = self.root / 'assets'
        self.icons = self.assets / 'icons'
        self.icons.mkdir(parents=True)
        self.outside = self.root / 'outside'
        self.outside.mkdir()

        self.red = self.icons / 'red.png'
        self.red.write_bytes(make_png(color=(255, 0, 0, 255)))
        # identical content under a different name
        self.red_copy = self.icons / 'red-copy.png'
        self.red_copy.write_bytes(make_png(color=(255, 0, 0, 255)))
        # transparent icon
        self.transparent = self.icons / 'transparent.png'
        self.transparent.write_bytes(make_png(color=(0, 0, 255, 0)))
        self.jpeg = self.icons / 'photo.jpg'
        self.jpeg.write_bytes(JPEG_DATA)
        self.secret = self.outside / 'secret.png'
        self.secret.write_bytes(make_png(color=(0, 255, 0, 255)))
        self.not_an_image = self.icons / 'broken.png'
        self.not_an_image.write_bytes(b'this is not an image')

    def tearDown(self):
        self._tmp.cleanup()

    def drawing(self, *images, size=(100, 100)):
        dwg = Drawing('drawing.svg', size=size)
        for image in images:
            dwg.add(image(dwg) if callable(image) else image)
        return dwg

    def images(self, dwg):
        return [el for el in etree.fromstring(dwg.tostring().encode())
                if el.tag.endswith('image')]


class TestSuccessfulEmbedding(AssetTestCase):
    def test_href_becomes_data_uri_and_pixels_are_preserved(self):
        dwg = Drawing(size=(100, 100))
        dwg.add(dwg.image('icons/red.png', insert=(10, 20), size=(30, 40),
                          opacity=0.5, transform='rotate(10)'))
        report = dwg.embed_images(root=self.assets)

        self.assertTrue(report.ok)
        self.assertEqual(report.embedded_count, 1)
        self.assertEqual(report.asset_count, 1)

        image = self.images(dwg)[0]
        href = image.get(XLINK)
        prefix, payload = decode_data_uri(href)
        self.assertTrue(prefix.startswith('data:image/png;'))
        self.assertIn('base64', prefix)
        self.assertEqual(payload, self.red.read_bytes())

        # size, position, transform and opacity are preserved
        self.assertEqual(image.get('x'), '10')
        self.assertEqual(image.get('y'), '20')
        self.assertEqual(image.get('width'), '30')
        self.assertEqual(image.get('height'), '40')
        self.assertEqual(image.get('opacity'), '0.5')
        self.assertEqual(image.get('transform'), 'rotate(10)')

    def test_svg_is_standalone_after_removing_assets(self):
        dwg = Drawing(size=(20, 20))
        dwg.add(dwg.image('icons/red.png', insert=(0, 0), size=(20, 20)))
        dwg.embed_images(root=self.assets)
        xml = dwg.tostring()
        self.assertNotIn('icons/red.png', xml)
        self.assertIn('data:image/png;base64,', xml)
        # parsing the output does not need any file access
        etree.fromstring(xml.encode())

    def test_transparent_icon_keeps_alpha(self):
        dwg = Drawing(size=(10, 10))
        dwg.add(dwg.image('icons/transparent.png', size=(10, 10), opacity=0.75))
        dwg.embed_images(root=self.assets)

        image = self.images(dwg)[0]
        _, payload = decode_data_uri(image.get(XLINK))
        self.assertEqual(payload, make_png(color=(0, 0, 255, 0)))
        self.assertEqual(image.get('opacity'), '0.75')

        # the alpha channel survives the round trip: parse the PNG chunks
        # and check that every pixel has alpha == 0
        self.assertEqual(png_pixels(payload), [(0, 0, 255, 0)] * 4)

    def test_jpeg_embedding(self):
        dwg = Drawing(size=(10, 10))
        dwg.add(dwg.image('icons/photo.jpg', size=(10, 10)))
        dwg.embed_images(root=self.assets)
        image = self.images(dwg)[0]
        prefix, payload = decode_data_uri(image.get(XLINK))
        self.assertTrue(prefix.startswith('data:image/jpeg;'))
        self.assertEqual(payload, JPEG_DATA)

    def test_duplicate_references_are_read_once(self):
        dwg = Drawing(size=(100, 100))
        dwg.add(dwg.image('icons/red.png', insert=(0, 0)))
        dwg.add(dwg.image('icons/red.png', insert=(10, 10)))
        dwg.add(dwg.g().add(dwg.image('icons/red.png', insert=(20, 20))))
        report = dwg.embed_images(root=self.assets)

        self.assertEqual(report.embedded_count, 3)
        self.assertEqual(report.asset_count, 1)
        self.assertEqual(report.read_count, 1)
        self.assertEqual(report.bytes_read, len(self.red.read_bytes()))
        hrefs = [image.get(XLINK) for image in self.images(dwg)]
        self.assertEqual(len(set(hrefs)), 1)

    def test_identical_content_is_deduplicated(self):
        dwg = Drawing(size=(100, 100))
        dwg.add(dwg.image('icons/red.png'))
        dwg.add(dwg.image('icons/red-copy.png'))
        report = dwg.embed_images(root=self.assets)

        self.assertEqual(report.embedded_count, 2)
        self.assertEqual(report.asset_count, 1)
        self.assertEqual(report.deduplicated_count, 1)
        hrefs = [image.get(XLINK) for image in self.images(dwg)]
        self.assertEqual(hrefs[0], hrefs[1])

    def test_deduplicate_disabled_reads_every_file(self):
        dwg = Drawing(size=(100, 100))
        dwg.add(dwg.image('icons/red.png'))
        dwg.add(dwg.image('icons/red.png'))
        report = dwg.embed_images(root=self.assets, deduplicate=False)
        self.assertEqual(report.embedded_count, 2)
        self.assertEqual(report.read_count, 2)

    def test_images_inside_defs_patterns_and_masks(self):
        dwg = Drawing(size=(100, 100))
        pattern = dwg.defs.add(dwg.pattern(id='p', size=(10, 10),
                                           patternUnits='userSpaceOnUse'))
        pattern.add(dwg.image('icons/red.png', size=(10, 10)))
        mask = dwg.defs.add(dwg.mask(id='m'))
        mask.add(dwg.image('icons/transparent.png', size=(10, 10)))
        dwg.add(dwg.rect(size=(100, 100), fill='url(#p)', mask='url(#m)'))

        report = dwg.embed_images(root=self.assets)
        self.assertEqual(report.embedded_count, 2)
        xml = dwg.tostring()
        self.assertNotIn('icons/', xml)

    def test_absolute_path_without_root(self):
        dwg = Drawing(size=(10, 10))
        dwg.add(dwg.image(str(self.red)))
        report = dwg.embed_images()
        self.assertEqual(report.embedded_count, 1)
        self.assertIn('data:image/png;base64,', dwg.tostring())

    def test_shared_cache_across_calls(self):
        cache = AssetCache()
        first = Drawing(size=(10, 10))
        first.add(first.image('icons/red.png'))
        first.embed_images(root=self.assets, cache=cache)
        second = Drawing(size=(10, 10))
        second.add(second.image('icons/red-copy.png'))
        report = second.embed_images(root=self.assets, cache=cache)
        self.assertEqual(report.read_count, 1)
        self.assertEqual(report.bytes_read, len(self.red.read_bytes()))
        self.assertEqual(report.deduplicated_count, 1)

    def test_shared_cache_respects_a_smaller_limit(self):
        cache = AssetCache()
        embed_image(Image('icons/red.png'), root=self.assets, cache=cache)
        with self.assertRaises(AssetTooLargeError):
            embed_image(Image('icons/red.png'), root=self.assets, cache=cache,
                        max_size=len(self.red.read_bytes()) - 1)

    def test_default_output_unchanged_without_embedding(self):
        dwg = Drawing(size=(10, 10))
        dwg.add(dwg.image('icons/red.png', insert=(1, 2), size=(3, 4)))
        self.assertEqual(
            dwg.tostring(),
            '<svg baseProfile="full" height="10" version="1.1" width="10" '
            'xmlns="http://www.w3.org/2000/svg" '
            'xmlns:ev="http://www.w3.org/2001/xml-events" '
            'xmlns:xlink="http://www.w3.org/1999/xlink"><defs />'
            '<image height="4" width="3" x="1" xlink:href="icons/red.png" '
            'y="2" /></svg>')

    def test_write_and_save_with_embedded_images(self):
        from io import StringIO
        dwg = Drawing(size=(10, 10))
        dwg.add(dwg.image('icons/red.png'))
        dwg.embed_images(root=self.assets)
        buffer = StringIO()
        dwg.write(buffer)
        self.assertIn('data:image/png;base64,', buffer.getvalue())
        self.assertIn('<?xml version="1.0"', buffer.getvalue())


class TestReferencesLeftUntouched(AssetTestCase):
    def test_remote_urls_are_skipped(self):
        dwg = Drawing(size=(10, 10))
        for href in ('http://example.com/a.png', 'https://example.com/a.jpg',
                     'ftp://example.com/a.png', '//cdn.example.com/a.png'):
            dwg.add(dwg.image(href))
        report = dwg.embed_images(root=self.assets)
        self.assertEqual(report.embedded_count, 0)
        self.assertEqual(report.skipped_count, 4)
        self.assertEqual([s.reason for s in report.skipped],
                         ['remote'] * 4)
        for image, href in zip(self.images(dwg),
                               ('http://example.com/a.png',
                                'https://example.com/a.jpg',
                                'ftp://example.com/a.png',
                                '//cdn.example.com/a.png')):
            self.assertEqual(image.get(XLINK), href)

    def test_existing_data_uri_is_untouched(self):
        original = 'data:image/png;base64,' + base64.b64encode(
            make_png()).decode()
        dwg = Drawing(size=(10, 10))
        dwg.add(dwg.image(original))
        report = dwg.embed_images(root=self.assets)
        self.assertEqual(report.embedded_count, 0)
        self.assertEqual(report.skipped[0].reason, 'data')
        self.assertEqual(self.images(dwg)[0].get(XLINK), original)

    def test_fragment_and_empty_hrefs_are_skipped(self):
        dwg = Drawing(size=(10, 10))
        dwg.add(dwg.image('#definition'))
        dwg.add(dwg.image(' #definition '))
        report = dwg.embed_images(root=self.assets)
        self.assertEqual(report.embedded_count, 0)
        self.assertEqual([s.reason for s in report.skipped],
                         ['fragment', 'fragment'])

    def test_embed_image_helper_returns_unchanged_value(self):
        image = Image('http://example.com/a.png')
        self.assertEqual(image.embed(), 'http://example.com/a.png')

    def test_image_embed_helper_embeds_in_place(self):
        image = Image('icons/red.png', insert=(1, 2), size=(3, 4),
                      opacity=0.5)
        href = image.embed(root=self.assets)
        self.assertTrue(href.startswith('data:image/png;base64,'))
        self.assertEqual(image['xlink:href'], href)
        self.assertEqual(image['opacity'], 0.5)


class TestErrors(AssetTestCase):
    def test_missing_file(self):
        with self.assertRaises(AssetNotFoundError) as context:
            Image('icons/missing.png').embed(root=self.assets)
        # also a FileNotFoundError for conventional callers
        self.assertIsInstance(context.exception, FileNotFoundError)
        self.assertIsInstance(context.exception, EmbedError)

    def test_relative_reference_without_root(self):
        with self.assertRaises(DocumentRootError):
            Image('icons/red.png').embed()
        self.assertIsInstance(DocumentRootError(), ValueError)

    def test_root_must_be_a_directory(self):
        with self.assertRaises(DocumentRootError):
            Image('icons/red.png').embed(root=self.red)
        with self.assertRaises(DocumentRootError):
            Image('icons/red.png').embed(root=self.root / 'missing')

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(PathTraversalError):
            Image('../outside/secret.png').embed(root=self.assets)
        with self.assertRaises(PathTraversalError):
            Image(str(self.secret)).embed(root=self.assets)

    def test_path_traversal_through_symlink_is_rejected(self):
        link = self.icons / 'link.png'
        try:
            link.symlink_to(self.secret)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links not supported')
        with self.assertRaises(PathTraversalError):
            Image('icons/link.png').embed(root=self.assets)

    def test_extension_content_mismatch(self):
        fake = self.icons / 'fake.png'
        fake.write_bytes(JPEG_DATA)
        with self.assertRaises(AssetTypeError):
            Image('icons/fake.png').embed(root=self.assets)

    def test_content_without_valid_image_format(self):
        with self.assertRaises(AssetTypeError):
            Image('icons/broken.png').embed(root=self.assets)

    def test_unsupported_extension(self):
        gif = self.icons / 'icon.gif'
        gif.write_bytes(b'GIF89a' + b'\x00' * 10)
        with self.assertRaises(UnsupportedAssetError):
            Image('icons/icon.gif').embed(root=self.assets)
        with self.assertRaises(AssetTypeError):
            Image('icons/icon.gif').embed(root=self.assets)

    def test_file_too_large_by_size(self):
        with self.assertRaises(AssetTooLargeError):
            Image('icons/red.png').embed(
                root=self.assets, max_size=len(self.red.read_bytes()) - 1)

    def test_size_limit_can_be_disabled(self):
        image = Image('icons/red.png')
        href = image.embed(root=self.assets, max_size=None)
        self.assertTrue(href.startswith('data:image/png;base64,'))

    def test_exact_size_limit_is_allowed(self):
        image = Image('icons/red.png')
        href = image.embed(root=self.assets,
                           max_size=len(self.red.read_bytes()))
        self.assertTrue(href.startswith('data:image/png;base64,'))

    def test_file_too_large_default_limit(self):
        self.assertGreater(DEFAULT_MAX_ASSET_SIZE, 0)

    def test_directory_is_not_an_image(self):
        (self.icons / 'folder.png').mkdir()
        with self.assertRaises(AssetTypeError):
            Image('icons/folder.png').embed(root=self.assets)

    def test_href_attribute_alias_is_embedded(self):
        image = Image('icons/red.png')
        del image.attribs['xlink:href']
        image.attribs['href'] = 'icons/red.png'
        report = embed_images(image, root=self.assets)
        self.assertEqual(report.embedded_count, 1)
        self.assertTrue(image.attribs['href'].startswith(
            'data:image/png;base64,'))

    def test_url_encoded_relative_path(self):
        spaced = self.icons / 'sp ace.png'
        spaced.write_bytes(self.red.read_bytes())
        href = Image('icons/sp%20ace.png').embed(root=self.assets)
        self.assertTrue(href.startswith('data:image/png;base64,'))

    def test_file_uri_is_a_local_reference(self):
        href = Image(self.red.as_uri()).embed(root=self.assets)
        self.assertTrue(href.startswith('data:image/png;base64,'))

    def test_extensionless_file_uses_content_type(self):
        noext = self.icons / 'noext'
        noext.write_bytes(self.red.read_bytes())
        href = Image('icons/noext').embed(root=self.assets)
        self.assertTrue(href.startswith('data:image/png;base64,'))

    def test_traversal_inside_root_is_allowed(self):
        href = Image('icons/../icons/red.png').embed(root=self.assets)
        self.assertTrue(href.startswith('data:image/png;base64,'))

    def test_circular_symlink(self):
        loop = self.icons / 'loop.png'
        try:
            loop.symlink_to(loop)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links not supported')
        with self.assertRaises(CircularReferenceError):
            Image('icons/loop.png').embed(root=self.assets)

    def test_circular_element_tree(self):
        dwg = Drawing(size=(10, 10), debug=False)
        group = dwg.g(debug=False)
        group.add(group)
        dwg.add(group)
        with self.assertRaises(CircularReferenceError):
            dwg.embed_images(root=self.assets)

    def test_shared_subtree_is_visited_once(self):
        dwg = Drawing(size=(10, 10))
        group = dwg.g()
        group.add(dwg.image('icons/red.png'))
        dwg.add(group)
        dwg.add(group)
        report = dwg.embed_images(root=self.assets)
        self.assertEqual(report.embedded_count, 1)
        self.assertEqual(report.read_count, 1)

    def test_image_referring_to_its_own_document(self):
        dwg = Drawing(str(self.root / 'drawing.svg'), size=(10, 10))
        dwg.add(dwg.image('drawing.svg'))
        with self.assertRaises(CircularReferenceError):
            dwg.embed_images(root=self.root)

    def test_invalid_max_size(self):
        for value in (-1, 1.5, '10', True):
            with self.assertRaises(ValueError):
                embed_images(Drawing(), max_size=value)

    def test_invalid_href_type(self):
        image = Image('x.png')
        image.attribs['xlink:href'] = 42
        with self.assertRaises(AssetTypeError):
            embed_image(image, root=self.assets)


class TestNonStrictMode(AssetTestCase):
    def test_problems_are_collected(self):
        dwg = Drawing(size=(10, 10))
        dwg.add(dwg.image('icons/red.png'))
        dwg.add(dwg.image('icons/missing.png'))
        dwg.add(dwg.image('../outside/secret.png'))
        dwg.add(dwg.image('icons/broken.png'))
        report = dwg.embed_images(root=self.assets, strict=False)

        self.assertFalse(report.ok)
        self.assertFalse(report)
        self.assertEqual(report.embedded_count, 1)
        self.assertEqual(len(report.issues), 3)
        kinds = {issue.kind for issue in report.issues}
        self.assertIn('AssetNotFoundError', kinds)
        self.assertIn('PathTraversalError', kinds)
        self.assertIn('AssetTypeError', kinds)

        hrefs = [image.get(XLINK) for image in self.images(dwg)]
        # broken references stay exactly as they were
        self.assertTrue(hrefs[0].startswith('data:image/png;base64,'))
        self.assertEqual(hrefs[1:], ['icons/missing.png',
                                     '../outside/secret.png',
                                     'icons/broken.png'])

    def test_report_repr(self):
        report = EmbedReport()
        self.assertIn('EmbedReport', repr(report))
        self.assertTrue(report.ok)


if __name__ == '__main__':
    unittest.main()
