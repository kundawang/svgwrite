#!/usr/bin/env python
#coding:utf-8
# Author:  mozman
# Purpose: drawing
# Created: 10.09.2010
# Copyright (C) 2010, Manfred Moitzi
# License: MIT License
"""
The *Drawing* object is the overall container for all SVG
elements. It provides the methods to store the drawing into a file or a
file-like object. If you want to use stylesheets, the reference links
to this stylesheets were also stored (`add_stylesheet`)
in the *Drawing* object.

set/get SVG attributes::

    element['attribute'] = value
    value = element['attribute']

The Drawing object also includes a defs section, add elements to the defs
section by::

    drawing.defs.add(element)

"""
import io

from svgwrite.container import SVG, Defs
from svgwrite.elementfactory import ElementFactory
from svgwrite.utils import pretty_xml
from svgwrite.audit import audit_references, ReferenceAuditError


class Drawing(SVG, ElementFactory):
    """ This is the SVG drawing represented by the top level *svg* element.

    A drawing consists of any number of SVG elements contained within the drawing
    element, stored in the *elements* attribute.

    A drawing can range from an empty drawing (i.e., no content inside of the drawing),
    to a very simple drawing containing a single SVG element such as a *rect*,
    to a complex, deeply nested collection of container elements and graphics elements.
    """
    def __init__(self, filename="noname.svg", size=('100%', '100%'), **extra):
        """
        :param string filename: filesystem filename valid for :func:`open`
        :param 2-tuple size: width, height
        :param keywords extra: additional svg-attributes for the *SVG* object

        Important (and not SVG Attributes) **extra** parameters:

        :param string profile: ``'tiny | full'`` - define the SVG baseProfile
        :param bool debug: switch validation on/off

        """
        super(Drawing, self).__init__(size=size, **extra)
        self.filename = filename
        self._stylesheets = []  # list of stylesheets appended

    def get_xml(self):
        """ Get the XML representation as `ElementTree` object.

        :return: XML `ElementTree` of this object and all its subelements

        """
        profile = self.profile
        version = self.version
        self.attribs['xmlns'] = "http://www.w3.org/2000/svg"
        self.attribs['xmlns:xlink'] = "http://www.w3.org/1999/xlink"
        self.attribs['xmlns:ev'] = "http://www.w3.org/2001/xml-events"

        self.attribs['baseProfile'] = profile
        self.attribs['version'] = version
        return super(Drawing, self).get_xml()

    def add_stylesheet(self, href, title, alternate="no", media="screen"):
        """ Add a stylesheet reference.

        :param string href: link to stylesheet <URI>
        :param string title: name of stylesheet
        :param string alternate: ``'yes'|'no'``
        :param string media: ``'all | aureal | braille | embossed | handheld | print | projection | screen | tty | tv'``

        """
        self._stylesheets.append((href, title, alternate, media))

    def audit_references(self, strict=True, max_depth=256):
        """ Audit all id/href/``url(#...)`` references of the drawing.

        Scans the complete document tree -- including the ``defs`` section
        and nested groups -- for missing references, duplicate/empty ids,
        reference type mismatches (e.g. a ``fill`` pointing at a ``rect``
        instead of a gradient) and circular ``use`` chains.  Forward
        references (definition appears after the referencing element) are
        legal and do not raise findings.

        :param bool strict: in strict mode every defect is an error; in
          non-strict mode empty ids, duplicate ids, type mismatches,
          percent-escaped fragment ids and excessive nesting are warnings
        :param int max_depth: nesting depth at which deep nesting is reported
        :returns: :class:`svgwrite.audit.AuditReport`
        """
        return audit_references(self, strict=strict, max_depth=max_depth)

    def write(self, fileobj, pretty=False, indent=2, audit=False,
              strict=True):
        """ Write XML string to `fileobj`.

        :param fileobj: a file-like object
        :param pretty: True for easy readable output
        :param indent: how much to indent if pretty is enabled, by default 2 spaces
        :param bool audit: run :meth:`audit_references` before writing; if
          the audit finds error-level problems a
          :class:`svgwrite.audit.ReferenceAuditError` is raised and nothing
          is written
        :param bool strict: strict/non-strict mode of the reference audit

        Python 3.x - set encoding at the open command::

            open('filename', 'w', encoding='utf-8')
        """
        if audit:
            self.audit_references(strict=strict).raise_if_errors()
        # write xml header
        fileobj.write('<?xml version="1.0" encoding="utf-8" ?>\n')

        # don't use DOCTYPE. It's useless. see also:
        # http://tech.groups.yahoo.com/group/svg-developers/message/48562
        # write stylesheets
        stylesheet_template = '<?xml-stylesheet href="%s" type="text/css" ' \
            'title="%s" alternate="%s" media="%s"?>\n'
        # removed map(), does not work with Python 3
        for stylesheet in self._stylesheets:
            fileobj.write(stylesheet_template % stylesheet)

        xml_string = self.tostring()
        if pretty:  # write easy readable XML file
            xml_string = pretty_xml(xml_string, indent=indent)
        fileobj.write(xml_string)

    def save(self, pretty=False, indent=2, audit=False, strict=True):
        """ Write the XML string to `self.filename`.

        :param pretty: True for easy readable output
        :param indent: how much to indent if pretty is enabled, by default 2 spaces
        :param bool audit: run :meth:`audit_references` before writing and
          raise :class:`svgwrite.audit.ReferenceAuditError` on broken refs;
          the file is not opened when the audit fails
        :param bool strict: strict/non-strict mode of the reference audit
        """
        if audit:
            self.audit_references(strict=strict).raise_if_errors()
        fileobj = io.open(self.filename, mode='w', encoding='utf-8')
        self.write(fileobj, pretty=pretty, indent=indent)
        fileobj.close()

    def saveas(self, filename, pretty=False, indent=2, audit=False,
               strict=True):
        """ Write the XML string to `filename`.

        :param string filename: filesystem filename valid for :func:`open`
        :param pretty: True for easy readable output
        :param indent: how much to indent if pretty is enabled, by default 2 spaces
        :param bool audit: run :meth:`audit_references` before writing and
          raise :class:`svgwrite.audit.ReferenceAuditError` on broken refs;
          the file is not opened when the audit fails
        :param bool strict: strict/non-strict mode of the reference audit
        """
        self.filename = filename
        self.save(pretty=pretty, indent=indent, audit=audit, strict=strict)

    def _repr_svg_(self):
        """ Show SVG in IPython, Jupyter Notebook, and Jupyter Lab

        :return: unicode XML string of this object and all its subelements

        """
        return self.tostring()
