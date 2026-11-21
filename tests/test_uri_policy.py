#!/usr/bin/env python
#coding:utf-8
# Purpose: test resource URI policy
# License: MIT License

import unittest

import svgwrite
from svgwrite.uri_policy import (
    ResourceURIPolicy,
    ResourceURIError,
    normalize_uri,
    iter_style_urls,
)


def make_policy(**kwargs):
    defaults = dict(
        allow_relative=True,
        allow_fragments=True,
        allow_data=True,
        data_mimetypes=('image/png', 'image/jpeg'),
        allowed_remote=('https://cdn.example.com',),
    )
    defaults.update(kwargs)
    return ResourceURIPolicy(**defaults)


# trusted/malicious resource matrix: (uri, allowed by make_policy())
URI_MATRIX = [
    # trusted: same package relative images
    ('icon.png', True),
    ('images/icon.png', True),
    ('images/sub/dir/icon.png', True),
    ('./images/icon.png', True),
    ('images/../icon.png', True),  # stays inside base directory
    # trusted: fragment references inside the document
    ('#shape', True),
    ('#', True),
    # trusted: empty URI references no resource
    ('', True),
    # trusted: data URI with whitelisted image MIME type
    ('data:image/png;base64,iVBORw0KGgo=', True),
    # trusted: configured remote origin, case-insensitive scheme/host
    ('https://cdn.example.com/assets/icon.png', True),
    ('HTTPS://CDN.EXAMPLE.COM/assets/icon.png', True),
    # malicious: path traversal out of the package
    ('../secret.png', False),
    ('../../etc/passwd', False),
    ('images/../../secret.png', False),
    ('..\\..\\secret.png', False),  # Windows separators
    # malicious: encoded and double encoded traversal
    ('%2e%2e/secret.png', False),
    ('%2e%2e%2fsecret.png', False),
    ('%252e%252e%252fsecret.png', False),
    ('images/%2e%2e/%2e%2e/secret.png', False),
    # malicious: local files
    ('file:///etc/passwd', False),
    ('FILE:///etc/passwd', False),
    ('/etc/passwd', False),
    ('C:\\Windows\\system32\\evil.dll', False),
    ('\\\\server\\share\\evil.png', False),  # UNC path
    # malicious: scripts
    ('javascript:alert(1)', False),
    ('JaVaScRiPt:alert(1)', False),
    ('java\tscript:alert(1)', False),  # tab smuggled scheme
    ('vbscript:msgbox(1)', False),
    ('data:text/html,<script>alert(1)</script>', False),  # MIME not whitelisted
    # malicious: remote origins not configured
    ('http://evil.com/tracker.png', False),
    ('https://evil.com/steal.js', False),
    ('//evil.com/tracker.png', False),  # protocol-relative
    ('https://cdn.example.com.evil.com/icon.png', False),  # suffix trick
    ('https://evil.com/cdn.example.com/icon.png', False),
    ('https://cdn.example.com@evil.com/icon.png', False),  # userinfo trick
    ('https://evil.com#@cdn.example.com', False),
    ('http://cdn.example.com:8080/icon.png', False),  # wrong scheme+port
]


class TestNormalizeURI(unittest.TestCase):
    def test_percent_decoding(self):
        self.assertEqual(normalize_uri('%2e%2e%2fsecret'), '../secret')

    def test_double_percent_decoding(self):
        self.assertEqual(normalize_uri('%252e%252e%252fsecret'), '../secret')

    def test_backslash_conversion(self):
        self.assertEqual(normalize_uri('..\\..\\secret'), '../../secret')

    def test_invisible_char_removal(self):
        self.assertEqual(normalize_uri('java\tscript:alert(1)'),
                         'javascript:alert(1)')

    def test_strip_whitespace(self):
        self.assertEqual(normalize_uri('  icon.png  '), 'icon.png')


class TestPolicyMatrix(unittest.TestCase):
    def test_matrix(self):
        policy = make_policy()
        for uri, allowed in URI_MATRIX:
            if allowed:
                try:
                    policy.check(uri, element='test')
                except ResourceURIError as err:
                    self.fail("trusted URI %r rejected: %s" % (uri, err))
            else:
                with self.assertRaises(ResourceURIError,
                                       msg="malicious URI %r accepted" % uri):
                    policy.check(uri, element='test')

    def test_none_and_non_string_are_no_resource(self):
        policy = make_policy()
        policy.check(None)
        policy.check(42)  # not a string, resolved elsewhere

    def test_diagnostics_carry_element_and_original_uri(self):
        policy = make_policy()
        original = '%2e%2e/secret.png'
        with self.assertRaises(ResourceURIError) as ctx:
            policy.check(original, element='image')
        err = ctx.exception
        self.assertEqual(err.element, 'image')
        self.assertEqual(err.uri, original)  # original, not normalized
        self.assertIn('image', str(err))
        self.assertIn(original, str(err))


class TestPolicyConfiguration(unittest.TestCase):
    def test_fragments_disabled(self):
        policy = make_policy(allow_fragments=False)
        with self.assertRaises(ResourceURIError):
            policy.check('#id', element='use')

    def test_relative_disabled(self):
        policy = make_policy(allow_relative=False)
        with self.assertRaises(ResourceURIError):
            policy.check('icon.png', element='image')
        policy.check('#id')  # fragments still allowed

    def test_relative_restricted_to_prefixes(self):
        policy = make_policy(allow_relative=('images/', 'icons/'))
        policy.check('images/icon.png')
        policy.check('icons/sub/icon.png')
        with self.assertRaises(ResourceURIError):
            policy.check('fonts/evil.ttf')
        with self.assertRaises(ResourceURIError):
            policy.check('icon.png')

    def test_data_disabled(self):
        policy = make_policy(allow_data=False)
        with self.assertRaises(ResourceURIError):
            policy.check('data:image/png;base64,iVBORw0KGgo=')

    def test_data_all_mimetypes(self):
        policy = make_policy(data_mimetypes=None)
        policy.check('data:text/html,<h1>hi</h1>')

    def test_absolute_paths_opt_in(self):
        policy = make_policy(allow_absolute=True)
        policy.check('/assets/icon.png')
        policy.check('/../etc/passwd')  # normalizes to '/etc/passwd'
        with self.assertRaises(ResourceURIError):
            make_policy().check('/assets/icon.png')

    def test_remote_host_only_entry(self):
        policy = make_policy(allowed_remote=('cdn.example.com',))
        policy.check('http://cdn.example.com/a.png')
        policy.check('https://cdn.example.com/a.png')
        with self.assertRaises(ResourceURIError):
            policy.check('https://other.example.com/a.png')

    def test_remote_with_port(self):
        policy = make_policy(allowed_remote=('https://cdn.example.com:8443',))
        policy.check('https://cdn.example.com:8443/a.png')
        with self.assertRaises(ResourceURIError):
            policy.check('https://cdn.example.com/a.png')

    def test_unknown_scheme_always_rejected(self):
        policy = make_policy()
        for uri in ('ftp://cdn.example.com/a.png', 'jar:file:///x!/y',
                    'gopher://old.example.com/'):
            with self.assertRaises(ResourceURIError, msg=uri):
                policy.check(uri)


class TestStyleURLExtraction(unittest.TestCase):
    def test_iter_style_urls(self):
        content = """
        @font-face { src: url("fonts/a.ttf"); }
        .icon { fill: url('#grad'); background: url(images/b.png); }
        @import "other.css";
        """
        urls = list(iter_style_urls(content))
        self.assertIn('fonts/a.ttf', urls)
        self.assertIn('#grad', urls)
        self.assertIn('images/b.png', urls)
        self.assertIn('other.css', urls)


class TestDrawingIntegration(unittest.TestCase):
    def test_no_policy_keeps_original_behavior(self):
        # without a policy even malicious URIs serialize unchanged
        dwg = svgwrite.Drawing('test.svg')
        dwg.add(dwg.image('http://evil.com/x.png', insert=(0, 0), size=(1, 1)))
        dwg.add(dwg.script(href='javascript:alert(1)'))
        dwg.add(dwg.image('../secret.png'))
        result = dwg.tostring()
        self.assertIn('http://evil.com/x.png', result)
        self.assertIn('javascript:alert(1)', result)
        self.assertIn('../secret.png', result)

    def test_allowed_resources_serialize(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.add(dwg.image('images/icon.png', insert=(0, 0), size=(10, 10)))
        dwg.add(dwg.use('#shape'))
        dwg.add(dwg.image('data:image/png;base64,iVBORw0KGgo='))
        dwg.add(dwg.image('https://cdn.example.com/icon.png'))
        result = dwg.tostring()
        self.assertIn('images/icon.png', result)
        self.assertIn('https://cdn.example.com/icon.png', result)

    def test_set_uri_policy_after_construction(self):
        dwg = svgwrite.Drawing('test.svg')
        dwg.set_uri_policy(make_policy())
        dwg.add(dwg.image('images/icon.png'))
        self.assertIn('images/icon.png', dwg.tostring())

    def test_malicious_image_blocked_at_serialization(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.add(dwg.image('http://evil.com/x.png'))
        with self.assertRaises(ResourceURIError) as ctx:
            dwg.tostring()
        self.assertEqual(ctx.exception.element, 'image')
        self.assertEqual(ctx.exception.uri, 'http://evil.com/x.png')

    def test_malicious_script_blocked(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.add(dwg.script(href='https://evil.com/steal.js'))
        with self.assertRaises(ResourceURIError) as ctx:
            dwg.tostring()
        self.assertEqual(ctx.exception.element, 'script')

    def test_malicious_hyperlink_blocked(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.add(dwg.a('javascript:alert(1)'))
        with self.assertRaises(ResourceURIError) as ctx:
            dwg.tostring()
        self.assertEqual(ctx.exception.element, 'a')

    def test_malicious_use_blocked(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.add(dwg.use('file:///etc/passwd'))
        with self.assertRaises(ResourceURIError) as ctx:
            dwg.tostring()
        self.assertEqual(ctx.exception.element, 'use')

    def test_nested_elements_checked(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        group = dwg.g()
        subgroup = dwg.g()
        subgroup.add(dwg.image('../secret.png'))
        group.add(subgroup)
        dwg.add(group)
        with self.assertRaises(ResourceURIError):
            dwg.tostring()

    def test_foreign_elements_checked(self):
        # element created standalone (own parameter, no policy) and added
        # to a drawing with a policy is checked as well
        from svgwrite.image import Image
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.add(Image('http://evil.com/x.png'))
        with self.assertRaises(ResourceURIError):
            dwg.tostring()

    def test_standalone_element_with_policy(self):
        from svgwrite.image import Image
        image = Image('http://evil.com/x.png', uri_policy=make_policy())
        with self.assertRaises(ResourceURIError):
            image.tostring()

    def test_standalone_element_without_policy_unchanged(self):
        from svgwrite.image import Image
        image = Image('http://evil.com/x.png')
        self.assertIn('http://evil.com/x.png', image.tostring())

    def test_use_with_object_reference_allowed(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        circle = dwg.circle(center=(0, 0), r=5)
        dwg.defs.add(circle)
        dwg.add(dwg.use(circle))  # resolves to '#id' fragment
        self.assertIn('xlink:href="#', dwg.tostring())

    def test_style_content_urls_checked(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.embed_stylesheet('.a { background: url("http://evil.com/x.png"); }')
        with self.assertRaises(ResourceURIError) as ctx:
            dwg.tostring()
        self.assertEqual(ctx.exception.element, 'style')

    def test_style_import_checked(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.embed_stylesheet('@import "https://evil.com/evil.css";')
        with self.assertRaises(ResourceURIError):
            dwg.tostring()

    def test_style_fragment_reference_allowed(self):
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.embed_stylesheet('.a { fill: url("#grad"); }')
        self.assertIn('url("#grad")', dwg.tostring())

    def test_stylesheet_reference_checked_on_write(self):
        import io
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.add_stylesheet('https://evil.com/evil.css', 'evil')
        with self.assertRaises(ResourceURIError) as ctx:
            dwg.write(io.StringIO())
        self.assertEqual(ctx.exception.element, 'xml-stylesheet')

    def test_allowed_stylesheet_reference_written(self):
        import io
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy())
        dwg.add_stylesheet('styles/main.css', 'main')
        out = io.StringIO()
        dwg.write(out)
        self.assertIn('href="styles/main.css"', out.getvalue())

    def test_empty_uri_deterministic(self):
        # debug=False: the SVG validator itself rejects an empty IRI
        dwg = svgwrite.Drawing('test.svg', uri_policy=make_policy(),
                               debug=False)
        dwg.add(dwg.image('', insert=(0, 0), size=(1, 1)))
        dwg.tostring()  # empty URI references no resource: allowed

    def test_cross_platform_paths_deterministic(self):
        policy = make_policy()
        # Windows style relative path inside the package is fine
        policy.check('images\\icons\\icon.png')
        # Windows drive letter is a local file reference: rejected
        with self.assertRaises(ResourceURIError):
            policy.check('C:\\assets\\icon.png')
        # UNC path is a remote/local file reference: rejected
        with self.assertRaises(ResourceURIError):
            policy.check('\\\\server\\share\\icon.png')


if __name__ == '__main__':
    unittest.main()
